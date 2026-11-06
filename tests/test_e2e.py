"""End-to-end against an in-memory Mongo (mongomock-motor)."""
import httpx
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from app.api import auth as auth_api
from app.core import db as dbmod
from app.core import rate_limit
from app.core.config import settings
from app.main import app
from app.services import content_orchestrator, leaderboard_service, seats

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


async def signup(c, email, alias=None, password="correct-horse-1"):
    r = await c.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if alias:
        r = await c.post("/api/users/me/profile", json={"name": alias.title(), "alias": alias}, headers=h)
        assert r.status_code == 201, r.text
    return h


def perfect(session_doc_pillar):
    p = session_doc_pillar
    qs = p["questions"]
    if p["pillar"] == "kinesthetic":
        return {"trials": [{"id": q["id"], "rt_ms": 260, "outcome": "hit"} for q in qs[:3]]}
    if p["pillar"] == "art":
        return {"answers": {q["id"]: "galaxy" for q in qs}}
    return {"answers": {q["id"]: q["answer"] for q in qs}}


async def test_full_circuit_and_leaderboard(client):
    h = await signup(client, "ann@example.com", "ann_x")
    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["today"] is None and dash["streak"] == 0

    r = await client.post("/api/sessions/circuit", json={"difficulty": "auto"}, headers=h)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert [p["pillar"] for p in r.json()["pillars"]] == ["arithmetic", "critical", "pattern", "art", "kinesthetic"]

    # cannot skip ahead
    assert (await client.post(f"/api/sessions/{sid}/pillars/1/start", headers=h)).status_code == 409

    doc = await dbmod.get_db().sessions.find_one({})
    for i, p in enumerate(doc["pillars"]):
        st = await client.post(f"/api/sessions/{sid}/pillars/{i}/start", headers=h)
        assert st.status_code == 200
        for q in st.json()["questions"]:     # answers must never reach the client
            assert "answer" not in q and "explanation" not in q
        sub = await client.post(f"/api/sessions/{sid}/pillars/{i}/submit", json=perfect(p), headers=h)
        assert sub.status_code == 200, sub.text
        # double submit is rejected
        assert (await client.post(f"/api/sessions/{sid}/pillars/{i}/submit", json=perfect(p), headers=h)).status_code == 409

    final = (await client.get(f"/api/sessions/{sid}", headers=h)).json()
    assert final["state"] == "finished" and final["report"]["total_points"] > 0
    assert final["report"]["streak"] == 1

    # a second scored circuit on the same IST day is refused
    again = await client.post("/api/sessions/circuit", json={"difficulty": "auto"}, headers=h)
    assert again.status_code == 409

    d = (await client.get("/api/leaderboard/daily", headers=h)).json()
    assert d["entries"][0]["alias"] == "ann_x" and d["me"]["rank"] == 1
    # privacy flag takes effect immediately
    await client.patch("/api/users/me", json={"display_on_leaderboard": False}, headers=h)
    assert (await client.get("/api/leaderboard/daily", headers=h)).json()["entries"] == []
    hall = (await client.get("/api/leaderboard/hall", headers=h)).json()
    assert hall["entries"] == [] and hall["me"]["rank"] == 1


async def test_sessions_are_private_to_their_owner(client):
    a = await signup(client, "a@example.com", "alpha")
    b = await signup(client, "b@example.com", "bravo")
    sid = (await client.post("/api/sessions/circuit", json={"difficulty": "easy"}, headers=a)).json()["id"]
    for method, path in [("get", f"/api/sessions/{sid}"), ("post", f"/api/sessions/{sid}/pillars/0/start")]:
        assert (await getattr(client, method)(path, headers=b)).status_code == 404
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 401


async def test_user_cap_and_admin_controls(client):
    admin = await signup(client, "boss@example.com", "bossman")
    assert (await client.get("/api/users/me", headers=admin)).json()["is_admin"] is True
    await signup(client, "u1@example.com", "user_one")
    await signup(client, "u2@example.com", "user_two")
    await signup(client, "u3@example.com", "user_three")      # 3 seats, admin exempt
    r = await client.post("/api/auth/register", json={"email": "u4@example.com", "password": "correct-horse-1"})
    assert r.status_code == 403 and "full" in r.json()["detail"].lower()
    assert (await client.get("/api/auth/config")).json()["seats_left"] == 0

    overview = (await client.get("/api/admin/overview", headers=admin)).json()
    assert overview["seats"]["used"] == 3 and len(overview["users"]) == 4

    # non-admin is locked out
    u1 = (await client.post("/api/auth/login", json={"email": "u1@example.com", "password": "correct-horse-1"})).json()
    assert (await client.get("/api/admin/overview", headers={"Authorization": f"Bearer {u1['access_token']}"})).status_code == 403

    victim = next(u for u in overview["users"] if u["email"] == "u2@example.com")
    assert (await client.delete(f"/api/admin/users/{victim['id']}", headers=admin)).status_code == 204
    assert (await client.get("/api/auth/config")).json()["seats_left"] == 1
    # admin cannot be removed
    me = next(u for u in overview["users"] if u["is_admin"])
    assert (await client.delete(f"/api/admin/users/{me['id']}", headers=admin)).status_code == 403

    # invites reserve a seat
    assert (await client.post("/api/admin/invites", json={"email": "vip@example.com"}, headers=admin)).status_code == 201
    assert (await client.get("/api/auth/config")).json()["seats_left"] == 0
    assert (await client.post("/api/auth/register", json={"email": "vip@example.com", "password": "correct-horse-1"})).status_code == 200  # invitee gets in
    assert (await client.post("/api/auth/register", json={"email": "rando@example.com", "password": "correct-horse-1"})).status_code == 403


async def test_reset_link_is_single_use(client):
    await signup(client, "v@example.com")
    assert (await client.post("/api/auth/forgot-password", json={"email": "v@example.com"})).status_code == 202
    tok = SENT["v@example.com"]
    assert (await client.post("/api/auth/set-password", json={"token": tok, "password": "first-pass-1"})).status_code == 200
    assert (await client.post("/api/auth/set-password", json={"token": tok, "password": "attacker-pass"})).status_code == 400
    assert (await client.post("/api/auth/login", json={"email": "v@example.com", "password": "first-pass-1"})).status_code == 200


async def test_duplicate_register_says_email_exists(client):
    await signup(client, "dup@example.com")
    r = await client.post("/api/auth/register", json={"email": "dup@example.com", "password": "another-pass-1"})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


async def test_refresh_reuse_clears_cookie_and_sessions(client):
    await signup(client, "r@example.com", "refr")
    old = client.cookies.get("bm_refresh")
    assert (await client.post("/api/auth/refresh")).status_code == 200
    import asyncio
    # backdate used_at beyond the grace window, then replay the old token
    await dbmod.get_db().refresh_tokens.update_many({"used_at": {"$ne": None}}, {"$set": {"used_at": __import__("datetime").datetime(2020, 1, 1, tzinfo=__import__("datetime").timezone.utc)}})
    client.cookies.set("bm_refresh", old, path="/api/auth")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401 and "bm_refresh" in r.headers.get("set-cookie", "")
    assert await dbmod.get_db().refresh_tokens.count_documents({}) == 0


async def test_internal_endpoint_fails_closed(client):
    assert (await client.post("/internal/generate")).status_code == 403
    assert (await client.post("/internal/generate", headers={"X-Cron-Secret": "wrong"})).status_code == 403
    ok = await client.post("/internal/generate", headers={"X-Cron-Secret": settings.internal_cron_secret})
    assert ok.status_code == 202


async def test_critical_falls_back_to_bank_without_gemini(client):
    from app.core.clock import today_ist
    pools = await content_orchestrator.resolve_pools(today_ist())
    assert all(len(pools["critical"][b]) >= 4 for b in ("easy", "medium", "hard", "expert"))
    q = pools["critical"]["easy"][0]
    assert q["options"][q["answer"]]   # shuffled in code, key consistent


async def test_practice_gradient_and_no_points(client):
    h = await signup(client, "p@example.com", "pract")
    r = await client.post("/api/practice", json={"pillar": "arithmetic", "difficulty": "custom", "slider": 0.25}, headers=h)
    assert r.status_code == 201 and r.json()["pillars"][0]["level"] == "easy to medium"
    sid = r.json()["id"]
    st = (await client.post(f"/api/sessions/{sid}/pillars/0/start", headers=h)).json()
    assert len(st["questions"]) == 15   # default arithmetic count
    await client.post(f"/api/sessions/{sid}/pillars/0/submit", json={"answers": {}}, headers=h)
    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["total_points"] == 0


async def test_customize_counts_drive_session_size_and_multipliers(client):
    h = await signup(client, "c@example.com", "custo")
    cz = (await client.get("/api/users/me/customize", headers=h)).json()
    assert cz["counts"]["arithmetic"] == 15 and cz["limits"]["arithmetic"]["max"] == 30

    # out-of-range / unknown / non-integer values are rejected
    for bad in ({"arithmetic": 31}, {"arithmetic": 4}, {"nope": 5}, {"critical": True}):
        assert (await client.put("/api/users/me/customize", json={"counts": bad}, headers=h)).status_code in (422,)

    r = await client.put("/api/users/me/customize", json={"counts": {"arithmetic": 6, "critical": 2, "pattern": 5, "art": 2, "kinesthetic": 8}}, headers=h)
    assert r.status_code == 200 and r.json()["counts"]["arithmetic"] == 6

    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["multipliers"]["arithmetic"]["count"] == 6
    assert dash["multipliers"]["arithmetic"]["volume"] < 1.0          # fewer questions = lower volume multiplier

    sid = (await client.post("/api/sessions/circuit", json={"difficulty": "easy"}, headers=h)).json()["id"]
    st = (await client.post(f"/api/sessions/{sid}/pillars/0/start", headers=h)).json()
    assert len(st["questions"]) == 6


async def test_report_carries_both_multipliers_and_questions_solved(client):
    h = await signup(client, "m@example.com", "multi")
    sid = (await client.post("/api/sessions/circuit", json={"difficulty": "hard"}, headers=h)).json()["id"]
    doc = await dbmod.get_db().sessions.find_one({})
    for i, p in enumerate(doc["pillars"]):
        await client.post(f"/api/sessions/{sid}/pillars/{i}/start", headers=h)
        await client.post(f"/api/sessions/{sid}/pillars/{i}/submit", json=perfect(p), headers=h)
    rep = (await client.get(f"/api/sessions/{sid}", headers=h)).json()["report"]
    row = rep["pillars"][0]
    assert row["weight"] and row["volume"] and row["offered"] == 15 and row["correct"] == 15
    assert row["points"] == round(row["score"] * row["weight"] * row["volume"])
    assert rep["questions_solved"] >= 15
    dash = (await client.get("/api/users/me/dashboard", headers=h)).json()
    assert dash["questions_solved"] == rep["questions_solved"] and dash["today_solved"] == rep["questions_solved"]


async def test_privacy_toggle_hides_and_restores(client):
    h = await signup(client, "pv@example.com", "privy")
    sid = (await client.post("/api/sessions/circuit", json={"difficulty": "easy"}, headers=h)).json()["id"]
    doc = await dbmod.get_db().sessions.find_one({})
    for i, p in enumerate(doc["pillars"]):
        await client.post(f"/api/sessions/{sid}/pillars/{i}/start", headers=h)
        await client.post(f"/api/sessions/{sid}/pillars/{i}/submit", json=perfect(p), headers=h)
    assert len((await client.get("/api/leaderboard/daily", headers=h)).json()["entries"]) == 1
    r = await client.patch("/api/users/me", json={"display_on_leaderboard": False}, headers=h)
    assert r.json()["display_on_leaderboard"] is False
    assert (await client.get("/api/leaderboard/daily", headers=h)).json()["entries"] == []
    assert (await client.get("/api/leaderboard/daily", headers=h)).json()["me"]["rank"] == 1   # you still see yourself
    await client.patch("/api/users/me", json={"display_on_leaderboard": True}, headers=h)
    assert len((await client.get("/api/leaderboard/daily", headers=h)).json()["entries"]) == 1


async def test_theme_preference_saved_per_account(client):
    h = await signup(client, "t@example.com", "themey")
    assert (await client.get("/api/users/me", headers=h)).json()["theme"] == "system"
    r = await client.patch("/api/users/me", json={"theme": "dark"}, headers=h)
    assert r.status_code == 200 and r.json()["theme"] == "dark"
    assert (await client.get("/api/users/me", headers=h)).json()["theme"] == "dark"
    assert (await client.patch("/api/users/me", json={"theme": "light"}, headers=h)).json()["theme"] == "light"
    assert (await client.patch("/api/users/me", json={"theme": "system"}, headers=h)).json()["theme"] == "system"
    assert (await client.patch("/api/users/me", json={"theme": "<script>"}, headers=h)).status_code == 422
