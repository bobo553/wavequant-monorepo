"""Private chart stages transfer read-only evidence without changing public ownership."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import fields, is_dataclass
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from pickle import Pickler
from typing import SupportsIndex, cast

import pytest

import wavequant.domain.strategies.chart_entry_history as chart_module
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_staging import TrendStageRows
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse, _exact_value
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.combined_a_entry import CombinedAContext, CombinedAHigh
from wavequant.domain.strategies.secondary_pullback_entry import SecondaryPullbackCandidate
from wavequant.domain.strategies.shallow_base_breakout import ShallowAlternationCandidate

from .test_staged_trend_pipeline import _multiple_path_bars


class _HookEvidence:
    copied = 0
    reduced = 0

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __deepcopy__(self, memo: dict[int, object]) -> '_HookEvidence':
        type(self).copied += 1
        return _HookEvidence(self.marker)

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[Callable[..., object], tuple[str]]:
        type(self).reduced += 1
        return _HookEvidence, (self.marker,)


def _snapshot_record(name: str, values: tuple[tuple[str, object], ...]) -> object:
    return name, values


def _snapshot_probe(marker: str) -> object:
    return marker


class _SnapshotPickler(Pickler):
    def reducer_override(self, value: object) -> tuple[Callable[..., object], tuple[object, ...]]:
        if value is _snapshot_record or value is _snapshot_probe:
            return cast(tuple[Callable[..., object], tuple[object, ...]], NotImplemented)
        if type(value) is _HookEvidence:
            return _snapshot_probe, (value.marker,)
        if type(value) in (EntryContext, CombinedAContext, CombinedAHigh,
                           SecondaryPullbackCandidate, ShallowAlternationCandidate):
            record = cast(EntryContext, value)
            assert vars(value).keys() == {field.name for field in fields(record)}
            return _snapshot_record, (type(value).__qualname__,
                tuple((field.name, getattr(value, field.name)) for field in fields(record)))
        raise AssertionError('a chart snapshot must use known records or exact builtin evidence')


def _typed(value: object) -> bytes:
    # Each input is an acyclic owned evidence tree. C pickle without identity
    # memoization compares complete values, types, key order and float bits;
    # intended private aliases are checked separately at the public boundary.
    output = BytesIO()
    snapshot = _SnapshotPickler(output, protocol=5)
    snapshot.fast = True
    snapshot.dump(value)
    return output.getvalue()


def _mutable_aliases(value: object) -> object:
    seen: dict[int, int] = {}

    def visit(child: object) -> object:
        if type(child) in (dict, list, set):
            if id(child) in seen:
                return 'reference', seen[id(child)]
            seen[id(child)] = len(seen)
        if type(child) is dict:
            return dict, tuple((visit(key), visit(item)) for key, item in cast(dict[object, object], child).items())
        if type(child) in (list, tuple):
            return type(child), tuple(visit(item) for item in cast(Sequence[object], child))
        if is_dataclass(child) and not isinstance(child, type):
            return type(child), tuple((field.name, visit(getattr(child, field.name))) for field in fields(child))
        if type(child) is _HookEvidence:
            return _typed(child)
        return _exact_value(child)

    return visit(value)


def _mutable_objects(value: object) -> list[dict[object, object] | list[object]]:
    result: list[dict[object, object] | list[object]] = []
    seen: set[int] = set()

    def visit(child: object) -> None:
        if id(child) in seen:
            return
        seen.add(id(child))
        if type(child) is dict:
            mapping = cast(dict[object, object], child)
            result.append(mapping)
            for item in mapping.values():
                visit(item)
        elif type(child) is list:
            sequence = cast(list[object], child)
            result.append(sequence)
            for item in sequence:
                visit(item)
        elif type(child) is tuple:
            for item in cast(tuple[object, ...], child):
                visit(item)
        elif is_dataclass(child) and not isinstance(child, type):
            for field in fields(child):
                visit(getattr(child, field.name))

    visit(value)
    return result


def _strategy(option: bool = False) -> SystemStrategy:
    # The independent N producer does not itself need the chart consumer.
    return SystemStrategy(pivot_mode='lecture_causal', shallow_base_breakout_enabled=False,
        strict_n_attack_quality=False, preflight_reward_risk=False, volume_filter=False,
        n_target_trend_confirmation_enabled=option)


@pytest.fixture(scope='module')
def paths() -> tuple[list[Bar], list[dict[str, object]]]:
    bars = _multiple_path_bars(count=24)
    return bars, generate_system_signals(bars, _strategy()).audit


@pytest.fixture(scope='module')
def real_history() -> tuple[list[Bar], list[dict[str, object]]]:
    data = cast(dict[str, object], json.loads(
        (Path(__file__).parent / 'fixtures/guilin_2022_hierarchy.json').read_text(encoding='utf-8')))
    rows = cast(list[tuple[str, float, float, float, float, float]], data['bars'])
    bars = [Bar(datetime.fromisoformat(day), cast(str, data['symbol']), opening, high, low, close, volume)
            for day, opening, high, low, close, volume in rows]
    return bars, generate_system_signals(bars, _strategy()).audit


def _chart(bars: list[Bar], audit: list[dict[str, object]], option: bool,
           *, owner: BacktestHistoryReuse | None = None) -> tuple[object, ...]:
    shallow: dict = {}
    combined: dict = {}
    pullback: dict = {}
    history, events = chart_module.chart_entry_history(bars, audit=audit,
        shallow_candidate_sink=shallow, combined_candidate_sink=combined, secondary_pullback_sink=pullback,
        n_target_trend_confirmation_enabled=option, _history_reuse=owner)
    assert set(history) == set(shallow) == set(combined) == set(pullback) == set(range(len(bars)))
    return history, events, shallow, combined, pullback


@pytest.mark.parametrize('option', (False, True))
@pytest.mark.parametrize('transfer', ('source', 'candidates', 'both'))
def test_owned_chart_matches_copying_in_every_stage_field_event_and_sidecar(
    option: bool, transfer: str, paths: tuple[list[Bar], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, audit = paths
    stages: list[object] = []
    prepare = chart_module.prepare_trend_lines
    attach = chart_module.attach_trend_permissions
    copying = False

    def record_prepare(source: Sequence[Mapping[str, object]], **kwargs: object) -> dict[str, object]:
        before = _typed(source)
        if copying and transfer in ('source', 'both'):
            kwargs['_owned_source'] = False
        result = cast(Callable[..., dict[str, object]], prepare)(source, **kwargs)
        assert before == _typed(source)
        stages.append(('prepare', kwargs['trend_level'], before, _typed(result)))
        return result

    def record_attach(prepared: Mapping[str, object], market: Sequence[Bar], **kwargs: object) -> dict[str, object]:
        before = _typed(prepared)
        if copying and transfer in ('candidates', 'both'):
            kwargs['_owned_candidates'] = False
        result = cast(Callable[..., dict[str, object]], attach)(prepared, market, **kwargs)
        assert before == _typed(prepared)
        stages.append(('attach', len(market), before, _typed(kwargs.get('qualified_source_points')), _typed(result)))
        return result

    monkeypatch.setattr(chart_module, 'prepare_trend_lines', record_prepare)
    monkeypatch.setattr(chart_module, 'attach_trend_permissions', record_attach)
    owned = _chart(bars, audit, option)
    owned_stages = tuple(stages)
    stages.clear()
    copying = True
    original = _chart(bars, audit, option)
    assert _typed(owned) == _typed(original)
    assert _mutable_aliases(owned) == _mutable_aliases(original)
    assert owned_stages == tuple(stages)


@pytest.mark.parametrize('option', (False, True))
def test_owned_chart_real_nonempty_pullback_and_public_mutable_aliases_match_original(
    option: bool, real_history: tuple[list[Bar], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, audit = real_history
    owned = _chart(bars, audit, option)
    prepare, attach = chart_module.prepare_trend_lines, chart_module.attach_trend_permissions

    def copying_prepare(*args: object, **kwargs: object) -> object:
        kwargs['_owned_source'] = False
        return cast(Callable[..., object], prepare)(*args, **kwargs)

    def copying_attach(*args: object, **kwargs: object) -> object:
        kwargs['_owned_candidates'] = False
        return cast(Callable[..., object], attach)(*args, **kwargs)

    monkeypatch.setattr(chart_module, 'prepare_trend_lines', copying_prepare)
    monkeypatch.setattr(chart_module, 'attach_trend_permissions', copying_attach)
    original = _chart(bars, audit, option)
    assert any(cast(dict[int, tuple[object, ...]], owned[4]).values())
    assert cast(list[dict[str, object]], owned[1])
    assert _typed(owned) == _typed(original)
    assert _mutable_aliases(owned) == _mutable_aliases(original)


def test_owned_closed_path_templates_restore_independent_rows_on_every_read(
    paths: tuple[list[Bar], list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, _ = paths
    restore = chart_module._ClosedPreparedPaths._restore
    restored = 0

    def record_restore(self: chart_module._ClosedPreparedPaths, index: int) -> dict[str, object]:
        nonlocal restored
        restored += 1
        return restore(self, index)

    monkeypatch.setattr(chart_module._ClosedPreparedPaths, '_restore', record_restore)
    # 闭合模板单独给定路径边界，不依赖外包十字星是否重置真实折线。
    source = chart_module.polyline_high_low_points(lecture_drawing(bars))
    source = [dict(point, source_path_end_index=len(bars)) for point in source]
    stages: list[TrendStageRows[dict[str, object]]] = []
    with ExitStack() as resources:
        for level in (1, 2, 3):
            templates = chart_module._ClosedPreparedPaths()
            prepared = chart_module.prepare_trend_lines(source, trend_level=level, _closed_paths=templates)
            stage = resources.enter_context(TrendStageRows(templates.decode))
            stages.append(stage)
            stage.write(templates.encode(prepared))
            first = list(stage.iter_rows())[-1]
            second = list(stage.iter_rows())[-1]
            expected = _typed(second)
            assert not ({id(value) for value in _mutable_objects(first)}
                        & {id(value) for value in _mutable_objects(second)})
            for value in _mutable_objects(first):
                value.clear()
            assert _typed(second) == expected
            assert _typed(list(stage.iter_rows())[-1]) == expected
            source = chart_module.high_low_points_from_strokes(second['strokes'])
    assert restored > 0
    assert all(stage.closed for stage in stages)


@pytest.mark.parametrize('option', (False, True))
def test_owned_public_chart_mutation_cannot_change_stored_getters(
    option: bool, real_history: tuple[list[Bar], list[dict[str, object]]],
) -> None:
    bars, audit = real_history
    strategy = _strategy(option)
    with BacktestHistoryReuse(bars, strategy) as owner:
        public = _chart(bars, audit, option, owner=owner)
        history = cast(dict[int, tuple[EntryContext, ...]], public[0])
        events = cast(list[dict[str, object]], public[1])
        owner.save_chart(bars, strategy, audit, (history, events))
        expected = _typed((history, events))
        for value in _mutable_objects(public):
            value.clear()
        first = owner.chart(bars, strategy, audit)
        assert _typed(first) == expected
        for value in _mutable_objects(first):
            value.clear()
        assert _typed(owner.chart(bars, strategy, audit)) == expected
    assert owner._minute.closed and owner._minute.disk_used == 0


@pytest.mark.parametrize('option', (False, True))
@pytest.mark.parametrize('metadata', ('tuple', 'unknown', 'nan', 'infinite'))
def test_owned_chart_stage_fallback_preserves_full_evidence_and_original_hook_counts(
    option: bool, metadata: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=14)
    source_decoder = chart_module._stage_source
    prepare, attach = chart_module.prepare_trend_lines, chart_module.attach_trend_permissions
    evidence: list[object] = []
    copying = False

    def decode(value: object) -> list[dict[str, object]]:
        result = source_decoder(value)
        for point in result:
            point['transfer_metadata'] = (
                (1, True, -0.) if metadata == 'tuple' else _HookEvidence('unknown') if metadata == 'unknown'
                else float('nan') if metadata == 'nan' else float('inf'))
        return result

    def observe_prepare(*args: object, **kwargs: object) -> dict[str, object]:
        if copying:
            kwargs['_owned_source'] = False
        result = cast(Callable[..., dict[str, object]], prepare)(*args, **kwargs)
        evidence.append(_typed(result))
        return result

    def observe_attach(*args: object, **kwargs: object) -> dict[str, object]:
        if copying:
            kwargs['_owned_candidates'] = False
        result = cast(Callable[..., dict[str, object]], attach)(*args, **kwargs)
        evidence.append(_typed(result))
        return result

    monkeypatch.setattr(chart_module, '_stage_source', decode)
    monkeypatch.setattr(chart_module, 'prepare_trend_lines', observe_prepare)
    monkeypatch.setattr(chart_module, 'attach_trend_permissions', observe_attach)
    _HookEvidence.copied = _HookEvidence.reduced = 0
    owned = _chart(bars, [], option)
    owned_counts = _HookEvidence.copied, _HookEvidence.reduced
    owned_evidence = tuple(evidence)
    evidence.clear()
    _HookEvidence.copied = _HookEvidence.reduced = 0
    copying = True
    original = _chart(bars, [], option)
    assert _typed(owned) == _typed(original)
    assert _mutable_aliases(owned) == _mutable_aliases(original)
    assert owned_evidence == tuple(evidence)
    assert owned_counts == (_HookEvidence.copied, _HookEvidence.reduced)
    if metadata == 'unknown':
        assert min(owned_counts) > 0
