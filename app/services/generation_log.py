"""Generation job log shared by automatic runs and admin-triggered runs.

One document per (run, pillar) in `gen_jobs`: who triggered it, when, which provider/model
generated it, how many items per bucket, duration, status and any error.
"""
from bson import ObjectId

from app.core.clock import now_utc
from app.core.db import get_db

ERROR_FIELD = {"critical": "last_error", "art": "art_last_error",
               "surveillance": "surv_last_error", "act_deceive": "ad_last_error"}
PILLAR_LABEL = {"critical": "Critical thinking", "art": "Art",
                "surveillance": "Surveillance", "act_deceive": "Act & Deceive"}


async def start_job(pillar: str, date: str, *, trigger: str = "auto", count: int = 1,
                    requested_by: str = "auto", eta: int = 0) -> ObjectId:
    job_id = ObjectId()
    await get_db().gen_jobs.insert_one({
        "_id": job_id, "pillar": pillar, "date": date, "status": "processing",
        "started_at": now_utc(), "finished_at": None, "message": "", "error": "",
        "count": count, "eta": eta, "trigger": trigger, "requested_by": requested_by,
        "counts": {}, "sources": {},
    })
    return job_id


def _src_text(src) -> str:
    if isinstance(src, dict):
        vals = sorted({str(v) for v in src.values() if v})
        return " / ".join(vals)
    return str(src or "")


async def finish_job(job_id, pillar: str, date: str, outcome: str, error: str | None = None) -> None:
    col = get_db().gen_jobs
    doc = await get_db().daily_challenges.find_one({"_id": date}) or {}
    if outcome == "failed":
        err = error or doc.get(ERROR_FIELD.get(pillar, "last_error")) or "generation failed"
        await col.update_one({"_id": job_id}, {"$set": {
            "status": "error", "finished_at": now_utc(), "error": str(err)[:500]}})
        return
    pools = (doc.get("pools") or {}).get(pillar) or {}
    src = (doc.get("sources") or {}).get(pillar)
    counts = {b: len(v) for b, v in pools.items()}
    src_txt = _src_text(src)
    message = " · ".join(p for p in [
        outcome,
        ", ".join(f"{b}:{n}" for b, n in counts.items()) or "no items",
        f"source: {src_txt}" if src_txt else "",
    ] if p)
    await col.update_one({"_id": job_id}, {"$set": {
        "status": "completed", "finished_at": now_utc(), "message": message,
        "counts": counts, "sources": src if isinstance(src, dict) else ({"default": src} if src else {})}})


def public_entry(job: dict, now) -> dict:
    def aware(dt):
        return dt if dt and dt.tzinfo else (dt.replace(tzinfo=now.tzinfo) if dt else None)

    started = aware(job.get("started_at")) or now
    ended = aware(job.get("finished_at")) or now
    status = job.get("status", "processing")
    eta = int(job.get("eta") or 0)
    elapsed = round((ended - started).total_seconds(), 1)
    sources = job.get("sources") or {}
    providers = sorted({str(v) for v in sources.values() if v}) if isinstance(sources, dict) else []
    counts = job.get("counts") or {}
    return {
        "id": str(job["_id"]), "pillar": job.get("pillar"), "pillar_label": PILLAR_LABEL.get(job.get("pillar"), job.get("pillar")),
        "date": job.get("date"), "status": status,
        "trigger": job.get("trigger") or ("admin" if job.get("requested_by") not in ("auto", None) else "auto"),
        "requested_by": job.get("requested_by"),
        "started_at": started.isoformat(), "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "elapsed": elapsed, "eta": eta,
        "remaining": max(0, eta - int(elapsed)) if status == "processing" and eta else 0,
        "count": int(job.get("count") or 1),
        "items": sum(int(v) for v in counts.values()) if counts else None,
        "counts": counts, "providers": providers,
        "provider": " / ".join(providers) or "-",
        "message": job.get("message") or "", "error": job.get("error") or "",
    }
