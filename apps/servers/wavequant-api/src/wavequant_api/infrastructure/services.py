"""Lifecycle, health, and graceful-degradation facade for external services."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from .cache import RedisJsonCache
from .database import ResearchRunRepository
from .settings import InfrastructureSettings


class Infrastructure:
    """Own optional SQL and Redis adapters without making dashboard reads depend on them."""

    def __init__(
        self,
        settings: InfrastructureSettings,
        *,
        database: ResearchRunRepository | None = None,
        cache: RedisJsonCache | None = None,
    ):
        self.settings = settings
        self.database = database
        self.cache = cache

    @classmethod
    def from_settings(cls, settings: InfrastructureSettings) -> Infrastructure:
        return cls(
            settings,
            database=ResearchRunRepository.from_settings(settings.database) if settings.database else None,
            cache=RedisJsonCache.from_settings(settings.redis) if settings.redis else None,
        )

    def initialize_database(self) -> None:
        if self.database is None:
            raise ValueError("WAVEQUANT_DATABASE_URL is not configured")
        self.database.initialize()

    def health(self) -> dict[str, object]:
        database = self._component_health(self.database)
        redis = self._component_health(self.cache)
        configured = [component for component in (database, redis) if component["configured"]]
        status = "disabled" if not configured else "ok" if all(component["healthy"] for component in configured) else "degraded"
        return {
            "status": status,
            "database": {**self.settings.public_summary()["database"], **database},
            "redis": {**self.settings.public_summary()["redis"], **redis},
        }

    def cached_json(self, key: str, loader: Callable[[], Any]) -> Any:
        if self.cache is None:
            return loader()
        try:
            cached = self.cache.get_json(key)
            if cached is not None:
                return cached
        except Exception as exc:
            logging.warning("Redis cache read failed (%s); using source data", type(exc).__name__)
        value = loader()
        try:
            self.cache.set_json(key, value)
        except Exception as exc:
            logging.warning("Redis cache write failed (%s); response remains available", type(exc).__name__)
        return value

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()
        if self.database is not None:
            self.database.close()

    @staticmethod
    def _component_health(component: object | None) -> dict[str, bool]:
        if component is None:
            return {"configured": False, "healthy": False}
        try:
            component.ping()  # type: ignore[attr-defined]
        except Exception:
            return {"configured": True, "healthy": False}
        return {"configured": True, "healthy": True}
