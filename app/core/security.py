import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.db import get_db

_bearer = HTTPBearer(auto_error=False)
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=12))


async def hash_password(password: str) -> str:
    hashed = await run_in_threadpool(bcrypt.hashpw, password.encode(), bcrypt.gensalt(rounds=12))
    return hashed.decode()


async def verify_password(password: str, hashed: str | None) -> bool:
    target = hashed.encode() if hashed else _DUMMY_HASH
    ok = await run_in_threadpool(bcrypt.checkpw, password.encode(), target)
    return bool(ok and hashed)


def pw_fingerprint(password_hash: str | None) -> str:
    """Embedded in set-password links so a link works exactly once (the hash changes after use)."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def create_token(sub: str, typ: str, ttl: timedelta, **extra) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    jti = uuid.uuid4().hex
    exp = now + ttl
    claims = {"sub": sub, "typ": typ, "jti": jti, "iat": now, "exp": exp, **extra}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti, exp


def decode_token(token: str, typ: str) -> dict:
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    if claims.get("typ") != typ:
        raise HTTPException(401, "Invalid token type")
    return claims


def to_object_id(value: str, status: int = 401, msg: str = "Invalid token subject") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status, msg)


def is_admin(user: dict) -> bool:
    return bool(user.get("email_verified")) and user["email"] in settings.admin_set


async def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """The ONLY source of user identity in the API. Never trust an id from body or URL."""
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    claims = decode_token(creds.credentials, "access")
    user = await get_db().users.find_one({"_id": to_object_id(claims["sub"])})
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user


async def get_admin(user: dict = Depends(get_current_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "Admins only")
    return user


async def get_profiled_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("alias"):
        raise HTTPException(409, "Create your profile first")
    return user
