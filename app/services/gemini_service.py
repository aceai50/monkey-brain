"""Thin Gemini wrapper (google-genai SDK). Used by the critical-thinking and art daily pools."""
import asyncio
import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from app.core.config import settings

log = logging.getLogger("gemini")
_env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "prompts"),
                   undefined=StrictUndefined, autoescape=False)
_client = None


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

class _ArtItem(BaseModel):
    kind: str
    a: float
    b: float
    c: float = 0
    d: float = 0
    e: float = 0
    phase: float = 0
    layers: int = 1
    rotate: int = 0
    prompt: str = "What is the first thing you see?"
    hint: str
    readings: _ArtReadings
    ambiguity_note: str


class _ArtBatch(BaseModel):
    items: list[_ArtItem]


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


def available() -> bool:
    return bool(settings.gemini_api_key)


def _get_client():
    global _client
    if _client is None:
        from google import genai  # lazy: tests and dev without a key never import it
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def _call(model: str, prompt: str, schema, temperature: float) -> dict:
    from google.genai import types
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                _get_client().aio.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", response_schema=schema, temperature=temperature),
                ),
                timeout=settings.gemini_call_timeout_s,
            )
            return json.loads(resp.text)
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Gemini call failed (attempt %d): %r", attempt + 1, e)
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Gemini failed: {last!r}")


async def generate_items(level_name: str, level_desc: str, n: int, avoid: list[str]) -> list[dict]:
    prompt = _env.get_template("critical_thinking.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none")
    data = await _call(settings.gemini_model, prompt, _Batch, 0.9)
    return data.get("items", [])


async def generate_art_items(level_name: str, level_desc: str, n: int, avoid: list[str]) -> list[dict]:
    prompt = _env.get_template("art_curves.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none")
    data = await _call(settings.gemini_model, prompt, _ArtBatch, 1.0)
    return data.get("items", [])


async def generate_surveillance_items(level_name: str, level_desc: str, n: int, avoid: list[str],
                                      labels: list[str]) -> list[dict]:
    prompt = _env.get_template("surveillance.j2").render(
        level_name=level_name, level_desc=level_desc, n=n, avoid=", ".join(avoid) or "none",
        labels=", ".join(labels) or "student_1, boss_1, owner_1")
    data = await _call(settings.gemini_model, prompt, _SurvBatch, 1.0)
    return data.get("items", [])


async def blind_solve(items: list[dict]) -> dict[str, int]:
    """A second, independent model answers WITHOUT seeing the key. Disagreement = item discarded."""
    prompt = _env.get_template("critical_solver.j2").render(items=items)
    data = await _call(settings.gemini_solver_model, prompt, _Picks, 0.0)
    return {a["id"]: int(a["choice"]) for a in data.get("answers", [])}
