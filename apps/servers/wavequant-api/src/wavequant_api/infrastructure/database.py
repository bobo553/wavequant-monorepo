"""Portable SQLAlchemy repository for shared WaveQuant run metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import JSONB, insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine, RowMapping

from .settings import DatabaseSettings


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
    }
)

research_runs = Table(
    "wavequant_research_runs",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("strategy", String(128), nullable=True),
    Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_wavequant_research_runs_status_updated", "status", "updated_at"),
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_STATUS = re.compile(r"[A-Z][A-Z0-9_-]{0,31}")


@dataclass(frozen=True)
class ResearchRun:
    """Durable metadata for one research run; large artifacts remain outside SQL."""

    run_id: str
    status: str
    payload: Mapping[str, Any]
    strategy: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ResearchRunRepository:
    """Idempotent run metadata storage shared by MySQL and PostgreSQL."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def from_settings(cls, settings: DatabaseSettings) -> ResearchRunRepository:
        connect_args: dict[str, object] = {}
        if settings.backend in {"mysql", "postgresql"}:
            connect_args["connect_timeout"] = settings.connect_timeout_seconds
        options: dict[str, object] = {"pool_pre_ping": True, "connect_args": connect_args}
        if settings.backend != "sqlite":
            options.update(pool_size=settings.pool_size, max_overflow=settings.pool_size, pool_recycle=1800)
        return cls(create_engine(settings.url, **options))

    def initialize(self) -> None:
        """Create the initial schema explicitly; never runs as an import side effect."""
        metadata.create_all(self.engine)

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    def save(self, run: ResearchRun) -> ResearchRun:
        values = self._values(run)
        dialect = self.engine.dialect.name
        with self.engine.begin() as connection:
            if dialect == "mysql":
                mysql_statement = mysql_insert(research_runs).values(**values)
                mysql_statement = mysql_statement.on_duplicate_key_update(
                    status=mysql_statement.inserted.status,
                    strategy=mysql_statement.inserted.strategy,
                    payload=mysql_statement.inserted.payload,
                    updated_at=mysql_statement.inserted.updated_at,
                )
                connection.execute(mysql_statement)
            elif dialect == "postgresql":
                postgresql_statement = postgresql_insert(research_runs).values(**values)
                postgresql_statement = postgresql_statement.on_conflict_do_update(
                    index_elements=[research_runs.c.run_id],
                    set_={key: values[key] for key in ("status", "strategy", "payload", "updated_at")},
                )
                connection.execute(postgresql_statement)
            elif dialect == "sqlite":
                sqlite_statement = sqlite_insert(research_runs).values(**values)
                sqlite_statement = sqlite_statement.on_conflict_do_update(
                    index_elements=[research_runs.c.run_id],
                    set_={key: values[key] for key in ("status", "strategy", "payload", "updated_at")},
                )
                connection.execute(sqlite_statement)
            else:
                raise ValueError(f"unsupported database dialect: {dialect}")
        stored = self.get(run.run_id)
        if stored is None:
            raise RuntimeError("database did not return the saved research run")
        return stored

    def get(self, run_id: str) -> ResearchRun | None:
        self._validate_identifier(run_id, "run_id")
        with self.engine.connect() as connection:
            row = connection.execute(select(research_runs).where(research_runs.c.run_id == run_id)).mappings().one_or_none()
        return self._from_row(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> Sequence[ResearchRun]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        statement = select(research_runs).order_by(research_runs.c.updated_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._from_row(row) for row in rows]

    def close(self) -> None:
        self.engine.dispose()

    @classmethod
    def _values(cls, run: ResearchRun) -> dict[str, object]:
        cls._validate_identifier(run.run_id, "run_id")
        if not _STATUS.fullmatch(run.status):
            raise ValueError("status must be an uppercase stable identifier")
        if run.strategy is not None:
            cls._validate_identifier(run.strategy, "strategy")
        payload = dict(run.payload)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "strategy": run.strategy,
            "payload": payload,
            "created_at": run.created_at or now,
            "updated_at": run.updated_at or now,
        }

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} contains unsupported characters")

    @staticmethod
    def _from_row(row: RowMapping) -> ResearchRun:
        return ResearchRun(
            run_id=row["run_id"],
            status=row["status"],
            strategy=row["strategy"],
            payload=row["payload"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
