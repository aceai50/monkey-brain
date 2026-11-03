"""Pluggable transactional email. Select with EMAIL_PROVIDER=brevo|resend|console.

Provider caveats (free tiers):
  * Brevo:  EMAIL_FROM must be a verified sender in your Brevo account.
  * Resend: without a verified domain you can only send from onboarding@resend.dev and ONLY
            to your own account email. Real users require a verified domain.
"""
import html
import logging
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings

log = logging.getLogger("email")


class EmailProvider(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, html_body: str, text_body: str) -> None: ...


class BrevoProvider(EmailProvider):
    URL = "https://api.brevo.com/v3/smtp/email"

    async def send(self, to, subject, html_body, text_body):
        payload = {
            "sender": {"name": settings.email_from_name, "email": settings.email_from},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
        headers = {"api-key": settings.brevo_api_key, "accept": "application/json"}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(self.URL, json=payload, headers=headers)
        r.raise_for_status()


class ResendProvider(EmailProvider):
    URL = "https://api.resend.com/emails"

    async def send(self, to, subject, html_body, text_body):
        payload = {
            "from": f"{settings.email_from_name} <{settings.email_from}>",
            "to": [to],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
        headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(self.URL, json=payload, headers=headers)
        r.raise_for_status()


class ConsoleProvider(EmailProvider):
    """Dev only: prints the email so you can click the link without an email account."""

    async def send(self, to, subject, html_body, text_body):
        log.warning("\n--- EMAIL to %s ---\n%s\n%s\n---", to, subject, text_body)


def get_provider() -> EmailProvider:
    return {"brevo": BrevoProvider, "resend": ResendProvider, "console": ConsoleProvider}[
        settings.email_provider
    ]()


async def send_set_password_email(to: str, token: str, *, reset: bool) -> None:
    """Called from BackgroundTasks, so it must never raise into the request."""
    link = f"{settings.app_base_url.rstrip('/')}/#/set-password?token={token}"
    hours = settings.reset_link_ttl_hours if reset else settings.signup_link_ttl_hours
    if reset:
        subject, lead = "Reset your Brain Monkey password", "Choose a new password for Brain Monkey."
    else:
        subject, lead = "Finish setting up Brain Monkey", "Confirm your email and choose a password to start training."
    text = f"{lead}\n{link}\n\nThis link works once and expires in {hours} hour(s). If this wasn't you, ignore this email."
    body = (
        f"<p>{html.escape(lead)}</p>"
        f'<p><a href="{html.escape(link)}">Set password</a></p>'
        f"<p>This link works once and expires in {hours} hour(s). If this wasn't you, ignore this email.</p>"
    )
    try:
        await get_provider().send(to, subject, body, text)
    except Exception:  # noqa: BLE001
        log.exception("Failed to send email via %s", settings.email_provider)
