import math
from datetime import timezone
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from app.core.clock import now_utc, shift_date, today_ist
from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import rate_limit
from app.core.security import get_admin, to_object_id
from app.generators.surveillance import LABEL_RE
from app.models.schemas import FaceIn, FaceRenameIn, GenerateIn, InviteIn
from app.services import content_orchestrator as content
from app.services import generation_log, leaderboard_service, observability, seats

router = APIRouter(prefix="/api/admin", tags=["admin"])

FACES_DIR = Path(__file__).resolve().parents[2] / "static" / "img" / "faces"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
GENERATORS = {
    "critical": content.generate_critical,
    "art": content.generate_art,
    "surveillance": content.generate_surveillance,
    "act_deceive": content.generate_act,
}
# Conservative wall-clock estimates: chunked requests are spaced ~30s apart (8K TPM).
ETA_S = {"critical": 300, "art": 300, "surveillance": 240, "act_deceive": 1500}
LOG_WINDOW_DAYS = 3


async def _run_manual(job_id, pillar: str, date: str, fn, args: tuple, trace: str | None = None) -> None:
    observability.start_run(pillar, date, trace_id=trace, trigger="admin")
    try:
        outcome = await fn(date, *args)
    except Exception as e:  # noqa: BLE001
        await generation_log.finish_job(job_id, pillar, date, "failed", error=repr(e))
        observability.finish_run("failed")
        return
    await generation_log.finish_job(job_id, pillar, date, outcome)
    observability.finish_run(outcome)


@router.get("/jobs")
async def jobs(admin: dict = Depends(get_admin), page: int = 1, page_size: int = 5):
    page = max(1, page)
    page_size = max(1, min(page_size, 20))
    col = get_db().gen_jobs
    total = await col.count_documents({})
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    skip = (page - 1) * page_size
    fetched = await col.find({}).sort("started_at", -1).to_list(max(1, skip + page_size))
    rows = fetched[skip:skip + page_size]
    now = now_utc()
    return {"jobs": [generation_log.public_entry(r, now) for r in rows],
            "total": total, "page": page, "pages": pages, "page_size": page_size}


@router.get("/logs")
async def logs(admin: dict = Depends(get_admin), page: int = 1, page_size: int = 10,
               include_archived: bool = False):
    """Generation log. Default window = the last 3 days; older runs are the archive and are
    only returned when include_archived=true."""
    page = max(1, page)
    page_size = max(1, min(page_size, 50))
    floor = shift_date(today_ist(), -(LOG_WINDOW_DAYS - 1))
    q = {} if include_archived else {"date": {"$gte": floor}}
    col = get_db().gen_jobs
    total = await col.count_documents(q)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    skip = (page - 1) * page_size
    fetched = await col.find(q).sort("started_at", -1).to_list(max(1, skip + page_size))
    rows = fetched[skip:skip + page_size]
    now = now_utc()
    archived = 0 if include_archived else await col.count_documents({"date": {"$lt": floor}})
    return {"entries": [generation_log.public_entry(r, now) for r in rows],
            "total": total, "page": page, "pages": pages, "page_size": page_size,
            "window_days": LOG_WINDOW_DAYS, "include_archived": include_archived,
            "archived": archived}


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, bg: BackgroundTasks, admin: dict = Depends(get_admin)):
    """Queue a pool (re)generation. Returns job ids the admin panel can poll.

    Act & Deceive can be generated per difficulty (easy/medium/hard) or as one sequential queue."""
    rate_limit("admin-generate", str(admin["_id"]), 30, 600)
    date = today_ist()
    await content.ensure_doc(date)
    spec: list[tuple[str, object, tuple, int, int]] = []
    if body.pillar == "act_deceive" and body.bucket:
        name = body.bucket
        if body.force:
            await content.reset_act_bucket(date, name)
        spec.append((f"act_deceive:{name}", content.generate_act_bucket,
                     (name, body.count, body.append, body.force), body.count,
                     content.ACT_BUCKET_ETA[name] * max(1, body.count)))
    elif body.pillar == "surveillance" and body.append:
        await content.reset_generation(date, "surveillance")
        spec.append(("surveillance", content.generate_surveillance,
                     (body.count, True), body.count, ETA_S["surveillance"] * max(1, body.count)))
    elif body.pillar == "act_deceive":
        if body.append or body.force:
            await content.reset_generation(date, "act_deceive")
        spec.append(("act_deceive", GENERATORS["act_deceive"],
                     (body.count, body.append, body.force), body.count,
                     ETA_S["act_deceive"] * max(1, body.count)))
    else:
        targets = list(GENERATORS) if body.pillar == "all" else [body.pillar]
        if body.force:
            for t in targets:
                await content.reset_generation(date, t)
        for t in targets:
            args = (1, False, body.force) if t == "act_deceive" else ()
            spec.append((t, GENERATORS[t], args, 1, ETA_S.get(t, 120)))
    jobs_out = []
    for label, fn, args, count, eta in spec:
        trace = observability.new_trace()
        job_id = await generation_log.start_job(label, date, trigger="admin", count=count,
                                                requested_by=str(admin["_id"]), eta=eta, trace_id=trace)
        bg.add_task(_run_manual, job_id, label, date, fn, args, trace)
        jobs_out.append({"id": str(job_id), "pillar": label, "eta": eta, "count": count,
                         "trace_id": trace})
    return {"queued": [j["pillar"] for j in jobs_out], "date": date, "jobs": jobs_out,
            "total_eta": sum(j["eta"] for j in jobs_out)}


@router.get("/metrics")
async def metrics(admin: dict = Depends(get_admin), days: int = 7):
    """Generation observability: success/latency, provider mix, fallback + retry rates, token
    usage and per-pillar rollups, aggregated from the job log over the requested window."""
    days = max(1, min(days, 30))
    floor = shift_date(today_ist(), -(days - 1))
    jobs = await get_db().gen_jobs.find({"date": {"$gte": floor}}).sort("started_at", -1).to_list(500)
    now = now_utc()
    total = len(jobs)
    completed = errored = processing = 0
    dur: list[float] = []
    calls = fails = retries = fallbacks = tok_in = tok_out = 0
    per_pillar: dict[str, dict] = {}
    providers: dict[str, int] = {}
    error_kinds: dict[str, int] = {}
    runs_by_day: dict[str, int] = {}
    for j in jobs:
        st = j.get("status")
        if st == "completed":
            completed += 1
        elif st == "error":
            errored += 1
        else:
            processing += 1
        e = generation_log.public_entry(j, now)
        el = e["elapsed"] if j.get("finished_at") else None
        if el is not None:
            dur.append(el)
        ev = j.get("evals") or {}
        calls += ev.get("calls", 0)
        fails += ev.get("fail_calls", 0)
        retries += ev.get("retries", 0)
        if ev.get("used_fallback"):
            fallbacks += 1
        tk = ev.get("tokens") or {}
        tok_in += tk.get("in", 0)
        tok_out += tk.get("out", 0)
        for k, n in (ev.get("error_kinds") or {}).items():
            error_kinds[k] = error_kinds.get(k, 0) + n
        p = per_pillar.setdefault(e["pillar_label"], {"runs": 0, "completed": 0, "errors": 0,
                                                     "items": 0, "_d": []})
        p["runs"] += 1
        p["completed"] += st == "completed"
        p["errors"] += st == "error"
        p["items"] += e["items"] or 0
        if el is not None:
            p["_d"].append(el)
        for pr in (e["providers"] or []):
            providers[pr] = providers.get(pr, 0) + 1
        runs_by_day[j.get("date")] = runs_by_day.get(j.get("date"), 0) + 1
    for p in per_pillar.values():
        d = p.pop("_d")
        p["avg_s"] = round(sum(d) / len(d), 1) if d else 0.0
    return {
        "window_days": days, "total": total, "completed": completed, "errors": errored,
        "processing": processing, "success_rate": round(completed / total, 3) if total else 0.0,
        "avg_duration_s": round(sum(dur) / len(dur), 1) if dur else 0.0,
        "provider_calls": {"total": calls, "failed": fails, "retries": retries,
                           "error_rate": round(fails / calls, 3) if calls else 0.0},
        "fallback_runs": fallbacks, "tokens": {"in": tok_in, "out": tok_out},
        "by_provider": dict(sorted(providers.items(), key=lambda x: -x[1])),
        "error_kinds": dict(sorted(error_kinds.items(), key=lambda x: -x[1])),
        "per_pillar": per_pillar, "runs_by_day": dict(sorted(runs_by_day.items())),
    }


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/overview")
async def overview(admin: dict = Depends(get_admin)):
    db = get_db()
    users = await db.users.find({}).sort("created_at", -1).to_list(500)
    invites = await db.invites.find({}).sort("created_at", -1).to_list(500)
    doc = await db.daily_challenges.find_one({"_id": today_ist()}) or {}
    pools = doc.get("pools") or {}
    src = doc.get("sources") or {}
    adb = doc.get("ad_buckets") or {}
    adpools = pools.get("act_deceive") or {}
    ad_buckets = {b: {"ready": bool((adb.get(b) or {}).get("ready")),
                      "attempts": (adb.get(b) or {}).get("attempts", 0),
                      "error": (adb.get(b) or {}).get("last_error"),
                      "source": (adb.get(b) or {}).get("source"),
                      "count": len(adpools.get(b) or [])} for b in ("easy", "medium", "hard")}
    content = {
        "critical": {"source": src.get("critical"), "ready": "critical" in pools,
                     "attempts": doc.get("attempts", 0), "error": doc.get("last_error")},
        "art": {"source": src.get("art"), "ready": bool(doc.get("art_ready")),
                "attempts": doc.get("art_attempts", 0), "error": doc.get("art_last_error")},
        "surveillance": {"source": src.get("surveillance"), "ready": bool(doc.get("surv_ready")),
                         "attempts": doc.get("surv_attempts", 0), "error": doc.get("surv_last_error"),
                         "characters": [{"name": c.get("name"), "difficulty": b, "label": c.get("image_label")}
                                        for b, items in (pools.get("surveillance") or {}).items()
                                        for c in items]},
        "act_deceive": {"source": src.get("act_deceive"), "ready": bool(doc.get("ad_ready")),
                        "attempts": doc.get("ad_attempts", 0), "error": doc.get("ad_last_error"),
                        "buckets": ad_buckets},
    }
    return {
        "date": today_ist(),
        "seats": {"used": await seats.seats_used(), "max": settings.max_users, "mode": settings.registration_mode},
        "content": content,
        "users": [{
            "id": str(u["_id"]), "email": u["email"], "alias": u.get("alias"), "name": u.get("name"),
            "verified": bool(u.get("email_verified")), "has_google": bool(u.get("google_sub")),
            "is_admin": u["email"] in settings.admin_set, "created_at": _iso(u.get("created_at")),
            "last_trained": u.get("last_trained_date"), "streak": u.get("streak", 0),
            "sessions": u.get("sessions", 0), "total_points": u.get("total_points", 0),
        } for u in users],
        "invites": [{"email": i["_id"], "created_at": _iso(i.get("created_at"))} for i in invites],
    }


@router.post("/invites", status_code=201)
async def add_invite(body: InviteIn, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    try:
        await seats.add_invite(body.email.lower(), admin["_id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"email": body.email.lower()}


@router.delete("/invites/{email}", status_code=204)
async def revoke_invite(email: str, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    if not await seats.revoke_invite(email.lower()):
        raise HTTPException(404, "No such invite")


@router.delete("/users/{uid}", status_code=204)
async def remove_user(uid: str, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    user = await get_db().users.find_one({"_id": to_object_id(uid, 404, "No such user")})
    if user is None:
        raise HTTPException(404, "No such user")
    if user["email"] in settings.admin_set:
        raise HTTPException(403, "Admin accounts can't be removed here.")
    await seats.purge_user(user)
    leaderboard_service.invalidate()


# ---------------------------------------------------------------- content pipeline
# (the /generate and /jobs handlers live near the top of this module, beside GENERATORS)


# ---------------------------------------------------------------- face library (S-mode)
def _face_files() -> list[str]:
    if not FACES_DIR.exists():
        return []
    return sorted(p.name for p in FACES_DIR.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def _check_label(label: str) -> str:
    label = label.strip().lower()
    if not LABEL_RE.match(label):
        raise HTTPException(422, "Label must be 2-32 chars: lowercase letters, numbers, underscore")
    return label


@router.get("/faces")
async def list_faces(admin: dict = Depends(get_admin)):
    rows = await get_db().faces.find({}).sort("_id", 1).to_list(500)
    return {"faces": [{"label": r["_id"], "filename": r.get("filename")} for r in rows], "files": _face_files()}


@router.post("/faces", status_code=201)
async def add_face(body: FaceIn, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    label = _check_label(body.label)
    if body.filename not in _face_files():
        raise HTTPException(400, f"No such file in static/img/faces: {body.filename}")
    try:
        await get_db().faces.insert_one({"_id": label, "filename": body.filename, "created_at": now_utc()})
    except DuplicateKeyError:
        raise HTTPException(409, "That label already exists")
    return {"label": label, "filename": body.filename}


@router.patch("/faces/{label}")
async def rename_face(label: str, body: FaceRenameIn, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    new = _check_label(body.label)
    face = await get_db().faces.find_one({"_id": label})
    if face is None:
        raise HTTPException(404, "No such face")
    try:
        await get_db().faces.insert_one({"_id": new, "filename": face.get("filename"), "created_at": now_utc()})
    except DuplicateKeyError:
        raise HTTPException(409, "That label already exists")
    await get_db().faces.delete_one({"_id": label})
    return {"label": new}


@router.delete("/faces/{label}", status_code=204)
async def delete_face(label: str, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    res = await get_db().faces.delete_one({"_id": label})
    if not res.deleted_count:
        raise HTTPException(404, "No such face")
