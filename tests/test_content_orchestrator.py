import httpx  # noqa: F401
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.core import db as dbmod
from app.services import content_orchestrator as co
from app.services import gemini_service

DATE = "2026-11-03"


def raw_items(n, tag):
    return [{"scenario": f"Scenario number {i} about {tag} with enough words to pass the length check easily.",
             "question": "What is the flaw in this reasoning?", "options": [f"A{i}", f"B{i}", f"C{i}", f"D{i}"],
             "correct_index": 0, "explanation": "Because the reasoning confuses two things clearly.", "tag": tag}
            for i in range(n)]


@pytest_asyncio.fixture
async def env(monkeypatch):
    dbmod._client = AsyncMongoMockClient(tz_aware=True)
    monkeypatch.setattr(gemini_service, "available", lambda: True)
    co._inflight.clear()


async def test_gemini_buckets_pass_only_when_blind_solver_agrees(env, monkeypatch):
    async def gen(level, desc, n, avoid):
        return raw_items(8, level)

    async def solve(items):          # agree on 'easy' (ids ct0-*), disagree everywhere else
        out = {}
        for it in items:
            truth = next(i for i, o in enumerate(it["options"]) if o.startswith("A"))
            out[it["id"]] = truth if it["id"].startswith("ct0-") else (truth + 1) % 4
        return out

    monkeypatch.setattr(gemini_service, "generate_items", gen)
    monkeypatch.setattr(gemini_service, "blind_solve", solve)
    assert await co.generate_critical(DATE) == "ready"
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": DATE})
    assert doc["sources"]["critical"] == {"easy": "gemini", "medium": "fallback_bank",
                                          "hard": "fallback_bank", "expert": "fallback_bank"}
    assert doc["status"] == "ready" and doc["lock"] is None
    assert len(doc["pools"]["critical"]["easy"]) == 8
    # a second run does nothing: critical already exists
    assert await co.generate_critical(DATE) == "skipped"


async def test_total_failure_backs_off_then_freezes_fallback(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(gemini_service, "generate_items", boom)
    assert await co.generate_critical(DATE) == "failed"
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": DATE})
    assert doc["attempts"] == 1 and "pools" in doc and "critical" not in doc["pools"] and doc["lock"] is None
    # inside the backoff window nobody else may start another attempt
    assert await co.generate_critical(DATE) == "skipped"
    # users still get content meanwhile
    pools = await co.resolve_pools(DATE)
    assert len(pools["critical"]["hard"]) >= 4
    # after MAX attempts the fallback is frozen into the doc and retries stop
    await dbmod.get_db().daily_challenges.update_one({"_id": DATE}, {"$set": {"attempts": co.MAX_ATTEMPTS - 1, "next_attempt_at": None}})
    assert await co.generate_critical(DATE) == "failed"
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": DATE})
    assert doc["status"] == "ready" and "critical" in doc["pools"]


async def test_live_lock_blocks_and_expired_lock_is_taken_over(env):
    from datetime import timedelta
    from app.core.clock import now_utc
    await co.ensure_doc(DATE)
    col = dbmod.get_db().daily_challenges
    await col.update_one({"_id": DATE}, {"$set": {"lock": {"owner": "zombie", "expires_at": now_utc() + timedelta(minutes=3)}}})
    assert await co._acquire(DATE, "me") is False
    await col.update_one({"_id": DATE}, {"$set": {"lock.expires_at": now_utc() - timedelta(seconds=1)}})
    assert await co._acquire(DATE, "me") is True
    # fencing: a zombie's late write must not land
    res = await col.update_one({"_id": DATE, "lock.owner": "zombie"}, {"$set": {"pools.critical": {"x": []}}})
    assert res.modified_count == 0
