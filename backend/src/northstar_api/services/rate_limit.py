from __future__ import annotations

import asyncio
import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from northstar_api.config import Settings, get_settings
from northstar_api.metrics import RATE_LIMITED

logger = structlog.get_logger(__name__)

_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""

_ROTATE_REFRESH_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
if current ~= ARGV[1] then
  redis.call('DEL', KEYS[1])
  return -1
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


class SessionStoreUnavailable(RuntimeError):
    pass


class RateLimitResult:
    __slots__ = ("allowed", "remaining", "retry_after")

    def __init__(self, allowed: bool, remaining: int, retry_after: int) -> None:
        self.allowed = allowed
        self.remaining = remaining
        self.retry_after = retry_after


class RedisServices:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = Redis.from_url(
            self.settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=self.settings.redis_connect_timeout_seconds,
            socket_timeout=self.settings.redis_socket_timeout_seconds,
        )
        self._memory_windows: dict[str, tuple[int, float]] = {}
        self._memory_refresh_families: dict[str, tuple[str, float]] = {}
        self._memory_lock = asyncio.Lock()

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
        *,
        scope: str = "chat",
    ) -> RateLimitResult:
        namespaced = f"northstar:rl:{key}:{int(time.time()) // window_seconds}"
        try:
            current, ttl = await self.client.eval(_FIXED_WINDOW_SCRIPT, 1, namespaced, window_seconds)
            current_int = int(current)
            allowed = current_int <= limit
            result = RateLimitResult(allowed, max(0, limit - current_int), max(1, int(ttl)))
        except RedisError as exc:
            logger.warning("redis_rate_limit_unavailable", error=type(exc).__name__)
            if not self.settings.rate_limit_fail_open:
                return RateLimitResult(False, 0, window_seconds)
            result = await self._memory_rate_limit(namespaced, limit, window_seconds)
        if not result.allowed:
            RATE_LIMITED.labels(scope).inc()
        return result

    async def _memory_rate_limit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        async with self._memory_lock:
            count, expires = self._memory_windows.get(key, (0, now + window_seconds))
            if expires <= now:
                count, expires = 0, now + window_seconds
            count += 1
            self._memory_windows[key] = (count, expires)
            if len(self._memory_windows) > 10_000:
                self._memory_windows = {
                    item_key: item for item_key, item in self._memory_windows.items() if item[1] > now
                }
        return RateLimitResult(count <= limit, max(0, limit - count), max(1, int(expires - now)))

    async def remember_refresh_family(self, family_id: str, token_id: str, ttl_seconds: int) -> None:
        key = f"northstar:refresh-family:{family_id}"
        try:
            await self.client.set(key, token_id, ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("redis_session_store_unavailable", error=type(exc).__name__)
            if not self.settings.rate_limit_fail_open:
                raise SessionStoreUnavailable("Session store unavailable") from exc
            async with self._memory_lock:
                self._memory_refresh_families[family_id] = (
                    token_id,
                    time.monotonic() + ttl_seconds,
                )

    async def rotate_refresh_family(
        self,
        family_id: str,
        current_token_id: str,
        next_token_id: str,
        ttl_seconds: int,
    ) -> bool:
        key = f"northstar:refresh-family:{family_id}"
        try:
            result = await self.client.eval(
                _ROTATE_REFRESH_SCRIPT,
                1,
                key,
                current_token_id,
                next_token_id,
                ttl_seconds,
            )
            return int(result) == 1
        except RedisError as exc:
            logger.warning("redis_session_store_unavailable", error=type(exc).__name__)
            if not self.settings.rate_limit_fail_open:
                raise SessionStoreUnavailable("Session store unavailable") from exc
            now = time.monotonic()
            async with self._memory_lock:
                current = self._memory_refresh_families.get(family_id)
                if not current or current[1] <= now:
                    self._memory_refresh_families.pop(family_id, None)
                    return False
                if current[0] != current_token_id:
                    # Reuse of an already-rotated token invalidates the current family.
                    self._memory_refresh_families.pop(family_id, None)
                    return False
                self._memory_refresh_families[family_id] = (
                    next_token_id,
                    now + ttl_seconds,
                )
                return True

    async def revoke_refresh_family(self, family_id: str) -> None:
        try:
            await self.client.delete(f"northstar:refresh-family:{family_id}")
        except RedisError as exc:
            logger.warning("redis_session_store_unavailable", error=type(exc).__name__)
            if not self.settings.rate_limit_fail_open:
                raise SessionStoreUnavailable("Session store unavailable") from exc
        async with self._memory_lock:
            self._memory_refresh_families.pop(family_id, None)


redis_services = RedisServices()
