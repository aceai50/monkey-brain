"""Act & Deceive: engine rules, validator, and the unified dark-practice flow."""
from itertools import combinations

import httpx
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.api import auth as auth_api
from app.core import db as dbmod
from app.core import rate_limit
from app.generators import act_deceive as A
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


def _sc():
    chars = A.bank_pool(0)
    assert chars
    return chars[0]


async def _next_key(sid: str) -> str:
    """Read the server's session to pick like a winning player would (kinds are hidden)."""
    from bson import ObjectId
    doc = await dbmod.get_db().sessions.find_one({"_id": ObjectId(sid)})
    act = doc["act"]
    node = doc["scenario"]["nodes"][act["node_id"]]
    studied = set(act["studied"])
    for i, o in enumerate(node["options"]):
        if o["kind"] == "commit" and o["topic"] in studied:
            return str(i)
    for i, o in enumerate(node["options"]):
        if o["kind"] == "evade":
            return str(i)
    return "0"


# ---------------------------------------------------------------- engine + validator
def test_bank_is_valid_for_every_difficulty():
    for i in range(3):
        assert A.bank_pool(i), f"bucket {i} has no playable scenario"


def test_unstudied_specific_is_an_improvised_claim():
    sc = _sc()
    node = sc["nodes"]["n1"]
    opt = next(o for o in node["options"] if o["kind"] == "commit")
    cid = opt["commits"][0]
    st = A.new_state(sc, [])
    A.apply_option(st, sc, node, opt)
    assert st["ledger"][cid]["improvised"] is True
    st2 = A.new_state(sc, [opt["topic"]])
    A.apply_option(st2, sc, node, opt)
    assert st2["ledger"][cid]["improvised"] is False


def test_bluff_is_caught_by_a_later_check():
    sc = _sc()

    def all_commits(node, opts):
        return next(o for o in opts if o["kind"] == "commit")

    out = A.walk(sc, [], all_commits)
    assert out["state"]["caught"], "an improvised claim must come due"
    assert any(e["survived"] is False for e in out["state"]["ledger"].values())


def test_always_evasive_busts():
    assert A._strategy(_sc(), [], "evade")["outcome"] == "busted"


def test_always_risky_busts():
    assert A._strategy(_sc(), [], "risk")["outcome"] == "busted"


def test_a_convincing_path_varies_its_answers():
    """The winning move must vary: at least 3 kinds, never twice in a row."""
    sc = _sc()
    found = False
    for combo in combinations(sc["prep_cards"], 3):
        _survive, convince, varied = A._dfs_can_win(sc, list(combo))
        if convince and varied:
            found = True
            break
    assert found


def test_validator_rejects_a_scenario_without_checks():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    bad["nodes"] = [n for n in bad["nodes"] if n.get("kind") != "check"]
    # re-link the last scene to the end
    scenes = [n for n in bad["nodes"] if (n.get("kind") or "scene") == "scene"]
    scenes[-1]["next"] = "end"
    assert A.validate_scenario(bad, "easy", 0) is None


def test_validator_rejects_missing_topic():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    for n in bad["nodes"]:
        for o in n.get("options") or []:
            if o["kind"] == "commit":
                o["topic"] = "t_not_a_real_topic"
                break
        else:
            continue
        break
    assert A.validate_scenario(bad, "easy", 0) is None


def test_validator_rejects_a_scenario_where_one_kind_always_wins():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    # make every commit trivially work and every other option fail
    for n in bad["nodes"]:
        for o in n.get("options") or []:
            if o["kind"] == "commit":
                o["works_if"] = {"all": [{"studied": o["topic"]}]}
            elif o["kind"] == "evade":
                o["works_if"] = {"all": [{"vague_streak_lt": 0}]}
            else:
                o["works_if"] = {"all": [{"turn_lt": 0}]}
    assert A.validate_scenario(bad, "easy", 0) is None


# ---------------------------------------------------------------- e2e
async def _start(c, h, pillar, difficulty="easy", settings=None):
    r = await c.post("/api/dark/practice", json={"pillar": pillar, "difficulty": difficulty,
                                                 "settings": settings or {}}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def test_pillars_endpoint_lists_both(tmp_path, client):
    h = await signup(client, "a1@example.com", "actor1")
    d = (await client.get("/api/dark/pillars", headers=h)).json()
    ids = {p["id"] for p in d["pillars"]}
    assert ids == {"surveillance", "act_deceive"}
    act = next(p for p in d["pillars"] if p["id"] == "act_deceive")
    assert act["settings"]["timer"] and act["difficulty"] == ["easy", "medium", "hard"]


async def test_act_full_run_and_no_mechanics_leak(client):
    h = await signup(client, "a2@example.com", "actor2")
    s = await _start(client, h, "act_deceive")
    assert s["phase"] == "prep" and s["runner"].startswith("#/act/")
    topics = [t["id"] for t in s["topics"]][:3]
    st = (await client.post(f"/api/dark/act/{s['id']}/prep", json={"topic_ids": topics}, headers=h)).json()
    assert st["phase"] == "scene" and st["doubt"] == 0
    for _ in range(20):
        if st["phase"] == "finished":
            break
        for o in st["node"]["options"]:
            assert set(o) <= {"key", "text", "tag", "topic_chip"}
        key = await _next_key(s["id"])
        st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    assert st["phase"] == "finished"
    rep = st["report"]
    assert rep["outcome"] in ("convinced", "partial", "busted")
    assert rep["dark_points"] >= 0 and rep["verdicts"]
    assert rep["log"]
    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["total_points"] == 0            # dark points never touch the normal score


async def test_act_timer_timeout_is_a_stammer(client):
    h = await signup(client, "a3@example.com", "actor3")
    s = await _start(client, h, "act_deceive", settings={"timer": {"enabled": True, "preset": "tight"}})
    topics = [t["id"] for t in s["topics"]][:3]
    st = (await client.post(f"/api/dark/act/{s['id']}/prep", json={"topic_ids": topics}, headers=h)).json()
    assert st["timer"]["enabled"] is True and st["timer"]["seconds_left"] > 0
    st2 = (await client.post(f"/api/dark/act/{s['id']}/timeout", headers=h)).json()
    assert st2["phase"] in ("scene", "finished")
    assert st2["doubt"] == 1
    if st2["phase"] == "scene":
        assert st2["node"]["options"]


async def test_act_ledger_visibility_toggle(client):
    h = await signup(client, "a4@example.com", "actor4")
    s = await _start(client, h, "act_deceive", settings={"show_improvised": False})
    topics = [t["id"] for t in s["topics"]][:3]
    st = (await client.post(f"/api/dark/act/{s['id']}/prep", json={"topic_ids": topics}, headers=h)).json()
    key = await _next_key(s["id"])
    st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    assert st["ledger"] and all(e["improvised"] is None for e in st["ledger"])


async def test_two_dark_pillars_coexist_and_resume(client):
    h = await signup(client, "a5@example.com", "actor5")
    surv = await _start(client, h, "surveillance")
    act = await _start(client, h, "act_deceive")
    assert surv["id"] != act["id"]
    again = await _start(client, h, "surveillance")
    assert again["id"] == surv["id"]            # resumes only its own pillar
    act2 = await _start(client, h, "act_deceive")
    assert act2["id"] == act["id"]
    assert (await client.get(f"/api/dark/act/{act['id']}", headers=h)).json()["pillar"] == "act_deceive"


async def test_act_history_carries_the_pillar(client):
    h = await signup(client, "a6@example.com", "actor6")
    s = await _start(client, h, "act_deceive")
    topics = [t["id"] for t in s["topics"]][:3]
    st = (await client.post(f"/api/dark/act/{s['id']}/prep", json={"topic_ids": topics}, headers=h)).json()
    for _ in range(20):
        if st["phase"] == "finished":
            break
        key = await _next_key(s["id"])
        st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    hist = (await client.get("/api/sessions/history?mode=dark", headers=h)).json()
    assert hist["entries"] and hist["entries"][0]["pillar"] == "act_deceive"

# ---------------------------------------------------------------- admin generation jobs
async def test_generation_job_completes_with_message(client):
    h = await signup(client, "boss@example.com", "bossman")
    r = await client.post("/api/admin/generate", json={"pillar": "act_deceive", "force": True}, headers=h)
    assert r.status_code == 202, r.text
    created = r.json()["jobs"]
    assert created and created[0]["pillar"] == "act_deceive"
    assert created[0]["eta"] >= 300 and r.json()["total_eta"] >= 300
    d = (await client.get("/api/admin/jobs", headers=h)).json()
    j = d["jobs"][0]
    assert j["id"] == created[0]["id"]
    assert j["status"] == "completed", j
    assert j["message"] and j["elapsed"] >= 0 and j["finished_at"]
    assert j["eta"] >= 300 and j["remaining"] == 0


async def test_generation_job_error_is_reported(client, monkeypatch):
    from app.api import admin as admin_api

    async def boom(date, *args):
        raise RuntimeError("gemini said no")

    monkeypatch.setitem(admin_api.GENERATORS, "act_deceive", boom)
    h = await signup(client, "boss@example.com", "bossman")
    r = await client.post("/api/admin/generate", json={"pillar": "act_deceive", "force": True}, headers=h)
    assert r.status_code == 202
    d = (await client.get("/api/admin/jobs", headers=h)).json()
    j = d["jobs"][0]
    assert j["status"] == "error" and "gemini said no" in j["error"]


async def test_processing_job_reports_running_seconds(client):
    from datetime import timedelta
    from app.core.clock import now_utc

    h = await signup(client, "boss@example.com", "bossman")
    await dbmod.get_db().gen_jobs.insert_one({
        "_id": __import__("bson").ObjectId(), "pillar": "art", "date": "2026-01-01",
        "status": "processing", "started_at": now_utc() - timedelta(seconds=5),
        "finished_at": None, "message": "", "error": "", "eta": 300})
    d = (await client.get("/api/admin/jobs", headers=h)).json()
    j = d["jobs"][0]
    assert j["status"] == "processing" and j["finished_at"] is None
    assert j["elapsed"] >= 4.5
    assert j["eta"] >= 120 and j["remaining"] >= 100

def test_assemble_nodes_chains_chunks_into_one_script():
    scene = [
        {"id": "chunk1-a", "speaker": "ravi", "line": "a", "options": [{"text": "t", "next": "bogus"}]},
        {"id": "chunk2-a", "speaker": "jack", "line": "b", "options": [{"text": "u"}]},
    ]
    checks = [{"speaker": "ravi", "claims": ["c1"], "line": "L"}]
    out = A.assemble_nodes(scene, checks, "bye")
    assert [n["id"] for n in out] == ["n1", "n2", "c1", "end"]
    assert out[0]["next"] == "n2" and out[1]["next"] == "c1" and out[2]["next"] == "end"
    assert out[0]["options"][0]["next"] is None

async def test_jobs_are_paginated_five_per_page(client):
    from datetime import timedelta
    from bson import ObjectId
    from app.core.clock import now_utc

    h = await signup(client, "boss@example.com", "bossman")
    for i in range(7):
        await dbmod.get_db().gen_jobs.insert_one({
            "_id": ObjectId(), "pillar": "art", "date": "2026-01-01",
            "status": "completed", "started_at": now_utc() - timedelta(minutes=i),
            "finished_at": now_utc() - timedelta(minutes=i) + timedelta(seconds=5),
            "message": "done", "error": "", "eta": 150, "count": 1})
    p1 = (await client.get("/api/admin/jobs?page=1&page_size=5", headers=h)).json()
    assert p1["total"] == 7 and p1["pages"] == 2 and len(p1["jobs"]) == 5
    p2 = (await client.get("/api/admin/jobs?page=2&page_size=5", headers=h)).json()
    assert len(p2["jobs"]) == 2


async def test_recent_ai_pool_is_used_when_today_is_missing(client):
    import copy
    from app.core.clock import today_ist, shift_date
    from app.generators.surveillance import bank_pool
    from app.services import content_orchestrator as co

    today = today_ist()
    y = shift_date(today, -1)
    await co.ensure_doc(y)
    char = copy.deepcopy(bank_pool(0)[0])
    char["id"] = "ai-yesterday"
    src = "gemini:gemini-2.5-flash"
    await dbmod.get_db().daily_challenges.update_one(
        {"_id": y}, {"$set": {"pools.surveillance": {"easy": [char], "medium": [char], "hard": [char]},
                              "sources.surveillance": {"easy": src, "medium": src, "hard": src}}})
    pools = await co.resolve_surveillance(today)
    assert all(pools[b] and pools[b][0]["id"] == "ai-yesterday" for b in ("easy", "medium", "hard"))

async def test_automatic_run_logs_every_pillar(client, monkeypatch):
    from app.core.clock import today_ist
    from app.services import content_orchestrator as co

    monkeypatch.setattr(co.settings, "generation_pace_s", 0)
    await co.run_all(today_ist(), "cron")
    h = await signup(client, "boss@example.com", "bossman")
    d = (await client.get("/api/admin/logs", headers=h)).json()
    assert d["total"] == 4 and d["window_days"] == 3
    assert {e["pillar"] for e in d["entries"]} == {"critical", "art", "surveillance", "act_deceive"}
    assert all(e["trigger"] == "cron" and e["status"] == "completed" for e in d["entries"])


async def test_logs_window_hides_archive_until_requested(client):
    from datetime import timedelta
    from bson import ObjectId
    from app.core.clock import now_utc, shift_date, today_ist

    h = await signup(client, "boss@example.com", "bossman")
    old = shift_date(today_ist(), -10)
    for d in (today_ist(), old):
        await dbmod.get_db().gen_jobs.insert_one({
            "_id": ObjectId(), "pillar": "art", "date": d, "status": "completed",
            "started_at": now_utc() - timedelta(seconds=5), "finished_at": now_utc(),
            "message": "done", "error": "", "eta": 300, "count": 1,
            "counts": {"easy": 1}, "sources": {"easy": "gemini:gemini-2.5-flash"},
            "trigger": "auto"})
    d1 = (await client.get("/api/admin/logs", headers=h)).json()
    assert d1["total"] == 1 and d1["archived"] == 1 and d1["entries"][0]["provider"] == "gemini:gemini-2.5-flash"
    d2 = (await client.get("/api/admin/logs?include_archived=true", headers=h)).json()
    assert d2["total"] == 2 and d2["pages"] == 1
