"""Validated, secret-safe infrastructure settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url


class ConfigurationError(ValueError):
    """Raised when infrastructure environment values are unsafe or unsupported."""


def _bounded_integer(values: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection settings for the durable research-run index."""

    url: str
    backend: str
    pool_size: int = 5
    connect_timeout_seconds: int = 3


@dataclass(frozen=True)
class RedisSettings:
    """Connection settings for short-lived cache and coordination data."""

    url: str
    prefix: str = "wavequant"
    ttl_seconds: int = 300
    connect_timeout_seconds: int = 2


@dataclass(frozen=True)
class InfrastructureSettings:
    """Optional SQL and Redis configuration loaded from environment variables."""

    database: DatabaseSettings | None = None
    redis: RedisSettings | None = None

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> InfrastructureSettings:
        source = os.environ if values is None else values
        database_url = source.get("WAVEQUANT_DATABASE_URL", "").strip()
        redis_url = source.get("WAVEQUANT_REDIS_URL", "").strip()

        database = None
        if database_url:
            try:
                parsed_database = make_url(database_url)
            except Exception as exc:
                raise ConfigurationError("WAVEQUANT_DATABASE_URL is invalid") from exc
            backend = parsed_database.get_backend_name()
            driver = parsed_database.get_driver_name()
            supported = {("mysql", "pymysql"), ("postgresql", "psycopg"), ("sqlite", "pysqlite")}
            if (backend, driver) not in supported:
                raise ConfigurationError(
                    "WAVEQUANT_DATABASE_URL must use mysql+pymysql, postgresql+psycopg, or sqlite+pysqlite"
                )
            database = DatabaseSettings(
                url=database_url,
                backend=backend,
                pool_size=_bounded_integer(source, "WAVEQUANT_DB_POOL_SIZE", 5, 1, 20),
                connect_timeout_seconds=_bounded_integer(source, "WAVEQUANT_DB_CONNECT_TIMEOUT_SECONDS", 3, 1, 30),
            )

        redis = None
        if redis_url:
            parsed_redis = urlsplit(redis_url)
            if parsed_redis.scheme not in {"redis", "rediss"} or not parsed_redis.hostname:
                raise ConfigurationError("WAVEQUANT_REDIS_URL must be a redis:// or rediss:// URL with a host")
            prefix = source.get("WAVEQUANT_REDIS_PREFIX", "wavequant").strip()
            if not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", prefix):
                raise ConfigurationError("WAVEQUANT_REDIS_PREFIX contains unsupported characters")
            redis = RedisSettings(
                url=redis_url,
                prefix=prefix,
                ttl_seconds=_bounded_integer(source, "WAVEQUANT_CACHE_TTL_SECONDS", 300, 1, 86_400),
                connect_timeout_seconds=_bounded_integer(
                    source, "WAVEQUANT_REDIS_CONNECT_TIMEOUT_SECONDS", 2, 1, 30
                ),
            )

        return cls(database=database, redis=redis)

    def public_summary(self) -> dict[str, dict[str, object]]:
        """Return configuration metadata that is safe to expose in health output."""
        return {
            "database": {"configured": self.database is not None, "backend": self.database.backend}
            if self.database
            else {"configured": False},
            "redis": {"configured": self.redis is not None, "tls": self.redis.url.startswith("rediss://")}
            if self.redis
            else {"configured": False},
        }
