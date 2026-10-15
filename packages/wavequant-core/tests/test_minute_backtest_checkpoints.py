"""Minute replay restores only actual previous-day states owned by this request."""

from copy import deepcopy
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta, tzinfo
from io import FileIO
from sys import getsizeof
from threading import Thread
from types import BuiltinMethodType, CellType, FunctionType
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.strategies.backtest_history_reuse import (
    BacktestHistoryReuse, _CheckpointChannel, _CheckpointStream, _MinuteCheckpointStore,
    _chart_audit, _exact_value, _past_attack_digest,
)
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import chart_entry_history
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.interfaces.research_tools.tdx_backtest import encode_research

from .test_lecture_strategy import history


def _strategy(option: bool = False) -> SystemStrategy:
    return SystemStrategy(
        pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', combined_a_entry_enabled=True,
        secondary_pullback_entry_enabled=True, shallow_base_breakout_enabled=True,
        n_target_trend_confirmation_enabled=option,
    )


@pytest.mark.parametrize('option', (False, True))
def test_minute_generation_uses_actual_prior_states_and_matches_every_cold_field(option: bool) -> None:
    bars = history(18, 40)
    strategy = _strategy(option)
    with BacktestHistoryReuse(bars, strategy) as owner:
        generate_system_signals(bars, strategy, history_reuse=owner)
        cache: dict = {}
        for size in (38, 39):
            partial = [*bars[:size-1], replace(bars[size-1], close=bars[size-1].open,
                                            volume=bars[size-1].volume / 2)]
            cold = generate_system_signals(partial, strategy)
            warm = generate_system_signals(partial, strategy, chart_history_cache=cache,
                                           _minute_history_reuse=owner)
            assert _exact_value(encode_research(partial, warm)) == _exact_value(encode_research(partial, cold))
        # The second hierarchy call can also advance the prior minute cache by
        # one completed day; either way it never replays the whole series.
        assert owner._minute.hits == {'chart': 2, 'hierarchy': 1}
        channels = list(owner._minute.channels.values())
        assert 0 < owner._minute.disk_used <= owner._minute.disk_bytes
    assert owner._minute.closed and owner._minute.disk_used == 0
    assert all(channel.closed and channel.journal.file.closed for channel in channels)


@pytest.mark.parametrize('change', ('past_price', 'past_factor', 'past_permission', 'past_type',
                                   'current_open', 'current_factor', 'current_permission',
                                   'current_symbol', 'current_date', 'strategy', 'n_mode'))
def test_changed_source_or_strategy_cannot_authorize_a_minute_seed(change: str) -> None:
    bars = history(18, 20)
    strategy = _strategy()
    partial = list(bars[:-1])
    selected = strategy
    if change == 'past_price':
        partial[0] = replace(partial[0], high=partial[0].high + .01)
    elif change == 'past_factor':
        partial[0] = replace(partial[0], adjustment_factor=2)
    elif change == 'past_permission':
        partial[0] = replace(partial[0], sellable=False)
    elif change == 'past_type':
        partial[0] = replace(partial[0], volume=float(partial[0].volume))
    elif change == 'current_open':
        partial[-1] = replace(partial[-1], open=partial[-1].open + .01)
    elif change == 'current_factor':
        partial[-1] = replace(partial[-1], adjustment_factor=2)
    elif change == 'current_permission':
        partial[-1] = replace(partial[-1], close_buyable=False)
    elif change == 'current_symbol':
        partial[-1] = replace(partial[-1], symbol='OTHER')
    elif change == 'current_date':
        partial[-1] = replace(partial[-1], timestamp=bars[-1].timestamp)
    elif change == 'strategy':
        selected = replace(strategy, volume_filter=not strategy.volume_filter)
    else:
        selected = replace(strategy, n_target_trend_confirmation_enabled=True)
    with BacktestHistoryReuse(bars, strategy) as owner:
        assert not owner.minute_input_matches(partial, selected)


@pytest.mark.parametrize('change', ('proof', 'type', 'last_tie', 'sink_flags'))
def test_chart_checkpoint_requires_full_past_attack_proof_and_exact_sink_flags(change: str) -> None:
    bars = history(18, 20)
    strategy = _strategy()
    audit = [dict(event='n_completed', direction='up', bar_index=6, known_at=6,
                  origin=2, pullback=5, defense=8.)]
    with BacktestHistoryReuse(bars, strategy) as owner:
        chart_entry_history(bars, audit=audit, _history_reuse=owner)
        options = (False, False, False, False)
        changed = deepcopy(audit)
        if change == 'proof':
            changed[0]['origin'] = 1
        elif change == 'type':
            changed[0]['defense'] = 8
        elif change == 'last_tie':
            changed.append(dict(changed[0], origin=1))
        else:
            options = (False, True, False, False)
        version = owner._minute.channels['chart'].version
        assert owner._chart_seed(bars[:-1], audit=changed, options=options, version=version) is None
        assert owner._chart_seed(bars[:-1], audit=audit,
                                 options=(False, False, False, False), version=version) is not None
        assert owner._chart_seed(bars[:-1], audit=audit,
                                 options=(False, False, False, False), version='wrong') is None


def test_seed_reads_are_isolated_and_cross_request_or_thread_is_a_miss() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    with BacktestHistoryReuse(bars, strategy) as owner, BacktestHistoryReuse(bars, strategy) as other:
        hierarchical_history(bars, decline_sink={}, _checkpoint_reuse=owner)
        options = (False, True, False)
        version = owner._minute.channels['hierarchy'].version
        first = owner._hierarchy_seed(bars[:-1], options=options, version=version)
        assert first is not None
        original = deepcopy(first)
        first['checkpoint'][0].clear()
        first['checkpoint'][2].clear()
        assert owner._hierarchy_seed(bars[:-1], options=options, version=version) == original
        assert owner._hierarchy_seed(bars[:-1], options=options, version='wrong') is None
        assert other._hierarchy_seed(bars[:-1], options=options, version=version) is None
        result: list[object] = []
        thread = Thread(target=lambda: result.append(owner._hierarchy_seed(bars[:-1], options=options, version=version)))
        thread.start()
        thread.join()
        assert result == [None]


def test_capacity_and_unknown_object_hooks_refuse_checkpoint_and_cleanup() -> None:
    class Unknown:
        hooks = 0

        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            self.hooks += 1
            raise AssertionError('checkpoint must reject before object hooks')

        def __getstate__(self) -> Never:
            self.hooks += 1
            raise AssertionError('checkpoint must reject before object hooks')

    unknown = Unknown()
    store = _MinuteCheckpointStore((1,), disk_bytes=256)
    channel = store.begin('chart', (False, False, False, False))
    assert channel is not None
    channel.state(0, 1, {'unknown': unknown})
    assert unknown.hooks == 0 and not channel.sealed
    assert store.disk_used <= store.disk_bytes
    channel.day(0, 0, b'x' * 1024)
    assert channel.closed and store.disk_used == 0
    store.close()
    assert channel.journal.file.closed


def test_context_owner_closes_checkpoint_streams_after_consumer_exception() -> None:
    bars = history(18, 20)
    owner = BacktestHistoryReuse(bars, _strategy())
    with pytest.raises(RuntimeError, match='consumer failed'):
        with owner:
            hierarchical_history(bars, decline_sink={}, _checkpoint_reuse=owner)
            raise RuntimeError('consumer failed')
    assert owner._minute.closed and owner._minute.disk_used == 0


@pytest.mark.parametrize('option', (False, True))
def test_indexed_hierarchy_preserves_every_geometry_permission_and_decline_field(
    option: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = history(18, 80)
    declines: dict = {}
    structure: dict = {}
    indexed = hierarchical_history(bars, decline_sink=declines, structure_sink=structure,
                                   n_target_trend_confirmation_enabled=option)
    fresh_declines: dict = {}
    fresh_structure: dict = {}
    with monkeypatch.context() as patch:
        patch.setattr(_MarketRangeIndex, 'build', lambda bars: None)
        unindexed = hierarchical_history(bars, decline_sink=fresh_declines, structure_sink=fresh_structure,
                                         n_target_trend_confirmation_enabled=option)
    assert _exact_value(indexed) == _exact_value(unindexed)
    assert _exact_value(structure) == _exact_value(fresh_structure)
    assert declines == fresh_declines


@pytest.mark.parametrize('option', (False, True))
@pytest.mark.parametrize('transfer', ('candidates', 'source', 'both'))
def test_hierarchy_owned_evidence_matches_original_copying_consumer_fields(
    option: bool, transfer: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wavequant.domain.strategies import hierarchical_entry

    bars = history(18, 80)
    frames: list[object] = []
    original_write = hierarchical_entry.TrendStageRows.write

    def record(self: object, value: object) -> object:
        frame = cast(hierarchical_entry._TrendHistoryFrame, value)
        frames.append((frame.index, frame.epoch, deepcopy(frame.source), deepcopy(frame.qualified_source)))
        return cast(Callable[..., object], original_write)(self, value)

    monkeypatch.setattr(hierarchical_entry.TrendStageRows, 'write', record)
    declines: dict = {}
    structure: dict = {}
    borrowed = hierarchical_history(bars, decline_sink=declines, structure_sink=structure,
                                    n_target_trend_confirmation_enabled=option)
    borrowed_frames = _exact_value(frames)
    frames.clear()
    original = hierarchical_entry.qualify_trend_structure
    original_judge = hierarchical_entry._TrendJudgementCache.judge

    def copying(*args: object, **kwargs: object) -> object:
        kwargs['_owned_candidates'] = False
        return cast(Callable[..., object], original)(*args, **kwargs)

    def copying_source(self: object, *args: object, **kwargs: object) -> object:
        kwargs['_owned_source'] = False
        return cast(Callable[..., object], original_judge)(self, *args, **kwargs)

    if transfer in ('candidates', 'both'):
        monkeypatch.setattr(hierarchical_entry, 'qualify_trend_structure', copying)
    if transfer in ('source', 'both'):
        monkeypatch.setattr(hierarchical_entry._TrendJudgementCache, 'judge', copying_source)
    copied_declines: dict = {}
    copied_structure: dict = {}
    copied = hierarchical_history(bars, decline_sink=copied_declines, structure_sink=copied_structure,
                                  n_target_trend_confirmation_enabled=option)
    assert _exact_value(borrowed) == _exact_value(copied)
    assert _exact_value(structure) == _exact_value(copied_structure)
    assert declines == copied_declines
    assert borrowed_frames == _exact_value(frames)


def test_index_refusal_does_not_access_unknown_future_bar_before_lecture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wavequant.domain.strategies import hierarchical_entry

    class FutureBar(Bar):
        hooks = 0

        def __getattribute__(self, name: str) -> object:
            if name not in ('hooks', '__dict__'):
                type(self).hooks += 1
            return super().__getattribute__(name)

    bars = history(18, 10)
    bars[-1] = FutureBar(**bars[-1].__dict__)

    def lecture(*args: object, **kwargs: object) -> None:
        assert FutureBar.hooks == 0
        raise RuntimeError('lecture reached before unknown future attributes')

    monkeypatch.setattr(hierarchical_entry, 'lecture_drawing', lecture)
    with pytest.raises(RuntimeError, match='lecture reached'):
        hierarchical_history(bars)
    assert FutureBar.hooks == 0


def test_unknown_audit_mapping_refuses_before_its_get_hook() -> None:
    class Audit(dict):
        hooks = 0

        def get(self, key: object, default: object = None) -> object:
            self.hooks += 1
            raise AssertionError('extra audit hook')

    row = Audit(event='n_completed', direction='up', bar_index=6)
    assert _past_attack_digest([row], 9) is None
    assert _chart_audit([row]) is None
    assert row.hooks == 0


def test_metadata_capacity_refuses_before_opening_a_channel() -> None:
    store = _MinuteCheckpointStore(tuple(range(16)), memory_bytes=1024)
    assert store.begin('hierarchy', (False, True, False)) is None
    assert store.channels == {} and store.disk_used == 0
    store.close()


def test_unknown_timezone_refuses_minute_source_without_offset_hooks() -> None:
    class UnknownTimezone(tzinfo):
        hooks = 0

        def utcoffset(self, stamp: object) -> timedelta:
            self.hooks += 1
            raise AssertionError('extra timezone hook')

        def dst(self, stamp: object) -> timedelta:
            self.hooks += 1
            raise AssertionError('extra timezone hook')

        def tzname(self, stamp: object) -> str:
            self.hooks += 1
            raise AssertionError('extra timezone hook')

    bars = history(18, 20)
    partial = list(bars[:-1])
    zone = UnknownTimezone()
    partial[-1] = replace(partial[-1], timestamp=partial[-1].timestamp.replace(tzinfo=zone))
    with BacktestHistoryReuse(bars, _strategy()) as owner:
        assert not owner.minute_input_matches(partial, _strategy())
    assert zone.hooks == 0


def test_actual_combined_context_nested_high_round_trips_through_owned_stream() -> None:
    from .test_combined_a_entry import setup

    _, _, context = setup()
    store = _MinuteCheckpointStore((1,))
    channel = store.begin('chart', (False, False, False, False))
    assert channel is not None
    channel.day(0, 1, (context,))
    channel.state(0, 1, (context,))
    restored = channel.read(1)
    assert restored == ([(0, 1, (context,))], {0: (context,)})
    store.close()
    assert store.disk_used == 0


def test_extended_trusted_checkpoint_value_refuses_without_unknown_reduce_hooks() -> None:
    from .test_combined_a_entry import setup

    class Unknown:
        hooks = 0

        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            type(self).hooks += 1
            raise AssertionError('unknown checkpoint metadata must not be serialized')

    _, _, context = setup()
    object.__setattr__(context, 'unknown_metadata', Unknown())
    store = _MinuteCheckpointStore((1,))
    channel = store.begin('chart', (False, False, False, False))
    assert channel is not None
    channel.day(0, 1, (context,))
    assert channel.closed and Unknown.hooks == 0
    assert store.disk_used == 0
    store.close()


def test_full_checkpoint_metadata_graph_stays_below_declared_ram_allowance() -> None:
    store = _MinuteCheckpointStore(tuple(range(16)))
    hierarchy = store.begin('hierarchy', (False, True, True), 'v119')
    chart = store.begin('chart', (False, True, True, True), 'v119')
    assert hierarchy is not None and chart is not None
    payload = bytes(65536)
    for index in range(16):
        hierarchy.day(0, index, payload)
        chart.day(0, index, payload)
        for level in range(4):
            hierarchy.state(level, index, payload)
        chart.state(0, index, payload, bytes(32))
    assert hierarchy.sealed == chart.sealed == set(range(16))
    # Walk the objects permanently owned by the store, including each instance
    # dict, map/set capacity and unbuffered FileIO object, counting cycles once.
    seen: set[int] = set()
    pending: list[object] = [store]
    file_types = (type(chart.journal.file), type(getattr(chart.journal.file, '_closer', None)))
    actual = 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        actual += getsizeof(value)
        if type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) in (tuple, list, set, frozenset):
            assert isinstance(value, (tuple, list, set, frozenset))
            pending.extend(value)
        elif type(value) in (_MinuteCheckpointStore, _CheckpointChannel, _CheckpointStream):
            pending.append(vars(value))
        elif type(value) in file_types or type(value) is FileIO:
            # Windows TemporaryFile owns a wrapper, closer and cached method
            # closures in addition to the raw unbuffered FileIO handle.
            pending.append(vars(value))
        elif type(value) is FunctionType:
            assert isinstance(value, FunctionType)
            pending.extend((vars(value), value.__defaults__, value.__kwdefaults__, value.__closure__))
        elif type(value) is CellType:
            assert isinstance(value, CellType)
            pending.append(value.cell_contents)
        elif type(value) is BuiltinMethodType:
            assert isinstance(value, BuiltinMethodType)
            pending.append(value.__self__)
    conservative = 16 * 1024 + 2 * 16 * 1024 + 16 * 5 * 8192
    assert 0 < actual < conservative <= store.memory_bytes
    assert store.disk_used > actual
    store.close()
    assert store.disk_used == 0
