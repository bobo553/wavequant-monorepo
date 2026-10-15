"""Request-local reuse preserves dated evidence and rejects altered certificates."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Signal
from wavequant.domain.market_state.market_regime import MarketRegime
from wavequant.domain.market_structure.squeeze_alternation import squeeze_alternations
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse, ChartHistory
from wavequant.domain.strategies.chart_entry_history import chart_entry_history, merge_squeeze_history
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result

from .test_lecture_strategy import history
from .test_squeeze_alternation import fixture as squeeze_fixture


def _strategy(*, option: bool = False) -> SystemStrategy:
    return SystemStrategy(
        pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', combined_a_entry_enabled=True,
        secondary_pullback_entry_enabled=True, n_target_trend_confirmation_enabled=option,
    )


@pytest.mark.parametrize('option', (False, True))
def test_generation_histories_match_fresh_replay_without_optional_sinks(option: bool) -> None:
    bars = history(18, 80)
    strategy = _strategy(option=option)
    reuse = BacktestHistoryReuse(bars, strategy)
    generated = generate_system_signals(bars, strategy, history_reuse=reuse)
    assert generated == generate_system_signals(bars, strategy)
    cached = reuse.chart(bars, strategy, generated.audit)
    assert cached is not None
    assert cached == chart_entry_history(bars, audit=generated.audit,
        n_target_trend_confirmation_enabled=option)
    levels = reuse.hierarchy(bars, n_target_trend_confirmation_enabled=option)
    assert levels == hierarchical_history(
        bars, n_target_trend_confirmation_enabled=option)
    assert levels is not None and set(levels[0]) == set(range(len(bars)))
    for known, frame in levels[0].items():
        assert all(point['index'] <= point['available_at'] <= known
                   for points in frame.values() for point in points)
    pivots = reuse.pivot(bars, strategy)
    assert pivots is not None
    assert all(point.point.index <= point.confirmed_index <= known
               for known, points in pivots[0].items() for point in points)


def _audit() -> list[dict[str, object]]:
    return [
        dict(event='n_completed', direction='up', bar_index=6, known_at=6, origin=2, pullback=5, defense=8),
        dict(event='regime_confirmation', bar_index=9, attack=6, regime='轧空'),
        dict(event='squeeze_resumption_observed', bar_index=12, attack=6, defense=8),
    ]


@pytest.mark.parametrize('field,value', [('origin', 1), ('known_at', 7), ('defense', 7),
                                       ('pullback', 4), ('outside_close_confirmed', True)])
def test_same_attack_with_changed_full_n_evidence_is_a_miss(field: str, value: object) -> None:
    bars = history(18, 20)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    audit = _audit()
    reuse.save_chart(bars, strategy, audit, ({i: () for i in range(len(bars))}, []))
    changed = deepcopy(audit)
    changed[0][field] = value
    assert reuse.chart(bars, strategy, changed) is None
    assert reuse.chart(bars, strategy, audit) is not None


def test_audit_certificate_preserves_trigger_ties_and_last_n_per_attack() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    audit = _audit()
    audit.append(dict(event='n_consolidation_gap_confirmed', bar_index=9, attack=6, defense=8))
    stale = dict(audit[0], origin=1)
    audit.insert(0, stale)
    value: ChartHistory = ({i: () for i in range(len(bars))}, [])
    reuse.save_chart(bars, strategy, audit, value)
    # Global date sorting and unrelated late diagnostics do not change consumption.
    reordered = sorted(audit, key=lambda row: cast(int, row['bar_index']))
    reordered.append(dict(event='long_signal', bar_index=15))
    assert reuse.chart(bars, strategy, reordered) == value
    changed_last = [row for row in audit if row is not stale]
    changed_last.insert(2, stale)
    assert reuse.chart(bars, strategy, changed_last) is None
    swapped = [*audit[:-1]]
    swapped.insert(2, audit[-1])
    assert reuse.chart(bars, strategy, swapped) is None


def test_late_non_squeeze_confirmation_is_ignored_but_bull_evidence_misses() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    audit = _audit()
    value: ChartHistory = ({i: () for i in range(len(bars))}, [])
    reuse.save_chart(bars, strategy, audit, value)
    later = dict(event='regime_confirmation', regime=MarketRegime.GRIND_UP.value, bar_index=14,
                 timestamp=bars[14].timestamp.isoformat(), symbol=bars[14].symbol, attack=6)
    assert reuse.chart(bars, strategy, [*audit, later]) == value
    assert reuse.chart(bars, strategy, [*audit, dict(later, regime=MarketRegime.BULL.value)]) is None


@pytest.mark.parametrize('change', ('prefix', 'price', 'factor', 'permission', 'symbol', 'strategy', 'mode'))
def test_reuse_rejects_changed_full_input(change: str) -> None:
    bars = history(18, 20)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    changed = list(bars)
    selected = strategy
    if change == 'prefix':
        changed.pop()
    elif change == 'price':
        changed[-1] = replace(changed[-1], close=changed[-1].close + .01)
    elif change == 'factor':
        changed[-1] = replace(changed[-1], adjustment_factor=2)
    elif change == 'permission':
        changed[-1] = replace(changed[-1], close_buyable=False)
    elif change == 'symbol':
        changed[-1] = replace(changed[-1], symbol='OTHER')
    elif change == 'strategy':
        selected = replace(strategy, volume_filter=not strategy.volume_filter)
    else:
        selected = replace(strategy, n_target_trend_confirmation_enabled=True)
    with pytest.raises(ValueError, match='same complete'):
        reuse.require_input(changed, selected)
    if change == 'mode':
        with pytest.raises(ValueError, match='same trend confirmation mode'):
            reuse.hierarchy(bars, n_target_trend_confirmation_enabled=True)


def test_returned_containers_and_original_audit_cannot_mutate_stored_evidence() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    audit = _audit()
    original = deepcopy(audit)
    events: list[dict[str, object]] = [dict(event='test', nested={'value': 1})]
    value: ChartHistory = ({i: () for i in range(len(bars))}, events)
    expected = deepcopy(value)
    reuse.save_chart(bars, strategy, audit, value)
    audit[0]['origin'] = 1
    events[0]['nested'] = {'value': 2}
    detached = reuse.chart(bars, strategy, original)
    assert detached == expected
    assert detached is not None
    detached[0].clear()
    cast(dict[str, object], detached[1][0]['nested'])['value'] = 3
    assert reuse.chart(bars, strategy, original) == expected
    levels = hierarchical_history(bars)
    reuse.save_hierarchy(bars, strategy, levels)
    first = reuse.hierarchy(bars, n_target_trend_confirmation_enabled=False)
    assert first is not None
    first[0].clear()
    first[1].clear()
    assert reuse.hierarchy(bars, n_target_trend_confirmation_enabled=False) == levels


def test_same_instant_with_different_local_knowledge_date_cannot_reuse() -> None:
    bars = history(18, 20)
    bars[0] = replace(bars[0], timestamp=datetime(2020, 1, 1, 0, 30, tzinfo=timezone.utc))
    changed = list(bars)
    changed[0] = replace(bars[0], timestamp=bars[0].timestamp.astimezone(timezone(timedelta(hours=-1))))
    assert bars[0].timestamp == changed[0].timestamp
    assert bars[0].timestamp.date() != changed[0].timestamp.date()
    reuse = BacktestHistoryReuse(bars, _strategy())
    with pytest.raises(ValueError, match='same complete bars'):
        reuse.require_input(changed, _strategy())


@pytest.mark.parametrize('change', ('bar_type', 'strategy_type', 'mode_type', 'audit_type'))
def test_equal_numbers_with_different_types_do_not_share_evidence(change: str) -> None:
    bars = history(18, 20)
    strategy = _strategy(option=True)
    reuse = BacktestHistoryReuse(bars, strategy)
    if change == 'mode_type':
        with pytest.raises(ValueError, match='same trend confirmation mode'):
            reuse.hierarchy(bars, n_target_trend_confirmation_enabled=cast(bool, 1))
    elif change == 'strategy_type':
        changed_strategy = replace(strategy, n_target_trend_confirmation_enabled=cast(bool, 1))
        with pytest.raises(ValueError, match='same complete strategy'):
            reuse.require_input(bars, changed_strategy)
    elif change == 'bar_type':
        changed_bars = list(bars)
        changed_bars[0] = replace(bars[0], open=float(bars[0].open))
        assert changed_bars == bars
        with pytest.raises(ValueError, match='same complete bars'):
            reuse.require_input(changed_bars, strategy)
    else:
        audit = _audit()
        reuse.save_chart(bars, strategy, audit, ({i: () for i in range(len(bars))}, []))
        audit[0]['defense'] = 8.0
        assert reuse.chart(bars, strategy, audit) is None


@pytest.mark.parametrize('revised_audit', (False, True))
def test_simulation_reuses_generation_without_replaying_and_preserves_nonempty_ledger(
    monkeypatch: pytest.MonkeyPatch, revised_audit: bool,
) -> None:
    bars = history(18, 80)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    generated = generate_system_signals(bars, strategy, history_reuse=reuse)
    # A last-day observation requires the full wave context but no minute replay.
    ready = dict(event='wave_continuation_ready', bar_index=len(bars)-1,
                 timestamp=bars[-1].timestamp.isoformat(), symbol=bars[-1].symbol,
                 origin_index=1, attack_index=4, squeeze_index=10,
                 origin=6.0, box_anchor=8.0, two_t=12.0, defense=6.5)
    bar = bars[30]
    entry = Signal(bar.timestamp, bar.symbol, 30, 'LONG', bar.close, bar.close * .9,
                   'reuse_regression', bars[20].timestamp, 0, 2, '轧空', bar.close * 1.5)
    generated = SystemResult([*generated.signals, entry], [*generated.audit, ready], generated.counts)
    if revised_audit:
        positive = next(row for row in generated.audit
                        if row['event'] == 'n_completed' and row.get('direction') == 'up')
        generated.audit.append(dict(event='regime_confirmation', bar_index=len(bars)-2,
            timestamp=bars[-2].timestamp.isoformat(), symbol=bars[-2].symbol,
            regime=MarketRegime.BULL.value, attack=positive['bar_index'],
            confirmation_source='multilevel_record_break'))
        assert reuse.chart(bars, strategy, generated.audit) is None
    execution = StrategyConfig(consolidation_entry_intraday=True, staged_exit_intraday=False,
                               trend_flip_adverse_exit=True, net_reward_risk_filter=False, max_participation=1)
    expected = single_stock_result(bars, asdict(strategy), execution.to_dict(), generated)
    assert any(order['side'] == 'BUY' and order['status'] == 'filled' for order in expected['orders'])
    empty = BacktestHistoryReuse(bars, strategy)
    assert single_stock_result(bars, asdict(strategy), execution.to_dict(), generated, history_reuse=empty) == expected

    def fail_replay(*args: object, **kwargs: object) -> object:
        raise AssertionError('a complete certified history must not be recomputed')

    monkeypatch.setattr('wavequant.application.analytics.intraday_entry.pivot_history', fail_replay)
    monkeypatch.setattr('wavequant.domain.strategies.chart_entry_history.chart_entry_history', fail_replay)
    monkeypatch.setattr('wavequant.domain.strategies.hierarchical_entry.hierarchical_history', fail_replay)
    actual = single_stock_result(bars, asdict(strategy), execution.to_dict(), generated, history_reuse=reuse)
    assert actual == expected


@pytest.mark.parametrize('failure', (False, True))
def test_revised_squeeze_rebuilds_confirmation_breakout_and_failure_from_original_base(failure: bool) -> None:
    bars, audit, anchors = squeeze_fixture()
    if failure:
        bars[10] = replace(bars[10], low=6, close=12)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    base_history = {i: () for i in range(len(bars))}
    base_events: list[dict[str, object]] = [dict(event='dated_geometry', bar_index=0, proof={'value': 4})]
    base_expected = deepcopy(base_events)
    reuse.capture_chart_base(bars, n_target_trend_confirmation_enabled=False,
        audit=audit[:1], history=base_history, events=base_events, anchors_at_attack=anchors)
    # The producer's subsequent mutations and callback container reuse are isolated.
    base_history.clear()
    base_events[0]['proof'] = {'value': 100}
    original = {i: () for i in range(len(bars))}
    additional = squeeze_alternations(bars, audit, anchors)
    expected = merge_squeeze_history(bars, original, additional), [*base_expected, *additional]
    refreshed = reuse.refresh_chart(bars, strategy, audit)
    assert refreshed == expected
    assert refreshed is not None and refreshed[0][8] == () and refreshed[0][9]
    assert refreshed[0][9][0].alternation_index == 9
    assert [row['event'] for row in refreshed[1][1:]] == [
        'squeeze_alternation_confirmed',
        'squeeze_alternation_invalidated' if failure else 'squeeze_alternation_breakout',
    ]
    if failure:
        assert refreshed[0][10] == ()
    else:
        assert refreshed[0][10][0].maturity_index == 10
    refreshed[0].clear()
    cast(dict[str, object], refreshed[1][0]['proof'])['value'] = 1000
    assert reuse.refresh_chart(bars, strategy, audit) == expected
    # Removing the trigger must remove every old confirmation and failure.
    withdrawn = reuse.refresh_chart(bars, strategy, audit[:1])
    assert withdrawn == (original, base_expected)
    # Reconfirmation can be replaced, never layered on the prior merged context.
    delayed = [audit[0], dict(audit[1], bar_index=10)]
    later = reuse.refresh_chart(bars, strategy, delayed)
    delayed_additional = squeeze_alternations(bars, delayed, anchors)
    assert later == (merge_squeeze_history(bars, original, delayed_additional),
                     [*base_expected, *delayed_additional])
    assert later is not None and later[0][9] == ()
    assert reuse.chart(bars, strategy, delayed) == later


@pytest.mark.parametrize('change', ('new_attack', 'removed_attack', 'origin', 'known_at', 'numeric_type'))
def test_base_refresh_rejects_changed_complete_attack_evidence(change: str) -> None:
    bars, audit, anchors = squeeze_fixture()
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    reuse.capture_chart_base(bars, n_target_trend_confirmation_enabled=False,
        audit=audit, history={i: () for i in range(len(bars))}, events=[], anchors_at_attack=anchors)
    changed = deepcopy(audit)
    if change == 'new_attack':
        changed.append(dict(audit[0], bar_index=9))
    elif change == 'removed_attack':
        changed.pop(0)
    elif change == 'numeric_type':
        changed[0]['known_at'] = 8.0
    else:
        changed[0][change] = 6
    assert reuse.refresh_chart(bars, strategy, changed) is None
    assert reuse.refresh_chart(bars, strategy, audit) is not None


def test_real_chart_capture_refresh_matches_cold_after_added_bull_trigger() -> None:
    bars = history(18, 80)
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    generated = generate_system_signals(bars, strategy, history_reuse=reuse)
    positive = next(row for row in generated.audit if row['event'] == 'n_completed' and row.get('direction') == 'up')
    revised = [*generated.audit, dict(event='regime_confirmation', bar_index=len(bars)-1,
        timestamp=bars[-1].timestamp.isoformat(), symbol=bars[-1].symbol,
        regime=MarketRegime.BULL.value, attack=positive['bar_index'], confirmation_source='multilevel_record_break')]
    assert reuse.chart(bars, strategy, revised) is None
    refreshed = reuse.refresh_chart(bars, strategy, revised)
    assert refreshed is not None
    assert refreshed == chart_entry_history(bars, audit=revised)
    assert reuse.refresh_chart(bars, strategy, generated.audit) == chart_entry_history(bars, audit=generated.audit)


def test_unknown_mutable_audit_metadata_takes_uncached_path_without_object_hooks() -> None:
    calls: list[str] = []

    class MutableMetadata:
        value = 1

        def __eq__(self, other: object) -> bool:
            calls.append('eq')
            raise AssertionError('unknown metadata must not participate in equality')

        def __hash__(self) -> int:
            calls.append('hash')
            raise AssertionError('unknown metadata must not be hashed')

        def __reduce__(self) -> Never:
            calls.append('reduce')
            raise AssertionError('unknown metadata must not be serialized')

        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            calls.append('reduce_ex')
            raise AssertionError('unknown metadata must not be serialized')

    bars, audit, anchors = squeeze_fixture()
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    metadata = MutableMetadata()
    audit[0]['provider_metadata'] = {'nested': [metadata]}
    value: ChartHistory = ({i: () for i in range(len(bars))}, [])
    reuse.save_chart(bars, strategy, audit, value)
    reuse.capture_chart_base(bars, n_target_trend_confirmation_enabled=False,
        audit=audit, history=value[0], events=value[1], anchors_at_attack=anchors)
    metadata.value = 2
    assert reuse.chart(bars, strategy, audit) is None
    assert reuse.refresh_chart(bars, strategy, audit) is None
    assert calls == []
    # The original entry reducer ignores unknown provider metadata normally.
    assert chart_entry_history(bars, audit=audit, _history_reuse=reuse) == chart_entry_history(
        bars, audit=[{key: item for key, item in row.items() if key != 'provider_metadata'} for row in audit])
    assert calls == []


def test_nested_builtin_sets_have_stable_isolated_audit_certificates() -> None:
    bars, audit, _ = squeeze_fixture()
    strategy = _strategy()
    reuse = BacktestHistoryReuse(bars, strategy)
    tags = {('provider', 1)}
    audit[0]['provider_metadata'] = {'tags': tags, 'frozen': frozenset({'stable'})}
    value: ChartHistory = ({i: () for i in range(len(bars))}, [])
    reuse.save_chart(bars, strategy, audit, value)
    assert reuse.chart(bars, strategy, deepcopy(audit)) == value
    tags.add(('revision', 2))
    assert reuse.chart(bars, strategy, audit) is None
