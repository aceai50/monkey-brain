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
            assert 4 <= len(ch["tags"]) <= 5 and ch["read"]
            reveals = {o["reveal"] for n in ch["nodes"].values() for o in n["options"] if o["reveal"]}
            assert reveals == set(SECRETS)
            winning = [o["tag"] for n in ch["nodes"].values() for o in n["options"] if o["outcome"] == "optimal"]
            losing = {o["tag"] for n in ch["nodes"].values() for o in n["options"] if o["outcome"] == "bad"}
            assert len(set(winning)) >= 4 and all(t in losing for t in winning)


def test_validator_rejects_missing_secret():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    for n in bad["nodes"]:
        for o in n["options"]:
            if o["reveal"] == "goal":
                o["reveal"] = None
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_unreachable_node():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    bad["nodes"].append({"id": "n99", "text": "orphan", "options": [
        {"text": "a", "tag": "warmth", "outcome": "optimal", "next": None, "reveal": None, "rationale": "x"},
        {"text": "b", "tag": "pressure", "outcome": "bad", "next": None, "reveal": None, "rationale": "x"},
        {"text": "c", "tag": "praise", "outcome": "neutral", "next": None, "reveal": None, "rationale": "x"},
        {"text": "d", "tag": "straight talk", "outcome": "bad", "next": None, "reveal": None, "rationale": "x"},
    ]})
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_duplicate_tags_in_a_turn():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    bad["nodes"][0]["options"][1]["tag"] = bad["nodes"][0]["options"][0]["tag"]
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_a_tag_that_never_loses():
    """A tag that always wins is exactly the predictability we are removing."""
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    bad["tags"].append("charm")
    bad["nodes"][0]["options"][0]["tag"] = "charm"      # n1's winner is now 'charm'
    bad["nodes"][0]["options"][0]["reveal"] = "weakness"
    assert validate_character(bad, 0, 0) is None


def test_validator_rejects_too_few_winning_tags():
    import copy
    bad = copy.deepcopy(BANK["easy"][0])
    n2 = bad["nodes"][1]
    for o in n2["options"]:
        if o["tag"] == "straight talk":
            o["tag"] = "warmth"       # winner repeats; a duplicate tag is then swapped in
        elif o["tag"] == "warmth":
            o["tag"] = "straight talk"
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


async def test_stale_old_schema_pool_is_healed(client):
    """A pool generated before dynamic tags must never crash or leak into a run."""
    from app.core.clock import today_ist
    from app.services import content_orchestrator as co
    date = today_ist()
    await co.ensure_doc(date)
    old = {"character": True, "name": "Old", "description": "x" * 20, "personality": "y" * 20,
           "tone": "flat", "secrets": {"strength": "a", "weakness": "b", "goal": "c"},
           "nodes": {"n1": {"id": "n1", "text": "hi", "options": [
               {"text": "a", "approach": "empathy", "outcome": "optimal", "next": None,
                "reveal": "goal", "rationale": "r"}]}}}
    await dbmod.get_db().daily_challenges.update_one(
        {"_id": date}, {"$set": {"pools.surveillance": {"easy": [old], "medium": [old], "hard": [old]},
                                 "surv_ready": True}})
    pools = await co.resolve_surveillance(date)
    for b in ("easy", "medium", "hard"):
        assert pools[b] and all(c.get("tags") for c in pools[b])


def _clone_chars():
    import copy
    from app.generators.surveillance import bank_pool
    base = bank_pool(0)[0]
    out = []
    for i, nm in enumerate(["Aaa", "Bbb", "Ccc"]):
        c = copy.deepcopy(base)
        c["id"] = f"x{i}"
        c["name"] = nm
        c["image_label"] = f"lbl_{i}"
        out.append(c)
    return out


async def test_skip_cycles_characters_and_discards_the_run(client, monkeypatch):
    from app.services import session_service as svc
    chars = _clone_chars()

    async def fake_pool(date):
        return {"easy": chars, "medium": chars, "hard": chars}

    monkeypatch.setattr(svc.content, "resolve_surveillance", fake_pool)
    h = await signup(client, "s1@example.com", "skip1")
    s = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)).json()
    assert s["can_skip"] is True and s["pool_size"] == 3
    ids, names = [s["id"]], [s["name"]]
    for _ in range(3):
        r = await client.post(f"/api/dark/{ids[-1]}/skip", headers=h)
        assert r.status_code == 201, r.text
        n = r.json()
        ids.append(n["id"])
        names.append(n["name"])
    assert len(set(ids)) == 4
    assert names == ["Aaa", "Bbb", "Ccc", "Aaa"]          # cyclic
    assert await dbmod.get_db().sessions.count_documents({"mode": "dark"}) == 1


async def test_skip_is_unavailable_with_one_character_and_after_finishing(client):
    h = await signup(client, "s2@example.com", "skip2")
    s = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)).json()
    assert s["can_skip"] is False            # the bundled bank has one character per bucket
    assert (await client.post(f"/api/dark/{s['id']}/skip", headers=h)).status_code == 409
    await _choose_until(client, h, s["id"], _optimal)
    assert (await client.post(f"/api/dark/{s['id']}/skip", headers=h)).status_code == 409


async def test_gemini_keeps_every_validated_character(client, monkeypatch):
    """One per level by default; an admin asks for more and nothing is truncated."""
    import copy
    from app.core.clock import today_ist
    from app.services import content_orchestrator as co
    from app.services import gemini_service
    raw = copy.deepcopy(BANK["easy"][0])

    async def fake_items(level, desc, n, avoid, labels):
        return [copy.deepcopy(raw) for _ in range(n)] if level == "easy" else []

    monkeypatch.setattr(gemini_service, "generate_surveillance_items", fake_items)
    one, _, _ = await co._gemini_surv_pools(today_ist(), {}, ["student_1"], count=1)
    assert len(one["easy"]) == 1
    many, sources, _ = await co._gemini_surv_pools(today_ist(), {}, ["student_1"], count=3)
    assert len(many["easy"]) == 3 and sources["easy"] == "gemini"


def test_merge_chars_dedupes_by_id_and_identity():
    from app.services.content_orchestrator import _merge_chars
    a = {"id": "ai-1", "image_label": "x", "name": "A"}
    b = {"id": "ai-2", "image_label": "y", "name": "B"}
    assert [c["id"] for c in _merge_chars([a], [a, b])] == ["ai-1", "ai-2"]


async def test_admin_generate_more_appends_and_enables_skip(client, monkeypatch):
    import copy
    from app.core.clock import today_ist
    from app.services import content_orchestrator as co
    from app.services import gemini_service
    raw = copy.deepcopy(BANK["easy"][0])
    raw.update({"name": "Zed", "image_label": "student_2"})

    async def fake_items(level, desc, n, avoid, labels):
        return [copy.deepcopy(raw) for _ in range(n)]

    monkeypatch.setattr(gemini_service, "generate_surveillance_items", fake_items)
    monkeypatch.setattr(gemini_service, "available", lambda: True)
    admin = await signup(client, "boss@example.com", "bossman")
    date = today_ist()
    await co.ensure_doc(date)
    await dbmod.get_db().daily_challenges.update_one(
        {"_id": date}, {"$set": {"pools.surveillance": {"easy": bank_pool(0), "medium": bank_pool(1),
                                                         "hard": bank_pool(2)},
                                 "surv_ready": True}})
    r = await client.post("/api/admin/generate", json={"pillar": "surveillance", "count": 2, "append": True},
                          headers=admin)
    assert r.status_code == 202, r.text
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": date})
    assert len(doc["pools"]["surveillance"]["easy"]) >= 2      # bank face + the new one(s)
    s = (await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=admin)).json()
    assert s["can_skip"] is True and s["pool_size"] >= 2


async def test_start_returns_public_card_without_outcomes(client):
    h = await signup(client, "d1@example.com", "dark1")
    r = await client.post("/api/dark/start", json={"difficulty": "easy"}, headers=h)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] and s["description"] and s["image_label"]
    assert s["points"] == 5 and s["stalls"] == 0
    assert s["node"]["options"] and all(set(o) == {"key", "text", "tag", "locked"} for o in s["node"]["options"])
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
