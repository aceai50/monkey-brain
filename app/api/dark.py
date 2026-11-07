from fastapi import APIRouter, Depends

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import DarkChooseIn, DarkStartIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/dark", tags=["dark"])


@router.post("/start", status_code=201)
async def start(body: DarkStartIn, user: dict = Depends(get_profiled_user)):
    """Resumes the active run if there is one, else starts a fresh conversation."""
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
