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
from app.services import generation_log, leaderboard_service, seats

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
ETA_S = {"critical": 300, "art": 300, "surveillance": 240, "act_deceive": 600}
LOG_WINDOW_DAYS = 3


async def _run_manual(job_id, pillar: str, date: str, fn, args: tuple) -> None:
    try:
        outcome = await fn(date, *args)
    except Exception as e:  # noqa: BLE001
        await generation_log.finish_job(job_id, pillar, date, "failed", error=repr(e))
        return
    await generation_log.finish_job(job_id, pillar, date, outcome)


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
    """Queue a pool (re)generation. Returns job ids the admin panel can poll."""
    rate_limit("admin-generate", str(admin["_id"]), 30, 600)
    date = today_ist()
    await content.ensure_doc(date)
    if body.pillar == "surveillance" and body.append:
        await content.reset_generation(date, "surveillance")
        targets, args = ["surveillance"], (body.count, True)
    elif body.pillar == "act_deceive" and body.append:
        await content.reset_generation(date, "act_deceive")
        targets, args = ["act_deceive"], (body.count, True)
    else:
        targets = list(GENERATORS) if body.pillar == "all" else [body.pillar]
        if body.force:
            for t in targets:
                await content.reset_generation(date, t)
        args = ()
    queued, jobs_out = [], []
    count = body.count if (body.append and body.pillar in ("surveillance", "act_deceive")) else 1
    for t in targets:
        eta = ETA_S.get(t, 120) * max(1, count)
        job_id = await generation_log.start_job(t, date, trigger="admin", count=count,
                                                requested_by=str(admin["_id"]), eta=eta)
        bg.add_task(_run_manual, job_id, t, date, GENERATORS[t], args)
        queued.append(t)
        jobs_out.append({"id": str(job_id), "pillar": t, "eta": eta, "count": count})
    return {"queued": queued, "date": date, "jobs": jobs_out,
            "total_eta": sum(j["eta"] for j in jobs_out)}


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
                        "attempts": doc.get("ad_attempts", 0), "error": doc.get("ad_last_error")},
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
