from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "production"] = "development"
    app_base_url: str = "http://localhost:10000"

    mongodb_uri: str
    mongodb_db: str = "brain_monkey"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 14
    signup_link_ttl_hours: int = 24
    reset_link_ttl_hours: int = 1
    cookie_secure: bool = False
    # Behind Render's proxy: read the rightmost X-Forwarded-For entry (the one Render appends).
    trust_proxy: bool = False

    google_client_id: str = ""

    email_provider: Literal["brevo", "resend", "console"] = "console"
    email_from: str = "no-reply@example.com"
    email_from_name: str = "Monkey Brain"
    brevo_api_key: str = ""
    resend_api_key: str = ""

    # Access control (V1 beta)
    admin_emails: str = ""                 # comma-separated; these accounts are admins and exempt from the cap
    max_users: int = 100
    registration_mode: Literal["open", "invite_only"] = "open"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_solver_model: str = "gemini-2.5-flash-lite"
    gemini_call_timeout_s: int = 60
    internal_cron_secret: str = ""
    s_mode_enabled: bool = True

    @property
    def admin_set(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.admin_emails.split(",") if e.strip())

    @model_validator(mode="after")
    def _guards(self):
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if self.env == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if self.email_provider == "console":
                raise ValueError("EMAIL_PROVIDER=console is not allowed in production")
            if self.email_provider == "brevo" and not self.brevo_api_key:
                raise ValueError("BREVO_API_KEY missing")
            if self.email_provider == "resend" and not self.resend_api_key:
                raise ValueError("RESEND_API_KEY missing")
            if not self.admin_set:
                raise ValueError("ADMIN_EMAILS must be set in production")
            if len(self.internal_cron_secret) < 24:
                raise ValueError("INTERNAL_CRON_SECRET must be at least 24 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
