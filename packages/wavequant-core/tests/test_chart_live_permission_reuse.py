"""请求内许可复用及间隔续算不得改变因果结构、事件或形态附件。"""

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

import wavequant.domain.strategies.chart_entry_history as chart_module
from wavequant.domain.market_structure.trend_staging import TrendStageRows
from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.trend_lines import (
    _TrendPermissionCache, attach_trend_permissions, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame, ChartDrawingHistory
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketCalculations, MarketDay

from .test_chart_owned_transfer import _typed as _snapshot
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.owned_evidence import CertifiedSource
from .test_staged_trend_pipeline import _multiple_path_bars


def _typed(value: object) -> bytes:
    # 私有版本在公开序列化边界是数组；保留所有字段和标量类型再比较。
    def arrays(item: object) -> object:
        if type(item) is dict:
            return {key: arrays(child) for key, child in cast(dict, item).items()}
        if type(item) in (list, PointVersion, CertifiedSource):
            return [arrays(child) for child in cast(Sequence[object], item)]
        if type(item) is tuple:
            return tuple(arrays(child) for child in cast(tuple[object, ...], item))
        return item

    return _snapshot(arrays(value))


def _strategy() -> SystemStrategy:
    return SystemStrategy(pivot_mode='lecture_causal', strict_n_attack_quality=False,
        preflight_reward_risk=False, volume_filter=False)


def _read(
    analysis: BacktestAnalysis, cache: dict[str, object] | None,
    audit: Sequence[dict[str, object]] = (), *, enabled: bool = False,
) -> tuple[object, ...]:
    shallow: dict = {}
    combined: dict = {}
    pullback: dict = {}
    history, events = chart_module.chart_entry_history(analysis.bars, audit=audit,
        shallow_candidate_sink=shallow, combined_candidate_sink=combined,
        secondary_pullback_sink=pullback, prefix_cache=cache, _prepared_history=analysis,
        n_target_trend_confirmation_enabled=enabled)
    return history, events, shallow, combined, pullback


def _record_starts(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    starts: list[int | None] = []
    original = ChartDrawingHistory.iter_rows

    def read(self: ChartDrawingHistory, *, shared: bool = False,
             start_index: int | None = None) -> Iterator[ChartDrawingFrame]:
        starts.append(start_index)
        return original(self, shared=shared, start_index=start_index)

    monkeypatch.setattr(ChartDrawingHistory, 'iter_rows', read)
    return starts


@pytest.mark.parametrize('enabled', (False, True))
def test_live_gap_replays_only_unfinished_suffix_and_matches_cold_results(
    enabled: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=16)
    checkpoints = (9, 21, len(bars))
    starts = _record_starts(monkeypatch)
    cache: dict[str, object] = {}
    permission_cache: _TrendPermissionCache | None = None
    resumes: list[int | None] = []
    owners: list[object] = []

    def consume(_day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal permission_cache
        if len(analysis.bars) not in checkpoints:
            return
        # 固定窗口只隔离调度变量；折线、趋势及许可判定均执行真实实现。
        current = replace(analysis, buy_days=(True,) * len(analysis.bars))
        owner = current.calculations
        assert owner is not None
        owners.append(owner)
        before = len(starts)
        warm = _read(current, cache, enabled=enabled)
        resumes.extend(starts[before:])
        current_cache = owner._permission_cache_for(current.bars)
        if permission_cache is not None:
            assert current_cache is permission_cache
        permission_cache = current_cache
        owner._strategy_permission_cache = None
        try:
            original_reader = TrendStageRows.iter_rows

            def fresh_reader(stage: TrendStageRows[object], *, seal: bool = True, start_row: int = 0,
                             _borrow_readonly: bool = False) -> Iterator[object]:
                return original_reader(stage, seal=seal, start_row=start_row, _borrow_readonly=False)

            with monkeypatch.context() as fresh:
                fresh.setattr(TrendStageRows, 'iter_rows', fresh_reader)
                cold = _read(current, None, enabled=enabled)
        finally:
            owner._strategy_permission_cache = current_cache
        assert _typed(warm) == _typed(cold)

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert resumes == [0, checkpoints[0] - 1, checkpoints[1] - 1]
    assert owners and all(getattr(owner, '_strategy_permission_cache') is None for owner in owners)


@pytest.mark.parametrize('revision', ('attack', 'eligibility', 'options', 'owner'))
def test_live_gap_rejects_revised_proof_permission_options_or_foreign_owner(
    revision: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=12)
    starts = _record_starts(monkeypatch)
    cache: dict[str, object] = {}
    first_count, second_count = 9, 20
    resumes: list[int | None] = []
    attack: dict[str, object] = dict(event='n_completed', direction='up', bar_index=5,
        known_at=5, origin=0, neckline=2, pullback=4, one_p=1500., two_t=1800.,
        proof={'version': 1})

    def consume(_day: MarketDay, analysis: BacktestAnalysis) -> None:
        count = len(analysis.bars)
        if count not in (first_count, second_count):
            return
        days = [True] * count
        audit = deepcopy([attack])
        enabled = False
        if count == second_count:
            if revision == 'attack':
                cast(dict[str, object], audit[0]['proof'])['version'] = 2
            elif revision == 'eligibility':
                days[2] = False
            elif revision == 'options':
                enabled = True
            elif revision == 'owner':
                # 模拟另一个请求的相同行情；仅对象身份变动也不能跨请求借用。
                cache['_live_owner'] = object()
        current = replace(analysis, buy_days=tuple(days))
        before = len(starts)
        warm = _read(current, cache, audit, enabled=enabled)
        resumes.extend(starts[before:])
        cold = _read(current, None, audit, enabled=enabled)
        assert _typed(warm) == _typed(cold)

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert resumes == [0, 0]


def test_minute_tail_revision_uses_its_own_index_without_replacing_daily_cache() -> None:
    bars = _multiple_path_bars(count=8)
    observed = False

    def consume(day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal observed
        if len(analysis.bars) != 10:
            return
        owner = analysis.calculations
        assert owner is not None
        daily = owner._permission_cache_for(analysis.bars)
        last = analysis.bars[-1]
        revised = (*analysis.bars[:-1], replace(last, close=last.high, volume=last.volume * 1.5))
        minute = owner._permission_cache_for(revised)
        assert minute is not daily
        assert minute.covers(revised)
        assert not daily.covers(revised)
        assert owner._permission_cache_for(analysis.bars) is daily
        cold = _TrendPermissionCache(revised)
        expected = attach_trend_permissions(day.levels[0], revised, _permission_cache=cold)
        actual = attach_trend_permissions(day.levels[0], revised, _permission_cache=minute)
        assert _typed(actual) == _typed(expected)
        actual.clear()
        assert _typed(attach_trend_permissions(day.levels[0], revised, _permission_cache=minute)) == _typed(expected)
        observed = True

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert observed


def test_public_permission_outputs_cannot_mutate_next_read_of_request_cache() -> None:
    bars = _multiple_path_bars(count=14)
    observed = False

    def consume(day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal observed
        if len(analysis.bars) != len(bars):
            return
        owner = analysis.calculations
        assert owner is not None
        cache = owner._permission_cache_for(analysis.bars)
        source = polyline_high_low_points(day.frame.drawing)
        source = [dict(point, source_path_end_index=len(bars)) for point in source]
        prepared = prepare_trend_lines(source, trend_level=1)
        first = attach_trend_permissions(prepared, analysis.bars, _permission_cache=cache)
        expected = _typed(first)
        # 只有资格输出允许修改；接口约定几何、来源和候选数组为只读借用。
        mutable: list[object] = [first['qualified_strokes']]
        visited: set[int] = set()
        while mutable:
            value = mutable.pop()
            if id(value) in visited:
                continue
            visited.add(id(value))
            if isinstance(value, dict):
                mutable.extend(value.values())
                value.clear()
            elif isinstance(value, list):
                mutable.extend(value)
                value.clear()
            elif isinstance(value, tuple):
                mutable.extend(value)
        second = attach_trend_permissions(prepared, analysis.bars, _permission_cache=cache)
        assert _typed(second) == expected
        observed = True

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert observed


def test_request_permission_cache_is_released_after_consumer_failure() -> None:
    bars = _multiple_path_bars(count=4)
    owner: MarketCalculations | None = None

    def consume(_day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal owner
        owner = analysis.calculations
        assert owner is not None
        owner._permission_cache_for(analysis.bars)
        raise RuntimeError('consumer interrupted')

    with pytest.raises(RuntimeError, match='consumer interrupted'):
        with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
            pass
    assert owner is not None
    assert owner._strategy_permission_cache is None


def test_consecutive_live_days_reuse_lifecycle_without_reading_or_decoding_the_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=8)
    cache: dict[str, object] = {}
    owner: MarketCalculations | None = None

    def consume(_day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal owner
        owner = analysis.calculations
        assert owner is not None
        current = replace(analysis, buy_days=(True,) * len(analysis.bars))
        expected = _typed(_read(current, None))
        original_changes = TrendChangeRows.iter_changes

        def forbid_checkpoint_read(history: TrendChangeRows[object], *, start_position: int = 0,
                                   _borrow_readonly: bool = False) -> Iterator[tuple[int, object]]:
            assert owner is not None
            if history is owner._checkpoints:
                raise AssertionError('consecutive live days must not decode lifecycle history')
            return original_changes(history, start_position=start_position, _borrow_readonly=_borrow_readonly)

        def forbid_checkpoint_restore(_value: object) -> TrendLifecycle:
            raise AssertionError('live state must not be serialized and parsed again')

        with monkeypatch.context() as live:
            live.setattr(TrendChangeRows, 'iter_changes', forbid_checkpoint_read)
            live.setattr(TrendLifecycle, 'from_checkpoint', forbid_checkpoint_restore)
            assert _typed(_read(current, cache)) == expected
        assert set(owner._live_lifecycle_days) == set(range(max(0, len(analysis.bars) - 2), len(analysis.bars)))

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert owner is not None
    assert owner._live_lifecycle_days == {}


@pytest.mark.parametrize('revision', ('window', 'removed', 'added', 'old_proof', 'budget'))
@pytest.mark.parametrize('enabled', (False, True))
def test_new_window_repairs_only_n_attachment_using_original_dated_geometry(
    revision: str, enabled: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wavequant.domain.strategies.combined_a_replay import CombinedAReplay

    bars = _multiple_path_bars(count=12)
    starts = _record_starts(monkeypatch)
    cache: dict[str, object] = {}
    first_count, second_count = 9, 20
    resumes: list[int | None] = []
    attack = dict(event='n_completed', direction='up', bar_index=5, known_at=5,
        origin=0, neckline=2, pullback=4, one_p=1500., two_t=1800., proof={'version': 1})

    def consume(_day: MarketDay, analysis: BacktestAnalysis) -> None:
        count = len(analysis.bars)
        if count not in (first_count, second_count):
            return
        audit = deepcopy([attack])
        if count == second_count:
            if revision == 'window':
                audit[0].update(known_at=second_count - 1, one_p=None, two_t=None, window_start=second_count - 1)
            elif revision == 'removed':
                audit.clear()
            elif revision == 'added':
                audit.append(dict(attack, bar_index=6, known_at=second_count - 1))
            elif revision == 'old_proof':
                audit[0]['proof'] = {'version': 2}
            elif revision == 'budget':
                audit[0]['known_at'] = second_count - 1
                cache['_combined_replay'] = CombinedAReplay(memory_bytes=0)
        current = replace(analysis, buy_days=(True,) * count)
        before = len(starts)
        warm = _read(current, cache, audit, enabled=enabled)
        resumes.extend(starts[before:])
        cold = _read(current, None, audit, enabled=enabled)
        assert _typed(warm) == _typed(cold)

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    expected = first_count - 1 if revision in ('window', 'removed') else 0
    assert resumes == [0, expected]
