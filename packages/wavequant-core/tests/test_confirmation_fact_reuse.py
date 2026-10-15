"""首证书复用只取自己的可知前缀，修订、未知标签和 N 沿用冷判断。"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_confirmation as confirmation
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.models.model import Bar


def _sample(up: bool, date_clock: bool) -> tuple[tuple[Bar, ...], list[dict[str, object]]]:
    prices = ((12., 10., 11.), (12., 10., 11.), (12., 10., 11.),
              (13., 10., 12.), (13., 11., 12.), (15., 11., 14.))
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'CACHE', close,
                     high, low, close, 100.) for index, (high, low, close) in enumerate(prices))
    if not up:
        bars = tuple(replace(bar, open=30-bar.open, high=30-bar.low, low=30-bar.high,
                             close=30-bar.close) for bar in bars)
    points: list[dict[str, object]] = []
    for index, high in ((0, True), (1, False), (3, True), (4, False)):
        high = high if up else not high
        day = bars[index].timestamp.date().isoformat()
        points.append(dict(index=index, ordinal=0, time=day, kind='H' if high else 'L',
                           value=bars[index].high if high else bars[index].low,
                           available_at=day if date_clock else index, label=f'point-{index}'))
    return bars, points


@pytest.mark.parametrize('up', (False, True))
@pytest.mark.parametrize('date_clock', (False, True))
@pytest.mark.parametrize('enabled', (False, True))
def test_reused_first_fact_matches_every_cold_cutoff_and_does_not_borrow_future(
    up: bool, date_clock: bool, enabled: bool,
) -> None:
    bars, source = _sample(up, date_clock)
    owner = _MarketRangeIndex(bars)
    prefix = _MarketPrefixIndex(owner, bars)
    qualify = confirmation.qualify_uptrend if up else confirmation.qualify_downtrend
    snapshot = confirmation.prepare_confirmation_context(source, bars, 5, _market_index=prefix,
        n_target_trend_confirmation_enabled=enabled)
    for cutoff in (0, 1, 2, 3, 4, 5, 2, 5, 3):
        context = confirmation.prepare_confirmation_context(source, bars, cutoff, _market_index=prefix,
            parsed_context=snapshot, n_target_trend_confirmation_enabled=enabled)
        actual = qualify(source, source[1], 1, source[0], bars, cutoff, context=context,
                         n_target_trend_confirmation_enabled=enabled)
        expected = qualify(source, source[1], 1, source[0], bars, cutoff,
                           n_target_trend_confirmation_enabled=enabled)
        assert actual == expected
        assert (actual is None) == (cutoff < 3)
    assert (owner.confirmation_reuse is None) == enabled


def test_successful_fact_survives_irrelevant_audit_but_rejects_real_source_and_key_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, source = _sample(True, False)
    owner = _MarketRangeIndex(bars)
    prefix = _MarketPrefixIndex(owner, bars)
    calls: list[int] = []
    first_extreme = owner.first_extreme

    def query(start: int, stop: int, price: float, *, high: bool) -> int | None:
        calls.append(stop)
        return first_extreme(start, stop, price, high=high)

    monkeypatch.setattr(owner, 'first_extreme', query)

    def actual(cutoff: int) -> dict[str, object] | None:
        context = confirmation.prepare_confirmation_context(source, bars, cutoff, _market_index=prefix)
        return confirmation.qualify_uptrend(source, source[1], 1, source[0], bars, cutoff, context=context)

    proof = actual(3)
    assert proof is not None and calls
    calls.clear()
    source[1]['audit'] = {'later': list(range(50))}
    reused = actual(5)
    assert reused == proof and calls == []
    assert reused is not None
    cast(dict[str, object], reused['origin'])['value'] = -1.
    assert actual(4) == proof
    source[2]['available_at'] = 4
    assert actual(5) == confirmation.qualify_uptrend(source, source[1], 1, source[0], bars, 5)
    assert calls
    calls.clear()
    source[0]['value'] = 13.
    assert actual(5) == confirmation.qualify_uptrend(source, source[1], 1, source[0], bars, 5)
    assert calls


def test_nonstandard_public_leaf_does_not_enter_fact_cache_or_change_its_aliases() -> None:
    bars, source = _sample(True, False)
    label: list[object] = ['mutable']
    source[1]['label'] = label
    owner = _MarketRangeIndex(bars)
    prefix = _MarketPrefixIndex(owner, bars)
    context = confirmation.prepare_confirmation_context(source, bars, 5, _market_index=prefix)
    proof = confirmation.qualify_uptrend(source, source[1], 1, source[0], bars, 5, context=context)
    assert proof is not None
    assert cast(dict[str, object], proof['origin'])['label'] is label
    assert owner.confirmation_reuse is None


def test_unique_ordered_prefix_uses_binary_visibility_without_reparsing_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, source = _sample(True, True)
    prefix = _MarketPrefixIndex(_MarketRangeIndex(bars), bars)
    snapshot = confirmation.prepare_confirmation_context(source, bars, 5, _market_index=prefix)
    assert snapshot._prefix_visibility is not None

    def forbidden(*args: object) -> None:
        raise AssertionError('ordered daily references were reprojected individually')

    monkeypatch.setattr(confirmation, '_reference_at_cutoff', forbidden)
    for cutoff in range(6):
        projected = confirmation.prepare_confirmation_context(source, bars, cutoff,
            parsed_context=snapshot, _market_index=prefix)
        cold = confirmation.prepare_confirmation_context(source, bars, cutoff, _market_index=prefix)
        assert projected.references == cold.references and projected.ordered == cold.ordered


def test_duplicate_sessions_keep_the_original_date_projection() -> None:
    bars, source = _sample(True, True)
    bars = (*bars[:4], replace(bars[4], timestamp=bars[3].timestamp+timedelta(hours=2)), bars[5])
    source[3]['time'] = source[2]['time']
    source[3]['available_at'] = source[2]['time']
    prefix = _MarketPrefixIndex(_MarketRangeIndex(bars), bars)
    snapshot = confirmation.prepare_confirmation_context(source, bars, 5, _market_index=prefix)
    assert snapshot._prefix_visibility is None and snapshot._proof_prefixes is None
    for cutoff in range(6):
        projected = confirmation.prepare_confirmation_context(source, bars, cutoff,
            parsed_context=snapshot, _market_index=prefix)
        cold = confirmation.prepare_confirmation_context(source, bars, cutoff, _market_index=prefix)
        assert projected.references == cold.references and projected.ordered == cold.ordered


@pytest.mark.parametrize('up', (False, True))
def test_historical_rejection_reuses_only_same_cutoff_and_same_known_facts(
    up: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, source = _sample(up, False)
    owner = _MarketRangeIndex(bars)
    prefix = _MarketPrefixIndex(owner, bars)
    qualify = confirmation.qualify_uptrend if up else confirmation.qualify_downtrend
    calls = 0
    original = owner.first_extreme

    def query(start: int, stop: int, price: float, *, high: bool) -> int | None:
        nonlocal calls
        calls += 1
        return original(start, stop, price, high=high)

    monkeypatch.setattr(owner, 'first_extreme', query)

    def at(cutoff: int) -> dict[str, object] | None:
        full = confirmation.prepare_confirmation_context(source, bars, 5, _market_index=prefix)
        context = confirmation.prepare_confirmation_context(source, bars, cutoff,
            parsed_context=full, _market_index=prefix)
        return qualify(source, source[1], 1, source[0], bars, cutoff, context=context)

    assert at(2) is None and calls > 0
    calls = 0
    assert at(2) is None and calls == 0
    # 新截止日不能沿用旧拒绝；更早同源关键位修订也必须重新核验。
    assert at(3) is not None and calls > 0
    source[0]['value'] = 11.5 if up else 18.5
    calls = 0
    revised = at(2)
    assert revised is not None and calls > 0
    assert revised == qualify(source, source[1], 1, source[0], bars, 2)
