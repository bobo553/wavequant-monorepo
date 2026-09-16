"""Portable SQLAlchemy repository for shared WaveQuant run metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, Integer, MetaData, String, Table, create_engine, func, select
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

# Signal families intentionally own separate durable read models. Existing
# legacy tables are left untouched because initialization is additive and never
# drops tables; only the current read models are declared here.
structure_signal_snapshots = Table(
    "wavequant_structure_signal_snapshots",
    metadata,
    Column("snapshot_id", String(64), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("variant", String(128), nullable=False),
    Column("source", String(32), nullable=False),
    Column("scope_symbol", String(32), nullable=True),
    Column("asof", String(10), nullable=False),
    Column("algorithm_version", String(64), nullable=False),
    Column("data_version", String(64), nullable=False),
    Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("total", Integer, nullable=False),
    Column("skipped", Integer, nullable=False),
    Column("failed", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_wavequant_structure_signal_lookup",
        "run_id",
        "variant",
        "source",
        "scope_symbol",
        "asof",
        "algorithm_version",
        "updated_at",
    ),
    Index(
        "ix_wavequant_structure_generation_lookup",
        "run_id",
        "source",
        "asof",
        "algorithm_version",
        "scope_symbol",
        "updated_at",
    ),
)

buy_signal_snapshots = Table(
    "wavequant_buy_signal_snapshots",
    metadata,
    Column("snapshot_id", String(64), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("variant", String(128), nullable=False),
    Column("scenario", String(128), nullable=False),
    Column("source", String(32), nullable=False),
    Column("scope_symbol", String(32), nullable=True),
    Column("asof", String(10), nullable=False),
    Column("start", String(10), nullable=False),
    Column("algorithm_version", String(64), nullable=False),
    Column("data_version", String(64), nullable=False),
    Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("total", Integer, nullable=False),
    Column("skipped", Integer, nullable=False),
    Column("failed", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_wavequant_buy_signal_lookup",
        "run_id",
        "variant",
        "scenario",
        "source",
        "scope_symbol",
        "asof",
        "start",
        "algorithm_version",
        "updated_at",
    ),
)

# Materialized chart bundles are a read model, not another source of truth.  A
# row is immutable for one (input data, algorithm) version and can therefore be
# safely addressed by its snapshot_id from HTTP and browser caches.
market_timeframe_snapshots = Table(
    "wavequant_market_timeframe_snapshots",
    metadata,
    Column("snapshot_id", String(64), primary_key=True),
    Column("source", String(32), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("timeframe", String(16), nullable=False),
    Column("requested_asof", String(10), nullable=False),
    Column("resolved_asof", String(10), nullable=False),
    Column("algorithm_version", String(64), nullable=False),
    Column("data_version", String(64), nullable=False),
    Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index(
        "ix_wavequant_market_timeframe_lookup",
        "source",
        "symbol",
        "timeframe",
        "requested_asof",
        "algorithm_version",
        "updated_at",
    ),
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


@dataclass(frozen=True)
class StructureSnapshot:
    """Published structure-search read model for one immutable input version."""

    snapshot_id: str
    run_id: str
    variant: str
    source: str
    asof: str
    algorithm_version: str
    data_version: str
    payload: Mapping[str, Any]
    total: int
    skipped: int
    failed: int
    scope_symbol: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class BuySignalSnapshot:
    """Published buy-signal read model for one immutable input version."""

    snapshot_id: str
    run_id: str
    variant: str
    scenario: str
    source: str
    asof: str
    start: str
    algorithm_version: str
    data_version: str
    payload: Mapping[str, Any]
    total: int
    skipped: int
    failed: int
    scope_symbol: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class MarketTimeframeSnapshot:
    """Precomputed candles and matching drawing overlays for one period."""

    snapshot_id: str
    source: str
    symbol: str
    timeframe: str
    requested_asof: str
    resolved_asof: str
    algorithm_version: str
    data_version: str
    payload: Mapping[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ResearchRunRepository:
    """Idempotent shared storage for run metadata and derived read models."""

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
            row = (
                connection.execute(select(research_runs).where(research_runs.c.run_id == run_id))
                .mappings()
                .one_or_none()
            )
        return self._from_row(row) if row is not None else None

    def list_recent(self, limit: int = 50) -> Sequence[ResearchRun]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        statement = select(research_runs).order_by(research_runs.c.updated_at.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._from_row(row) for row in rows]

    def save_structure_snapshot(self, snapshot: StructureSnapshot) -> StructureSnapshot:
        """Atomically publish a complete snapshot; partial jobs never call this method."""
        values = self._structure_values(snapshot)
        dialect = self.engine.dialect.name
        with self.engine.begin() as connection:
            if dialect == "mysql":
                mysql_statement = mysql_insert(structure_signal_snapshots).values(**values)
                mysql_statement = mysql_statement.on_duplicate_key_update(
                    payload=mysql_statement.inserted.payload,
                    total=mysql_statement.inserted.total,
                    skipped=mysql_statement.inserted.skipped,
                    failed=mysql_statement.inserted.failed,
                    updated_at=mysql_statement.inserted.updated_at,
                )
                connection.execute(mysql_statement)
            elif dialect == "postgresql":
                postgresql_statement = postgresql_insert(structure_signal_snapshots).values(**values)
                postgresql_statement = postgresql_statement.on_conflict_do_update(
                    index_elements=[structure_signal_snapshots.c.snapshot_id],
                    set_={key: values[key] for key in ("payload", "total", "skipped", "failed", "updated_at")},
                )
                connection.execute(postgresql_statement)
            elif dialect == "sqlite":
                sqlite_statement = sqlite_insert(structure_signal_snapshots).values(**values)
                sqlite_statement = sqlite_statement.on_conflict_do_update(
                    index_elements=[structure_signal_snapshots.c.snapshot_id],
                    set_={key: values[key] for key in ("payload", "total", "skipped", "failed", "updated_at")},
                )
                connection.execute(sqlite_statement)
            else:
                raise ValueError(f"unsupported database dialect: {dialect}")
        stored = self.find_structure_snapshot(
            snapshot.run_id,
            snapshot.variant,
            snapshot.source,
            snapshot.asof,
            snapshot.algorithm_version,
            scope_symbol=snapshot.scope_symbol,
            data_version=snapshot.data_version,
        )
        if stored is None:
            raise RuntimeError("database did not return the saved structure snapshot")
        return stored

    def find_structure_snapshot(
        self,
        run_id: str,
        variant: str,
        source: str,
        asof: str,
        algorithm_version: str,
        *,
        scope_symbol: str | None = None,
        data_version: str | None = None,
    ) -> StructureSnapshot | None:
        """Read the newest fully published snapshot matching the causal version."""
        for value, name in (
            (run_id, "run_id"),
            (variant, "variant"),
            (source, "source"),
            (algorithm_version, "algorithm_version"),
        ):
            self._validate_identifier(value, name)
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("asof must use YYYY-MM-DD")
        if scope_symbol is not None:
            self._validate_identifier(scope_symbol, "scope_symbol")
        statement = select(structure_signal_snapshots).where(
            structure_signal_snapshots.c.run_id == run_id,
            structure_signal_snapshots.c.variant == variant,
            structure_signal_snapshots.c.source == source,
            structure_signal_snapshots.c.scope_symbol == scope_symbol,
            structure_signal_snapshots.c.asof == asof,
            structure_signal_snapshots.c.algorithm_version == algorithm_version,
        )
        if data_version is not None:
            self._validate_digest(data_version, "data_version")
            statement = statement.where(structure_signal_snapshots.c.data_version == data_version)
        statement = statement.order_by(structure_signal_snapshots.c.updated_at.desc()).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return self._structure_from_row(row) if row is not None else None

    def list_structure_snapshots(
        self,
        run_id: str,
        variant: str | None,
        source: str,
        asof: str,
        algorithm_version: str,
        *,
        limit: int = 20_000,
    ) -> Sequence[StructureSnapshot]:
        """Return the newest published shard for every covered symbol."""
        for value, name in ((run_id, "run_id"), (source, "source"), (algorithm_version, "algorithm_version")):
            self._validate_identifier(value, name)
        if variant is not None:
            self._validate_identifier(variant, "variant")
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("asof must use YYYY-MM-DD")
        if type(limit) is not int or not 1 <= limit <= 20_000:
            raise ValueError("limit must be between 1 and 20000")
        filters = [
            structure_signal_snapshots.c.run_id == run_id,
            structure_signal_snapshots.c.source == source,
            structure_signal_snapshots.c.scope_symbol.is_not(None),
            structure_signal_snapshots.c.asof == asof,
            structure_signal_snapshots.c.algorithm_version == algorithm_version,
        ]
        if variant is not None:
            filters.append(structure_signal_snapshots.c.variant == variant)
        ranked = (
            select(
                *structure_signal_snapshots.c,
                func.row_number()
                .over(
                    partition_by=structure_signal_snapshots.c.scope_symbol,
                    order_by=structure_signal_snapshots.c.updated_at.desc(),
                )
                .label("scope_rank"),
            )
            .where(*filters)
            .subquery()
        )
        # Rank before applying the caller's scope limit. Limiting raw history
        # rows first can silently drop symbols that sort after stocks with many
        # data-version snapshots.
        statement = select(ranked).where(ranked.c.scope_rank == 1).order_by(ranked.c.scope_symbol).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._structure_from_row(row) for row in rows]

    def list_structure_snapshot_generations(
        self,
        run_id: str,
        source: str,
        asof: str,
        *,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]:
        """Describe published per-symbol generations at or before a requested market date.

        A generation is the immutable ``(asof, algorithm_version)`` partition.
        Counting distinct symbols keeps historical data-version rewrites and
        strategy variants from inflating its coverage.
        """
        for value, name in ((run_id, "run_id"), (source, "source")):
            self._validate_identifier(value, name)
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("asof must use YYYY-MM-DD")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        ranked = (
            select(
                structure_signal_snapshots.c.asof,
                structure_signal_snapshots.c.algorithm_version,
                structure_signal_snapshots.c.scope_symbol,
                structure_signal_snapshots.c.payload,
                structure_signal_snapshots.c.updated_at,
                func.row_number()
                .over(
                    partition_by=(
                        structure_signal_snapshots.c.asof,
                        structure_signal_snapshots.c.algorithm_version,
                        structure_signal_snapshots.c.scope_symbol,
                    ),
                    order_by=structure_signal_snapshots.c.updated_at.desc(),
                )
                .label("scope_rank"),
            )
            .where(
                structure_signal_snapshots.c.run_id == run_id,
                structure_signal_snapshots.c.source == source,
                structure_signal_snapshots.c.scope_symbol.is_not(None),
                structure_signal_snapshots.c.asof <= asof,
            )
            .subquery()
        )
        statement = (
            select(
                ranked.c.asof,
                ranked.c.algorithm_version,
                func.count().label("published_stocks"),
                func.max(ranked.c.updated_at).label("updated_at"),
                func.max(ranked.c.payload["market_total"].as_integer()).label("market_total"),
                func.coalesce(func.sum(ranked.c.payload["stale"].as_integer()), 0).label("stale_stocks"),
            )
            .where(ranked.c.scope_rank == 1)
            .group_by(ranked.c.asof, ranked.c.algorithm_version)
            .order_by(ranked.c.asof.desc(), func.max(ranked.c.updated_at).desc())
            .limit(limit)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def save_buy_signal_snapshot(self, snapshot: BuySignalSnapshot) -> BuySignalSnapshot:
        """Atomically publish a completed buy-signal snapshot."""
        values = self._buy_values(snapshot)
        self._upsert_snapshot(buy_signal_snapshots, values)
        stored = self.find_buy_signal_snapshot(
            snapshot.run_id,
            snapshot.variant,
            snapshot.scenario,
            snapshot.source,
            snapshot.asof,
            snapshot.start,
            snapshot.algorithm_version,
            scope_symbol=snapshot.scope_symbol,
            data_version=snapshot.data_version,
        )
        if stored is None:
            raise RuntimeError("database did not return the saved buy signal snapshot")
        return stored

    def find_buy_signal_snapshot(
        self,
        run_id: str,
        variant: str,
        scenario: str,
        source: str,
        asof: str,
        start: str,
        algorithm_version: str,
        *,
        scope_symbol: str | None = None,
        data_version: str | None = None,
    ) -> BuySignalSnapshot | None:
        for value, name in (
            (run_id, "run_id"),
            (variant, "variant"),
            (scenario, "scenario"),
            (source, "source"),
            (algorithm_version, "algorithm_version"),
        ):
            self._validate_identifier(value, name)
        if scope_symbol is not None:
            self._validate_identifier(scope_symbol, "scope_symbol")
        for value, name in ((asof, "asof"), (start, "start")):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must use YYYY-MM-DD")
        statement = select(buy_signal_snapshots).where(
            buy_signal_snapshots.c.run_id == run_id,
            buy_signal_snapshots.c.variant == variant,
            buy_signal_snapshots.c.scenario == scenario,
            buy_signal_snapshots.c.source == source,
            buy_signal_snapshots.c.scope_symbol == scope_symbol,
            buy_signal_snapshots.c.asof == asof,
            buy_signal_snapshots.c.start == start,
            buy_signal_snapshots.c.algorithm_version == algorithm_version,
        )
        if data_version is not None:
            self._validate_digest(data_version, "data_version")
            statement = statement.where(buy_signal_snapshots.c.data_version == data_version)
        statement = statement.order_by(buy_signal_snapshots.c.updated_at.desc()).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return self._buy_from_row(row) if row is not None else None

    def save_market_timeframe_snapshot(
        self, snapshot: MarketTimeframeSnapshot
    ) -> MarketTimeframeSnapshot:
        """Atomically publish one complete chart bundle."""

        values = self._market_timeframe_values(snapshot)
        self._upsert_snapshot(
            market_timeframe_snapshots,
            values,
            update_fields=("payload", "resolved_asof", "updated_at"),
        )
        stored = self.find_market_timeframe_snapshot(
            snapshot.source,
            snapshot.symbol,
            snapshot.timeframe,
            snapshot.requested_asof,
            snapshot.algorithm_version,
            data_version=snapshot.data_version,
        )
        if stored is None:
            raise RuntimeError("database did not return the saved market timeframe snapshot")
        return stored

    def find_market_timeframe_snapshot(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        requested_asof: str,
        algorithm_version: str,
        *,
        data_version: str | None = None,
    ) -> MarketTimeframeSnapshot | None:
        """Return the newest complete bundle for the requested causal version."""

        for value, name in (
            (source, "source"),
            (symbol, "symbol"),
            (timeframe, "timeframe"),
        ):
            self._validate_identifier(value, name)
        self._validate_digest(algorithm_version, "algorithm_version")
        if date.fromisoformat(requested_asof).isoformat() != requested_asof:
            raise ValueError("requested_asof must use YYYY-MM-DD")
        statement = select(market_timeframe_snapshots).where(
            market_timeframe_snapshots.c.source == source,
            market_timeframe_snapshots.c.symbol == symbol,
            market_timeframe_snapshots.c.timeframe == timeframe,
            market_timeframe_snapshots.c.requested_asof == requested_asof,
            market_timeframe_snapshots.c.algorithm_version == algorithm_version,
        )
        if data_version is not None:
            self._validate_digest(data_version, "data_version")
            statement = statement.where(market_timeframe_snapshots.c.data_version == data_version)
        statement = statement.order_by(market_timeframe_snapshots.c.updated_at.desc()).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one_or_none()
        return self._market_timeframe_from_row(row) if row is not None else None

    def _upsert_snapshot(
        self,
        table: Table,
        values: dict[str, object],
        *,
        update_fields: Sequence[str] = ("payload", "total", "skipped", "failed", "updated_at"),
    ) -> None:
        dialect = self.engine.dialect.name
        with self.engine.begin() as connection:
            if dialect == "mysql":
                mysql_statement = mysql_insert(table).values(**values)
                connection.execute(mysql_statement.on_duplicate_key_update(
                    **{key: getattr(mysql_statement.inserted, key) for key in update_fields}
                ))
            elif dialect == "postgresql":
                postgresql_statement = postgresql_insert(table).values(**values)
                connection.execute(postgresql_statement.on_conflict_do_update(
                    index_elements=[table.c.snapshot_id],
                    set_={key: values[key] for key in update_fields},
                ))
            elif dialect == "sqlite":
                sqlite_statement = sqlite_insert(table).values(**values)
                connection.execute(sqlite_statement.on_conflict_do_update(
                    index_elements=[table.c.snapshot_id],
                    set_={key: values[key] for key in update_fields},
                ))
            else:
                raise ValueError(f"unsupported database dialect: {dialect}")

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

    @classmethod
    def _structure_values(cls, snapshot: StructureSnapshot) -> dict[str, object]:
        for value, name in (
            (snapshot.snapshot_id, "snapshot_id"),
            (snapshot.run_id, "run_id"),
            (snapshot.variant, "variant"),
            (snapshot.source, "source"),
        ):
            cls._validate_identifier(value, name)
        if snapshot.scope_symbol is not None:
            cls._validate_identifier(snapshot.scope_symbol, "scope_symbol")
        cls._validate_digest(snapshot.algorithm_version, "algorithm_version")
        cls._validate_digest(snapshot.data_version, "data_version")
        if date.fromisoformat(snapshot.asof).isoformat() != snapshot.asof:
            raise ValueError("asof must use YYYY-MM-DD")
        for count, count_name in (
            (snapshot.total, "total"),
            (snapshot.skipped, "skipped"),
            (snapshot.failed, "failed"),
        ):
            if type(count) is not int or count < 0:
                raise ValueError(f"{count_name} must be a non-negative integer")
        payload = dict(snapshot.payload)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "run_id": snapshot.run_id,
            "variant": snapshot.variant,
            "source": snapshot.source,
            "scope_symbol": snapshot.scope_symbol,
            "asof": snapshot.asof,
            "algorithm_version": snapshot.algorithm_version,
            "data_version": snapshot.data_version,
            "payload": payload,
            "total": snapshot.total,
            "skipped": snapshot.skipped,
            "failed": snapshot.failed,
            "created_at": snapshot.created_at or now,
            "updated_at": snapshot.updated_at or now,
        }

    @classmethod
    def _buy_values(cls, snapshot: BuySignalSnapshot) -> dict[str, object]:
        for value, name in (
            (snapshot.snapshot_id, "snapshot_id"),
            (snapshot.run_id, "run_id"),
            (snapshot.variant, "variant"),
            (snapshot.scenario, "scenario"),
            (snapshot.source, "source"),
        ):
            cls._validate_identifier(value, name)
        if snapshot.scope_symbol is not None:
            cls._validate_identifier(snapshot.scope_symbol, "scope_symbol")
        cls._validate_digest(snapshot.algorithm_version, "algorithm_version")
        cls._validate_digest(snapshot.data_version, "data_version")
        for value, name in ((snapshot.asof, "asof"), (snapshot.start, "start")):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must use YYYY-MM-DD")
        for count, name in ((snapshot.total, "total"), (snapshot.skipped, "skipped"), (snapshot.failed, "failed")):
            if type(count) is not int or count < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        payload = dict(snapshot.payload)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "run_id": snapshot.run_id,
            "variant": snapshot.variant,
            "scenario": snapshot.scenario,
            "source": snapshot.source,
            "scope_symbol": snapshot.scope_symbol,
            "asof": snapshot.asof,
            "start": snapshot.start,
            "algorithm_version": snapshot.algorithm_version,
            "data_version": snapshot.data_version,
            "payload": payload,
            "total": snapshot.total,
            "skipped": snapshot.skipped,
            "failed": snapshot.failed,
            "created_at": snapshot.created_at or now,
            "updated_at": snapshot.updated_at or now,
        }

    @classmethod
    def _market_timeframe_values(cls, snapshot: MarketTimeframeSnapshot) -> dict[str, object]:
        for value, name in (
            (snapshot.snapshot_id, "snapshot_id"),
            (snapshot.source, "source"),
            (snapshot.symbol, "symbol"),
            (snapshot.timeframe, "timeframe"),
        ):
            cls._validate_identifier(value, name)
        cls._validate_digest(snapshot.algorithm_version, "algorithm_version")
        cls._validate_digest(snapshot.data_version, "data_version")
        for value, name in (
            (snapshot.requested_asof, "requested_asof"),
            (snapshot.resolved_asof, "resolved_asof"),
        ):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must use YYYY-MM-DD")
        payload = dict(snapshot.payload)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "source": snapshot.source,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.timeframe,
            "requested_asof": snapshot.requested_asof,
            "resolved_asof": snapshot.resolved_asof,
            "algorithm_version": snapshot.algorithm_version,
            "data_version": snapshot.data_version,
            "payload": payload,
            "created_at": snapshot.created_at or now,
            "updated_at": snapshot.updated_at or now,
        }

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} contains unsupported characters")

    @staticmethod
    def _validate_digest(value: str, name: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")

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

    @staticmethod
    def _structure_from_row(row: RowMapping) -> StructureSnapshot:
        return StructureSnapshot(
            snapshot_id=row["snapshot_id"],
            run_id=row["run_id"],
            variant=row["variant"],
            source=row["source"],
            asof=row["asof"],
            algorithm_version=row["algorithm_version"],
            data_version=row["data_version"],
            payload=row["payload"],
            total=row["total"],
            skipped=row["skipped"],
            failed=row["failed"],
            scope_symbol=row["scope_symbol"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _buy_from_row(row: RowMapping) -> BuySignalSnapshot:
        return BuySignalSnapshot(
            snapshot_id=row["snapshot_id"],
            run_id=row["run_id"],
            variant=row["variant"],
            scenario=row["scenario"],
            source=row["source"],
            scope_symbol=row["scope_symbol"],
            asof=row["asof"],
            start=row["start"],
            algorithm_version=row["algorithm_version"],
            data_version=row["data_version"],
            payload=row["payload"],
            total=row["total"],
            skipped=row["skipped"],
            failed=row["failed"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _market_timeframe_from_row(row: RowMapping) -> MarketTimeframeSnapshot:
        return MarketTimeframeSnapshot(
            snapshot_id=row["snapshot_id"],
            source=row["source"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            requested_asof=row["requested_asof"],
            resolved_asof=row["resolved_asof"],
            algorithm_version=row["algorithm_version"],
            data_version=row["data_version"],
            payload=row["payload"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
