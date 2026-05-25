"""
Minimal async key/value + set store used by live_trader.py.

Two backends with identical methods (`get`, `set`, `sadd`, `srem`, `smembers`,
`scan_iter`, `delete`, `ping`):

  - RedisStore   — thin wrapper over redis.asyncio.Redis
  - MemoryStore  — in-process dict; loses state on restart (DB remains the
                   durable source of truth)

Selection: if REDIS_URL is empty/unset OR the first ping fails, MemoryStore is
used. Otherwise RedisStore. Either way, callers see the same async interface.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

log = logging.getLogger("tg-store")


class MemoryStore:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    async def sadd(self, key: str, member: str) -> None:
        self._sets.setdefault(key, set()).add(member)

    async def srem(self, key: str, member: str) -> None:
        self._sets.get(key, set()).discard(member)

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def scan_iter(self, match: str) -> AsyncIterator[str]:
        import fnmatch
        keys = set(self._kv.keys()) | set(self._sets.keys())
        for k in list(keys):
            if fnmatch.fnmatchcase(k, match):
                yield k

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._kv.pop(k, None)
            self._sets.pop(k, None)


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis
        self._r = aioredis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        return await self._r.ping()

    async def get(self, key):     return await self._r.get(key)
    async def set(self, k, v):    return await self._r.set(k, v)
    async def sadd(self, k, m):   return await self._r.sadd(k, m)
    async def srem(self, k, m):   return await self._r.srem(k, m)
    async def smembers(self, k):  return await self._r.smembers(k)
    async def delete(self, *keys): return await self._r.delete(*keys)

    async def scan_iter(self, match):
        async for k in self._r.scan_iter(match=match):
            yield k


async def make_store(redis_url: str | None):
    """Build a store. Returns (store, kind) — kind is 'redis' or 'memory'."""
    if redis_url:
        try:
            store = RedisStore(redis_url)
            await store.ping()
            return store, "redis"
        except Exception as e:
            log.warning("Redis unreachable at %s (%s) — falling back to in-memory store", redis_url, e)
    return MemoryStore(), "memory"
