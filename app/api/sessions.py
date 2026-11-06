import csv
import io
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import CircuitIn, SubmitIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("/circuit", status_code=201)
async def start_circuit(body: CircuitIn, user: dict = Depends(get_profiled_user)):
    rate_limit("circuit", str(user["_id"]), 20, 3600)
    return svc.view(await svc.create_circuit(user, body.difficulty))


@router.get("/history")
async def get_history(
    days: int = Query(30, ge=1, le=365), before: str | None = None,
    limit: int = Query(10, ge=1, le=60), include_archived: bool = False,
    user: dict = Depends(get_profiled_user),
):
    rate_limit("history", str(user["_id"]), 60, 600)
    return await svc.history(user, days, before, limit, include_archived)


@router.get("/history/download")
async def download_history(
    format: str = Query("csv", pattern="^(csv|json)$"), days: str = Query("30", max_length=8),
    user: dict = Depends(get_profiled_user),
):
    rate_limit("history-download", str(user["_id"]), 10, 600)
    include_archived = days == "all"
    window = 365 if include_archived else max(1, int(days) if days.isdigit() else 30)
    data = await svc.history(user, window, None, svc.HISTORY_PAGE_MAX, include_archived)
    entries = data["entries"]
    if format == "json":
        body, media = json.dumps({"days": entries}, indent=1), "application/json"
        fname = "monkey-brain-history.json"
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "pillar", "level", "correct", "offered", "accuracy", "score",
                    "difficulty_x", "volume_x", "points"])
        for e in entries:
            for p in e["pillars"]:
                w.writerow([e["date"], p["pillar"], p["level"], p["correct"], p["offered"],
                            p["accuracy"], p["score"], p.get("weight"), p.get("volume"), p.get("points")])
            w.writerow([e["date"], "TOTAL", "", "", "", "", "", "", "", e["total_points"]])
        body, media, fname = buf.getvalue(), "text/csv", "monkey-brain-history.csv"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{sid}")
async def get_session(sid: str, user: dict = Depends(get_profiled_user)):
    return svc.view(await svc.get_session(user, sid))


@router.post("/{sid}/pillars/{i}/start")
async def start_pillar(sid: str, i: int, user: dict = Depends(get_profiled_user)):
    rate_limit("pillar-start", str(user["_id"]), 60, 600)
    return await svc.start_pillar(user, sid, i)


@router.post("/{sid}/pillars/{i}/submit")
async def submit_pillar(sid: str, i: int, body: SubmitIn, user: dict = Depends(get_profiled_user)):
    rate_limit("pillar-submit", str(user["_id"]), 60, 600)
    return await svc.submit_pillar(user, sid, i, body)
