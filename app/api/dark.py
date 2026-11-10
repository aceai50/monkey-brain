from fastapi import APIRouter, Depends

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import DarkChooseIn, DarkPracticeIn, DarkStartIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/dark", tags=["dark"])


@router.get("/pillars")
async def pillars(user: dict = Depends(get_profiled_user)):
    """What the S-mode practice chooser shows, plus any active runs per pillar."""
    return await svc.dark_pillars_payload(user)


@router.post("/practice", status_code=201)
async def practice(body: DarkPracticeIn, user: dict = Depends(get_profiled_user)):
    """One entry for every dark pillar, exactly like /api/practice is for the regular gym."""
    rate_limit("dark-practice", str(user["_id"]), 60, 3600)
    doc, runner = await svc.create_dark_run(user, body.pillar, body.difficulty, body.settings)
    public = svc.act_public(doc) if body.pillar == svc.ACT_PILLAR else svc.dark_public(doc)
    return {**public, "runner": runner}


@router.post("/start", status_code=201)
async def start(body: DarkStartIn, user: dict = Depends(get_profiled_user)):
    """Back-compat alias for the surveillance-only start."""
    rate_limit("dark-start", str(user["_id"]), 40, 3600)
    return svc.dark_public(await svc.create_dark(user, body.difficulty))


@router.get("/{sid}")
async def state(sid: str, user: dict = Depends(get_profiled_user)):
    return svc.dark_public(await svc.get_dark(user, sid))


@router.post("/{sid}/choose")
async def choose(sid: str, body: DarkChooseIn, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-choose", str(user["_id"]), 600, 3600)
    return svc.dark_public(await svc.dark_choose(user, sid, body.key))


@router.post("/{sid}/restart", status_code=201)
async def restart(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-start", str(user["_id"]), 40, 3600)
    return svc.dark_public(await svc.dark_restart(user, sid))


@router.post("/{sid}/skip", status_code=201)
async def skip(sid: str, user: dict = Depends(get_profiled_user)):
    """Discard this character and start the next one in the pool (cyclic)."""
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.dark_public(await svc.dark_skip(user, sid))


@router.post("/{sid}/next", status_code=201)
async def next_scenario(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.dark_public(await svc.dark_step(user, sid, "next"))


@router.post("/{sid}/prev", status_code=201)
async def prev_scenario(sid: str, user: dict = Depends(get_profiled_user)):
    rate_limit("dark-skip", str(user["_id"]), 200, 3600)
    return svc.dark_public(await svc.dark_step(user, sid, "prev"))
