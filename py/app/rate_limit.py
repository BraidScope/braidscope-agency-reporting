# SPDX-License-Identifier: AGPL-3.0-only
"""Per-IP rate limits on the endpoints worth abusing.

Fixed one-minute windows counted in Redis (shared across workers) with an
in-memory fallback when Redis is not configured. Everything else is
unlimited. Limits are per minute, per client IP, per group:

    chat         Ask AI requests                           30
    oauth        connector sign-in starts                  20
    invitations  invite sends and accepts                  10

A request over the limit gets 429 with a Retry-After header. Set
RATE_LIMIT_DISABLED=1 to turn it off (tests, local debugging).
"""
import os
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[assignment]

LIMITS = {"chat": 30, "oauth": 20, "invitations": 10}
WINDOW_SECONDS = 60

_memory: dict[str, int] = defaultdict(int)
_memory_window = 0
_redis: "Redis | None" = None


def group_for(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    if path == "/py/chat":
        return "chat"
    if path.startswith("/py/connectors/") and path.endswith("/oauth/start"):
        return "oauth"
    if path in ("/py/invitations/accept", "/py/settings/team/invite") or (
        path.startswith("/py/clients/") and path.endswith("/invitations")
    ):
        return "invitations"
    return None


def client_ip(request: Request) -> str:
    # Caddy sits in front and sets X-Forwarded-For; the first entry is the caller.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _count(key: str) -> int:
    global _redis, _memory_window
    url = os.getenv("REDIS_URL", "").strip()
    if url and Redis is not None:
        try:
            if _redis is None:
                _redis = Redis.from_url(url, decode_responses=True)
            count = await _redis.incr(key)
            if count == 1:
                await _redis.expire(key, WINDOW_SECONDS)
            return int(count)
        except Exception:  # Redis down: fall back rather than block traffic
            pass
    window = int(time.time()) // WINDOW_SECONDS
    if window != _memory_window:
        _memory.clear()
        _memory_window = window
    _memory[key] += 1
    return _memory[key]


async def rate_limit_middleware(request: Request, call_next):
    if os.getenv("RATE_LIMIT_DISABLED", "").strip() in ("1", "true", "yes"):
        return await call_next(request)
    group = group_for(request.method, request.url.path)
    if group is None:
        return await call_next(request)
    window = int(time.time()) // WINDOW_SECONDS
    key = f"ratelimit:{group}:{client_ip(request)}:{window}"
    count = await _count(key)
    if count > LIMITS[group]:
        retry_after = WINDOW_SECONDS - int(time.time()) % WINDOW_SECONDS
        return JSONResponse(
            status_code=429,
            content={"detail": f"Too many requests. Try again in {retry_after} seconds."},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)
