from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError

from app.api.auth import _clear_refresh_cookie
from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import rate_limit
from app.core.security import get_current_user, get_profiled_user, verify_password
from app.services import customize, leaderboard_service, seats, session_service
from app.models.schemas import CustomizeIn, DeleteAccountIn, ProfileCreateIn, ProfileUpdateIn, user_out

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user_out(user)


@router.post("/me/profile", status_code=201)
async def create_profile(body: ProfileCreateIn, user: dict = Depends(get_current_user)):
    if user.get("alias"):
        raise HTTPException(409, "Profile already created")
    db = get_db()
    try:
        await db.users.update_one(
            {"_id": user["_id"], "alias": {"$exists": False}},
            {"$set": {
                "name": body.name, "alias": body.alias, "alias_lower": body.alias.lower(),
                "profile_created_at": datetime.now(timezone.utc),
                # Adaptive-engine state (Phase 2). d starts low = "easy first".
                "difficulty": {p: 0.2 for p in ("arithmetic", "critical", "pattern", "art", "kinesthetic")},
                "manual_history": [], "best_reaction_ms": None,
                "streak": 0, "last_trained_date": None, "total_points": 0, "sessions": 0,
            }},
        )
    except DuplicateKeyError:
        raise HTTPException(409, "That alias is taken")
    return user_out(await db.users.find_one({"_id": user["_id"]}))


@router.patch("/me")
async def update_me(body: ProfileUpdateIn, user: dict = Depends(get_current_user)):
    if not user.get("alias"):
        raise HTTPException(409, "Create your profile first")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        return user_out(user)
    if "alias" in changes:
        changes["alias_lower"] = changes["alias"].lower()
    try:
        await get_db().users.update_one({"_id": user["_id"]}, {"$set": changes})
    except DuplicateKeyError:
        raise HTTPException(409, "That alias is taken")
    leaderboard_service.invalidate()   # alias/privacy changes show up immediately
    return user_out(await get_db().users.find_one({"_id": user["_id"]}))


@router.get("/me/dashboard")
async def dashboard(user: dict = Depends(get_profiled_user)):
    return await session_service.dashboard(user)


def _customize_payload(user: dict) -> dict:
    return {
        "limits": {p: {"label": customize.LABELS[p], "min": lo, "max": hi, "default": d}
                   for p, (lo, hi, d) in customize.LIMITS.items()},
        "counts": customize.counts_for(user),
        "volume_floor": customize.VOLUME_FLOOR,
    }


@router.get("/me/customize")
async def get_customize(user: dict = Depends(get_profiled_user)):
    return _customize_payload(user)


@router.put("/me/customize")
async def put_customize(body: CustomizeIn, user: dict = Depends(get_profiled_user)):
    try:
        counts = customize.validate_counts(body.counts)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await get_db().users.update_one({"_id": user["_id"]}, {"$set": {"question_counts": counts}})
    return _customize_payload({**user, "question_counts": counts})


@router.post("/me/delete", status_code=204)
async def delete_me(body: DeleteAccountIn, request: Request, response: Response,
                    user: dict = Depends(get_current_user)):
    rate_limit("delete", str(user["_id"]), 5, 3600)
    if user.get("password_hash"):
        if not body.password or not await verify_password(body.password, user["password_hash"]):
            raise HTTPException(403, "Wrong password")
    if user["email"] in settings.admin_set:
        raise HTTPException(403, "The admin account can't be deleted here.")
    await seats.purge_user(user)   # sessions, scores, tokens, and frees the seat
    leaderboard_service.invalidate()
    _clear_refresh_cookie(response)
