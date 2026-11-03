"""Seat accounting for the capped beta (settings.max_users).

`counters/seats.used` = non-admin users + pending invites. It is incremented with an atomic
conditional update, so two simultaneous signups can never both take the last seat.
"""
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.db import get_db

SEATS_ID = "seats"


def _non_admin_filter() -> dict:
    return {"email": {"$nin": list(settings.admin_set)}}


async def reconcile() -> None:
    """Self-heal the counter at startup (users + invites is the source of truth)."""
    db = get_db()
    used = await db.users.count_documents(_non_admin_filter()) + await db.invites.count_documents({})
    await db.counters.update_one({"_id": SEATS_ID}, {"$set": {"used": used}}, upsert=True)


async def seats_used() -> int:
    doc = await get_db().counters.find_one({"_id": SEATS_ID})
    return int(doc["used"]) if doc else 0


async def seats_left() -> int:
    return max(0, settings.max_users - await seats_used())


async def claim_for_signup(email: str) -> tuple[bool, str | None]:
    """Returns (counted, error). counted=True means a seat is now held for this user and must be
    released if the user is later removed. error is a user-facing message when signup is refused."""
    db = get_db()
    if email in settings.admin_set:
        return False, None
    invite = await db.invites.find_one_and_delete({"_id": email})
    if invite is not None:
        return True, None  # the invite already held the seat
    if settings.registration_mode == "invite_only":
        return False, "Monkey Brain is invite-only right now."
    res = await db.counters.find_one_and_update(
        {"_id": SEATS_ID, "used": {"$lt": settings.max_users}}, {"$inc": {"used": 1}}
    )
    if res is None:
        return False, f"Monkey Brain is full. All {settings.max_users} early-access seats are taken."
    return True, None


async def release(count: int = 1) -> None:
    await get_db().counters.update_one({"_id": SEATS_ID, "used": {"$gte": count}}, {"$inc": {"used": -count}})


async def add_invite(email: str, admin_id) -> None:
    """Raises ValueError with a user-facing message."""
    db = get_db()
    if email in settings.admin_set:
        raise ValueError("That's an admin account. It doesn't need a seat.")
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise ValueError("That email already has an account.")
    res = await db.counters.find_one_and_update(
        {"_id": SEATS_ID, "used": {"$lt": settings.max_users}}, {"$inc": {"used": 1}}
    )
    if res is None:
        raise ValueError("All seats are taken. Remove a user or invite first.")
    try:
        await db.invites.insert_one({"_id": email, "invited_by": admin_id, "created_at": datetime.now(timezone.utc)})
    except DuplicateKeyError:
        await release()
        raise ValueError("That email is already invited.")


async def revoke_invite(email: str) -> bool:
    res = await get_db().invites.delete_one({"_id": email})
    if res.deleted_count:
        await release()
    return bool(res.deleted_count)


async def purge_user(user: dict) -> None:
    """Delete a user and everything attached to them. Shared by self-delete and admin-remove."""
    db = get_db()
    uid = user["_id"]
    await db.refresh_tokens.delete_many({"user_id": uid})
    await db.sessions.delete_many({"user_id": uid})
    await db.daily_scores.delete_many({"user_id": uid})
    await db.users.delete_one({"_id": uid})
    if user["email"] not in settings.admin_set:
        await release()
