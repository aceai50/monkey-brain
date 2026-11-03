"""Fail-closed validation of LLM output. Anything that doesn't pass is dropped, never repaired."""
import re

from pydantic import BaseModel, ValidationError

from app.core.hashrng import HashRng, stable_key

BAD_CHARS = re.compile(r"[<>`]|[\x00-\x08\x0b-\x1f]")


class RawItem(BaseModel):
    scenario: str
    question: str
    options: list[str]
    correct_index: int
    explanation: str
    tag: str = ""


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def stem_hash(item: RawItem) -> str:
    return stable_key(normalize(item.scenario))[:16]


def validate_raw(raw) -> RawItem | None:
    try:
        it = RawItem.model_validate(raw)
    except ValidationError:
        return None
    texts = [it.scenario, it.question, it.explanation, it.tag, *it.options]
    if any(BAD_CHARS.search(t) for t in texts):
        return None
    if not (30 <= len(it.scenario) <= 700 and 5 <= len(it.question) <= 300 and len(it.explanation) >= 15):
        return None
    if len(it.options) != 4 or any(not (1 <= len(o.strip()) <= 200) for o in it.options):
        return None
    if len({normalize(o) for o in it.options}) != 4:
        return None
    if not 0 <= it.correct_index <= 3:
        return None
    return it


def to_question(item: RawItem, *, date: str, bucket: int, key: str) -> dict:
    """Options are shuffled HERE (LLMs over-pick positions 0/1 for the right answer)."""
    order = HashRng("ctopt", date, bucket, key).shuffle([0, 1, 2, 3])
    return {
        "id": f"ct{bucket}-{key}", "type": "mcq", "scenario": item.scenario.strip(),
        "prompt": item.question.strip(), "options": [item.options[i].strip() for i in order],
        "answer": order.index(item.correct_index), "explanation": item.explanation.strip(), "tag": item.tag.strip(),
    }
