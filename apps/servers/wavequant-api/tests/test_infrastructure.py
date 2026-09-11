"""Infrastructure tests use sealed in-memory fakes unless explicitly opted into integration."""

from __future__ import annotations

import math
import os
from typing import Any, cast
import unittest

import pytest
from redis import Redis
from sqlalchemy import create_engine

from wavequant_api.infrastructure import (
    ConfigurationError,
    Infrastructure,
    InfrastructureSettings,
    RedisJsonCache,
    ResearchRun,
    ResearchRunRepository,
)
from wavequant_api.cli import index_runs


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        return ex > 0

    def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    def close(self) -> None:
        return None


class InfrastructureTests(unittest.TestCase):
    def test_settings_support_mysql_postgres_sqlite_and_redis_without_exposing_secrets(self) -> None:
        mysql = InfrastructureSettings.from_env(
            {
                "WAVEQUANT_DATABASE_URL": "mysql+pymysql://wavequant:secret@127.0.0.1:3306/wavequant",
                "WAVEQUANT_REDIS_URL": "rediss://:secret@cache.example:6380/0",
                "WAVEQUANT_REDIS_PREFIX": "wavequant:test",
            }
        )
        self.assertEqual(mysql.database.backend if mysql.database else None, "mysql")
        self.assertEqual(
            mysql.public_summary(),
            {
                "database": {"configured": True, "backend": "mysql"},
                "redis": {"configured": True, "tls": True},
            },
        )
        self.assertNotIn("secret", str(mysql.public_summary()))

        postgres = InfrastructureSettings.from_env(
            {"WAVEQUANT_DATABASE_URL": "postgresql+psycopg://wavequant@db.example/wavequant"}
        )
        sqlite = InfrastructureSettings.from_env({"WAVEQUANT_DATABASE_URL": "sqlite+pysqlite:///:memory:"})
        self.assertEqual(postgres.database.backend if postgres.database else None, "postgresql")
        self.assertEqual(sqlite.database.backend if sqlite.database else None, "sqlite")

    def test_settings_reject_unsupported_drivers_and_unbounded_values(self) -> None:
        with self.assertRaises(ConfigurationError):
            InfrastructureSettings.from_env({"WAVEQUANT_DATABASE_URL": "mysql://user:secret@localhost/db"})
        with self.assertRaises(ConfigurationError):
            InfrastructureSettings.from_env(
                {"WAVEQUANT_REDIS_URL": "redis://localhost/0", "WAVEQUANT_CACHE_TTL_SECONDS": "0"}
            )
        with self.assertRaises(ConfigurationError):
            InfrastructureSettings.from_env(
                {"WAVEQUANT_REDIS_URL": "redis://localhost/0", "WAVEQUANT_REDIS_PREFIX": "../unsafe"}
            )

    def test_database_repository_initializes_and_idempotently_updates_runs(self) -> None:
        repository = ResearchRunRepository(create_engine("sqlite+pysqlite:///:memory:"))
        self.addCleanup(repository.close)
        repository.initialize()
        created = repository.save(ResearchRun("run-001", "RUNNING", {"symbols": 2}, strategy="strict_full"))
        updated = repository.save(ResearchRun("run-001", "COMPLETED", {"symbols": 3}, strategy="strict_full"))

        self.assertEqual(created.status, "RUNNING")
        self.assertEqual(updated.status, "COMPLETED")
        self.assertEqual(updated.payload, {"symbols": 3})
        self.assertEqual(len(repository.list_recent()), 1)
        repository.ping()

    def test_database_repository_validates_identifiers_json_and_limits(self) -> None:
        repository = ResearchRunRepository(create_engine("sqlite+pysqlite:///:memory:"))
        self.addCleanup(repository.close)
        repository.initialize()
        with self.assertRaises(ValueError):
            repository.save(ResearchRun("../unsafe", "RUNNING", {}))
        with self.assertRaises(ValueError):
            repository.save(ResearchRun("run", "lowercase", {}))
        with self.assertRaises(ValueError):
            repository.save(ResearchRun("run", "RUNNING", {"value": math.nan}))
        with self.assertRaises(ValueError):
            repository.list_recent(0)

    def test_catalog_index_is_explicit_and_idempotent(self) -> None:
        repository = ResearchRunRepository(create_engine("sqlite+pysqlite:///:memory:"))
        self.addCleanup(repository.close)
        repository.initialize()
        services = Infrastructure(InfrastructureSettings(), database=repository)
        catalog = {"runs": [{"id": "run-001", "completed_at": "2026-09-11", "rows": 10}]}
        self.assertEqual(index_runs(services, catalog), 1)
        self.assertEqual(index_runs(services, catalog), 1)
        stored = repository.get("run-001")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.payload["rows"] if stored else None, 10)

    def test_redis_json_cache_namespaces_values_and_enforces_ttl(self) -> None:
        client = FakeRedis()
        cache = RedisJsonCache(cast(Redis, client), prefix="wavequant:test", ttl_seconds=60)
        cache.set_json("catalog:v1", {"runs": ["a"]})
        self.assertEqual(cache.get_json("catalog:v1"), {"runs": ["a"]})
        self.assertIn("wavequant:test:catalog:v1", client.values)
        cache.delete("catalog:v1")
        self.assertIsNone(cache.get_json("catalog:v1"))
        with self.assertRaises(ValueError):
            cache.set_json("bad/key", {})
        with self.assertRaises(ValueError):
            cache.set_json("key", {}, ttl_seconds=0)

    def test_infrastructure_degrades_safely_and_cache_failure_falls_back(self) -> None:
        class BrokenCache:
            def ping(self) -> None:
                raise ConnectionError

            def get_json(self, key: str) -> object:
                raise ConnectionError

            def set_json(self, key: str, value: object) -> None:
                raise ConnectionError

            def close(self) -> None:
                return None

        settings = InfrastructureSettings.from_env({"WAVEQUANT_REDIS_URL": "redis://localhost:6379/0"})
        services = Infrastructure(settings, cache=cast(Any, BrokenCache()))
        self.assertEqual(services.health()["status"], "degraded")
        self.assertEqual(services.cached_json("catalog", lambda: {"source": True}), {"source": True})


@pytest.mark.integration
def test_configured_mysql_and_redis_services() -> None:
    """Opt-in smoke test; CI unit tests never depend on local Docker."""
    database_url = os.environ.get("WAVEQUANT_TEST_DATABASE_URL")
    redis_url = os.environ.get("WAVEQUANT_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("set WAVEQUANT_TEST_DATABASE_URL and WAVEQUANT_TEST_REDIS_URL")
    settings = InfrastructureSettings.from_env(
        {"WAVEQUANT_DATABASE_URL": database_url, "WAVEQUANT_REDIS_URL": redis_url}
    )
    services = Infrastructure.from_settings(settings)
    try:
        services.initialize_database()
        assert services.health()["status"] == "ok"
        assert services.cache is not None
        services.cache.set_json("integration:probe", {"ok": True}, ttl_seconds=30)
        assert services.cache.get_json("integration:probe") == {"ok": True}
        services.cache.delete("integration:probe")
    finally:
        services.close()
