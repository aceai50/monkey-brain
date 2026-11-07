"""Server-authoritative training sessions.

* Questions (WITH answers) are snapshotted into the session when it is created; the client only
  ever receives answer-free copies, and only for the pillar it has started.
* Timers are enforced from server timestamps (started_at / deadline_at). The client clock is cosmetic.
* Every state change is a compare-and-set on the previous status, so double submits, two tabs and
  retries are harmless. Everything is scoped by user_id taken from the JWT (no IDOR).
"""
from datetime import datetime, timedelta, timezone
import uuid

from bson import ObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.clock import days_between, now_utc, shift_date, today_ist, yesterday_ist
from app.core.db import get_db
from app.core.hashrng import HashRng
from app.core.security import to_object_id
from app.generators import BUCKETS
from app.generators.surveillance import SECRETS as DARK_SECRETS
from app.services import content_orchestrator as content
from app.services.adaptive_engine import (
    PICK_LEVEL, decay_for_absence, level_from_d, plan_buckets, slider_to_level, update_difficulty,
)
from app.services.customize import PILLARS, counts_for
from app.services.scoring import day_points, grade, level_weight, row_points, volume_weight

PILLAR_SECONDS = 240
BREAK_SECONDS = 30
GRACE_S = 8
STALE_PRACTICE = timedelta(hours=2)
TERMINAL = ("submitted", "expired")
HIDDEN = {"answer", "explanation", "tag", "readings", "ambiguity_note"}
DEFAULT_D = {p: 0.2 for p in PILLARS}


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def public_q(q: dict) -> dict:
    return {k: v for k, v in q.items() if k not in HIDDEN}


def level_name(level: float) -> str:
    lo = int(level)
    if abs(level - lo) < 1e-9 or lo >= 3:
        return BUCKETS[min(lo, 3)]
    return f"{BUCKETS[lo]} to {BUCKETS[lo + 1]}"


def select_questions(n: int, level: float, pool: dict[str, list[dict]]) -> list[dict]:
    """Take n questions following the easy-to-hard plan; borrow from the nearest bucket if one runs dry."""
    plan = plan_buckets(n, level)
    ptr = {b: 0 for b in range(4)}
    out: list[dict] = []
    for b in plan:
        order = sorted(range(4), key=lambda x: (abs(x - b), x))
        for cand in order:
            items = pool.get(BUCKETS[cand], [])
            if ptr[cand] < len(items):
                out.append(items[ptr[cand]])
                ptr[cand] += 1
                break
    return out


def _pillar_entry(pillar: str, level: float, questions: list[dict]) -> dict:
    return {"pillar": pillar, "level": level, "status": "pending", "questions": questions, "offered": len(questions),
            "started_at": None, "deadline_at": None, "submitted_at": None, "result": None}


def view(s: dict) -> dict:
    return {
        "id": str(s["_id"]), "mode": s["mode"], "date": s["date"], "state": s["state"],
        "difficulty": s["difficulty"], "server_now": now_utc().isoformat(),
        "pillar_seconds": PILLAR_SECONDS, "break_seconds": BREAK_SECONDS,
        "pillars": [{"pillar": p["pillar"], "status": p["status"], "level": level_name(p["level"])} for p in s["pillars"]],
        "report": s.get("report") if s["state"] == "finished" else None,
    }


# ---------------------------------------------------------------- create
async def create_circuit(user: dict, difficulty: str) -> dict:
    db = get_db()
    date = today_ist()
    existing = await db.sessions.find_one({"user_id": user["_id"], "date": date, "mode": "circuit"})
    if existing is None:
        pools = await content.resolve_pools(date)
        d = {**DEFAULT_D, **(user.get("difficulty") or {})}
        last = user.get("last_trained_date")
        if last:
            away = days_between(last, date)
            d = {p: decay_for_absence(v, away) for p, v in d.items()}
        counts = counts_for(user)   # the player's Customize choices, snapshotted into the session
        pillars = []
        for p in PILLARS:
            level = level_from_d(d[p]) if difficulty == "auto" else PICK_LEVEL[difficulty]
            pillars.append(_pillar_entry(p, level, select_questions(counts[p], level, pools[p])))
        doc = {"_id": ObjectId(), "user_id": user["_id"], "date": date, "mode": "circuit",
               "difficulty": difficulty, "state": "active", "pillars": pillars, "d_before": d,
               "created_at": now_utc(), "finished_at": None, "report": None}
        try:
            await db.sessions.insert_one(doc)
            existing = doc
        except DuplicateKeyError:
            existing = await db.sessions.find_one({"user_id": user["_id"], "date": date, "mode": "circuit"})
    s = await refresh_state(existing)
    if s["state"] == "finished":
        raise HTTPException(409, {"code": "ALREADY_DONE", "message": "Today's circuit is done. The next one opens at midnight IST."})
    return s


async def create_practice(user: dict, pillar: str, difficulty: str, slider: float) -> dict:
    sid = ObjectId()
    level = slider_to_level(slider) if difficulty == "custom" else PICK_LEVEL[difficulty]
    n = counts_for(user)[pillar]
    if pillar in ("critical", "art"):
        pools = await content.resolve_pools(today_ist())
        questions = select_questions(n, level, pools[pillar])
    else:
        gen = content.GENERATORS[pillar]
        counts = {b: 0 for b in range(4)}
        for b in plan_buckets(n, level):
            counts[b] += 1
        questions = []
        for b in range(4):  # easy first
            if counts[b]:
                questions += gen(b, counts[b], f"practice:{sid}")
    doc = {"_id": sid, "user_id": user["_id"], "date": today_ist(), "mode": "practice",
           "difficulty": difficulty if difficulty != "custom" else f"custom:{slider:.2f}",
           "state": "active", "pillars": [_pillar_entry(pillar, level, questions)],
           "created_at": now_utc(), "finished_at": None, "report": None}
    await get_db().sessions.insert_one(doc)
    return doc


# ---------------------------------------------------------------- state machine
def _zero(pillar: str, flag: str, offered: int) -> dict:
    return {"pillar": pillar, "offered": offered, "score": 0.0, "accuracy": 0.0, "completion": 0.0, "perf": 0.0,
            "correct": 0, "wrong": 0, "answered": 0, "flags": [flag], "review": []}


async def refresh_state(s: dict) -> dict:
    """Lazy finalisation: there is no sweeper on a free instance, so state is repaired on read."""
    if s["state"] != "active":
        return s
    now = now_utc()
    stale = (s["mode"] == "circuit" and s["date"] != today_ist()) or (
        s["mode"] == "practice" and now - aware(s["created_at"]) > STALE_PRACTICE)
    changed = {}
    for i, p in enumerate(s["pillars"]):
        overdue = p["status"] == "started" and now > aware(p["deadline_at"]) + timedelta(seconds=GRACE_S)
        if overdue or (p["status"] == "pending" and stale):
            changed[f"pillars.{i}.status"] = "expired"
            changed[f"pillars.{i}.result"] = _zero(p["pillar"], "no_submission", p.get("offered", len(p["questions"])))
            p["status"] = "expired"
    if changed:
        await get_db().sessions.update_one({"_id": s["_id"], "state": "active"}, {"$set": changed})
        s = await get_db().sessions.find_one({"_id": s["_id"]})
    if s["state"] == "active" and all(p["status"] in TERMINAL for p in s["pillars"]):
        s = await finalize(s)
    return s


async def get_session(user: dict, sid: str) -> dict:
    oid = to_object_id(sid, 404, "Session not found")
    s = await get_db().sessions.find_one({"_id": oid, "user_id": user["_id"]})
    if s is None:
        raise HTTPException(404, "Session not found")
    return await refresh_state(s)


async def start_pillar(user: dict, sid: str, i: int) -> dict:
    s = await get_session(user, sid)
    if s["state"] != "active":
        raise HTTPException(409, "This session is finished")
    if not 0 <= i < len(s["pillars"]):
        raise HTTPException(404, "No such set")
    p = s["pillars"][i]
    if p["status"] == "pending":
        if i > 0 and s["pillars"][i - 1]["status"] not in TERMINAL:
            raise HTTPException(409, "Finish the previous set first")
        now = now_utc()
        await get_db().sessions.update_one(
            {"_id": s["_id"], "user_id": user["_id"], "state": "active", f"pillars.{i}.status": "pending"},
            {"$set": {f"pillars.{i}.status": "started", f"pillars.{i}.started_at": now,
                      f"pillars.{i}.deadline_at": now + timedelta(seconds=PILLAR_SECONDS)}})
        s = await get_db().sessions.find_one({"_id": s["_id"]})
        p = s["pillars"][i]
    if p["status"] != "started":
        raise HTTPException(409, "This set is already finished")
    now = now_utc()
    left = max(0.0, (aware(p["deadline_at"]) - now).total_seconds())
    return {"index": i, "pillar": p["pillar"], "level": level_name(p["level"]),
            "questions": [public_q(q) for q in p["questions"]],
            "started_at": aware(p["started_at"]).isoformat(), "deadline_at": aware(p["deadline_at"]).isoformat(),
            "server_now": now.isoformat(), "seconds_left": round(left, 2)}


async def submit_pillar(user: dict, sid: str, i: int, payload) -> dict:
    s = await get_session(user, sid)
    if not 0 <= i < len(s["pillars"]):
        raise HTTPException(404, "No such set")
    p = s["pillars"][i]
    if s["state"] != "active" or p["status"] != "started":
        raise HTTPException(409, "This set was already submitted or has expired")
    now = now_utc()
    elapsed_ms = (now - aware(p["started_at"])).total_seconds() * 1000
    result = grade(p["pillar"], p["questions"], payload, elapsed_ms)
    res = await get_db().sessions.update_one(
        {"_id": s["_id"], "user_id": user["_id"], "state": "active", f"pillars.{i}.status": "started"},
        {"$set": {f"pillars.{i}.status": "submitted", f"pillars.{i}.submitted_at": now,
                  f"pillars.{i}.result": result}})
    if res.modified_count != 1:
        raise HTTPException(409, "This set was already submitted")
    s = await get_db().sessions.find_one({"_id": s["_id"]})
    finished = False
    if all(x["status"] in TERMINAL for x in s["pillars"]):
        s = await finalize(s)
        finished = True
    return {"answered": result["answered"], "finished": finished}


# ---------------------------------------------------------------- finalise
def _report(s: dict, streak: int | None) -> dict:
    results = {p["pillar"]: p["result"] for p in s["pillars"]}
    levels = {p["pillar"]: p["level"] for p in s["pillars"]}
    circuit = s["mode"] == "circuit"
    rows = []
    for p in s["pillars"]:
        r = p["result"]
        rows.append({**r, "level": level_name(p["level"]),
                     "weight": level_weight(p["level"]) if circuit else None,          # difficulty multiplier
                     "volume": volume_weight(p["pillar"], r["offered"]) if circuit else None,  # volume multiplier
                     "points": row_points(p["pillar"], r, p["level"]) if circuit else None})
    solved = sum(r["correct"] for r in results.values())
    offered = sum(r["offered"] for r in results.values())
    return {"mode": s["mode"], "pillars": rows, "total_points": day_points(results, levels) if circuit else None,
            "level_avg": round(sum(levels.values()) / len(levels), 2), "streak": streak,
            "questions_solved": solved, "questions_offered": offered}


async def finalize(s: dict) -> dict:
    db = get_db()
    user = await db.users.find_one({"_id": s["user_id"]})
    streak = None
    if s["mode"] == "circuit" and user is not None:
        last = user.get("last_trained_date")
        streak = (user.get("streak") or 0) if last == s["date"] else (
            (user.get("streak") or 0) + 1 if last == shift_date(s["date"], -1) else 1)
    report = _report(s, streak)
    flip = await db.sessions.update_one(
        {"_id": s["_id"], "state": "active"},
        {"$set": {"state": "finished", "finished_at": now_utc(), "report": report}})
    if flip.modified_count == 1 and user is not None:   # exactly one caller runs the side effects
        if s["mode"] == "circuit":
            await _circuit_effects(s, user, report, streak)
        else:
            await _practice_effects(s, user)
    return await db.sessions.find_one({"_id": s["_id"]})


def _best_rt(user: dict, results: list[dict]) -> int | None:
    rts = [r["best_rt_ms"] for r in results if r.get("best_rt_ms")]
    if not rts:
        return None
    best = user.get("best_reaction_ms")
    return min(rts) if best is None or min(rts) < best else None


async def _circuit_effects(s: dict, user: dict, report: dict, streak: int) -> None:
    db = get_db()
    pick = s["difficulty"]
    history = ((user.get("manual_history") or []) + [pick])[-7:]
    new_d = dict(s["d_before"])
    for p in s["pillars"]:
        if p["pillar"] != "art" and p["status"] == "submitted":   # skipped sets don't teach the engine
            # perf = right-to-attempted blended with attempted-to-offered (see scoring._finish)
            new_d[p["pillar"]] = update_difficulty(s["d_before"][p["pillar"]], p["result"]["perf"], pick, history)
    pts = report["total_points"]
    await db.daily_scores.update_one(
        {"_id": f"{s['date']}:{user['_id']}"},
        {"$setOnInsert": {"user_id": user["_id"], "date": s["date"], "points": pts, "mode": pick,
                          "level_avg": report["level_avg"], "created_at": now_utc()}}, upsert=True)
    sets = {"difficulty": new_d, "last_trained_date": s["date"], "streak": streak, "manual_history": history}
    rt = _best_rt(user, [p["result"] for p in s["pillars"]])
    if rt is not None:
        sets["best_reaction_ms"] = rt
    await db.users.update_one({"_id": user["_id"]}, {"$set": sets, "$inc": {
        "total_points": pts, "sessions": 1, "questions_solved": report["questions_solved"]}})


async def _practice_effects(s: dict, user: dict) -> None:
    sets = {}
    rt = _best_rt(user, [p["result"] for p in s["pillars"]])
    if rt is not None:
        sets["best_reaction_ms"] = rt
    upd = {"$inc": {"practice_sessions": 1,
                    "questions_solved": sum(p["result"]["correct"] for p in s["pillars"])}}
    if sets:
        upd["$set"] = sets
    await get_db().users.update_one({"_id": user["_id"]}, upd)


# ---------------------------------------------------------------- dark sessions (S-mode)
DARK_START_POINTS = 5
DARK_MAX_STALLS = 3
DARK_BUCKETS = ("easy", "medium", "hard")


def _dark_order(seed: str, node: dict) -> list[int]:
    return HashRng("surv", seed, node["id"]).shuffle(list(range(len(node["options"]))))


def _dark_report(s: dict) -> dict:
    d, ch = s["dark"], s["character"]
    found = {k: bool(d["revealed"].get(k)) for k in DARK_SECRETS}
    n_found = sum(found.values())
    turns = max(1, d["turns"])
    efficiency = max(0.0, 1 - d["wasted"] / turns)
    score = round(100 * (0.5 * n_found / 3 + 0.3 * d["points"] / DARK_START_POINTS + 0.2 * efficiency))
    style: dict[str, int] = {}
    for entry in d["log"]:
        k = entry.get("tag") or entry.get("approach") or "?"
        style[k] = style.get(k, 0) + 1
    return {
        "mode": "dark", "pillar": "surveillance", "outcome": d.get("outcome"),
        "dark_points": score, "points_left": d["points"], "stalls": d["stalls"],
        "turns": d["turns"], "wasted": d["wasted"], "found": found, "secrets": ch["secrets"],
        "name": ch["name"], "personality": ch["personality"], "tone": ch["tone"],
        "image_label": ch["image_label"], "style": style,
        "tags": ch["tags"], "read": ch["read"], "log": d["log"],
    }


def dark_public(s: dict) -> dict:
    "Client-safe dark state: outcomes, trait sheet and unmatched secrets stay server-side."
    ch, d = s["character"], s["dark"]
    out = {
        "id": str(s["_id"]), "mode": "dark", "state": s["state"], "difficulty": s["difficulty"],
        "outcome": d.get("outcome"), "name": ch["name"], "description": ch["description"],
        "image_label": ch["image_label"], "tone": ch["tone"],
        "image_url": s.get("image_url") or f"/static/img/faces/{ch['image_label']}.jpg",
        "points": d["points"], "max_points": DARK_START_POINTS,
        "stalls": d["stalls"], "max_stalls": DARK_MAX_STALLS,
        "can_skip": int(s.get("pool_size") or 1) > 1, "pool_size": int(s.get("pool_size") or 1),
        "found": {k: bool(d["revealed"].get(k)) for k in DARK_SECRETS},
        "secrets": {k: (ch["secrets"][k] if d["revealed"].get(k) else None) for k in DARK_SECRETS},
        "messages": [m for entry in d["log"] for m in (
            {"role": "character", "text": entry["text"]},
            {"role": "player", "text": entry["chosen"]})],
        "report": s.get("report") if s["state"] == "finished" else None,
        "server_now": now_utc().isoformat(),
    }
    if s["state"] != "active":
        out["node"] = None
        return out
    node = ch["nodes"][d["node_id"]]
    locked = set(d["locked"].get(node["id"], []))
    out["node"] = {
        "id": node["id"], "text": node["text"],
        "options": [{"key": str(i), "text": node["options"][i]["text"],
                     "tag": node["options"][i].get("tag") or node["options"][i].get("approach") or "?",
                     "locked": i in locked}
                    for i in d["order"][node["id"]]],
    }
    return out


async def _create_dark_with(user: dict, bucket: str, ch: dict, pool_size: int, attempt: int = 1) -> dict:
    db = get_db()
    face = await db.faces.find_one({"_id": ch["image_label"]})
    image_url = (f"/static/img/faces/{face['filename']}" if face
                 else f"/static/img/faces/{ch['image_label']}.jpg")
    seed = uuid.uuid4().hex
    doc = {
        "_id": ObjectId(), "user_id": user["_id"], "date": today_ist(), "mode": "dark", "pillar": "surveillance",
        "difficulty": bucket, "state": "active", "character": ch, "image_url": image_url,
        "pool_size": pool_size,
        "dark": {"node_id": ch["start"], "points": DARK_START_POINTS, "stalls": 0, "locked": {},
                 "revealed": {}, "turns": 0, "wasted": 0, "outcome": None, "log": [],
                 "attempt": attempt, "seed": seed,
                 "order": {nid: _dark_order(seed, node) for nid, node in ch["nodes"].items()}},
        "created_at": now_utc(), "finished_at": None, "report": None,
    }
    await db.sessions.insert_one(doc)
    return doc


async def create_dark(user: dict, difficulty: str) -> dict:
    """Start or resume a dark run: an active run is returned untouched (leave/resume)."""
    db = get_db()
    active = await db.sessions.find_one({"user_id": user["_id"], "mode": "dark", "state": "active"})
    if active is not None:
        return active
    date = today_ist()
    bucket = difficulty if difficulty in DARK_BUCKETS else "medium"
    pool = await content.resolve_surveillance(date)
    items = pool.get(bucket) or pool.get("medium") or []
    if not items:
        raise HTTPException(503, "No S-mode scenarios are available yet.")
    seen = set(await db.sessions.distinct(
        "character.image_label", {"user_id": user["_id"], "mode": "dark", "state": "finished"}))
    fresh = [c for c in items if c["image_label"] not in seen] or items
    runs = await db.sessions.count_documents({"user_id": user["_id"], "mode": "dark"})
    ch = fresh[runs % len(fresh)]
    return await _create_dark_with(user, bucket, ch, len(items), attempt=runs + 1)


async def dark_skip(user: dict, sid: str) -> dict:
    """Discard this run and start the next loaded character in the same bucket (cyclic)."""
    s = await get_dark(user, sid)
    if s["state"] != "active":
        raise HTTPException(409, "This run is already over")
    bucket = s["difficulty"]
    pool = await content.resolve_surveillance(today_ist())
    items = pool.get(bucket) or pool.get("medium") or []
    if len(items) < 2:
        raise HTTPException(409, "There is no other character to skip to")
    cur = s["character"]
    idx = next((i for i, c in enumerate(items)
                if c.get("id") == cur.get("id")
                or (c.get("image_label") == cur.get("image_label") and c.get("name") == cur.get("name"))), -1)
    nxt = items[(idx + 1) % len(items)]
    await get_db().sessions.delete_one(
        {"_id": s["_id"], "user_id": user["_id"], "mode": "dark", "state": "active"})
    return await _create_dark_with(user, bucket, nxt, len(items))


async def get_dark(user: dict, sid: str) -> dict:
    oid = to_object_id(sid, 404, "Session not found")
    s = await get_db().sessions.find_one({"_id": oid, "user_id": user["_id"], "mode": "dark"})
    if s is None:
        raise HTTPException(404, "Session not found")
    if s["state"] == "active":   # keep skip availability in sync with today's (healing) pool
        pool = await content.resolve_surveillance(today_ist())
        n = len(pool.get(s["difficulty"]) or [])
        if n and n != s.get("pool_size"):
            s["pool_size"] = n
            await get_db().sessions.update_one({"_id": s["_id"]}, {"$set": {"pool_size": n}})
    return s


async def dark_choose(user: dict, sid: str, key: str) -> dict:
    s = await get_dark(user, sid)
    if s["state"] != "active":
        raise HTTPException(409, "This run is already over")
    ch, d = s["character"], s["dark"]
    node = ch["nodes"][d["node_id"]]
    try:
        oi = int(key)
    except (TypeError, ValueError):
        raise HTTPException(400, "Bad option")
    if not 0 <= oi < len(node["options"]) or oi in d["locked"].get(node["id"], []):
        raise HTTPException(409, "That option is already used")
    o = node["options"][oi]
    d["turns"] += 1
    entry = {"node": node["id"], "text": node["text"], "chosen": o["text"],
             "tag": o.get("tag") or o.get("approach") or "?",
             "outcome": o["outcome"], "rationale": o["rationale"], "reveal": o["reveal"]}
    if o["outcome"] == "optimal":
        d["points"] = min(DARK_START_POINTS, d["points"] + 1)
        if o["reveal"]:
            d["revealed"][o["reveal"]] = True
        d["node_id"] = o["next"] or d["node_id"]
        if all(d["revealed"].get(k) for k in DARK_SECRETS):
            d["outcome"] = "success"
        elif o["next"] is None:
            d["outcome"] = "bored"      # the script ran out before all three were earned
    elif o["outcome"] == "neutral":
        d["stalls"] += 1
        d["wasted"] += 1
        d["locked"].setdefault(node["id"], []).append(oi)
        if d["stalls"] >= DARK_MAX_STALLS:
            d["outcome"] = "bored"
    else:
        d["points"] -= 1
        d["wasted"] += 1
        d["locked"].setdefault(node["id"], []).append(oi)
        if d["points"] <= 0:
            d["outcome"] = "burned"
    entry["points"] = d["points"]
    d["log"].append(entry)
    sets = {"dark": d}
    if d["outcome"]:
        sets.update({"state": "finished", "finished_at": now_utc()})
        sets["report"] = _dark_report({**s, "dark": d, "state": "finished"})
    out = await get_db().sessions.find_one_and_update(
        {"_id": s["_id"], "user_id": user["_id"], "state": "active"}, {"$set": sets},
        return_document=ReturnDocument.AFTER)
    return out or await get_dark(user, sid)


async def dark_restart(user: dict, sid: str) -> dict:
    s = await get_dark(user, sid)
    if s["state"] == "active":
        raise HTTPException(409, "Finish the current conversation first")
    return await create_dark(user, s["difficulty"])


# ---------------------------------------------------------------- history
HISTORY_WINDOW = 30
HISTORY_PAGE_MAX = 366


def _finished_query(user: dict, mode: str = "circuit") -> dict:
    return {"user_id": user["_id"], "mode": mode, "state": "finished", "report": {"$ne": None}}


def _day_entry(s: dict) -> dict:
    rep = s.get("report") or {}
    if s["mode"] == "dark":
        found = rep.get("found") or {}
        n = sum(1 for k in DARK_SECRETS if found.get(k))
        pts = rep.get("dark_points") or 0
        return {
            "date": s["date"], "difficulty": s.get("difficulty"), "total_points": pts,
            "level_avg": None, "streak": None, "outcome": rep.get("outcome"),
            "questions_solved": n, "questions_offered": len(DARK_SECRETS), "dark": True,
            "pillars": [{"pillar": "surveillance", "level": s.get("difficulty"), "correct": n,
                         "offered": len(DARK_SECRETS), "accuracy": round(n / len(DARK_SECRETS), 3),
                         "score": pts, "weight": None, "volume": None, "points": pts, "review": []}],
        }
    return {
        "date": s["date"], "difficulty": s.get("difficulty"),
        "total_points": rep.get("total_points"), "level_avg": rep.get("level_avg"),
        "streak": rep.get("streak"),
        "questions_solved": rep.get("questions_solved"), "questions_offered": rep.get("questions_offered"),
        "pillars": rep.get("pillars", []),
    }


async def history(user: dict, days: int, before: str | None, limit: int, include_archived: bool,
                  mode: str = "circuit") -> dict:
    """Finished runs, newest day first. Default window is the last `days` (30) IST days;
    anything older is 'archived' and only returned when include_archived is set."""
    days = max(1, min(days, 365))
    limit = max(1, min(limit, HISTORY_PAGE_MAX))
    mode = mode if mode in ("circuit", "dark") else "circuit"
    floor = shift_date(today_ist(), -(days - 1))
    q = _finished_query(user, mode)
    if before:
        q["date"] = {"$lt": before} if include_archived else {"$lt": before, "$gte": floor}
    elif not include_archived:
        q["date"] = {"$gte": floor}
    rows = await get_db().sessions.find(q).sort("date", -1).to_list(limit)
    entries = [_day_entry(s) for s in rows]
    archived_days = 0
    if not include_archived:
        archived_days = await get_db().sessions.count_documents(
            {**_finished_query(user, mode), "date": {"$lt": floor}})
    return {
        "window_days": days, "include_archived": include_archived, "mode": mode,
        "entries": entries,
        "next_before": entries[-1]["date"] if len(rows) >= limit else None,
        "archived_days": archived_days,
    }


# ---------------------------------------------------------------- dashboard
async def dashboard(user: dict) -> dict:
    db = get_db()
    date = today_ist()
    today = await db.sessions.find_one({"user_id": user["_id"], "date": date, "mode": "circuit"})
    today_view = view(await refresh_state(today)) if today else None
    since = shift_date(date, -6)
    rows = await db.daily_scores.find({"user_id": user["_id"], "date": {"$gte": since}}).to_list(10)
    by_date = {r["date"]: r["points"] for r in rows}
    history = [{"date": shift_date(since, k), "points": by_date.get(shift_date(since, k))} for k in range(7)]
    streak = user.get("streak") or 0
    if user.get("last_trained_date") not in (date, yesterday_ist()):
        streak = 0
    d = {**DEFAULT_D, **(user.get("difficulty") or {})}
    counts = counts_for(user)
    multipliers = {p: {"level": round(d[p] * 100), "difficulty": level_weight(level_from_d(d[p])),
                       "count": counts[p], "volume": volume_weight(p, counts[p])} for p in PILLARS}
    today_report = (today_view or {}).get("report") or {}
    return {
        "date": date, "today": today_view, "streak": streak,
        "total_points": user.get("total_points", 0), "sessions": user.get("sessions", 0),
        "best_reaction_ms": user.get("best_reaction_ms"), "history": history,
        "difficulty": {p: round(v * 100) for p, v in d.items()},
        "today_points": by_date.get(date),
        "questions_solved": user.get("questions_solved", 0),
        "today_solved": today_report.get("questions_solved"), "today_offered": today_report.get("questions_offered"),
        "multipliers": multipliers,
    }
