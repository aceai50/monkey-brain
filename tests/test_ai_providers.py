"""Provider cascade: Gemini -> Groq (two models) -> caller fallback."""
import asyncio

import pytest

from app.core.config import settings
from app.services import gemini_service as g
from app.services.gemini_service import AIBlockedError, AIQuotaError, AIUnavailable, _Batch


@pytest.fixture(autouse=True)
def _reset_provider_state():
    g._exhausted.clear()
    g._groq_sem = None
    g._groq_last = 0.0
    yield
    g._exhausted.clear()
    g._groq_sem = None
    g._groq_last = 0.0


async def test_gemini_quota_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")

    async def gemini_boom(model, prompt, schema, temperature):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    async def groq_ok(model, prompt, schema, temperature):
        return {"items": []}

    monkeypatch.setattr(g, "_gemini_once", gemini_boom)
    monkeypatch.setattr(g, "_groq_call", groq_ok)
    assert await g._call("gemini-model", "prompt", _Batch, 0.5) == {"items": []}
    assert g._spent("gemini"), "Gemini must be marked exhausted for the day"


async def test_groq_429_tries_the_fallback_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(settings, "groq_model", "primary-model")
    monkeypatch.setattr(settings, "groq_fallback_model", "backup-model")
    called = []

    async def groq(model, prompt, schema, temperature):
        called.append(model)
        if model == "primary-model":
            raise AIQuotaError("429")
        return {"items": []}

    monkeypatch.setattr(g, "_groq_call", groq)
    assert await g._call("x", "prompt", _Batch, 0.5) == {"items": []}
    assert called == ["primary-model", "backup-model"]
    assert g._spent("groq:primary-model") and not g._spent("groq:backup-model")


async def test_groq_blocked_model_is_disabled_and_fallback_used(monkeypatch):
    """A 400/403 from one Groq model must not be retried on every generation."""
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(settings, "groq_model", "broken-model")
    monkeypatch.setattr(settings, "groq_fallback_model", "good-model")

    async def groq(model, prompt, schema, temperature):
        if model == "broken-model":
            raise AIBlockedError("groq broken-model: 400 max_completion_tokens too large")
        return {"items": []}

    monkeypatch.setattr(g, "_groq_call", groq)
    assert await g._call("x", "prompt", _Batch, 0.5) == {"items": []}
    assert g._spent("groq:broken-model")


async def test_no_provider_raises_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(AIUnavailable):
        await g._call("x", "prompt", _Batch, 0.5)


def test_available_with_groq_only(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    assert g.available() is True


async def test_groq_calls_are_serialized(monkeypatch):
    """The free tier is 8K TPM: never send two Groq requests at once."""
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(settings, "groq_min_interval_s", 0.0)
    active = peak = 0

    async def fake(model, prompt, schema, temperature):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {}

    monkeypatch.setattr(g, "_groq_once", fake)
    await asyncio.gather(*[g._groq_call("m", "p", _Batch, 0.0) for _ in range(5)])
    assert peak == 1

async def test_truncated_json_uses_fallback_with_short_cooldown(monkeypatch):
    """A json_validate_failed 400 is transient: don't disable the model for an hour."""
    import time
    from app.services.gemini_service import AIGenerationError

    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(settings, "groq_model", "primary")
    monkeypatch.setattr(settings, "groq_fallback_model", "backup")

    async def groq(model, prompt, schema, temperature):
        if model == "primary":
            raise AIGenerationError("groq primary: 400 json_validate_failed")
        return {"items": []}

    monkeypatch.setattr(g, "_groq_call", groq)
    assert await g._call("x", "p", _Batch, 0.5) == {"items": []}
    remaining = g._exhausted["groq:primary"] - time.monotonic()
    assert 0 < remaining <= 31

async def test_last_used_records_provider_and_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    g._last_ai.set(None)

    async def gem(model, prompt, schema, temperature):
        return {"items": []}

    monkeypatch.setattr(g, "_gemini_once", gem)
    await g._call("my-model", "p", _Batch, 0.1)
    assert g.last_used() == {"provider": "gemini", "model": "my-model"}
