"""逐根校验和请求内窗口证书只省重复工作，不放宽公开行情边界。"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wavequant.domain.market_state import wave_strength
from wavequant.domain.market_structure import bull_alternation_confirmation as alternation
from wavequant.domain.market_structure import n_shape, price_action
from wavequant.domain.market_structure.bull_alternation_confirmation import AlternationPolicy
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, MilestoneBasis, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.market_structure.trend_lifecycle import _Pivot
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars


START = datetime(2024, 1, 1)


def _bars(count: int) -> tuple[Bar, ...]:
    return tuple(Bar(START + timedelta(days=index), 'TEST', 10., 11., 9., 10., 100.)
                 for index in range(count))


def _pending_setup() -> NSetup:
    return NSetup('TEST', '1d', Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 6),
                  'test_confirmed_pivots', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)


@pytest.fixture
def validated_indices(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    seen: list[datetime] = []
    original = price_action._validate_bar

    def counted(bar: Bar) -> None:
        seen.append(bar.timestamp)
        original(bar)

    monkeypatch.setattr(price_action, '_validate_bar', counted)
    monkeypatch.setattr(wave_strength, '_validate_bar', counted)
    monkeypatch.setattr(n_shape, '_validate_bar', counted)
    return seen


def test_each_raw_prefix_bar_is_checked_once_but_public_pair_still_checks_both(
    validated_indices: list[datetime],
) -> None:
    bars = _bars(5)
    wave_strength.validate_prefix(bars, symbol='TEST', end=4)
    assert validated_indices == [bar.timestamp for bar in bars]
    validated_indices.clear()
    observed = n_shape.observe_n(bars, _pending_setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    assert observed.status == n_shape.NStatus.AWAIT_ANCHORS
    assert validated_indices == [bar.timestamp for bar in bars]
    validated_indices.clear()
    price_action.teaching_inside(bars[0], bars[1])
    assert validated_indices == [bars[0].timestamp, bars[1].timestamp]


def test_long_market_windows_only_validate_new_tail_and_connection(
    validated_indices: list[datetime],
) -> None:
    bars = _bars(2145)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    first = owner.validated_window(0, 2125)
    assert isinstance(first, ValidatedBars)
    assert tuple(first) == bars[:2125]
    assert len(validated_indices) == 2125
    validated_indices.clear()
    for stop in range(2126, 2146):
        latest = owner.validated_window(100, stop)
        assert isinstance(latest, ValidatedBars)
        assert tuple(latest) == bars[100:stop]
    # 每次追加只允许新根及其接缝，禁止再扫描 2,125 根历史。
    assert len(validated_indices) <= 40
    validated_indices.clear()
    for start, stop in ((1025, 2100), (0, 2125), (10, 2145)):
        window = owner.validated_window(start, stop)
        assert isinstance(window, ValidatedBars)
        assert tuple(window) == bars[start:stop]
    assert validated_indices == []
    assert tuple(first) == bars[:2125]


@pytest.mark.parametrize('boundary', ('symbol', 'timezone', 'order'))
def test_adjacent_windows_do_not_certify_an_unchecked_connection(boundary: str) -> None:
    bars = list(_bars(4))
    if boundary == 'symbol':
        bars[2:] = [replace(bar, symbol='OTHER') for bar in bars[2:]]
    elif boundary == 'timezone':
        bars[2:] = [replace(bar, timestamp=bar.timestamp.replace(tzinfo=timezone.utc)) for bar in bars[2:]]
    else:
        bars[2:] = [replace(bar, timestamp=bar.timestamp - timedelta(days=3)) for bar in bars[2:]]
    frozen = tuple(bars)
    owner = _MarketRangeIndex.build(frozen)
    assert owner is not None
    assert isinstance(owner.validated_window(0, 2), ValidatedBars)
    assert isinstance(owner.validated_window(2, 4), ValidatedBars)
    assert owner.validated_window(0, 4) is None
    with pytest.raises(ValueError):
        wave_strength.validate_prefix(frozen, symbol='TEST', end=3)
    assert isinstance(owner.validated_window(2, 4), ValidatedBars)


def test_certification_does_not_read_bad_bars_outside_requested_window(
    validated_indices: list[datetime],
) -> None:
    bars = list(_bars(10))
    bars[0] = replace(bars[0], open=float('nan'))
    bars[8] = replace(bars[8], open=float('nan'))
    owner = _MarketRangeIndex.build(tuple(bars))
    assert owner is not None
    window = owner.validated_window(2, 7)
    assert isinstance(window, ValidatedBars)
    assert tuple(window) == tuple(bars[2:7])
    assert validated_indices == [bar.timestamp for bar in bars[2:7]]
    assert owner.validated_window(2, 9) is None
    with pytest.raises(ValueError, match='open must be finite and positive'):
        wave_strength.validate_prefix(bars[2:9], symbol='TEST', end=6)
    assert isinstance(owner.validated_window(2, 7), ValidatedBars)
    assert owner.validated_window(0, 7) is None
    assert isinstance(owner.validated_window(9, 10), ValidatedBars)


def test_public_mutable_sequence_is_revalidated_after_replacement() -> None:
    bars = list(_bars(5))
    n_shape.observe_n(bars, _pending_setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    bars[1] = replace(bars[1], open=float('nan'))
    with pytest.raises(ValueError, match='open must be finite and positive'):
        n_shape.observe_n(bars, _pending_setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    with pytest.raises(ValueError, match='open must be finite and positive'):
        price_action.teaching_inside(bars[0], bars[1])


def test_window_certificates_belong_to_their_market_owner() -> None:
    bars = _bars(6)
    first = _MarketRangeIndex.build(bars)
    revised = _MarketRangeIndex.build((*bars[:3], replace(bars[3], open=float('nan')), *bars[4:]))
    assert first is not None and revised is not None
    original = first.validated_window(0, 6)
    assert isinstance(original, ValidatedBars)
    assert revised.validated_window(0, 6) is None
    assert tuple(original) == bars
    assert isinstance(first.validated_window(0, 6), ValidatedBars)


def test_bull_alternation_passes_certified_windows_to_unchanged_n_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [(10., 13., 9., 10.), (10., 11., 8., 10.), (10., 12., 9., 10.),
            (10., 10.2, 9., 10.), (10., 11., 9.8, 10.), (10., 10.3, 9.5, 10.),
            (10., 10.8, 9.6, 10.), (10., 10.9, 9.7, 10.)]
    bars = tuple(Bar(START + timedelta(days=index), 'TEST', *prices, 100.)
                 for index, prices in enumerate(rows))
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    broken, origin, high = _Pivot(0, 0, 'H', 13., 0), _Pivot(1, 0, 'L', 8., 1), _Pivot(2, 0, 'H', 12., 2)
    polyline = (_Pivot(3, 0, 'L', 9., 3), _Pivot(4, 0, 'H', 11., 4), _Pivot(5, 0, 'L', 9.5, 5))
    seen: list[bool] = []
    original = alternation.observe_n

    def observed(
        data: Sequence[Bar], setup: NSetup, *, timeframe: str,
        milestone_basis: MilestoneBasis, measure_targets: bool,
    ) -> n_shape.NObservation:
        seen.append(isinstance(data, ValidatedBars))
        return original(data, setup, timeframe=timeframe, milestone_basis=milestone_basis,
                        measure_targets=measure_targets)

    monkeypatch.setattr(alternation, 'observe_n', observed)
    for now in (6, 7):
        actual = alternation.confirm_bull_alternation(bars, now, origin=origin, high=high, broken=broken,
            points=(broken, origin, high), polyline=polyline, level=1, path='test',
            policy=AlternationPolicy(), allow_gap_confirmation=False, _market_index=owner)
        assert seen and all(seen)
        seen.clear()
        expected = alternation.confirm_bull_alternation(bars, now, origin=origin, high=high, broken=broken,
            points=(broken, origin, high), polyline=polyline, level=1, path='test',
            policy=AlternationPolicy(), allow_gap_confirmation=False)
        assert actual == expected
        assert seen and not any(seen)
        seen.clear()
