# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - dependency may be absent in local dev before install
    Redis = None  # type: ignore[assignment]

from fastapi import BackgroundTasks

from .core import logger, utc_now

DashboardFactory = Callable[[], Awaitable[dict[str, Any]]]

_REDIS: Redis | None = None


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "").strip()


def dashboard_cache_enabled() -> bool:
    return bool(_redis_url() and Redis is not None)


def _redis() -> Redis | None:
    global _REDIS
    if not dashboard_cache_enabled():
        return None
    if _REDIS is None:
        _REDIS = Redis.from_url(_redis_url(), decode_responses=True)
    return _REDIS


def _now() -> datetime:
    return utc_now()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pack(payload: dict[str, Any], ttl_seconds: int, stale_seconds: int) -> str:
    now = _now()
    return json.dumps(
        {
            "payload": payload,
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "stale_until": (now + timedelta(seconds=ttl_seconds + stale_seconds)).isoformat(),
        },
        separators=(",", ":"),
    )


def _unpack(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
        return None
    return item


def _with_cache_meta(payload: dict[str, Any], item: dict[str, Any] | None, status: str) -> dict[str, Any]:
    result = dict(payload)
    result["cache"] = {
        "status": status,
        "cached_at": item.get("cached_at") if item else None,
        "expires_at": item.get("expires_at") if item else None,
    }
    return result


async def _store(cache_key: str, payload: dict[str, Any], ttl_seconds: int, stale_seconds: int) -> dict[str, Any]:
    redis = _redis()
    if redis is None or not payload.get("ok", False):
        return payload
    raw = _pack(payload, ttl_seconds, stale_seconds)
    await redis.set(cache_key, raw, ex=ttl_seconds + stale_seconds)
    return _with_cache_meta(payload, _unpack(raw), "refreshed")


async def _refresh_in_background(
    cache_key: str,
    lock_key: str,
    factory: DashboardFactory,
    ttl_seconds: int,
    stale_seconds: int,
) -> None:
    redis = _redis()
    try:
        payload = await factory()
        await _store(cache_key, payload, ttl_seconds, stale_seconds)
    except Exception as exc:  # pragma: no cover - defensive; callers should not fail because refresh failed
        logger.warning("Dashboard background refresh failed for %s: %s", cache_key, exc)
    finally:
        if redis is not None:
            try:
                await redis.delete(lock_key)
            except Exception:
                pass


async def get_or_refresh_dashboard_cache(
    *,
    cache_key: str,
    factory: DashboardFactory,
    background_tasks: BackgroundTasks | None = None,
    ttl_seconds: int = 900,
    stale_seconds: int = 86_400,
    lock_seconds: int = 120,
) -> dict[str, Any]:
    redis = _redis()
    if redis is None:
        payload = await factory()
        return _with_cache_meta(payload, None, "bypass")

    lock_key = f"lock:{cache_key}"
    now = _now()

    try:
        item = _unpack(await redis.get(cache_key))
    except Exception as exc:
        logger.warning("Dashboard cache read failed for %s: %s", cache_key, exc)
        payload = await factory()
        return _with_cache_meta(payload, None, "bypass")

    if item:
        expires_at = _parse_dt(item.get("expires_at"))
        stale_until = _parse_dt(item.get("stale_until"))
        payload = item["payload"]
        if expires_at and expires_at > now:
            return _with_cache_meta(payload, item, "fresh")

        if stale_until and stale_until > now:
            try:
                got_lock = await redis.set(lock_key, "1", nx=True, ex=lock_seconds)
            except Exception:
                got_lock = False
            if got_lock and background_tasks is not None:
                background_tasks.add_task(_refresh_in_background, cache_key, lock_key, factory, ttl_seconds, stale_seconds)
            elif got_lock:
                asyncio.create_task(_refresh_in_background(cache_key, lock_key, factory, ttl_seconds, stale_seconds))
            return _with_cache_meta(payload, item, "stale")

    try:
        got_lock = await redis.set(lock_key, "1", nx=True, ex=lock_seconds)
    except Exception as exc:
        logger.warning("Dashboard cache lock failed for %s: %s", cache_key, exc)
        payload = await factory()
        return _with_cache_meta(payload, None, "bypass")

    if got_lock:
        try:
            payload = await factory()
            return await _store(cache_key, payload, ttl_seconds, stale_seconds)
        finally:
            try:
                await redis.delete(lock_key)
            except Exception:
                pass

    for _ in range(12):
        await asyncio.sleep(0.25)
        item = _unpack(await redis.get(cache_key))
        if item:
            return _with_cache_meta(item["payload"], item, "fresh_after_wait")

    payload = await factory()
    return _with_cache_meta(payload, None, "miss_after_wait")
