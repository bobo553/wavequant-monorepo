"""Redis JSON cache with bounded keys, TTLs, and lock lifetime."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import re
from typing import Any

from redis import Redis

from .settings import RedisSettings


_KEY = re.compile(r"[A-Za-z0-9:._-]{1,192}")


class RedisJsonCache:
    """Store replaceable JSON values in Redis; never use this as the fact source."""

    def __init__(self, client: Redis, *, prefix: str = "wavequant", ttl_seconds: int = 300):
        self.client = client
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_settings(cls, settings: RedisSettings) -> RedisJsonCache:
        client = Redis.from_url(
            settings.url,
            decode_responses=True,
            socket_connect_timeout=settings.connect_timeout_seconds,
            socket_timeout=settings.connect_timeout_seconds,
            health_check_interval=30,
        )
        return cls(client, prefix=settings.prefix, ttl_seconds=settings.ttl_seconds)

    def ping(self) -> None:
        if not self.client.ping():
            raise ConnectionError("Redis ping returned false")

    def get_json(self, key: str) -> Any | None:
        raw = self.client.get(self._key(key))
        if raw is None:
            return None
        if not isinstance(raw, (str, bytes, bytearray)):
            raise TypeError("Redis cache value is not JSON text")
        return json.loads(raw)

    def set_json(self, key: str, value: object, *, ttl_seconds: int | None = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if not 1 <= ttl <= 86_400:
            raise ValueError("Redis TTL must be between 1 and 86400 seconds")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self.client.set(self._key(key), encoded, ex=ttl)

    def delete(self, key: str) -> None:
        self.client.delete(self._key(key))

    @contextmanager
    def lock(self, key: str, *, timeout_seconds: int = 30, wait_seconds: int = 2) -> Iterator[bool]:
        if not 1 <= timeout_seconds <= 300 or not 0 <= wait_seconds <= 30:
            raise ValueError("Redis lock timeout is outside the supported range")
        lock = self.client.lock(
            self._key(f"lock:{key}"), timeout=timeout_seconds, blocking_timeout=wait_seconds, thread_local=False
        )
        acquired = bool(lock.acquire())
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def close(self) -> None:
        self.client.close()

    def _key(self, key: str) -> str:
        if not _KEY.fullmatch(key):
            raise ValueError("Redis key contains unsupported characters")
        return f"{self.prefix}:{key}"
