from fastapi import APIRouter, Depends

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import CircuitIn, SubmitIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("/circuit", status_code=201)
async def start_circuit(body: CircuitIn, user: dict = Depends(get_profiled_user)):
    rate_limit("circuit", str(user["_id"]), 20, 3600)
    return svc.view(await svc.create_circuit(user, body.difficulty))


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
