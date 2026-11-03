import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import client_ip, rate_limit
from app.core.security import (
    create_token, decode_token, hash_password, pw_fingerprint, to_object_id, verify_password,
)
from app.models.schemas import EmailIn, GoogleIn, LoginIn, RegisterIn, SetPasswordIn
from app.services import seats
from app.services.email_service import send_set_password_email

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "bm_refresh"
GENERIC_MSG = "If that email can be used, a link to set your password is on its way."
LINK_COOLDOWN_S = 60
REFRESH_REUSE_GRACE_S = 10  # tolerate two tabs refreshing at the same instant


def _set_refresh_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE, token, max_age=settings.refresh_ttl_days * 86400, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path="/api/auth",
    )


def _clear_refresh_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/api/auth")


def _deny(status: int, msg: str) -> JSONResponse:
    """Failure that really clears the cookie. (Headers set on an injected Response are DISCARDED
    when an HTTPException is raised, so refresh failures must return a response instead.)"""
    r = JSONResponse({"detail": msg}, status_code=status)
    _clear_refresh_cookie(r)
    return r


async def _issue_session(user: dict, resp: Response) -> dict:
    uid = str(user["_id"])
    access, _, _ = create_token(uid, "access", timedelta(minutes=settings.access_ttl_minutes))
    refresh, jti, exp = create_token(uid, "refresh", timedelta(days=settings.refresh_ttl_days))
    await get_db().refresh_tokens.insert_one(
        {"_id": jti, "user_id": user["_id"], "expires_at": exp, "used_at": None}
    )
    _set_refresh_cookie(resp, refresh)
    return {
        "access_token": access, "token_type": "bearer",
        "expires_in": settings.access_ttl_minutes * 60,
        "profile_complete": bool(user.get("alias")),
    }


def _pwset_token(user: dict, *, reset: bool) -> str:
    ttl = timedelta(hours=settings.reset_link_ttl_hours if reset else settings.signup_link_ttl_hours)
    token, _, _ = create_token(
        str(user["_id"]), "pwset", ttl, email=user["email"], pv=pw_fingerprint(user.get("password_hash"))
    )
    return token


@router.get("/config")
async def public_config():
    return {
        "google_client_id": settings.google_client_id,
        "seats_left": await seats.seats_left(),
        "max_users": settings.max_users,
        "invite_only": settings.registration_mode == "invite_only",
    }


@router.post("/register", status_code=200)
async def register(body: RegisterIn, request: Request, response: Response):
    """Direct email and password registration without email verification codes."""
    email = body.email.lower()
    rate_limit("register-ip", client_ip(request), 8, 3600)
    rate_limit("register-email", email, 4, 3600)
    db = get_db()
    now = datetime.now(timezone.utc)
    
    existing = await db.users.find_one({"email": email})
    if existing is not None:
        raise HTTPException(400, "Email already exists")

    counted, err = await seats.claim_for_signup(email)
    if err:
        raise HTTPException(403, err)

    password_hash = hash_password(body.password)
    doc = {
        "email": email,
        "password_hash": password_hash,
        "email_verified": True,
        "display_on_leaderboard": True,
        "created_at": now,
        "counted_seat": counted,
    }
    try:
        doc["_id"] = (await db.users.insert_one(doc)).inserted_id
    except DuplicateKeyError:
        if counted:
            await seats.release()
        raise HTTPException(400, "Email already exists")

    return await _issue_session(doc, response)


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: EmailIn, request: Request, bg: BackgroundTasks):
    email = body.email.lower()
    rate_limit("forgot-ip", client_ip(request), 8, 3600)
    rate_limit("forgot-email", email, 3, 3600)
    db = get_db()
    user = await db.users.find_one({"email": email})
    now = datetime.now(timezone.utc)
    if user:
        last = user.get("link_sent_at")
        if not last or (now - last).total_seconds() >= LINK_COOLDOWN_S:
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"link_sent_at": now}})
            reset = bool(user.get("password_hash") or user.get("google_sub"))
            bg.add_task(send_set_password_email, email, _pwset_token(user, reset=reset), reset=reset)
    return {"message": GENERIC_MSG}


@router.post("/set-password")
async def set_password(body: SetPasswordIn, request: Request):
    """Completes signup AND password reset. Proves inbox ownership, so it also verifies the email."""
    rate_limit("setpw-ip", client_ip(request), 15, 600)
    claims = decode_token(body.token, "pwset")
    db = get_db()
    uid = to_object_id(claims["sub"], 400, "This link is not valid")
    user = await db.users.find_one({"_id": uid})
    if user is None or user["email"] != claims.get("email"):
        raise HTTPException(400, "This link is not valid")
    if claims.get("pv") != pw_fingerprint(user.get("password_hash")):
        raise HTTPException(400, "This link was already used. Request a new one.")
    new_hash = await hash_password(body.password)
    # Compare-and-set on the old hash: two concurrent submissions of one link cannot both win.
    old = user.get("password_hash")
    cond = {"_id": uid, "password_hash": old} if old else {"_id": uid, "password_hash": {"$exists": False}}
    res = await db.users.update_one(cond, {"$set": {"password_hash": new_hash, "email_verified": True}})
    if res.modified_count != 1:
        raise HTTPException(400, "This link was already used. Request a new one.")
    await db.refresh_tokens.delete_many({"user_id": uid})  # a password change logs out every device
    return {"message": "Password saved. You can log in now."}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    email = body.email.lower()
    rate_limit("login-ip", client_ip(request), 20, 600)
    rate_limit("login-email", email, 8, 600)
    user = await get_db().users.find_one({"email": email})
    ok = await verify_password(body.password, user.get("password_hash") if user else None)
    if not ok or not user.get("email_verified"):
        raise HTTPException(401, "Wrong email or password")
    return await _issue_session(user, response)


@router.post("/google")
async def google_login(body: GoogleIn, request: Request, response: Response):
    rate_limit("google-ip", client_ip(request), 30, 600)
    if not settings.google_client_id:
        raise HTTPException(503, "Google sign-in is not configured")
    try:
        info = await run_in_threadpool(
            g_id_token.verify_oauth2_token, body.credential, g_requests.Request(), settings.google_client_id
        )
    except ValueError:
        raise HTTPException(401, "Google sign-in failed")
    if not info.get("email_verified"):
        raise HTTPException(401, "Google account email is not verified")

    db = get_db()
    sub, email = info["sub"], info["email"].lower()
    user = await db.users.find_one({"google_sub": sub})
    if user is None:
        user = await db.users.find_one({"email": email})
        if user is not None:
            update: dict = {"$set": {"google_sub": sub, "email_verified": True}}
            if not user.get("email_verified"):
                update["$unset"] = {"password_hash": ""}  # legacy safety: never keep an unverified password
            await db.users.update_one({"_id": user["_id"]}, update)
            user = await db.users.find_one({"_id": user["_id"]})
        else:
            counted, err = await seats.claim_for_signup(email)
            if err:
                raise HTTPException(403, err)
            doc = {
                "email": email, "google_sub": sub, "email_verified": True, "counted_seat": counted,
                "display_on_leaderboard": True, "created_at": datetime.now(timezone.utc),
            }
            try:
                doc["_id"] = (await db.users.insert_one(doc)).inserted_id
            except DuplicateKeyError:
                if counted:
                    await seats.release()
                doc = await db.users.find_one({"email": email})
            user = doc
    return await _issue_session(user, response)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    rate_limit("refresh-ip", client_ip(request), 60, 600)
    token = request.cookies.get(COOKIE)
    if not token:
        return JSONResponse({"detail": "No session"}, status_code=401)
    try:
        claims = decode_token(token, "refresh")
        uid = to_object_id(claims["sub"])
    except HTTPException:
        return _deny(401, "Session expired")
    db = get_db()
    now = datetime.now(timezone.utc)

    # Atomic single-use: only the first caller flips used_at from None.
    doc = await db.refresh_tokens.find_one_and_update(
        {"_id": claims["jti"], "user_id": uid, "used_at": None}, {"$set": {"used_at": now}}
    )
    if doc is None:
        prior = await db.refresh_tokens.find_one({"_id": claims["jti"], "user_id": uid})
        if prior and prior.get("used_at") and (now - prior["used_at"]).total_seconds() < REFRESH_REUSE_GRACE_S:
            return JSONResponse({"detail": "Refresh already in progress, retry"}, status_code=409)
        await db.refresh_tokens.delete_many({"user_id": uid})  # reuse = likely theft: kill all sessions
        return _deny(401, "Session expired")

    user = await db.users.find_one({"_id": uid})
    if user is None:
        return _deny(401, "Account no longer exists")
    return await _issue_session(user, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        try:
            claims = decode_token(token, "refresh")
            await get_db().refresh_tokens.delete_one({"_id": claims["jti"]})
        except HTTPException:
            pass
    _clear_refresh_cookie(response)
