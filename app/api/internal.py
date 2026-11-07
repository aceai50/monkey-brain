import hmac

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.core.clock import today_ist
from app.core.config import settings
from app.core.rate_limit import client_ip, rate_limit
from app.services import content_orchestrator as content
from app.services import gemini_service

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


async def _work(date: str) -> None:
    await content.ensure_doc(date)
    if gemini_service.available():
        for gen in (content.generate_critical, content.generate_art, content.generate_surveillance):
            try:
                await gen(date)
            except Exception:  # noqa: BLE001 - one pillar's failure must not cancel the others
                pass


@router.post("/generate", status_code=202)
async def generate(request: Request, bg: BackgroundTasks, x_cron_secret: str = Header(default="")):
    """Called by an external scheduler (cron-job.org / GitHub Actions). Returns 202 immediately,
    because schedulers time out at ~30s and Gemini takes longer. Idempotent: safe to call twice."""
    rate_limit("internal", client_ip(request), 30, 3600)
    secret = settings.internal_cron_secret
    # Fail closed: an unset secret must never match an empty header.
    if not secret or not hmac.compare_digest(x_cron_secret.encode(), secret.encode()):
        raise HTTPException(403, "Forbidden")
    date = today_ist()
    bg.add_task(_work, date)
    return {"accepted": True, "date": date}
