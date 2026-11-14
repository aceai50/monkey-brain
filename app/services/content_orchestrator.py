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
import time
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from pymongo.errors import DuplicateKeyError

from app.core.clock import now_utc, shift_date, today_ist
from app.core.config import settings
from app.core.db import get_db
from app.core.hashrng import stable_key
from app.generators import BUCKETS, act_deceive, arithmetic, art_params, kinesthetic, pattern, surveillance
from app.services import gemini_service, generation_log, observability, validator

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
ACT_BUCKETS = ("easy", "medium", "hard")
ACT_MAX_ASK = 6
ACT_NODES_PER_CALL = 2         # chunk size: keeps every request inside the 2048-token Groq completion budget
ACT_BUCKET_TIMEOUT_S = 600     # per-difficulty wall clock; easy->medium->hard run as a queue so early work is kept
ACT_BUCKET_LOCK_TTL = timedelta(minutes=12)   # must outlive ACT_BUCKET_TIMEOUT_S
ACT_BUCKET_ETA = {"easy": 300, "medium": 450, "hard": 600}
# (name, desc, cast_size, facts_count, glimpsed_count, turns)
ACT_LEVELS = [
    ("easy", "Open, chatty observer. Obvious probes, low scrutiny.", 1, 4, 2, 4),
    ("medium", "Two observers who compare notes; one is guarded.", 2, 5, 2, 5),
    ("hard", "Three observers: an expert and someone who knows the real you.", 3, 6, 2, 6),
]
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
                "art_attempts": 0, "surv_attempts": 0, "ad_attempts": 0,
                "art_ready": False, "surv_ready": False, "ad_ready": False,
                "next_attempt_at": None, "art_next_attempt_at": None, "surv_next_attempt_at": None,
                "ad_next_attempt_at": None,
                "ad_buckets": {b: {"ready": False, "attempts": 0, "source": None, "last_error": None,
                                   "next_attempt_at": None, "lock": None} for b in ACT_BUCKETS},
                "lock": None, "art_lock": None, "surv_lock": None, "ad_lock": None,
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
    """The most recent real AI pool from the previous 7 days, then the bundled bank.
    A day that itself used the bank is never reused as a source, so chains can't grow."""
    recent = await _recent_pools("critical", date, lambda q: bool(q.get("options")))
    bank = _bank_pools(date)
    pools, sources = {}, {}
    for name in BUCKETS:
        if recent and recent.get(name):
            pools[name], sources[name] = recent[name], "fallback_recent"
        else:
            pools[name], sources[name] = bank[name], "fallback_bank"
    return pools, sources


async def resolve_pools(date: str) -> dict:
    """What a session reads. Never blocks on Gemini."""
    doc = await ensure_doc(date)
    pools = doc["pools"]
    ready = ("critical" in pools and doc.get("art_ready") and doc.get("surv_ready")
             and doc.get("ad_ready"))
    if not ready and gemini_service.available():
        kick(date)   # one kick runs every unready pillar; per-pillar locks dedupe it
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


PILLAR_GENERATORS = (("critical", "generate_critical"), ("art", "generate_art"),
                     ("surveillance", "generate_surveillance"), ("act_deceive", "generate_act"))


async def run_all(date: str, trigger: str = "auto") -> None:
    """Generate every unready pillar in order, logging each run to `gen_jobs`."""
    for pillar, fn_name in PILLAR_GENERATORS:
        gen = globals()[fn_name]
        trace = observability.new_trace()
        observability.start_run(pillar, date, trace_id=trace, trigger=trigger)
        job = await generation_log.start_job(pillar, date, trigger=trigger, trace_id=trace)
        try:
            result = await gen(date)
        except Exception as e:  # noqa: BLE001
            log.exception("%s generation crashed", pillar)
            await generation_log.finish_job(job, pillar, date, "failed", error=repr(e))
            observability.finish_run("failed")
            continue
        await generation_log.finish_job(job, pillar, date, result)
        observability.finish_run(result)
        if result == "ready":
            # deliberate pacing between pillars: keeps a single run gentle on the providers
            await asyncio.sleep(settings.generation_pace_s)


async def _run(date: str) -> None:
    try:
        await run_all(date, "auto")
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


def _is_ai_source(v) -> bool:
    if isinstance(v, dict):
        return any(_is_ai_source(x) for x in v.values())
    return isinstance(v, str) and ("gemini" in v or "groq" in v)


def _usage_label(fallback: str = "gemini") -> str:
    """e.g. 'gemini:gemini-2.5-flash' or 'groq:openai/gpt-oss-120b'."""
    u = gemini_service.last_used()
    return f"{u['provider']}:{u['model']}" if u else fallback


async def _recent_pools(pillar: str, date: str, valid, days: int = 7):
    """Reuse the most recent real AI-generated pool from the last N days (fallback only)."""
    db = get_db()
    for k in range(1, days + 1):
        d = shift_date(date, -k)
        doc = await db.daily_challenges.find_one({"_id": d})
        if not doc:
            continue
        src = (doc.get("sources") or {}).get(pillar)
        if not _is_ai_source(src):
            continue
        pools = (doc.get("pools") or {}).get(pillar) or {}
        cleaned = {b: [x for x in (pools.get(b) or []) if valid(x)] for b in pools}
        if cleaned and all(cleaned.get(b) for b in cleaned):
            return cleaned
    return None


async def _gemini_pools(date: str, prev_meta: dict) -> tuple[dict, dict, dict]:
    avoid = (prev_meta.get("tags") or [])[:20]
    prev_stems = set(prev_meta.get("stems") or [])
    fb_all = None
    pools, sources, stems, tags = {}, {}, [], []
    any_ok = False
    for b, (name, desc) in enumerate(LEVELS):
        kept: list[dict] = []
        used: set[str] = set()
        try:
            raws = await gemini_service.generate_items(name, desc, ITEMS_PER_BUCKET, avoid)
            used.add(_usage_label())
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
                used.add(_usage_label())
                for q, (it, h) in zip(qs, items):
                    if picks.get(q["id"]) == q["answer"]:
                        kept.append(q)
                        stems.append(h)
                        tags.append(q["tag"])
        except Exception as e:  # noqa: BLE001
            log.warning("bucket %s failed: %r", name, e)
        if len(kept) >= MIN_KEPT:
            pools[name], sources[name] = kept, " + ".join(sorted(used)) or "gemini"
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
    if not gemini_service.available():      # no key: publish the bank/yesterday fallback
        fb, src = await fallback_critical(date)
        await col.update_one({"_id": date}, {"$set": {
            "pools.critical": fb, "sources.critical": src, "status": "ready",
            "updated_at": now_utc(), "last_error": None, "lock": None}})
        return "ready"
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
        used = ""
        try:
            raws = await gemini_service.generate_art_items(name, desc, ART_ITEMS_PER_BUCKET, avoid)
            used = _usage_label()
            seen: set[str] = set()
            for r in raws:
                q = art_params.validate_art_item(r, b, len(kept), name)
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
            pools[name], sources[name] = kept, used or "gemini"
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
    if not gemini_service.available():      # no key: publish the procedural pool
        await col.update_one({"_id": date}, {"$set": {
            "pools.art": build_procedural(date)["art"],
            "sources.art": {b: "procedural" for b in BUCKETS}, "art_ready": True,
            "updated_at": now_utc(), "art_last_error": None, "art_lock": None}})
        return "ready"
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
        used = ""
        try:
            raws = await gemini_service.generate_surveillance_items(name, desc, count, avoid, labels)
            used = _usage_label()
            full = [c for c in (surveillance.validate_character(r, b, k) for k, r in enumerate(raws)) if c]
            for c in full:
                c["id"] = f"ai-{uuid.uuid4().hex[:10]}"   # unique across appends
            fresh = [c for c in full if c["image_label"] not in prev_labels]   # replays should feel new
            kept = fresh or full
        except Exception as e:  # noqa: BLE001
            log.warning("surveillance bucket %s failed: %r", name, e)
        if kept:
            pools[name], sources[name] = kept, used or "gemini"
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
    if not gemini_service.available():      # no key: publish the bank, stay playable
        if existing and append:
            return "skipped"
        await col.update_one({"_id": date}, {"$set": {
            "pools.surveillance": _surv_bank(), "sources.surveillance": {b: "bank" for b in SURV_BUCKETS},
            "surv_ready": True, "updated_at": now_utc(), "surv_last_error": None, "surv_lock": None}})
        return "ready"
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
                new_src = sources.get(b)
                merged = _merge_chars([c for c in (existing.get(b) or []) if _surv_shape_ok(c)],
                                      pools.get(b) or [])
                pools[b] = merged
                sources[b] = new_src if any(str(c.get("id", "")).startswith("ai") for c in merged) else "bank"
        res = await col.update_one(
            {"_id": date, "surv_lock.owner": token},
            {"$set": {"pools.surveillance": pools, "sources.surveillance": sources, "surv_meta": meta,
                      "surv_ready": True, "updated_at": now_utc(), "surv_last_error": None, "surv_lock": None}})
        _invalidate_dark_stack("surveillance")
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


# ---------------------------------------------------------------- dark scenario stack
DARK_WINDOW_DAYS = 7
_STACK_TTL_S = 60.0
_STACK_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def _invalidate_dark_stack(pillar: str) -> None:
    for k in [k for k in _STACK_CACHE if k[0] == pillar]:
        _STACK_CACHE.pop(k, None)


async def dark_stack(pillar: str, difficulty: str, days: int = DARK_WINDOW_DAYS) -> list[dict]:
    """Newest-first stack of usable scenarios across the unarchived window.

    Cached in-process for ~60s so cycling next/prev doesn't re-scan the DB; invalidated when
    an admin (re)generates that pillar. Falls back to the bundled bank only if the whole
    window has no AI content.
    """
    key = (pillar, difficulty, today_ist())
    hit = _STACK_CACHE.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    valid = _surv_shape_ok if pillar == "surveillance" else _act_shape_ok
    db = get_db()
    stack: list[dict] = []
    for k in range(days):
        d = shift_date(today_ist(), -k)
        doc = await db.daily_challenges.find_one({"_id": d})
        if not doc:
            continue
        src = (doc.get("sources") or {}).get(pillar)
        if not _is_ai_source(src):
            continue
        pools = (doc.get("pools") or {}).get(pillar) or {}
        for sc in pools.get(difficulty) or []:
            if valid(sc):
                stack.append({"scenario": sc, "date": d, "index": len(stack)})
    if not stack:
        idx = ("easy", "medium", "hard").index(difficulty) if difficulty in ("easy", "medium", "hard") else 1
        bank = (surveillance.bank_pool(idx) if pillar == "surveillance" else act_deceive.bank_pool(idx))
        for sc in bank:
            stack.append({"scenario": sc, "date": today_ist(), "index": len(stack)})
    _STACK_CACHE[key] = (time.monotonic() + _STACK_TTL_S, stack)
    return stack


async def resolve_surveillance(date: str) -> dict:
    """Never blocks: today's pool, else the most recent AI pool (7 days), else the bank."""
    doc = await ensure_doc(date)
    pools = (doc.get("pools") or {}).get("surveillance") or {}
    cleaned = {b: [c for c in (pools.get(b) or []) if _surv_shape_ok(c)] for b in SURV_BUCKETS}
    if all(cleaned[b] for b in SURV_BUCKETS):
        return cleaned
    recent = await _recent_pools("surveillance", date, _surv_shape_ok)
    bank = _surv_bank()
    for b in SURV_BUCKETS:
        if not cleaned[b]:
            cleaned[b] = (recent or {}).get(b) or bank[b]
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
    elif pillar == "act_deceive":
        await col.update_one({"_id": date}, {"$set": {"ad_ready": False, "ad_attempts": 0,
                                                      "ad_lock": None, "ad_next_attempt_at": None,
                                                      "ad_buckets": {b: _bucket_default() for b in ACT_BUCKETS}}})


# ---------------------------------------------------------------- dark pillar: act & deceive
def _act_bank() -> dict:
    return {b: act_deceive.bank_pool(i) for i, b in enumerate(ACT_BUCKETS)}


def _act_shape_ok(sc) -> bool:
    return (isinstance(sc, dict) and isinstance(sc.get("nodes"), dict) and sc.get("cast")
            and sc.get("facts") and sc.get("role") and sc.get("version") == act_deceive.VERSION)


async def resolve_act(date: str) -> dict:
    """Never blocks: today's pool, else the most recent AI pool (7 days), else the bank.
    Kicks generation in the background when today's pool is missing."""
    doc = await ensure_doc(date)
    pools = (doc.get("pools") or {}).get("act_deceive") or {}
    cleaned = {b: [sc for sc in (pools.get(b) or []) if _act_shape_ok(sc)] for b in ACT_BUCKETS}
    if all(cleaned[b] for b in ACT_BUCKETS):
        return cleaned
    recent = await _recent_pools("act_deceive", date, _act_shape_ok)
    bank = _act_bank()
    for b in ACT_BUCKETS:
        if not cleaned[b]:
            cleaned[b] = (recent or {}).get(b) or bank[b]
    buckets = doc.get("ad_buckets") or {}
    eligible = any(not _act_bucket_ok(pools, b)
                   and (buckets.get(b) or {}).get("attempts", 0) < MAX_ATTEMPTS for b in ACT_BUCKETS)
    if gemini_service.available() and eligible:
        kick(date)
    return cleaned


def _bucket_default(source: str | None = None, ready: bool = False) -> dict:
    return {"ready": ready, "attempts": 0, "source": source, "last_error": None,
            "next_attempt_at": None, "lock": None}


def _act_bucket_ok(existing: dict, name: str) -> bool:
    return bool(existing) and any(_act_shape_ok(sc) for sc in (existing.get(name) or []))


async def _acquire_act_bucket(date: str, name: str, token: str) -> bool:
    now = now_utc()
    f = f"ad_buckets.{name}"
    res = await get_db().daily_challenges.update_one(
        {"_id": date,
         "$and": [
             {"$or": [{f"{f}.attempts": {"$exists": False}}, {f"{f}.attempts": {"$lt": MAX_ATTEMPTS}}]},
             {"$or": [{f"{f}.lock": None}, {f"{f}.lock.expires_at": {"$lt": now}}]},
             {"$or": [{f"{f}.next_attempt_at": None}, {f"{f}.next_attempt_at": {"$lte": now}}]},
         ]},
        {"$set": {f"{f}.lock": {"owner": token, "expires_at": now + ACT_BUCKET_LOCK_TTL}}},
    )
    return res.modified_count == 1


def _act_meta_bits(kept: list[dict]) -> dict:
    names, labels, facts = [], [], []
    for sc in kept:
        names.append(sc["role"][:80])
        labels.extend(c.get("image_label", "") for c in sc["cast"])
        facts.extend(f["id"] for f in sc["facts"])
    return {"names": names, "labels": sorted(set(labels)), "facts": sorted(set(facts))}


async def _fail_act_bucket(date: str, name: str, token: str, err: str) -> None:
    col = get_db().daily_challenges
    f = f"ad_buckets.{name}"
    doc = await col.find_one({"_id": date}, {f: 1}) or {}
    attempts = (doc.get("ad_buckets") or {}).get(name, {}).get("attempts", 0) + 1
    await col.update_one({"_id": date, f"{f}.lock.owner": token},
                         {"$set": {f"{f}.lock": None, f"{f}.attempts": attempts, f"{f}.ready": False,
                                   f"{f}.last_error": err,
                                   f"{f}.next_attempt_at": now_utc() + timedelta(minutes=15 * attempts),
                                   "updated_at": now_utc()}})


async def _gen_act_bucket(date: str, name: str, level, avoid: list[str], labels: list[str],
                          wanted: int, append: bool, token: str) -> tuple[list[dict], str, dict]:
    """Generate one difficulty under its own timeout and store it atomically the moment it is
    complete, so a later failure never discards earlier work. Caller must hold the bucket lock."""
    col = get_db().daily_challenges
    f = f"ad_buckets.{name}"
    try:
        kept, used = await asyncio.wait_for(_act_bucket(level, avoid, labels, wanted),
                                            ACT_BUCKET_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        await _fail_act_bucket(date, name, token, repr(e)[:300])
        log.warning("act bucket %s failed: %r", name, e)
        return [], "", {}
    if not kept:
        await _fail_act_bucket(date, name, token, "no valid scenario passed validation")
        return [], "", {}
    source = " + ".join(sorted(used)) or "gemini"
    bits = _act_meta_bits(kept)
    if append:
        prior = ((await col.find_one({"_id": date}, {"pools.act_deceive": 1}) or {})
                 .get("pools") or {}).get("act_deceive", {}).get(name) or []
        kept = _merge_acts([sc for sc in prior if _act_shape_ok(sc)], kept)
    await col.update_one(
        {"_id": date, f"{f}.lock.owner": token},
        {"$set": {f"pools.act_deceive.{name}": kept, f"sources.act_deceive.{name}": source,
                  f"{f}.lock": None, f"{f}.ready": True, f"{f}.attempts": 0, f"{f}.source": source,
                  f"{f}.last_error": None, f"{f}.next_attempt_at": None,
                  f"{f}.updated_at": now_utc(), "updated_at": now_utc()}})
    _invalidate_dark_stack("act_deceive")
    return kept, source, bits


async def _publish_act_bank(date: str) -> None:
    await get_db().daily_challenges.update_one({"_id": date}, {"$set": {
        "pools.act_deceive": _act_bank(), "sources.act_deceive": {b: "bank" for b in ACT_BUCKETS},
        "ad_buckets": {b: _bucket_default("bank", True) for b in ACT_BUCKETS},
        "ad_ready": True, "updated_at": now_utc(), "ad_last_error": None}})


async def _act_run_state(date: str) -> tuple[dict, dict, list[str]]:
    col = get_db().daily_challenges
    cur = await col.find_one({"_id": date}, {"pools.act_deceive": 1, "ad_buckets": 1, "ad_meta": 1}) or {}
    prev = await col.find_one({"_id": shift_date(date, -1)}, {"ad_meta": 1}) or {}
    existing = (cur.get("pools") or {}).get("act_deceive") or {}
    meta, pm = cur.get("ad_meta") or {}, prev.get("ad_meta") or {}
    avoid = ((pm.get("names") or []) + (pm.get("labels") or []) + (pm.get("facts") or [])
             + (meta.get("names") or []) + (meta.get("labels") or []) + (meta.get("facts") or []))
    return existing, cur.get("ad_buckets") or {}, [a[:120] for a in avoid][:40]


async def _recompute_ad_ready(date: str) -> str:
    col = get_db().daily_challenges
    existing = ((await col.find_one({"_id": date}, {"pools.act_deceive": 1}) or {})
                .get("pools") or {}).get("act_deceive") or {}
    ready = {b: _act_bucket_ok(existing, b) for b in ACT_BUCKETS}
    await col.update_one({"_id": date}, {"$set": {"ad_ready": all(ready.values()),
                                                  "updated_at": now_utc()}})
    return "ready" if all(ready.values()) else ("partial" if any(ready.values()) else "failed")


async def generate_act(date: str, count: int = 1, append: bool = False, force: bool = False) -> str:
    """Sequential difficulty queue: easy -> medium -> hard. Each difficulty is generated and stored
    atomically, so a slow or failing difficulty never discards the ones already done. Completed
    buckets are skipped unless append/force."""
    await ensure_doc(date)
    if not gemini_service.available():
        await _publish_act_bank(date)
        return "ready"
    labels = await _surv_labels()
    wanted = max(1, min(count, ACT_MAX_ASK))
    existing, buckets, avoid = await _act_run_state(date)
    meta_acc = {"names": [], "labels": [], "facts": []}
    for name, level in zip(ACT_BUCKETS, ACT_LEVELS):
        already = _act_bucket_ok(existing, name) or buckets.get(name, {}).get("ready")
        if already and not append and not force:
            continue
        token = uuid.uuid4().hex
        if not await _acquire_act_bucket(date, name, token):
            continue
        kept, source, bits = await _gen_act_bucket(date, name, level, avoid, labels, wanted, append, token)
        if kept:
            avoid = (avoid + bits["names"] + bits["labels"] + bits["facts"])[:40]
            for k in meta_acc:
                meta_acc[k] = sorted(set(meta_acc[k] + bits[k]))
    col = get_db().daily_challenges
    cur_meta = (await col.find_one({"_id": date}, {"ad_meta": 1}) or {}).get("ad_meta") or {}
    merged = {k: sorted(set((cur_meta.get(k) or []) + meta_acc[k])) for k in ("names", "labels", "facts")}
    await col.update_one({"_id": date}, {"$set": {"ad_meta": {
        "names": merged["names"][:40], "labels": merged["labels"][:40],
        "facts": merged["facts"][:80]}}})
    return await _recompute_ad_ready(date)


async def generate_act_bucket(date: str, name: str, count: int = 1, append: bool = False,
                              force: bool = False) -> str:
    """Manual single-difficulty Act & Deceive generation (admin). Stores just that difficulty."""
    if name not in ACT_BUCKETS:
        return "failed"
    await ensure_doc(date)
    if not gemini_service.available():
        await _publish_act_bank(date)
        return "ready"
    labels = await _surv_labels()
    wanted = max(1, min(count, ACT_MAX_ASK))
    existing, buckets, avoid = await _act_run_state(date)
    if (_act_bucket_ok(existing, name) or buckets.get(name, {}).get("ready")) and not append and not force:
        return "skipped"
    token = uuid.uuid4().hex
    if not await _acquire_act_bucket(date, name, token):
        return "skipped"
    level = ACT_LEVELS[ACT_BUCKETS.index(name)]
    kept, source, bits = await _gen_act_bucket(date, name, level, avoid, labels, wanted, append, token)
    if kept:
        col = get_db().daily_challenges
        cur_meta = (await col.find_one({"_id": date}, {"ad_meta": 1}) or {}).get("ad_meta") or {}
        merged = {k: sorted(set((cur_meta.get(k) or []) + bits[k]))[:80] for k in ("names", "labels", "facts")}
        await col.update_one({"_id": date}, {"$set": {"ad_meta": merged}})
    await _recompute_ad_ready(date)
    return "ready" if kept else "failed"


async def reset_act_bucket(date: str, name: str) -> None:
    if name not in ACT_BUCKETS:
        return
    await get_db().daily_challenges.update_one(
        {"_id": date}, {"$set": {f"ad_buckets.{name}": _bucket_default()}})


def _act_raw(preview: dict, script: dict) -> dict:
    def cond(atoms):
        parts = []
        for a in atoms or []:
            atom = {k: v for k, v in a.items() if v is not None}
            if atom:
                parts.append(atom)
        return {"all": parts} if parts else {}

    nodes = []
    for n in script.get("nodes") or []:
        kind = n.get("kind") or "scene"
        node = {"id": n["id"], "kind": kind, "line": n.get("line") or "", "next": n.get("next")}
        if kind == "check":
            node.update({"speaker": n.get("speaker"), "fact": n.get("fact"),
                         "exposed_line": n.get("exposed_line") or "",
                         "held_line": n.get("held_line") or "", "penalty": n.get("penalty") or 1})
        elif kind != "end":
            node["speaker"] = n.get("speaker")
            node["options"] = [{
                "kind": o["kind"], "tag": o["tag"], "text": o.get("text", ""),
                "fact": o.get("fact"), "value": o.get("value"), "works_if": cond(o.get("works_if")),
                "doubt_on_success": o.get("doubt_on_success", 0),
                "doubt_on_fail": o.get("doubt_on_fail", 1),
                "line_if_pass": o.get("line_if_pass", ""), "line_if_fail": o.get("line_if_fail", ""),
                "rationale": o.get("rationale", "")} for o in (n.get("options") or [])]
        nodes.append(node)
    return {
        "role": preview.get("role", ""), "setting": preview.get("setting", ""),
        "facts": preview.get("facts") or [], "cast": preview.get("cast") or [],
        "tags": preview.get("tags") or [], "nodes": nodes,
        "verdicts": script.get("verdicts") or [],
    }


async def _act_scene(preview: dict, name: str, budget: int) -> dict:
    """Generate the scene in small chunks, then concatenate them server-side into one script."""
    nodes: list[dict] = []
    prior: list[str] = []
    used: set[str] = set()
    produced = 0
    for _ in range(ACT_MAX_ASK + 8):
        if produced >= budget:
            break
        chunk = await gemini_service.generate_act_scene_chunk(
            preview, name, min(ACT_NODES_PER_CALL, budget - produced), prior, used)
        chunk_nodes = [n for n in (chunk.get("nodes") or []) if isinstance(n, dict) and n.get("options")]
        if not chunk_nodes:
            raise RuntimeError("empty scene chunk")
        nodes.extend(chunk_nodes)
        produced += len(chunk_nodes)
        prior = [f"{n.get('speaker')}: {n.get('line')}" for n in chunk_nodes[-2:]]
        used |= {o.get("fact") for n in chunk_nodes for o in (n.get("options") or []) if o.get("fact")}
    checks = await gemini_service.generate_act_checks(preview, nodes, name)
    return {
        "nodes": act_deceive.assemble_nodes(nodes, checks.get("checks") or [],
                                            checks.get("end_line") or ""),
        "verdicts": checks.get("verdicts") or [],
    }


async def _act_bucket(level, avoid: list[str], labels: list[str], wanted: int) -> tuple[list[dict], set[str]]:
    name, desc, cast_size, facts_count, glimpsed_count, turns = level
    kept: list[dict] = []
    used: set[str] = set()
    attempts = 0
    while len(kept) < wanted and attempts < wanted + 1:
        attempts += 1
        try:
            preview = await gemini_service.generate_act_cast(
                name, desc, avoid, labels, cast_size, facts_count, glimpsed_count)
            used.add(_usage_label())
            for budget in (turns, max(4, turns - 2)):
                script = await _act_scene(preview, name, budget)
                used.add(_usage_label())
                got = act_deceive.validate_scenario(_act_raw(preview, script), name, len(kept))
                if got:
                    kept.append(got)
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("act bucket %s attempt failed: %r", name, e)
    return kept, used


def _merge_acts(existing: list[dict], new: list[dict]) -> list[dict]:
    out, ids, sigs = [], set(), set()
    for sc in (existing or []) + (new or []):
        first = (sc.get("cast") or [{}])[0].get("id")
        sig = (sc.get("role"), first, tuple(f.get("id") for f in (sc.get("facts") or [])))
        if sc.get("id") in ids or sig in sigs:
            continue
        ids.add(sc.get("id"))
        sigs.add(sig)
        out.append(sc)
    return out
