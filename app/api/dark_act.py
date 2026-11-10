from fastapi import APIRouter, Depends

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import ActPrepIn, DarkChooseIn, DarkStartIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/dark/act", tags=["dark"])


@router.post("/start", status_code=201)
async def start(body: DarkStartIn, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-start", str(user["_id"]), 40, 3600)
    return svc.act_public(await svc.create_act(user, body.difficulty, None))


@router.post("/{sid}/prep")
async def prep(sid: str, body: ActPrepIn, user: dict = Depends(get_profiled_user)):
    return svc.act_public(await svc.act_prep(user, sid, body.topic_ids))


@router.get("/{sid}")
async def state(sid: str, user: dict = Depends(get_profiled_user)):
    return svc.act_public(await svc.get_act(user, sid))


@router.post("/{sid}/choose")
async def choose(sid: str, body: DarkChooseIn, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-choose", str(user["_id"]), 600, 3600)
    return svc.act_public(await svc.act_choose(user, sid, body.key))


@router.post("/{sid}/timeout")
async def timeout(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-choose", str(user["_id"]), 600, 3600)
    return svc.act_public(await svc.act_timeout(user, sid))


@router.post("/{sid}/skip", status_code=201)
async def skip(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.act_public(await svc.act_skip(user, sid))


@router.post("/{sid}/next", status_code=201)
async def next_scenario(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.act_public(await svc.act_step(user, sid, "next"))


@router.post("/{sid}/prev", status_code=201)
async def prev_scenario(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.act_public(await svc.act_step(user, sid, "prev"))


@router.post("/{sid}/restart", status_code=201)
async def restart(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-start", str(user["_id"]), 40, 3600)
    return svc.act_public(await svc.act_restart(user, sid))
