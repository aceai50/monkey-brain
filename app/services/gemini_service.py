"""AI content generation with a provider cascade: Gemini -> Groq (two models) -> caller fallback.

Both providers are asked for JSON and the result is validated with the pydantic schema. A 429 /
quota error marks that provider (or that Groq model) as exhausted for the rest of the IST day, so
we never hammer it with retries. Groq calls are serialized and spaced (semaphore + interval) to
respect its 8K TPM free-tier limit.
"""
import asyncio
import json
import logging
import time
from contextvars import ContextVar
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from app.core.config import settings

log = logging.getLogger("gemini")
_env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "prompts"),
                   undefined=StrictUndefined, autoescape=False)
_client = None

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_exhausted: dict[str, float] = {}   # provider key -> monotonic time it may be used again
_groq_sem: asyncio.Semaphore | None = None
_groq_last = 0.0
_last_ai: ContextVar[dict | None] = ContextVar("last_ai", default=None)


def last_used() -> dict | None:
    """The provider/model that served the most recent successful call in this task."""
    return _last_ai.get()


class AIQuotaError(Exception):
    """Provider reported 429 / quota exhaustion."""

    def __init__(self, message: str, retry_after: float = 120.0):
        super().__init__(message)
        self.retry_after = retry_after


class AIBlockedError(Exception):
    """Provider rejected the request itself (400/401/403/404) — do not keep retrying it."""


class AIGenerationError(Exception):
    """The model produced invalid/truncated JSON (retry with a smaller ask, not a ban)."""


class AIUnavailable(Exception):
    """Every configured provider is exhausted or failed."""


class _Item(BaseModel):
    scenario: str
    question: str
    options: list[str]
    correct_index: int
    explanation: str
    tag: str


class _Batch(BaseModel):
    items: list[_Item]


class _Pick(BaseModel):
    id: str
    choice: int


class _Picks(BaseModel):
    answers: list[_Pick]


class _ArtReadings(BaseModel):
    organic: list[str]
    structural: list[str]
    cosmic: list[str]
    figure: list[str]


class _SceneLayer(BaseModel):
    glyph: str
    role: str = "anchor"
    count: int = 1
    size: float = 0.6
    scale: float = 1.0
    rotation: int = 0
    seed: int = 0


class _ArtSceneItem(BaseModel):
    concept: str
    layout: str
    palette: str = "mono"
    layers: list[_SceneLayer]
    prompt: str = "What is the first thing you see?"
    hint: str
    readings: _ArtReadings
    ambiguity_note: str = ""


class _ArtSceneBatch(BaseModel):
    items: list[_ArtSceneItem]


class _SurvSecrets(BaseModel):
    strength: str
    weakness: str
    goal: str


class _SurvOption(BaseModel):
    text: str
    tag: str
    outcome: str
    next: str | None = None
    reveal: str | None = None
    rationale: str


class _SurvNode(BaseModel):
    id: str
    text: str
    options: list[_SurvOption]


class _SurvItem(BaseModel):
    image_label: str
    name: str
    description: str
    personality: str
    tone: str
    read: str
    tags: list[str]
    secrets: _SurvSecrets
    nodes: list[_SurvNode]


class _SurvBatch(BaseModel):
    items: list[_SurvItem]


class _ActCover(BaseModel):
    role: str
    setting: str
    brief: str


class _ActCastMember(BaseModel):
    id: str
    name: str
    image_label: str
    role: str
    personality: str
    hint: str


class _ActTopic(BaseModel):
    id: str
    name: str
    study_card: str


class _ActPreview(BaseModel):
    image_label: str
    cover: _ActCover
    cast: list[_ActCastMember]
    topics: list[_ActTopic]
    prep_cards: list[str]
    tags: list[str]


class _Atom(BaseModel):
    studied: str | None = None
    not_studied: str | None = None
    vague_streak_lt: int | None = None
    target: str | None = None
    target_personality: str | None = None
    claim_present: str | None = None
    claim_absent: str | None = None
    turn_lt: int | None = None
    doubt_lt: int | None = None


class _ActOptionIn(BaseModel):
    text: str
    tag: str
    kind: str
    topic: str | None = None
    commits: list[str] = []
    works_if: list[_Atom] = []
    doubt_on_success: int = 0
    doubt_on_fail: int = 1
    line_if_pass: str
    line_if_fail: str
    rationale: str


class _ActNodeIn(BaseModel):
    id: str
    kind: str = "scene"
    speaker: str | None = None
    line: str
    next: str | None = None
    options: list[_ActOptionIn] = []
    claims: list[str] = []
    bluff_line: str | None = None
    confirm_line: str | None = None
    penalty: int = 1


class _ActVerdict(BaseModel):
    id: str
    bought_it: str
    suspicious: str
    sees_through: str


class _ActScriptIn(BaseModel):
    nodes: list[_ActNodeIn]
    verdicts: list[_ActVerdict]


class _ActSceneChunk(BaseModel):
    nodes: list[_ActNodeIn]


class _ActCheckIn(BaseModel):
    speaker: str
    claims: list[str]
    line: str
    bluff_line: str
    confirm_line: str
    penalty: int = 1


class _ActChecks(BaseModel):
    checks: list[_ActCheckIn]
    verdicts: list[_ActVerdict]
    end_line: str


def available() -> bool:
    return bool(settings.gemini_api_key or settings.groq_api_key)


def _spent(key: str) -> bool:
    return time.monotonic() < _exhausted.get(key, 0.0)


def _mark_spent(key: str, seconds: float) -> None:
    _exhausted[key] = time.monotonic() + max(5.0, seconds)


def _retry_after(resp: httpx.Response, default: float = 120.0) -> float:
    try:
        return min(3600.0, max(5.0, float(resp.headers.get("retry-after", default))))
    except (TypeError, ValueError):
        return default


def _is_quota(exc: Exception) -> bool:
    s = str(exc).lower()
    return "429" in s or "resource_exhausted" in s or "quota" in s or "rate limit" in s


def _groq_models() -> list[str]:
    models = [settings.groq_model]
    if settings.groq_fallback_model and settings.groq_fallback_model != settings.groq_model:
        models.append(settings.groq_fallback_model)
    return models


def _groq_semaphore() -> asyncio.Semaphore:
    global _groq_sem
    if _groq_sem is None:
        _groq_sem = asyncio.Semaphore(1)   # one Groq request at a time, ever
    return _groq_sem


def _get_client():
    global _client
    if _client is None:
        from google import genai  # lazy: tests and dev without a key never import it
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def _gemini_once(model: str, prompt: str, schema, temperature: float) -> dict:
    from google.genai import types
    resp = await asyncio.wait_for(
        _get_client().aio.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=schema, temperature=temperature),
        ),
        timeout=settings.gemini_call_timeout_s,
    )
    return json.loads(resp.text)


def _strip_fence(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    return content


async def _groq_once(model: str, prompt: str, schema, temperature: float) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return only one valid JSON object, no prose. "
                                          "It must match this JSON schema:\n"
                                          + json.dumps(schema.model_json_schema())},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_completion_tokens": settings.groq_max_completion_tokens,
    }
    if settings.groq_reasoning_effort and (model.startswith("openai/gpt-oss") or model.startswith("qwen/")):
        payload["reasoning_effort"] = settings.groq_reasoning_effort
    async with httpx.AsyncClient(timeout=settings.gemini_call_timeout_s) as c:
        r = await c.post(GROQ_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                         json=payload)
    if r.status_code != 200:
        body = r.text[:300]
        if r.status_code == 429:
            raise AIQuotaError(f"groq {model}: 429 {body}", retry_after=_retry_after(r))
        if r.status_code in (400, 401, 403, 404):
            low = body.lower()
            if r.status_code == 400 and ("json_validate_failed" in low or "failed_generation" in low):
                # The model's JSON was truncated/empty (usually reasoning ate the token budget).
                raise AIGenerationError(f"groq {model}: JSON generation failed: {body}")
            raise AIBlockedError(f"groq {model}: {r.status_code} {body}")
        raise RuntimeError(f"groq {model}: {r.status_code} {body}")
    content = _strip_fence(r.json()["choices"][0]["message"]["content"])
    return schema.model_validate(json.loads(content)).model_dump()


async def _groq_call(model: str, prompt: str, schema, temperature: float) -> dict:
    """Serialize + space Groq calls (free tier is 8K TPM; never burst)."""
    global _groq_last
    async with _groq_semaphore():
        gap = settings.groq_min_interval_s - (time.monotonic() - _groq_last)
        if gap > 0:
            await asyncio.sleep(gap)
        try:
            return await _groq_once(model, prompt, schema, temperature)
        finally:
            _groq_last = time.monotonic()


async def _call(model: str, prompt: str, schema, temperature: float) -> dict:
    """Gemini first, then Groq (primary model, then its fallback model)."""
    errors: list[Exception] = []
    if settings.gemini_api_key and not _spent("gemini"):
        for attempt in range(2):
            try:
                out = await _gemini_once(model, prompt, schema, temperature)
                _last_ai.set({"provider": "gemini", "model": model})
                return out
            except Exception as e:  # noqa: BLE001
                if _is_quota(e):
                    _mark_spent("gemini", 6 * 3600)
                    log.warning("Gemini quota exhausted; switching to Groq for a while")
                    break
                errors.append(e)
                if isinstance(e, (asyncio.TimeoutError, TimeoutError)) and attempt == 0:
                    log.warning("Gemini timeout; retrying once")
                    continue
                log.warning("Gemini call failed: %r", e)
                break
    for m in _groq_models():
        key = f"groq:{m}"
        if not settings.groq_api_key or _spent(key):
            continue
        try:
            out = await _groq_call(m, prompt, schema, temperature)
            _last_ai.set({"provider": "groq", "model": m})
            return out
        except AIQuotaError as e:
            _mark_spent(key, e.retry_after)   # cool down, then we may try this model again
            errors.append(e)
            log.warning("Groq %s rate-limited; cooling down %.0fs", m, e.retry_after)
        except AIBlockedError as e:
            _mark_spent(key, 3600)            # bad request/permission: stop calling this model
            errors.append(e)
            log.warning("Groq %s rejected the request; disabling it for an hour: %s", m, e)
        except AIGenerationError as e:
            _mark_spent(key, 30)              # transient truncation: short pause, then other model
            errors.append(e)
            log.warning("Groq %s produced invalid JSON; trying the next model: %s", m, e)
        except Exception as e:  # noqa: BLE001
            if _is_quota(e):
                _mark_spent(key, 120)
            errors.append(e)
            log.warning("Groq %s failed: %r", m, e)
    raise AIUnavailable(f"no AI provider available: {errors!r}")


async def generate_items(level_name: str, level_desc: str, n: int, avoid: list[str]) -> list[dict]:
    prompt = _env.get_template("critical_thinking.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none")
    data = await _call(settings.gemini_model, prompt, _Batch, 0.9)
    return data.get("items", [])


async def generate_art_items(level_name: str, level_desc: str, n: int, avoid: list[str]) -> list[dict]:
    prompt = _env.get_template("art_scene.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none")
    data = await _call(settings.gemini_model, prompt, _ArtSceneBatch, 1.0)
    return data.get("items", [])


async def generate_surveillance_items(level_name: str, level_desc: str, n: int, avoid: list[str],
                                      labels: list[str]) -> list[dict]:
    prompt = _env.get_template("surveillance.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none",
        labels=", ".join(labels) or "student_1, boss_1, owner_1")
    data = await _call(settings.gemini_model, prompt, _SurvBatch, 1.0)
    return data.get("items", [])


async def generate_act_cast(level_name: str, level_desc: str, avoid: list[str], labels: list[str],
                            cast_size: int, topics_count: int, prep_count: int) -> dict:
    prompt = _env.get_template("act_cast.j2").render(
        level_name=level_name, level_desc=level_desc, avoid=", ".join(avoid) or "none",
        labels=", ".join(labels) or "student_1, boss_1, owner_1",
        cast_size=cast_size, topics_count=topics_count, prep_count=prep_count)
    return await _call(settings.gemini_model, prompt, _ActPreview, 1.0)


async def generate_act_scene_chunk(preview: dict, level_name: str, count: int,
                                   prior_lines: list[str], used_topics: set[str]) -> dict:
    """One small piece of the scene. The caller concatenates the nodes and chains them."""
    cast = "\n".join(f"{c['id']} | {c['name']} | {c['personality']} | {c['role']}"
                     for c in preview.get("cast", []))
    topics = ", ".join(f"{t['id']} | {t['name']}" for t in preview.get("topics", []))
    prompt = _env.get_template("act_scene_chunk.j2").render(
        cast=cast, topics=topics, tags=", ".join(preview.get("tags", [])),
        used=", ".join(sorted(used_topics)) or "none",
        prior="\n".join(prior_lines) or "(the scene has just begun)",
        count=count)
    return await _call(settings.gemini_model, prompt, _ActSceneChunk, 0.9)


async def generate_act_checks(preview: dict, scene_nodes: list[dict], level_name: str) -> dict:
    cast = "\n".join(f"{c['id']} | {c['name']} | {c['personality']}" for c in preview.get("cast", []))
    claims = [f"{cid} (node {n.get('id')})"
              for n in scene_nodes
              for o in (n.get("options") or [])
              for cid in (o.get("commits") or [])]
    prompt = _env.get_template("act_checks.j2").render(
        cast=cast, claims="\n".join(claims) or "none")
    return await _call(settings.gemini_model, prompt, _ActChecks, 0.7)


async def blind_solve(items: list[dict]) -> dict[str, int]:
    """A second, independent model answers WITHOUT seeing the key. Disagreement = item discarded."""
    prompt = _env.get_template("critical_solver.j2").render(items=items)
    data = await _call(settings.gemini_solver_model, prompt, _Picks, 0.0)
    return {a["id"]: int(a["choice"]) for a in data.get("answers", [])}
