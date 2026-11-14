import re
from typing import Any, Literal

from app.core.security import is_admin
from pydantic import BaseModel, EmailStr, Field, field_validator

ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{3,20}$")
RESERVED_ALIASES = {
    "admin", "administrator", "root", "system", "support", "staff", "moderator", "mod",
    "brainmonkey", "brain_monkey", "brain-monkey", "official", "null", "undefined", "anonymous",
}


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if not 1 <= len(v) <= 50:
        raise ValueError("Name must be 1-50 characters")
    if re.search(r"[<>&\"`\\]|[\x00-\x1f]", v):
        raise ValueError("Name contains characters that are not allowed")
    return v


def _clean_alias(v: str) -> str:
    v = v.strip()
    if not ALIAS_RE.match(v):
        raise ValueError("Alias must be 3-20 characters: letters, numbers, dot, dash, underscore")
    if v.lower() in RESERVED_ALIASES:
        raise ValueError("That alias is reserved")
    return v


class PasswordMixin(BaseModel):
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        if len(v.encode()) > 72:  # bcrypt silently truncates beyond 72 bytes
            raise ValueError("Password must be at most 72 bytes")
        return v


class RegisterIn(PasswordMixin):
    email: EmailStr


class SetPasswordIn(PasswordMixin):
    token: str = Field(min_length=20, max_length=2048)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class GoogleIn(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


class EmailIn(BaseModel):
    email: EmailStr


class ProfileCreateIn(BaseModel):
    name: str
    alias: str

    _n = field_validator("name")(_clean_name)
    _a = field_validator("alias")(_clean_alias)


class ProfileUpdateIn(BaseModel):
    name: str | None = None
    alias: str | None = None
    display_on_leaderboard: bool | None = None
    theme: Literal["light", "dark", "system"] | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _clean_name(v)

    @field_validator("alias")
    @classmethod
    def _a(cls, v):
        return None if v is None else _clean_alias(v)


class DeleteAccountIn(BaseModel):
    confirm: Literal["DELETE"]
    password: str | None = Field(default=None, max_length=72)


class InviteIn(BaseModel):
    email: EmailStr


class CircuitIn(BaseModel):
    difficulty: Literal["auto", "easy", "medium", "hard"] = "auto"


class PracticeIn(BaseModel):
    pillar: Literal["arithmetic", "critical", "pattern", "art", "kinesthetic"]
    difficulty: Literal["easy", "medium", "hard", "custom"] = "medium"
    slider: float = Field(default=0.5, ge=0.0, le=1.0)  # 0 easy, 0.5 medium, 1 hard


class DarkStartIn(BaseModel):
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    settings: dict = Field(default_factory=dict)


class DarkChooseIn(BaseModel):
    key: str = Field(min_length=1, max_length=8)


class FaceIn(BaseModel):
    label: str = Field(min_length=2, max_length=32)
    filename: str = Field(min_length=3, max_length=120)


class FaceRenameIn(BaseModel):
    label: str = Field(min_length=2, max_length=32)


class GenerateIn(BaseModel):
    pillar: Literal["all", "critical", "art", "surveillance", "act_deceive"]
    force: bool = True
    count: int = Field(default=1, ge=1, le=10)
    append: bool = False
    bucket: Literal["easy", "medium", "hard"] | None = None   # act_deceive only: generate just this difficulty


class DarkPracticeIn(BaseModel):
    pillar: Literal["surveillance", "act_deceive"]
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    settings: dict = Field(default_factory=dict)


class TrialIn(BaseModel):
    id: str = Field(max_length=32)
    rt_ms: float | None = Field(default=None, ge=0, le=60000)
    outcome: Literal["hit", "miss", "false_start"]


class CustomizeIn(BaseModel):
    counts: dict[str, int] = Field(max_length=10)   # pillar -> number of questions


class SubmitIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict, max_length=200)
    trials: list[TrialIn] = Field(default_factory=list, max_length=120)


def user_out(u: dict) -> dict:
    """Whitelist serializer. Never return the raw document (it holds password_hash etc.)."""
    return {
        "id": str(u["_id"]),
        "email": u["email"],
        "name": u.get("name"),
        "alias": u.get("alias"),
        "profile_complete": bool(u.get("alias")),
        "display_on_leaderboard": u.get("display_on_leaderboard", True),
        "has_password": bool(u.get("password_hash")),
        "has_google": bool(u.get("google_sub")),
        "is_admin": is_admin(u),
        "theme": u.get("theme") or "system",
    }
