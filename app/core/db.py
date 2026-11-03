from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

from app.core.config import settings

_client: AsyncMongoClient | None = None


def get_db():
    assert _client is not None, "DB not initialised"
    return _client[settings.mongodb_db]


async def connect() -> None:
    global _client
    _client = AsyncMongoClient(settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=8000)
    await ensure_indexes()


async def ensure_indexes() -> None:
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index(
        "alias_lower", unique=True, partialFilterExpression={"alias_lower": {"$type": "string"}}
    )
    await db.users.create_index(
        "google_sub", unique=True, partialFilterExpression={"google_sub": {"$type": "string"}}
    )
    await db.users.create_index([("display_on_leaderboard", ASCENDING), ("total_points", DESCENDING)])
    await db.refresh_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.refresh_tokens.create_index([("user_id", ASCENDING)])
    # one scored circuit per user per IST day; practice sessions are unconstrained
    await db.sessions.create_index(
        [("user_id", ASCENDING), ("date", ASCENDING), ("mode", ASCENDING)],
        unique=True, partialFilterExpression={"mode": "circuit"},
    )
    await db.sessions.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await db.daily_scores.create_index([("date", ASCENDING), ("points", DESCENDING)])
    await db.daily_scores.create_index([("user_id", ASCENDING), ("date", DESCENDING)])
    from app.services import seats  # local import: avoids a cycle
    await seats.reconcile()


async def close() -> None:
    if _client is not None:
        await _client.close()
