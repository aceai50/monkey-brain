"""Generation job log shared by automatic runs and admin-triggered runs.

One document per (run, pillar) in `gen_jobs`: who triggered it, when, which provider/model
generated it, how many items per bucket, duration, status and any error.
"""
from bson import ObjectId

from app.core.clock import now_utc
from app.core.db import get_db
from app.services import observability

ERROR_FIELD = {"critical": "last_error", "art": "art_last_error",
               "surveillance": "surv_last_error", "act_deceive": "ad_last_error"}
PILLAR_LABEL = {"critical": "Critical thinking", "art": "Art",
                "surveillance": "Surveillance", "act_deceive": "Act & Deceive"}


def _base(pillar: str) -> str:
    return pillar.split(":", 1)[0]


async def start_job(pillar: str, date: str, *, trigger: str = "auto", count: int = 1,
                    requested_by: str = "auto", eta: int = 0, trace_id: str | None = None) -> ObjectId:
    job_id = ObjectId()
    trace_id = trace_id or observability.new_trace()
    await get_db().gen_jobs.insert_one({
        "_id": job_id, "pillar": pillar, "date": date, "status": "processing",
        "started_at": now_utc(), "finished_at": None, "message": "", "error": "",
        "count": count, "eta": eta, "trigger": trigger, "requested_by": requested_by,
        "trace_id": trace_id, "counts": {}, "sources": {}, "evals": {},
    })
    return job_id


def _src_text(src) -> str:
    if isinstance(src, dict):
        vals = sorted({str(v) for v in src.values() if v})
        return " / ".join(vals)
    return str(src or "")


def _pools_and_src(doc: dict, pillar: str) -> tuple[dict, object]:
    """Resolve counts + source for a pillar, including per-difficulty jobs labelled 'a:bucket'."""
    base, _, bucket = pillar.partition(":")
    pools = (doc.get("pools") or {}).get(base) or {}
    src = (doc.get("sources") or {}).get(base)
    if bucket:
        return {bucket: len(pools.get(bucket) or [])}, (src.get(bucket) if isinstance(src, dict) else src)
    return {b: len(v) for b, v in pools.items()}, src


async def finish_job(job_id, pillar: str, date: str, outcome: str, error: str | None = None) -> None:
    col = get_db().gen_jobs
    doc = await get_db().daily_challenges.find_one({"_id": date}) or {}
    snap = observability.snapshot()
    if outcome == "failed":
        err = error or doc.get(ERROR_FIELD.get(_base(pillar), "last_error")) or "generation failed"
        await col.update_one({"_id": job_id}, {"$set": {
            "status": "error", "finished_at": now_utc(), "error": str(err)[:500], "evals": snap}})
        observability.log("generation.error", trace=snap.get("trace_id"), pillar=pillar,
                          date=date, error=str(err)[:200])
        return
    counts, src = _pools_and_src(doc, pillar)
    total = sum(counts.values())
    src_txt = _src_text(src)
    message = " · ".join(p for p in [
        outcome,
        (", ".join(f"{b}:{n}" for b, n in counts.items()) if total else "no items"),
        f"source: {src_txt}" if src_txt else "",
    ] if p)
    sources = src if isinstance(src, dict) else ({pillar: src} if src else {})
    await col.update_one({"_id": job_id}, {"$set": {
        "status": "completed", "finished_at": now_utc(), "message": message,
        "counts": counts, "sources": sources, "evals": snap}})


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
    evals = job.get("evals") or {}
    return {
        "id": str(job["_id"]), "pillar": job.get("pillar"),
        "pillar_label": PILLAR_LABEL.get(_base(job.get("pillar")), _base(job.get("pillar"))),
        "bucket": job.get("pillar", "").partition(":")[2] or None,
        "date": job.get("date"), "status": status,
        "trigger": job.get("trigger") or ("admin" if job.get("requested_by") not in ("auto", None) else "auto"),
        "requested_by": job.get("requested_by"), "trace_id": job.get("trace_id"),
        "started_at": started.isoformat(), "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "elapsed": elapsed, "eta": eta,
        "remaining": max(0, eta - int(elapsed)) if status == "processing" and eta else 0,
        "count": int(job.get("count") or 1),
        "items": sum(int(v) for v in counts.values()) if counts else None,
        "counts": counts, "providers": providers,
        "provider": " / ".join(providers) or "-",
        "evals": {"calls": evals.get("calls", 0), "fail_calls": evals.get("fail_calls", 0),
                  "retries": evals.get("retries", 0), "used_fallback": evals.get("used_fallback"),
                  "tokens": evals.get("tokens"), "by_provider": evals.get("by_provider"),
                  "error_kinds": evals.get("error_kinds"), "trace_id": evals.get("trace_id")},
        "message": job.get("message") or "", "error": job.get("error") or "",
    }
