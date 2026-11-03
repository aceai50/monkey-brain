"""Read-optimised leaderboards. Scores store only user_id + points; alias and the privacy flag are
joined from `users` at cache-refresh time, so hiding yourself takes effect on the next refresh and
there is nothing denormalised to go stale."""
import time

from app.core.db import get_db

TTL_S = 300
TOP = 50
_cache: dict[str, tuple[float, list[dict]]] = {}


def _cached(key: str):
    hit = _cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < TTL_S else None


def invalidate() -> None:
    _cache.clear()


def _rank(rows: list[dict], field: str) -> list[dict]:
    last, rank = None, 0
    for i, r in enumerate(rows):
        if r[field] != last:
            rank, last = i + 1, r[field]
        r["rank"] = rank
    return rows


async def daily(date: str) -> list[dict]:
    key = f"daily:{date}"
    if (c := _cached(key)) is not None:
        return c
    db = get_db()
    scores = await db.daily_scores.find({"date": date}).sort("points", -1).limit(TOP * 3).to_list(TOP * 3)
    users = {u["_id"]: u for u in await db.users.find(
        {"_id": {"$in": [s["user_id"] for s in scores]}, "display_on_leaderboard": True, "alias": {"$exists": True}},
        {"alias": 1, "streak": 1}).to_list(TOP * 3)}
    rows = [{"alias": users[s["user_id"]]["alias"], "points": s["points"], "mode": s.get("mode")}
            for s in scores if s["user_id"] in users][:TOP]
    _cache[key] = (time.monotonic(), _rank(rows, "points"))
    return _cache[key][1]


async def hall() -> list[dict]:
    if (c := _cached("hall")) is not None:
        return c
    cur = get_db().users.find(
        {"display_on_leaderboard": True, "total_points": {"$gt": 0}, "alias": {"$exists": True}},
        {"alias": 1, "total_points": 1, "streak": 1, "sessions": 1},
    ).sort([("total_points", -1), ("sessions", -1)]).limit(TOP)
    rows = [{"alias": u["alias"], "points": u["total_points"], "streak": u.get("streak", 0),
             "sessions": u.get("sessions", 0)} for u in await cur.to_list(TOP)]
    _cache["hall"] = (time.monotonic(), _rank(rows, "points"))
    return _cache["hall"][1]


async def my_daily_rank(user: dict, date: str) -> dict | None:
    db = get_db()
    mine = await db.daily_scores.find_one({"_id": f"{date}:{user['_id']}"})
    if not mine:
        return None
    ahead = await db.daily_scores.count_documents({"date": date, "points": {"$gt": mine["points"]}})
    return {"rank": ahead + 1, "points": mine["points"]}


async def my_hall_rank(user: dict) -> dict | None:
    pts = user.get("total_points", 0)
    if pts <= 0:
        return None
    ahead = await get_db().users.count_documents(
        {"display_on_leaderboard": True, "total_points": {"$gt": pts}, "alias": {"$exists": True}})
    return {"rank": ahead + 1, "points": pts}
