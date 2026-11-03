from fastapi import APIRouter, Depends

from app.core.rate_limit import rate_limit
from app.core.security import get_profiled_user
from app.models.schemas import PracticeIn
from app.services import session_service as svc

router = APIRouter(prefix="/api/practice", tags=["practice"])


@router.post("", status_code=201)
async def start_practice(body: PracticeIn, user: dict = Depends(get_profiled_user)):
    """Single-pillar practice. No adaptive engine, no leaderboard points."""
    rate_limit("practice", str(user["_id"]), 60, 3600)
    return svc.view(await svc.create_practice(user, body.pillar, body.difficulty, body.slider))
