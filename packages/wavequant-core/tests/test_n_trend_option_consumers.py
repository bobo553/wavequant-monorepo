"""Real chart and scanner consumers keep the optional N route isolated."""

from collections.abc import Mapping, Sequence
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
from threading import Lock
from time import monotonic
from typing import cast

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.interfaces.charts.akshare_browser import AkShareBrowser
from wavequant.interfaces.charts.chart_geometry import cached_geometry
from wavequant.interfaces.charts.market_data_repository import MarketDataRepository
from wavequant.interfaces.charts.visualization import ChartRepository, VARIANTS
from wavequant.interfaces.screening.buy_scanner import BuyScanner
from wavequant.interfaces.screening.structure_scanner import StructureScanner


FLAG = "n_target_trend_confirmation_enabled"
LEVELS = ("reversal_trends", "secondary_trends", "tertiary_trends")
SYMBOL = "sz.000678"
ASOF = "2018-09-25"


def _record(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(Mapping[str, object], value)


def _records(value: object) -> Sequence[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    return [_record(item) for item in value]


@pytest.fixture
def bars() -> tuple[Bar, ...]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8")
    )
    return tuple(
        Bar(datetime.fromisoformat(day), SYMBOL, *prices)
        for day, *prices in raw["rows"] if "2018-09-03" <= day <= ASOF
    )


class MemoryAdapter:
    """Replace provider I/O with a fixed, genuine OHLCV prefix."""

    source = "akshare"

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.rows = list(bars)

    def catalog(self) -> dict[str, object]:
        return {"stocks": [{"symbol": SYMBOL, "name": "襄阳轴承", "has_data": True, "last": ASOF}]}

    def load(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        assert symbol == SYMBOL
        return [bar for bar in self.rows if bar.timestamp.date().isoformat() <= asof], list(self.rows)

    def metadata(self) -> dict[str, object]:
        return {}


class TracedMarketDataRepository(MarketDataRepository):
    def __init__(self, adapter: MemoryAdapter) -> None:
        super().__init__([adapter])
        self.receipts: list[tuple[bool, Mapping[str, object]]] = []

    def theory(self, source: str | None, symbol: str, asof: str, *,
               n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
        result = super().theory(source, symbol, asof,
                               n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        self.receipts.append((n_target_trend_confirmation_enabled, result))
        return result


class MemoryChartRepository(ChartRepository):
    """Keep production profiles, signals, rendering and execution; replace sealed-file I/O."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.rows = list(bars)
        self.runs = {"test": {"report": {"data": {"end": ASOF}}}}
        self.theory_lock = Lock()
        self.stock_lock = Lock()
        adapter = MemoryAdapter(bars)
        self.akshare = cast(AkShareBrowser, adapter)
        self.traced_market_data = TracedMarketDataRepository(adapter)
        self.market_data = self.traced_market_data
        self.tdx = None
        base = {
            "strategy": asdict(SystemStrategy()),
            "scenarios": {"base": {"execution": asdict(StrategyConfig())}},
        }
        self.report: dict[str, object] = {"variants": {"strict_full": base, "proxy_full": deepcopy(base)}}
        self.receipts: list[tuple[str, bool, Mapping[str, object]]] = []

    @lru_cache(maxsize=8)
    def bars(self, rid: str) -> dict[str, list[Bar]]:
        self._run(rid)
        return {SYMBOL: self.rows}

    def _json(self, rid: str, relative: str) -> dict[str, object]:
        self._run(rid)
        assert relative == "research/report.json"
        return self.report

    def _bytes(self, rid: str, relative: str) -> bytes:
        self._run(rid)
        assert relative == "snapshot/daily.csv"
        return b"verified-test-provider"

    def theory(self, rid: str, variant: str, symbol: str, asof: str, *,
               n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
        result = super().theory(rid, variant, symbol, asof,
                               n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        self.receipts.append(("theory", n_target_trend_confirmation_enabled, result))
        return cast(dict[str, object], result)

    def stock_view(self, rid: str, variant: str, symbol: str, asof: str, scenario: str = "base", *,
                   n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
        result = super().stock_view(rid, variant, symbol, asof, scenario,
                                   n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        self.receipts.append(("stock_view", n_target_trend_confirmation_enabled, result))
        return cast(dict[str, object], result)

    def akshare_signal_view(self, rid: str, variant: str, symbol: str, asof: str, scenario: str = "base",
                            start: str = "1990-01-01", *,
                            n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
        result = super().akshare_signal_view(rid, variant, symbol, asof, scenario, start,
                                            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        self.receipts.append(("akshare_signal_view", n_target_trend_confirmation_enabled, result))
        return cast(dict[str, object], result)


def _assert_geometry_mode(geometry: Mapping[str, object], enabled: bool) -> None:
    for name in LEVELS:
        assert _record(geometry[name])[FLAG] is enabled
    first = _record(geometry["reversal_trends"])
    origin = [
        point
        for field in ("strokes", "developing_strokes")
        for stroke in _records(first[field])
        for point in _records(stroke["points"])
        if point["time"] == "2018-09-11"
        and point.get("trend_confirmation_route") == "source_n_strict_one_p_target"
    ]
    assert bool(origin) is enabled
    if enabled:
        proof = _record(origin[0]["trend_confirmation"])
        assert proof["available_at"] == "2018-09-20"
        assert proof["one_p_target"] == pytest.approx(5.32)


@pytest.mark.parametrize("first_mode", (False, True))
def test_persistent_geometry_keeps_modes_separate_after_reopening(
    bars: tuple[Bar, ...], tmp_path: Path, first_mode: bool,
) -> None:
    cache = ArtifactCache(tmp_path)
    engine = {"test": "current-core"}
    modes = {}
    for enabled in (first_mode, not first_mode):
        geometry = cached_geometry(cache, bars, "raw_unadjusted", engine,
                                   n_target_trend_confirmation_enabled=enabled)
        _assert_geometry_mode(geometry, enabled)
        modes[enabled] = geometry
    with closing(sqlite3.connect(cache.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE namespace = 'geometry'").fetchone()[0] == 2
    reopened = ArtifactCache(tmp_path)
    for enabled in (False, True):
        assert cached_geometry(reopened, bars, "raw_unadjusted", engine,
                               n_target_trend_confirmation_enabled=enabled) == modes[enabled]
    assert cached_geometry(reopened, bars, "raw_unadjusted", engine) == modes[False]


@pytest.mark.parametrize("first_mode", (False, True))
def test_market_repository_returns_the_correct_cached_mode(bars: tuple[Bar, ...], first_mode: bool) -> None:
    repository = MarketDataRepository([MemoryAdapter(bars)])
    initial = repository.theory("akshare", SYMBOL, ASOF, n_target_trend_confirmation_enabled=first_mode)
    switched = repository.theory("akshare", SYMBOL, ASOF, n_target_trend_confirmation_enabled=not first_mode)
    _assert_geometry_mode(initial, first_mode)
    _assert_geometry_mode(switched, not first_mode)
    assert repository.theory("akshare", SYMBOL, ASOF,
                             n_target_trend_confirmation_enabled=first_mode) is initial
    unchecked = initial if not first_mode else switched
    assert repository.theory("akshare", SYMBOL, ASOF) is unchecked
    assert len(repository._theory) == 2


@pytest.mark.parametrize("enabled", (False, True))
def test_theory_rendering_recomputes_geometry_from_the_other_mode(
    bars: tuple[Bar, ...], tmp_path: Path, enabled: bool,
) -> None:
    repository = MemoryChartRepository(bars)
    config = SystemStrategy(**repository.strategy_config(
        "test", "lecture_v3", n_target_trend_confirmation_enabled=enabled,
    )["strategy"])
    generated = generate_system_signals(bars, config)
    wrong_geometry = cached_geometry(ArtifactCache(tmp_path), bars, "raw_unadjusted", {"test": "current-core"},
                                     n_target_trend_confirmation_enabled=not enabled)
    original = deepcopy(wrong_geometry)
    rendered = repository.render_theory(bars, config, generated, ASOF, geometry=wrong_geometry)
    assert rendered == repository.render_theory(bars, config, generated, ASOF)
    _assert_geometry_mode(rendered, enabled)
    assert wrong_geometry == original


def test_all_profile_consumers_default_to_false_and_do_not_mutate_the_sealed_profile(bars: tuple[Bar, ...]) -> None:
    repository = MemoryChartRepository(bars)
    original = deepcopy(repository.report)
    for variant in VARIANTS:
        default = repository.strategy_config("test", variant)
        assert default == repository.strategy_config("test", variant, n_target_trend_confirmation_enabled=False)
        assert default["strategy"][FLAG] is False
        selected = repository.strategy_config("test", variant, n_target_trend_confirmation_enabled=True)
        assert selected["strategy"][FLAG] is True
        assert selected["definition"][FLAG] is True
    assert repository.report == original


def test_snapshot_theory_and_signal_execution_caches_keep_the_selected_mode(bars: tuple[Bar, ...]) -> None:
    repository = MemoryChartRepository(bars)
    selected = repository.theory("test", "lecture_v3", SYMBOL, ASOF, n_target_trend_confirmation_enabled=True)
    unchecked = repository.theory("test", "lecture_v3", SYMBOL, ASOF)
    _assert_geometry_mode(selected, True)
    _assert_geometry_mode(unchecked, False)
    for enabled in (True, False):
        theory = repository.theory("test", "lecture_v3", SYMBOL, ASOF,
                                  n_target_trend_confirmation_enabled=enabled)
        assert theory is (selected if enabled else unchecked)
        view = repository.stock_view("test", "lecture_v2", SYMBOL, ASOF,
                                     n_target_trend_confirmation_enabled=enabled)
        assert _record(_record(view["backtest"])["strategy"])[FLAG] is enabled
        assert view is repository.stock_view("test", "lecture_v2", SYMBOL, ASOF,
                                             n_target_trend_confirmation_enabled=enabled)


def _complete(scanner: StructureScanner | BuyScanner, initial: Mapping[str, object]) -> Mapping[str, object]:
    job = initial
    deadline = monotonic() + 5
    while job["status"] in ("running", "cancelling") and monotonic() < deadline:
        job = scanner.get(job["id"], after=job["revision"], timeout=1)
    assert job["status"] == "completed", job
    assert job["processed"] == 1 and job["failed"] == 0, job
    return job


@pytest.mark.parametrize("source", ("snapshot", "akshare"))
@pytest.mark.parametrize("option", (None, False, True))
def test_structure_scanner_passes_its_mode_to_the_real_chart_consumer(
    bars: tuple[Bar, ...], source: str, option: bool | None,
) -> None:
    repository = MemoryChartRepository(bars)
    scanner = StructureScanner(repository)
    params = dict(run="test", variant="lecture_v3", source=source, asof=ASOF,
                  lookback=20, signal_type="any", trend_level=0)
    if option is not None:
        params[FLAG] = option
    if source == "akshare":
        params["symbol"] = SYMBOL
        job = scanner.current_akshare(params)
        assert job["status"] == "ready"
        receipts = repository.traced_market_data.receipts
        assert len(receipts) == 1
        received_mode, theory = receipts[0]
    else:
        job = _complete(scanner, scanner.start(params))
        assert len(repository.receipts) == 1
        method, received_mode, theory = repository.receipts[0]
        assert method == "theory"
    enabled = option is True
    assert received_mode is enabled
    _assert_geometry_mode(theory, enabled)
    assert _record(job["params"])[FLAG] is enabled
    assert scanner.algorithm_version() == scanner.algorithm_version(n_target_trend_confirmation_enabled=False)
    assert scanner.algorithm_version() != scanner.algorithm_version(n_target_trend_confirmation_enabled=True)


@pytest.mark.parametrize("source", ("snapshot", "akshare"))
@pytest.mark.parametrize("option", (None, False, True))
def test_buy_scanner_passes_its_mode_to_the_real_signal_consumer(
    bars: tuple[Bar, ...], source: str, option: bool | None,
) -> None:
    repository = MemoryChartRepository(bars)
    scanner = BuyScanner(repository)
    params = dict(run="test", variant="lecture_v2", scenario="base", source=source, asof=ASOF,
                  start="2018-09-03", lookback=20)
    if option is not None:
        params[FLAG] = option
    if source == "akshare":
        params["symbol"] = SYMBOL
    job = _complete(scanner, scanner.start(params))
    enabled = option is True
    assert _record(job["params"])[FLAG] is enabled
    assert len(repository.receipts) == 1
    method, received_mode, view = repository.receipts[0]
    assert method == ("stock_view" if source == "snapshot" else "akshare_signal_view")
    assert received_mode is enabled
    effective = SystemStrategy(**repository.strategy_config(
        "test", "lecture_v2", n_target_trend_confirmation_enabled=enabled,
    )["strategy"])
    generated = generate_system_signals(bars, effective)
    assert _records(view["audit"]) == _records(generated.audit)
    if source == "snapshot":
        assert _record(_record(view["backtest"])["strategy"])[FLAG] is enabled


@pytest.mark.parametrize("invalid", (None, 0, 1, "false", "true"))
def test_geometry_and_market_consumers_reject_non_boolean_options(
    bars: tuple[Bar, ...], tmp_path: Path, invalid: object,
) -> None:
    option = cast(bool, invalid)
    with pytest.raises(ValueError, match="must be a boolean"):
        cached_geometry(ArtifactCache(tmp_path), bars, "raw_unadjusted", {},
                        n_target_trend_confirmation_enabled=option)
    with pytest.raises(ValueError, match="must be a boolean"):
        MarketDataRepository([MemoryAdapter(bars)]).theory(
            "akshare", SYMBOL, ASOF, n_target_trend_confirmation_enabled=option,
        )
