"""Act & Deceive (v2): engine rules, validator, and the unified dark-practice flow."""
from bson import ObjectId

import httpx
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.api import auth as auth_api
from app.core import db as dbmod
from app.core import rate_limit
from app.generators import act_deceive as A
from app.main import app
from app.services import content_orchestrator as co, leaderboard_service, seats

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
    co._STACK_CACHE.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def signup(c, email, alias):
    r = await c.post("/api/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await c.post("/api/users/me/profile", json={"name": alias.title(), "alias": alias}, headers=h)
    assert r.status_code == 201, r.text
    return h


def _sc(bucket=0):
    chars = A.bank_pool(bucket)
    assert chars
    return chars[0]


def _pick(kind):
    def p(node, opts):
        return next(o for o in opts if o["kind"] == kind)
    return p


async def _next_key(sid: str) -> str:
    """Read the server's session and pick like a winning player (kinds are hidden to the client)."""
    doc = await dbmod.get_db().sessions.find_one({"_id": ObjectId(sid)})
    act = doc["act"]
    node = doc["scenario"]["nodes"][act["node_id"]]
    for i, o in enumerate(node["options"]):
        if o["kind"] == "safe":
            return str(i)
    for i, o in enumerate(node["options"]):
        if o["kind"] != "overreach":
            return str(i)
    return "0"


# ---------------------------------------------------------------- engine + validator
def test_bank_is_valid_for_every_difficulty():
    for i in range(3):
        assert A.bank_pool(i), f"bucket {i} has no playable scenario"


def test_a_convincing_path_exists_in_every_bank_scenario():
    for i in range(3):
        for sc in A.bank_pool(i):
            assert A._dfs_win(sc), f"{sc['id']} has no winning path"


def test_a_safe_line_never_exposes_you():
    """Sticking to what you actually glimpsed keeps doubt at zero and exposes nothing."""
    for i in range(3):
        out = A.walk(_sc(i), _pick("safe"))
        assert out["state"]["doubt"] == 0 and out["exposed"] == 0


def test_always_overreaching_comes_due():
    out = A.walk(_sc(0), _pick("overreach"))
    assert out["state"]["caught"], "every overreach must be called in by a later observer"
    assert out["outcome"] == "busted"


def test_overreach_on_a_glimpsed_fact_is_rejected_by_the_validator():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    glimpsed = {f["id"] for f in bad["facts"] if f["glimpsed"]}
    for n in bad["nodes"]:
        for o in n.get("options") or []:
            if o["kind"] == "overreach":
                o["fact"] = next(iter(glimpsed))
                assert A.validate_scenario(_chain(bad), "easy", 0) is None
                return
    raise AssertionError("no overreach option found")


def _chain(nodes_raw):
    import copy
    r = copy.deepcopy(nodes_raw)
    for a, b in zip(r["nodes"], r["nodes"][1:]):
        a["next"] = b["id"]
    r["nodes"][-1]["next"] = None
    return r


def test_safe_on_an_unknown_fact_is_rejected():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    unknown = next(f["id"] for f in bad["facts"] if not f["glimpsed"])
    for n in bad["nodes"]:
        for o in n.get("options") or []:
            if o["kind"] == "safe":
                o["fact"] = unknown
                o["value"] = "whatever"
                assert A.validate_scenario(_chain(bad), "easy", 0) is None
                return
    raise AssertionError("no safe option found")


def test_contradicting_your_own_claim_costs_doubt():
    sc = {"facts": [{"id": "a", "true_value": "X", "glimpsed": True, "glimpse": ""}],
          "cast": [{"id": "j", "name": "J", "image_label": "boss_1", "role": "", "personality": "curious",
                    "hint": ""}],
          "nodes": {}}
    node = {"id": "n1", "speaker": "j"}
    st = A.new_state(sc)
    A.apply_option(st, sc, node, {"kind": "safe", "tag": "t", "text": "X", "fact": "a", "value": "X",
                                  "works_if": {}, "doubt_on_success": 0, "doubt_on_fail": 1,
                                  "line_if_pass": "ok", "line_if_fail": "no"})
    r = A.apply_option(st, sc, node, {"kind": "reinforce", "tag": "t", "text": "Y", "fact": "a", "value": "Y",
                                      "works_if": {}, "doubt_on_success": 0, "doubt_on_fail": 1,
                                      "line_if_pass": "ok", "line_if_fail": "no"})
    assert r["result"] == "contradiction"
    assert st["doubt"] >= 2


def test_validator_rejects_a_scenario_without_checks():
    import copy
    bad = copy.deepcopy(A.BANK["easy"][0])
    bad["nodes"] = [n for n in bad["nodes"] if n.get("kind") != "check"]
    scenes = [n for n in bad["nodes"] if (n.get("kind") or "scene") == "scene"]
    scenes[-1]["next"] = "end"
    assert A.validate_scenario(bad, "easy", 0) is None


# ---------------------------------------------------------------- e2e
async def _start(c, h, pillar, difficulty="easy", settings=None):
    r = await c.post("/api/dark/practice", json={"pillar": pillar, "difficulty": difficulty,
                                                 "settings": settings or {}}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _play(c, h, sid):
    r = await c.post(f"/api/dark/act/{sid}/play", headers=h)
    assert r.status_code == 200, r.text
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
    assert s["phase"] == "lobby" and s["runner"].startswith("#/act/")
    assert s["role"] and s["glimpses"] and s["cast"]
    st = await _play(client, h, s["id"])
    assert st["phase"] == "scene" and st["doubt"] == 0
    for _ in range(20):
        if st["phase"] == "finished":
            break
        for o in st["node"]["options"]:
            assert set(o) <= {"key", "text", "tag"}
        key = await _next_key(s["id"])
        st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    assert st["phase"] == "finished"
    rep = st["report"]
    assert rep["outcome"] in ("convinced", "partial", "busted")
    assert rep["dark_points"] >= 0 and rep["verdicts"] and rep["log"]
    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["total_points"] == 0            # dark points never touch the normal score


async def test_act_timer_timeout_is_a_stammer(client):
    h = await signup(client, "a3@example.com", "actor3")
    s = await _start(client, h, "act_deceive", settings={"timer": {"enabled": True, "preset": "tight"}})
    st = await _play(client, h, s["id"])
    assert st["timer"]["enabled"] is True and st["timer"]["seconds_left"] > 0
    st2 = (await client.post(f"/api/dark/act/{s['id']}/timeout", headers=h)).json()
    assert st2["phase"] in ("scene", "finished")
    assert st2["doubt"] == 1
    if st2["phase"] == "scene":
        assert st2["node"]["options"]


async def test_act_custom_timer_seconds(client):
    h = await signup(client, "a3b@example.com", "actor3b")
    s = await _start(client, h, "act_deceive",
                     settings={"timer": {"enabled": True, "preset": "normal", "seconds": 45}})
    st = await _play(client, h, s["id"])
    assert st["timer"]["seconds"] == 45


async def test_act_debts_toggle_hides_settled_state(client):
    h = await signup(client, "a4@example.com", "actor4")
    s = await _start(client, h, "act_deceive", settings={"show_debts": False})
    st = await _play(client, h, s["id"])
    key = await _next_key(s["id"])
    st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    assert st["show_debts"] is False
    for e in st["commitments"]:
        assert "held" not in e and "exposed" not in e


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
    st = await _play(client, h, s["id"])
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
        "_id": ObjectId(), "pillar": "art", "date": "2026-01-01",
        "status": "processing", "started_at": now_utc() - timedelta(seconds=5),
        "finished_at": None, "message": "", "error": "", "eta": 300})
    d = (await client.get("/api/admin/jobs", headers=h)).json()
    j = d["jobs"][0]
    assert j["status"] == "processing" and j["finished_at"] is None
    assert j["elapsed"] >= 4.5
    assert j["eta"] >= 120 and j["remaining"] >= 100


def test_assemble_nodes_chains_chunks_into_one_script():
    scene = [
        {"id": "chunk1-a", "speaker": "ravi", "line": "a", "options": [{"kind": "safe", "text": "t"}]},
        {"id": "chunk2-a", "speaker": "jack", "line": "b", "options": [{"kind": "deflect", "text": "u"}]},
    ]
    checks = [{"speaker": "ravi", "fact": "firm", "line": "L", "exposed_line": "x", "held_line": "y"}]
    out = A.assemble_nodes(scene, checks, "bye")
    assert [n["id"] for n in out] == ["n1", "n2", "c1", "end"]
    assert out[0]["next"] == "n2" and out[1]["next"] == "c1" and out[2]["next"] == "end"
    assert out[0]["options"][0]["next"] is None


def test_orchestrator_reassembles_ai_shape_into_a_valid_scenario():
    """Feed the generator's AI-shaped preview + scene + checks through _act_raw and validate it."""
    import copy
    from app.services.content_orchestrator import _act_raw

    raw_bank = copy.deepcopy(A.BANK["easy"][0])
    nodes = raw_bank["nodes"]

    def to_atoms(wi):
        if not wi:
            return []
        return wi["all"] if "all" in wi else [wi]

    preview = {"role": raw_bank["role"], "setting": raw_bank["setting"],
               "facts": raw_bank["facts"], "cast": raw_bank["cast"], "tags": []}
    scene_nodes = []
    for n in nodes:
        if n.get("kind") != "scene":
            continue
        scene_nodes.append({"id": n["id"], "speaker": n["speaker"], "line": n["line"],
                            "options": [{**o, "works_if": to_atoms(o.get("works_if"))}
                                        for o in n["options"]]})
    checks = [{"speaker": c["speaker"], "fact": c["fact"], "line": c["line"],
               "exposed_line": c["exposed_line"], "held_line": c["held_line"], "penalty": c["penalty"]}
              for c in nodes if c.get("kind") == "check"]
    end_line = next(n["line"] for n in nodes if n.get("kind") == "end")
    script = {"nodes": A.assemble_nodes(scene_nodes, checks, end_line), "verdicts": raw_bank["verdicts"]}
    sc = A.validate_scenario(_act_raw(preview, script), "easy", 0)
    assert sc and sc["version"] == A.VERSION and A._dfs_win(sc)


# ---------------------------------------------------------------- difficulty queue + manual bucket
async def test_act_queue_keeps_easy_when_medium_times_out(client, monkeypatch):
    """Each difficulty is stored atomically; a later failure must not discard the earlier one."""
    from app.core.clock import today_ist
    from app.services import gemini_service

    monkeypatch.setattr(gemini_service, "available", lambda: True)

    async def fake_bucket(level, avoid, labels, wanted):
        name = level[0]
        if name == "easy":
            return A.bank_pool(0), {"gemini:test"}
        if name == "medium":
            raise asyncio_timeout()
        return [], set()

    def asyncio_timeout():
        import asyncio
        return asyncio.TimeoutError()

    monkeypatch.setattr(co, "_act_bucket", fake_bucket)
    res = await co.generate_act(today_ist())
    assert res == "partial"
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": today_ist()})
    pools = doc["pools"]["act_deceive"]
    assert pools.get("easy") and not pools.get("medium") and not pools.get("hard")
    assert doc["ad_buckets"]["easy"]["ready"] is True
    assert doc["ad_buckets"]["medium"]["attempts"] >= 1 and doc["ad_buckets"]["medium"]["last_error"]
    assert doc["ad_ready"] is False


async def test_generate_act_bucket_manual_single_difficulty(client, monkeypatch):
    from app.core.clock import today_ist
    from app.services import gemini_service

    monkeypatch.setattr(gemini_service, "available", lambda: True)

    async def fake_bucket(level, avoid, labels, wanted):
        return A.bank_pool(co.ACT_BUCKETS.index(level[0])), {"gemini:test"}

    monkeypatch.setattr(co, "_act_bucket", fake_bucket)
    res = await co.generate_act_bucket(today_ist(), "medium")
    assert res == "ready"
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": today_ist()})
    assert doc["pools"]["act_deceive"]["medium"]
    assert doc["ad_buckets"]["medium"]["ready"] is True


async def test_manual_generate_endpoint_routes_act_bucket(client, monkeypatch):
    from app.services import gemini_service

    monkeypatch.setattr(gemini_service, "available", lambda: True)

    async def fake_bucket(level, avoid, labels, wanted):
        return A.bank_pool(co.ACT_BUCKETS.index(level[0])), {"gemini:test"}

    monkeypatch.setattr(co, "_act_bucket", fake_bucket)
    h = await signup(client, "boss@example.com", "bossman")
    r = await client.post("/api/admin/generate",
                          json={"pillar": "act_deceive", "bucket": "easy", "force": True}, headers=h)
    assert r.status_code == 202, r.text
    job = r.json()["jobs"][0]
    assert job["pillar"] == "act_deceive:easy"
    d = (await client.get("/api/admin/jobs", headers=h)).json()
    j = d["jobs"][0]
    assert j["status"] == "completed", j
    assert "easy:1" in j["message"] and "no items" not in j["message"]
    assert j["trace_id"] and j["evals"]["calls"] == 0
    doc = await dbmod.get_db().daily_challenges.find_one({"_id": __import__(
        "app.core.clock", fromlist=["today_ist"]).today_ist()})
    assert doc["pools"]["act_deceive"]["easy"]


async def test_admin_metrics_endpoint_aggregates(client, monkeypatch):
    from app.services import gemini_service

    monkeypatch.setattr(gemini_service, "available", lambda: True)

    async def fake_bucket(level, avoid, labels, wanted):
        return A.bank_pool(co.ACT_BUCKETS.index(level[0])), {"gemini:test"}

    monkeypatch.setattr(co, "_act_bucket", fake_bucket)
    h = await signup(client, "boss@example.com", "bossman")
    await client.post("/api/admin/generate",
                      json={"pillar": "act_deceive", "bucket": "medium", "force": True}, headers=h)
    m = (await client.get("/api/admin/metrics?days=7", headers=h)).json()
    assert m["total"] >= 1 and m["completed"] >= 1
    assert "Act & Deceive" in m["per_pillar"]
    assert m["per_pillar"]["Act & Deceive"]["items"] >= 1
    assert "provider_calls" in m and "success_rate" in m


async def test_jobs_are_paginated_five_per_page(client):
    from datetime import timedelta
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

    monkeypatch.setattr(co.settings, "generation_pace_s", 0)
    await co.run_all(today_ist(), "cron")
    h = await signup(client, "boss@example.com", "bossman")
    d = (await client.get("/api/admin/logs", headers=h)).json()
    assert d["total"] == 4 and d["window_days"] == 3
    assert {e["pillar"] for e in d["entries"]} == {"critical", "art", "surveillance", "act_deceive"}
    assert all(e["trigger"] == "cron" and e["status"] == "completed" for e in d["entries"])


async def test_logs_window_hides_archive_until_requested(client):
    from datetime import timedelta
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


async def test_review_shows_reaction_and_per_option_breakdown(client):
    h = await signup(client, "a7@example.com", "actor7")
    s = await _start(client, h, "act_deceive")
    st = await _play(client, h, s["id"])
    key = await _next_key(s["id"])
    st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)).json()
    assert st["phase"] in ("scene", "finished")
    if st["phase"] == "scene":
        roles = [m["role"] for m in st["transcript"]]
        assert "me" in roles and "them" in roles
    for _ in range(20):
        if st["phase"] == "finished":
            break
        k = await _next_key(s["id"])
        st = (await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": k}, headers=h)).json()
    scene = [e for e in st["report"]["log"] if e.get("options")]
    assert scene, "report should carry per-turn option breakdowns"
    e = scene[0]
    assert len(e["options"]) == 4
    assert all({"tag", "text", "kind", "delta"} <= set(o) for o in e["options"])
    assert {"reaction", "better", "result", "optkind"} <= set(e)


async def test_dark_stack_is_newest_first_and_cached(client):
    import copy
    from app.core.clock import today_ist, shift_date
    from app.generators.surveillance import bank_pool
    co._STACK_CACHE.clear()
    today = today_ist(); y = shift_date(today, -1)
    base = bank_pool(0)[0]
    for d, i in ((today, 0), (y, 1)):
        await co.ensure_doc(d)
        c = copy.deepcopy(base); c["id"] = f"ai-{i}"; c["name"] = f"N{i}"
        await dbmod.get_db().daily_challenges.update_one({"_id": d}, {"$set": {
            "pools.surveillance": {"easy": [c], "medium": [c], "hard": [c]},
            "sources.surveillance": {"easy": "gemini:m", "medium": "gemini:m", "hard": "gemini:m"}}})
    stack = await co.dark_stack("surveillance", "easy")
    assert [e["date"] for e in stack] == [today, y]
    assert stack[0]["scenario"]["name"] == "N0"
    assert await co.dark_stack("surveillance", "easy") is stack   # cached, no re-scan


async def test_act_choose_survives_legacy_option_without_tag(client):
    """A run whose options lack a display tag must not crash on choose."""
    h = await signup(client, "a8@example.com", "legacy")
    s = await _start(client, h, "act_deceive")
    await _play(client, h, s["id"])
    oid = ObjectId(s["id"])
    doc = await dbmod.get_db().sessions.find_one({"_id": oid})
    for n in doc["scenario"]["nodes"].values():
        for o in (n.get("options") or []):
            o.pop("tag", None)
            o.pop("rationale", None)
    await dbmod.get_db().sessions.update_one({"_id": oid}, {"$set": {"scenario": doc["scenario"]}})
    key = await _next_key(s["id"])
    r = await client.post(f"/api/dark/act/{s['id']}/choose", json={"key": key}, headers=h)
    assert r.status_code == 200, r.text


async def test_legacy_v1_act_run_is_closed_not_crashed(client):
    """An active run created before v2 must not 500 on read; it is finished with a note."""
    from app.core.clock import now_utc, today_ist

    h = await signup(client, "a9@example.com", "legacy9")
    user = await dbmod.get_db().users.find_one({"alias": "legacy9"})
    legacy_sc = {
        "id": "ad-old-1", "version": 1, "difficulty": "easy",
        "cover": {"role": "a chef", "setting": "a dinner", "brief": "x"},
        "image_label": "boss_1", "topics": [{"id": "t1", "name": "Knife work", "study_card": "c"}],
        "prep_cards": ["t1"], "tags": ["name-drop"],
        "cast": [{"id": "j", "name": "J", "image_label": "boss_1", "role": "host",
                  "personality": "curious", "hint": "h"}], "turns": 1,
        "nodes": {"n1": {"id": "n1", "kind": "scene", "speaker": "j", "line": "hi", "next": "end",
                         "options": [{"text": "a", "tag": "t", "kind": "commit", "topic": "t1",
                                      "commits": ["c1"], "works_if": {}, "doubt_on_success": 0,
                                      "doubt_on_fail": 1, "line_if_pass": "p", "line_if_fail": "f"}]},
                  "end": {"id": "end", "kind": "end", "line": "bye", "next": None}},
        "verdicts": {"j": {"bought_it": "b", "suspicious": "s", "sees_through": "x"}}}
    act = {"phase": "prep", "studied": [], "doubt": 0, "vague_streak": 0, "vague_count": 0, "turn": 0,
           "node_id": "n1", "ledger": {}, "caught": [], "wasted": 0, "log": [], "suspicion": {"j": 0},
           "timer": {"enabled": False, "preset": "normal", "seconds": 20}, "show_improvised": True,
           "deadline_at": None, "attempt": 1, "seed": "s", "order": {"n1": [0]}, "outcome": None}
    doc = {"_id": ObjectId(), "user_id": user["_id"], "date": today_ist(), "mode": "dark",
           "pillar": "act_deceive", "difficulty": "easy", "state": "active", "scenario": legacy_sc,
           "image_url": "/x", "cast_media": {}, "pool_size": 1, "stack_index": 0, "act": act,
           "created_at": now_utc(), "finished_at": None, "report": None}
    await dbmod.get_db().sessions.insert_one(doc)
    st = (await client.get(f"/api/dark/act/{str(doc['_id'])}", headers=h)).json()
    assert st["phase"] == "finished" and st["report"]["note"]
    rs = await client.post(f"/api/dark/act/{str(doc['_id'])}/restart", headers=h)
    assert rs.status_code == 201, rs.text
    assert rs.json()["phase"] == "lobby"
    fresh = await _start(client, h, "act_deceive")
    assert fresh["id"] != str(doc["_id"]) and fresh["phase"] == "lobby"
