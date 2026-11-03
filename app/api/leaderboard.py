from fastapi import APIRouter, Depends

from app.core.clock import today_ist
from app.core.security import get_profiled_user
from app.services import leaderboard_service as lb

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


@router.get("/daily")
async def daily(user: dict = Depends(get_profiled_user)):
    date = today_ist()
    return {"date": date, "entries": await lb.daily(date), "me": await lb.my_daily_rank(user, date)}


@router.get("/hall")
async def hall(user: dict = Depends(get_profiled_user)):
    return {"entries": await lb.hall(), "me": await lb.my_hall_rank(user)}
