"""Builds the shared daily content document (_id = 'YYYY-MM-DD' in IST).

Stage 1 (instant, cannot fail): procedural pools for arithmetic / pattern / art / kinesthetic,
        written with $setOnInsert. Deterministic, so concurrent writers produce identical data.
Stage 2 (background, may fail): Gemini critical-thinking pool, guarded by an owner-token lock with
        expiry and fenced writes. Users never wait on it: until it lands (or if it never does) they
        get yesterday's verified items or the bundled emergency bank.
"""
import asyncio
import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError

from app.core.clock import now_utc, shift_date
from app.core.db import get_db
from app.core.hashrng import stable_key
from app.generators import BUCKETS, arithmetic, art_params, kinesthetic, pattern, surveillance
from app.services import gemini_service, validator

log = logging.getLogger("content")
SCHEMA_VERSION = 1
POOL_SIZE = {"arithmetic": 60, "pattern": 30, "art": 8, "kinesthetic": 42}
GENERATORS = {"arithmetic": arithmetic.generate, "pattern": pattern.generate,
              "art": art_params.generate, "kinesthetic": kinesthetic.generate}
LOCK_TTL = timedelta(minutes=5)
GEN_TIMEOUT_S = 240            # must stay below LOCK_TTL
MAX_ATTEMPTS = 4
MIN_KEPT = 4                   # items a bucket needs after blind-solve to count as a Gemini bucket
ITEMS_PER_BUCKET = 8
ART_ITEMS_PER_BUCKET = 6
ART_MIN_KEPT = 3
ART_STALE_OVERLAP = 0.4        # reject an item if >40% of its readings appeared yesterday
LEVELS = [
    ("easy", "Everyday situations. The flaw is easy to spot for a beginner."),
    ("medium", "Workplace and news-style situations. One concept per item, moderately subtle."),
    ("hard", "Subtle flaws involving statistics or hidden assumptions."),
    ("expert", "Multi-step reasoning: base rates, confounders, Simpson's paradox, absolute vs relative risk."),
]
ART_LEVELS = [
    ("easy", "Simple, legible shapes: low petal counts, layers 1, the reading is fairly open."),
    ("medium", "More crossings and overlays: layers 2, the shape hides a bit."),
    ("hard", "Dense overlays: layers 3, several genuinely competing readings."),
    ("expert", "Very dense: layers 4, the shape hides well and viewers split widely."),
]
SURV_BUCKETS = ("easy", "medium", "hard")
SURV_DEFAULT = 1               # the daily job creates one character per bucket
SURV_MAX_ASK = 10              # admin "generate more" ceiling per bucket per call
SURV_LEVELS = [
    ("easy", "Open, talkative character. Obvious tells, gentle misdirection."),
    ("medium", "Guarded character with a professional mask. Reads well, hides better."),
    ("hard", "Hostile or evasive character. Respect and nerve matter; pity and flattery backfire."),
]
_BANK = json.loads((Path(__file__).resolve().parent.parent / "data" / "emergency_critical.json").read_text("utf-8"))
_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()


def build_procedural(date: str) -> dict:
    return {
        p: {BUCKETS[b]: gen(b, POOL_SIZE[p], f"{date}:{p}:{BUCKETS[b]}") for b in range(4)}
        for p, gen in GENERATORS.items()
    }


async def ensure_doc(date: str) -> dict:
    col = get_db().daily_challenges
    doc = await col.find_one({"_id": date})
    if doc is not None:
        return doc
    pools = await run_in_threadpool(build_procedural, date)
    now = now_utc()
    try:
        await col.update_one(
            {"_id": date},
            {"$setOnInsert": {
                "schema_version": SCHEMA_VERSION, "created_at": now, "updated_at": now, "status": "partial",
                "pools": pools, "sources": {p: "procedural" for p in pools}, "attempts": 0,
                "art_attempts": 0, "surv_attempts": 0,
                "art_ready": False, "surv_ready": False,
                "next_attempt_at": None, "art_next_attempt_at": None, "surv_next_attempt_at": None,
                "lock": None, "art_lock": None, "surv_lock": None,
            }}, upsert=True)
    except DuplicateKeyError:
        pass
    return await col.find_one({"_id": date})


def _bank_pools(date: str) -> dict:
    out = {}
    for b, name in enumerate(BUCKETS):
        items = [validator.validate_raw(r) for r in _BANK[name]]
        qs = [validator.to_question(it, date=date, bucket=b, key=f"fb{k}") for k, it in enumerate(items) if it]
        out[name] = sorted(qs, key=lambda q: stable_key(date, q["id"]))
    return out


async def fallback_critical(date: str) -> tuple[dict, dict]:
    """Yesterday's Gemini-verified items first, then the bundled bank. Chains can't grow: a day that
    itself used the bank is never reused as a source."""
    prev = await get_db().daily_challenges.find_one({"_id": shift_date(date, -1)}, {"pools.critical": 1, "sources": 1})
    bank = _bank_pools(date)
    pools, sources = {}, {}
    for name in BUCKETS:
        prev_src = ((prev or {}).get("sources", {}).get("critical") or {})
        prev_items = (((prev or {}).get("pools") or {}).get("critical") or {}).get(name)
        if isinstance(prev_src, dict) and prev_src.get(name) == "gemini" and prev_items:
            pools[name], sources[name] = prev_items, "fallback_yesterday"
        else:
            pools[name], sources[name] = bank[name], "fallback_bank"
    return pools, sources


async def resolve_pools(date: str) -> dict:
    """What a session reads. Never blocks on Gemini."""
    doc = await ensure_doc(date)
    pools = doc["pools"]
    need_critical = "critical" not in pools
    need_art = not doc.get("art_ready")
    if gemini_service.available() and doc.get("attempts", 0) < MAX_ATTEMPTS and need_critical:
        kick(date)
    if gemini_service.available() and doc.get("art_attempts", 0) < MAX_ATTEMPTS and need_art:
        kick(date)
    if "critical" in pools:
        return pools
    fb, _ = await fallback_critical(date)
    return {**pools, "critical": fb}


def kick(date: str) -> None:
    if date in _inflight:
        return
    _inflight.add(date)
    t = asyncio.create_task(_run(date))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _run(date: str) -> None:
    try:
        await generate_critical(date)
    except Exception:  # noqa: BLE001
        log.exception("critical generation crashed")
    try:
        await generate_art(date)
    except Exception:  # noqa: BLE001
        log.exception("art generation crashed")
    try:
        await generate_surveillance(date)
    except Exception:  # noqa: BLE001
        log.exception("surveillance generation crashed")
    finally:
        _inflight.discard(date)


async def _acquire(date: str, token: str) -> bool:
    now = now_utc()
    res = await get_db().daily_challenges.update_one(
        {"_id": date, "pools.critical": {"$exists": False}, "attempts": {"$lt": MAX_ATTEMPTS},
         "$and": [{"$or": [{"lock": None}, {"lock.expires_at": {"$lt": now}}]},
                  {"$or": [{"next_attempt_at": None}, {"next_attempt_at": {"$lte": now}}]}]},
        {"$set": {"lock": {"owner": token, "expires_at": now + LOCK_TTL}}},
    )
    return res.modified_count == 1


async def _gemini_pools(date: str, prev_meta: dict) -> tuple[dict, dict, dict]:
    avoid = (prev_meta.get("tags") or [])[:20]
    prev_stems = set(prev_meta.get("stems") or [])
    fb_all = None
    pools, sources, stems, tags = {}, {}, [], []
    any_ok = False
    for b, (name, desc) in enumerate(LEVELS):
        kept: list[dict] = []
        try:
            raws = await gemini_service.generate_items(name, desc, ITEMS_PER_BUCKET, avoid)
            seen, items = set(prev_stems), []
            for r in raws:
                it = validator.validate_raw(r)
                h = validator.stem_hash(it) if it else None
                if it and h not in seen:
                    seen.add(h)
                    items.append((it, h))
            qs = [validator.to_question(it, date=date, bucket=b, key=str(k)) for k, (it, _) in enumerate(items)]
            if qs:
                picks = await gemini_service.blind_solve(
                    [{"id": q["id"], "scenario": q["scenario"], "prompt": q["prompt"], "options": q["options"]} for q in qs])
                for q, (it, h) in zip(qs, items):
                    if picks.get(q["id"]) == q["answer"]:
                        kept.append(q)
                        stems.append(h)
                        tags.append(q["tag"])
        except Exception as e:  # noqa: BLE001
            log.warning("bucket %s failed: %r", name, e)
        if len(kept) >= MIN_KEPT:
            pools[name], sources[name] = kept, "gemini"
            any_ok = True
        else:
            if fb_all is None:
                fb_all = await fallback_critical(date)
            pools[name], sources[name] = fb_all[0][name], fb_all[1][name]
    if not any_ok:
        raise RuntimeError("no bucket passed validation")
    return pools, sources, {"stems": stems, "tags": tags}


async def generate_critical(date: str) -> str:
    """Returns 'ready' | 'skipped' | 'failed'."""
    col = get_db().daily_challenges
    await ensure_doc(date)
    token = uuid.uuid4().hex
    if not await _acquire(date, token):
        return "skipped"
    try:
        prev = await col.find_one({"_id": shift_date(date, -1)}, {"critical_meta": 1}) or {}
        pools, sources, meta = await asyncio.wait_for(
            _gemini_pools(date, prev.get("critical_meta") or {}), timeout=GEN_TIMEOUT_S)
        res = await col.update_one(   # fenced: only the current lock owner may publish
            {"_id": date, "lock.owner": token},
            {"$set": {"pools.critical": pools, "sources.critical": sources, "critical_meta": meta,
                      "status": "ready", "updated_at": now_utc(), "last_error": None, "lock": None}})
        return "ready" if res.modified_count else "skipped"
    except Exception as e:  # noqa: BLE001
        doc = await col.find_one({"_id": date}, {"attempts": 1}) or {}
        attempts = doc.get("attempts", 0) + 1
        upd = {"attempts": attempts, "last_error": repr(e)[:300], "lock": None,
               "next_attempt_at": now_utc() + timedelta(minutes=15 * attempts)}
        if attempts >= MAX_ATTEMPTS:   # stop retrying: freeze the fallback into the doc
            fb, src = await fallback_critical(date)
            upd.update({"pools.critical": fb, "sources.critical": src, "status": "ready"})
        await col.update_one({"_id": date, "lock.owner": token}, {"$set": upd})
        log.warning("critical generation failed (%d/%d): %r", attempts, MAX_ATTEMPTS, e)
        return "failed"


# ---------------------------------------------------------------- art (Gemini curves)
def _procedural_art(date: str) -> dict:
    return {BUCKETS[b]: art_params.generate(b, POOL_SIZE["art"], f"{date}:art:{BUCKETS[b]}") for b in range(4)}


async def _acquire_art(date: str, token: str) -> bool:
    now = now_utc()
    res = await get_db().daily_challenges.update_one(
        {"_id": date, "art_ready": {"$ne": True},
         "$and": [
             {"$or": [{"art_attempts": {"$exists": False}}, {"art_attempts": {"$lt": MAX_ATTEMPTS}}]},
             {"$or": [{"art_lock": None}, {"art_lock.expires_at": {"$lt": now}}]},
             {"$or": [{"art_next_attempt_at": None}, {"art_next_attempt_at": {"$lte": now}}]},
         ]},
        {"$set": {"art_lock": {"owner": token, "expires_at": now + LOCK_TTL}}},
    )
    return res.modified_count == 1


def _item_words(q: dict) -> set[str]:
    return {w for ws in (q.get("readings") or {}).values() for w in ws}


async def _gemini_art_pools(date: str, prev_meta: dict) -> tuple[dict, dict, dict]:
    avoid = (prev_meta.get("readings") or [])[:60]
    prev_words = set(prev_meta.get("readings") or [])
    procedural = await run_in_threadpool(_procedural_art, date)
    pools, sources, words = {}, {}, []
    any_ok = False
    for b, (name, desc) in enumerate(ART_LEVELS):
        kept: list[dict] = []
        try:
            raws = await gemini_service.generate_art_items(name, desc, ART_ITEMS_PER_BUCKET, avoid)
            seen: set[str] = set()
            for r in raws:
                q = art_params.validate_art_item(r, b, len(kept))
                if q is None:
                    continue
                ws = _item_words(q)
                if prev_words and len(ws & prev_words) / max(1, len(ws)) > ART_STALE_OVERLAP:
                    continue   # too close to yesterday's answers -> not fresh
                if ws & seen:
                    continue
                seen |= ws
                kept.append(q)
        except Exception as e:  # noqa: BLE001
            log.warning("art bucket %s failed: %r", name, e)
        if len(kept) >= ART_MIN_KEPT:
            pools[name], sources[name] = kept, "gemini"
            words.extend(sorted(ws for q in kept for ws in _item_words(q)))
            any_ok = True
        else:
            pools[name], sources[name] = procedural[name], "procedural"
    if not any_ok:
        raise RuntimeError("no art bucket passed validation")
    return pools, sources, {"readings": words[:200]}


async def generate_art(date: str) -> str:
    """Returns 'ready' | 'skipped' | 'failed'. Independent of critical: own lock, attempts, fallback."""
    col = get_db().daily_challenges
    await ensure_doc(date)
    token = uuid.uuid4().hex
    if not await _acquire_art(date, token):
        return "skipped"
    try:
        prev = await col.find_one({"_id": shift_date(date, -1)}, {"art_meta": 1}) or {}
        pools, sources, meta = await asyncio.wait_for(
            _gemini_art_pools(date, prev.get("art_meta") or {}), timeout=GEN_TIMEOUT_S)
        res = await col.update_one(   # fenced: only the current lock owner may publish
            {"_id": date, "art_lock.owner": token},
            {"$set": {"pools.art": pools, "sources.art": sources, "art_meta": meta, "art_ready": True,
                      "updated_at": now_utc(), "art_last_error": None, "art_lock": None}})
        return "ready" if res.modified_count else "skipped"
    except Exception as e:  # noqa: BLE001
        doc = await col.find_one({"_id": date}, {"art_attempts": 1}) or {}
        attempts = doc.get("art_attempts", 0) + 1
        upd = {"art_attempts": attempts, "art_last_error": repr(e)[:300], "art_lock": None,
               "art_next_attempt_at": now_utc() + timedelta(minutes=15 * attempts)}
        await col.update_one({"_id": date, "art_lock.owner": token}, {"$set": upd})
        log.warning("art generation failed (%d/%d): %r", attempts, MAX_ATTEMPTS, e)
        return "failed"


# ---------------------------------------------------------------- dark pillar: surveillance
def _surv_bank() -> dict:
    return {b: surveillance.bank_pool(i) for i, b in enumerate(SURV_BUCKETS)}


async def _surv_labels() -> list[str]:
    rows = await get_db().faces.find({}, {"_id": 1}).to_list(200)
    return [str(r["_id"]) for r in rows] or surveillance.DEFAULT_IMAGE_LABELS


async def _acquire_surv(date: str, token: str) -> bool:
    now = now_utc()
    res = await get_db().daily_challenges.update_one(
        {"_id": date,
         "$and": [
             {"$or": [{"surv_attempts": {"$exists": False}}, {"surv_attempts": {"$lt": MAX_ATTEMPTS}}]},
             {"$or": [{"surv_lock": None}, {"surv_lock.expires_at": {"$lt": now}}]},
             {"$or": [{"surv_next_attempt_at": None}, {"surv_next_attempt_at": {"$lte": now}}]},
         ]},
        {"$set": {"surv_lock": {"owner": token, "expires_at": now + LOCK_TTL}}},
    )
    return res.modified_count == 1


def _merge_chars(existing: list[dict], new: list[dict]) -> list[dict]:
    """Admin-added characters accumulate; duplicates (same id or label+name) are ignored."""
    out, seen_ids, seen_sig = [], set(), set()
    for c in (existing or []) + (new or []):
        sig = (c.get("image_label"), c.get("name"))
        cid = c.get("id")
        if cid in seen_ids or sig in seen_sig:
            continue
        seen_ids.add(cid)
        seen_sig.add(sig)
        out.append(c)
    return out


async def _gemini_surv_pools(date: str, prev_meta: dict, labels: list[str],
                             count: int = SURV_DEFAULT) -> tuple[dict, dict, dict]:
    avoid = (prev_meta.get("names") or []) + (prev_meta.get("labels") or []) + (prev_meta.get("personalities") or [])
    avoid = [a[:120] for a in avoid][:40]
    prev_labels = set(prev_meta.get("labels") or [])
    count = max(1, min(count, SURV_MAX_ASK))
    pools, sources = {}, {}
    names, used_labels, personalities = [], [], []
    any_ok = False
    for b, (name, desc) in enumerate(SURV_LEVELS):
        kept: list[dict] = []
        try:
            raws = await gemini_service.generate_surveillance_items(name, desc, count, avoid, labels)
            full = [c for c in (surveillance.validate_character(r, b, k) for k, r in enumerate(raws)) if c]
            for c in full:
                c["id"] = f"ai-{uuid.uuid4().hex[:10]}"   # unique across appends
            fresh = [c for c in full if c["image_label"] not in prev_labels]   # replays should feel new
            kept = fresh or full
        except Exception as e:  # noqa: BLE001
            log.warning("surveillance bucket %s failed: %r", name, e)
        if kept:
            pools[name], sources[name] = kept, "gemini"
            any_ok = True
            for c in kept:
                names.append(c["name"])
                used_labels.append(c["image_label"])
                personalities.append(c["personality"][:120])
        else:
            pools[name], sources[name] = surveillance.bank_pool(b), "bank"
            used_labels.extend(c["image_label"] for c in pools[name])
    if not any_ok:
        raise RuntimeError("no surveillance bucket passed validation")
    return pools, sources, {"names": names[:40], "labels": sorted(set(used_labels))[:60], "personalities": personalities[:20]}


async def generate_surveillance(date: str, count: int = SURV_DEFAULT, append: bool = False) -> str:
    """Daily: one character per bucket. Admin 'generate more' passes count>1 + append=True,
    which adds the new characters to the existing pool instead of replacing it."""
    col = get_db().daily_challenges
    await ensure_doc(date)
    doc = await col.find_one({"_id": date}, {"pools.surveillance": 1}) or {}
    existing = (doc.get("pools") or {}).get("surveillance")
    if existing and not append:
        return "skipped"
    token = uuid.uuid4().hex
    if not await _acquire_surv(date, token):
        return "skipped"
    try:
        prev = await col.find_one({"_id": shift_date(date, -1)}, {"surv_meta": 1}) or {}
        labels = await _surv_labels()
        pools, sources, meta = await asyncio.wait_for(
            _gemini_surv_pools(date, prev.get("surv_meta") or {}, labels, count), timeout=GEN_TIMEOUT_S)
        if append and existing:
            for b in SURV_BUCKETS:
                merged = _merge_chars([c for c in (existing.get(b) or []) if _surv_shape_ok(c)],
                                      pools.get(b) or [])
                pools[b] = merged
                sources[b] = "gemini" if any(str(c.get("id", "")).startswith("ai") for c in merged) else "bank"
        res = await col.update_one(
            {"_id": date, "surv_lock.owner": token},
            {"$set": {"pools.surveillance": pools, "sources.surveillance": sources, "surv_meta": meta,
                      "surv_ready": True, "updated_at": now_utc(), "surv_last_error": None, "surv_lock": None}})
        return "ready" if res.modified_count else "skipped"
    except Exception as e:  # noqa: BLE001
        doc = await col.find_one({"_id": date}, {"surv_attempts": 1}) or {}
        attempts = doc.get("surv_attempts", 0) + 1
        upd = {"surv_attempts": attempts, "surv_last_error": repr(e)[:300], "surv_lock": None,
               "surv_next_attempt_at": now_utc() + timedelta(minutes=15 * attempts)}
        await col.update_one({"_id": date, "surv_lock.owner": token}, {"$set": upd})
        log.warning("surveillance generation failed (%d/%d): %r", attempts, MAX_ATTEMPTS, e)
        return "failed"


def _surv_shape_ok(c: dict) -> bool:
    if not c.get("tags") or not isinstance(c.get("nodes"), dict):
        return False
    return all(isinstance(o.get("tag"), str) and o.get("outcome") in ("optimal", "neutral", "bad")
               for n in c["nodes"].values() for o in (n.get("options") or []))


async def resolve_surveillance(date: str) -> dict:
    """Never blocks on Gemini: the bundled bank is always playable. Old-schema pools are healed."""
    doc = await ensure_doc(date)
    pools = (doc.get("pools") or {}).get("surveillance") or {}
    cleaned = {b: [c for c in (pools.get(b) or []) if _surv_shape_ok(c)] for b in SURV_BUCKETS}
    if all(cleaned[b] for b in SURV_BUCKETS):
        return cleaned
    bank = _surv_bank()
    for b in SURV_BUCKETS:
        if not cleaned[b]:
            cleaned[b] = bank[b]
    if pools and gemini_service.available() and doc.get("surv_attempts", 0) < MAX_ATTEMPTS:
        await get_db().daily_challenges.update_one({"_id": date}, {"$set": {"surv_ready": False}})
        kick(date)
    return cleaned


async def reset_generation(date: str, pillar: str) -> None:
    """Admin 'regenerate': clear the readiness/backoff so the next call actually runs."""
    col = get_db().daily_challenges
    if pillar == "critical":
        await col.update_one({"_id": date}, {"$unset": {"pools.critical": "", "sources.critical": ""},
                                             "$set": {"attempts": 0, "lock": None, "next_attempt_at": None}})
    elif pillar == "art":
        await col.update_one({"_id": date}, {"$set": {"art_ready": False, "art_attempts": 0,
                                                      "art_lock": None, "art_next_attempt_at": None}})
    elif pillar == "surveillance":
        await col.update_one({"_id": date}, {"$set": {"surv_ready": False, "surv_attempts": 0,
                                                      "surv_lock": None, "surv_next_attempt_at": None}})
