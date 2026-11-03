"""In-memory sliding-window limiter. Correct for a single Render instance (which is what the free
tier gives you). With >1 instance this becomes per-instance: move it to Mongo/Redis then."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings

_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """Behind Render's proxy the RIGHTMOST X-Forwarded-For entry is the one the proxy appended.
    The leftmost is client-controlled and must never be used for rate limiting."""
    if settings.trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, key: str, limit: int, window_s: int) -> None:
    now = time.monotonic()
    q = _hits[(bucket, key)]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Wait a bit and try again.")
    q.append(now)
    if len(_hits) > 50_000:
        for k in [k for k, v in _hits.items() if not v][:10_000]:
            _hits.pop(k, None)
