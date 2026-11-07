"""Surveillance (S-mode): validator rules + a full server-authoritative run."""
import httpx
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.api import auth as auth_api
from app.core import db as dbmod
from app.core import rate_limit
from app.generators.surveillance import BANK, SECRETS, bank_pool, validate_character
from app.main import app
from app.services import leaderboard_service, seats

SENT: dict[str, str] = {}


async def fake_send(to, token, *, reset):
    SENT[to] = token


@pytest_asyncio.fixture
async def client(monkeypatch):
    dbmod._client = AsyncMongoMockClient(tz_aware=True)
    SENT.clear()
    rate_limit._hits.clear()
    leaderboard_service.invalidate()
    monkeypatch.setattr(auth_api, "send_set_password_email", fake_send)
    await seats.reconcile()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def signup(c, email, alias):
    r = await c.post("/api/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await c.post("/api/users/me/profile", json={"name": alias.title(), "alias": alias}, headers=h)
    assert r.status_code == 201, r.text
    return h


# ---------------------------------------------------------------- validator
def test_bank_is_valid():
    for i in range(3):
        chars = bank_pool(i)
        assert chars, "bundled bank must always be playable"
        for ch in chars:
            assert ch["responds_to"] and ch["shuts_down_to"]
            reveals = {o["reveal"] for n in ch["nodes"].values() for o in n["options"] if o["reveal"]}
            assert reveals == set(SECRETS)


def test_validator_rejects_missing_secret():
    raw = BANK["easy"][0]
    import copy
    bad = copy.deepcopy(raw)
    for n in bad["nodes"]:
        for o in n["options"]:
            if o["reveal"] == "goal":
                o["reveal"] = None
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_unreachable_node():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    bad["nodes"].append({"id": "n99", "text": "orphan", "options": [
        {"text": "a", "approach": "empathy", "next": None, "reveal": None, "rationale": "x"},
        {"text": "b", "approach": "pressure", "next": None, "reveal": None, "rationale": "x"},
    ]})
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_unknown_approach():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    bad["nodes"][0]["options"][0]["approach"] = "mind_control"
    assert validate_character(bad, 0, 0) is None


# ---------------------------------------------------------------- e2e run
async def test_art_and_surv_generation_locks_acquire_on_a_fresh_doc(client):
    """Regression: $lt never matches a missing field, so generation was silently skipped."""
    from app.core.clock import today_ist
    from app.services import content_orchestrator as co
    date = today_ist()
    await co.ensure_doc(date)
    assert await co._acquire_art(date, "t-art") is True
    assert await co._acquire_surv(date, "t-surv") is True


async def _doc():
    return await dbmod.get_db().sessions.find_one({"mode": "dark"})


async def test_start_returns_public_card_without_outcomes(client):
    h = await signup(client, "d1@example.com", "dark1")
    r = await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] and s["description"] and s["image_label"]
    assert s["points"] == 5 and s["stalls"] == 0
    assert s["node"]["options"] and all(set(o) == {"key", "text", "approach", "locked"} for o in s["node"]["options"])
    assert set(s["found"]) == set(SECRETS) and all(v is False for v in s["found"].values())
    # resume: a second start returns the same run
    again = await client.post("/api/dark/start", json={"difficulty": "hard"}, headers=h)
    assert again.json()["id"] == s["id"]


async def _choose_until(client, h, sid, picker, limit=40):
    for _ in range(limit):
        doc = await _doc()
        if doc["state"] != "active":
            return doc
        node = doc["character"]["nodes"][doc["dark"]["node_id"]]
        locked = set(doc["dark"]["locked"].get(node["id"], []))
        idx = picker(node, locked)
        r = await client.post(f"/api/dark/{sid}/choose", json={"key": str(idx)}, headers=h)
        assert r.status_code == 200, r.text
    raise AssertionError("run did not finish")


def _optimal(node, locked):
    return next(i for i, o in enumerate(node["options"]) if o["outcome"] == "optimal")


async def test_optimal_run_finds_all_secrets(client):
    h = await signup(client, "d2@example.com", "dark2")
    sid = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)).json()["id"]
    doc = await _choose_until(client, h, sid, _optimal)
    assert doc["state"] == "finished" and doc["report"]["outcome"] == "success"
    assert all(doc["report"]["found"].values())
    assert doc["report"]["dark_points"] > 0
    stuck = await client.post(f"/api/dark/{sid}/choose", json={"key": "0"}, headers=h)
    assert stuck.status_code == 409


async def test_bad_picks_burn_the_run(client):
    h = await signup(client, "d3@example.com", "dark3")
    sid = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)).json()["id"]

    def bad_or_advance(node, locked):
        bads = [i for i, o in enumerate(node["options"]) if o["outcome"] == "bad" and i not in locked]
        return bads[0] if bads else _optimal(node, locked)

    doc = await _choose_until(client, h, sid, bad_or_advance, limit=60)
    assert doc["state"] == "finished" and doc["report"]["outcome"] == "burned"
    assert doc["report"]["points_left"] <= 0


async def test_three_stalls_end_the_conversation(client):
    h = await signup(client, "d4@example.com", "dark4")
    sid = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)).json()["id"]

    def neutral_or_advance(node, locked):
        neut = [i for i, o in enumerate(node["options"]) if o["outcome"] == "neutral" and i not in locked]
        return neut[0] if neut else _optimal(node, locked)

    doc = await _choose_until(client, h, sid, neutral_or_advance, limit=60)
    assert doc["state"] == "finished" and doc["report"]["outcome"] == "bored"
    assert doc["report"]["stalls"] >= 3


async def test_restart_starts_a_new_run_and_history_tracks_dark(client):
    h = await signup(client, "d5@example.com", "dark5")
    sid = (await client.post("/api/dark/start", json={"difficulty": "medium"}, headers=h)).json()["id"]
    await _choose_until(client, h, sid, _optimal)
    again = await client.post(f"/api/dark/{sid}/restart", headers=h)
    assert again.status_code == 201, again.text
    nxt = again.json()
    assert nxt["id"] != sid and nxt["state"] == "active" and nxt["points"] == 5
    hist = (await client.get("/api/sessions/history?mode=dark", headers=h)).json()
    assert hist["mode"] == "dark" and hist["entries"]
    e = hist["entries"][0]
    assert e["dark"] is True and e["pillars"][0]["pillar"] == "surveillance"
    normal = (await client.get("/api/sessions/history?mode=circuit", headers=h)).json()
    assert all(not x.get("dark") for x in normal["entries"])
