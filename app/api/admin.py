from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import rate_limit
from app.core.security import get_admin, to_object_id
from app.models.schemas import InviteIn
from app.services import leaderboard_service, seats

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/overview")
async def overview(admin: dict = Depends(get_admin)):
    db = get_db()
    users = await db.users.find({}).sort("created_at", -1).to_list(500)
    invites = await db.invites.find({}).sort("created_at", -1).to_list(500)
    return {
        "seats": {"used": await seats.seats_used(), "max": settings.max_users, "mode": settings.registration_mode},
        "users": [{
            "id": str(u["_id"]), "email": u["email"], "alias": u.get("alias"), "name": u.get("name"),
            "verified": bool(u.get("email_verified")), "has_google": bool(u.get("google_sub")),
            "is_admin": u["email"] in settings.admin_set, "created_at": _iso(u.get("created_at")),
            "last_trained": u.get("last_trained_date"), "streak": u.get("streak", 0),
            "sessions": u.get("sessions", 0), "total_points": u.get("total_points", 0),
        } for u in users],
        "invites": [{"email": i["_id"], "created_at": _iso(i.get("created_at"))} for i in invites],
    }


@router.post("/invites", status_code=201)
async def add_invite(body: InviteIn, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    try:
        await seats.add_invite(body.email.lower(), admin["_id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"email": body.email.lower()}


@router.delete("/invites/{email}", status_code=204)
async def revoke_invite(email: str, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    if not await seats.revoke_invite(email.lower()):
        raise HTTPException(404, "No such invite")


@router.delete("/users/{uid}", status_code=204)
async def remove_user(uid: str, admin: dict = Depends(get_admin)):
    rate_limit("admin-write", str(admin["_id"]), 60, 600)
    user = await get_db().users.find_one({"_id": to_object_id(uid, 404, "No such user")})
    if user is None:
        raise HTTPException(404, "No such user")
    if user["email"] in settings.admin_set:
        raise HTTPException(403, "Admin accounts can't be removed here.")
    await seats.purge_user(user)
    leaderboard_service.invalidate()
