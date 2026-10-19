"""Causal risk streams retain dated state across skipped days and source revisions."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import trend_flip_exit as trend_module
from wavequant.domain.strategies.pressure_exit import (
    PressureExitStream, pressure_exit_history, record_high_massive_resistance_history,
    record_high_resistance_history,
)
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def pressure_market():
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', 9, 9.2, 8.8, 9, 10000)
            for i in range(24)]
    bars[20] = replace(bars[20], open=10.5, high=11, low=9, close=9.2, volume=50000)
    bars[21] = replace(bars[21], open=9.2, high=9.4, low=9, close=9.3)
    bars[22] = replace(bars[22], open=9.4, high=10, low=9.4, close=9.9)
    bars[23] = replace(bars[23], open=9.9, high=10.3, low=9.85, close=9.95)
    return bars, [None] * 22 + [22, 22]


def test_pressure_first_active_date_catches_up_without_losing_the_old_zone():
    bars, context = pressure_market()
    stream = PressureExitStream(bars, StrategyConfig())
    try:
        assert stream.advance(21, context[:22])[0] == {}
        result = stream.advance(23, context)[0]
        assert list(result) == [23]
        assert result[23]['pressure_date'] == '2024-01-21'
        assert result[23]['reason'] == 'pressure_adverse_clear'
        assert result[23]['exit_fraction'] == 1
        # A same-length context correction must remove the now-unowned exit.
        assert stream.advance(23, [None] * 24)[0] == {}
        assert stream.advance(23, context)[0] == result
    finally:
        stream.close()


def test_pressure_stream_matches_each_visible_prefix_in_all_three_rules():
    bars, context = pressure_market()
    config = StrategyConfig()
    stream = PressureExitStream(bars, config)
    try:
        for end in range(len(bars)):
            assert stream.advance(end, context[:end + 1]) == (
                pressure_exit_history(bars[:end + 1], context[:end + 1], config),
                record_high_resistance_history(bars[:end + 1], config),
                record_high_massive_resistance_history(bars[:end + 1], config),
            )
    finally:
        stream.close()


def trend_market():
    rows = [(9.5, 10, 9, 9.5), (9.4, 11, 9.4, 10.5), (10.4, 11, 10, 10.8),
            (10.9, 11, 10.1, 10.2), (10.3, 10.5, 9.9, 10)]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(rows)]
    return bars, {i: {3: [dict(index=0, kind='H', value=10, available_at=0)]}
                  for i in range(len(bars))}


def test_trend_stream_uses_previous_day_points_and_repairs_same_length_edits():
    bars, history = trend_market()
    stream = TrendFlipExitStream(bars)
    try:
        assert stream.advance(2, history) == {}
        risk = deepcopy(stream.advance(3, history))
        assert risk[3]['reason'] == 'trend_flip_resistance_adverse_clear'
        assert risk[3]['trend_level'] == 3
        assert risk[3]['exit_fraction'] == 1
        for levels in history.values():
            levels[3][0]['available_at'] = 4
        assert stream.advance(3, history) == {}
        assert risk[3]['trend_key_date'] == '2025-01-01'
        assert stream.advance(4, history) == trend_flip_exit_history(bars, history)
    finally:
        stream.close()


def test_trend_append_does_not_reobserve_prior_candles(monkeypatch):
    bars, history = trend_market()
    original = trend_module.observe_resistance
    calls = []

    def observe(previous, current, **options):
        calls.append(current.timestamp)
        return original(previous, current, **options)

    monkeypatch.setattr(trend_module, 'observe_resistance', observe)
    stream = TrendFlipExitStream(bars)
    owner = object()
    try:
        stream.advance(3, history, _history_owner=owner)
        calls.clear()
        stream.advance(4, history, _history_owner=owner)
        assert all(when == bars[4].timestamp for when in calls)
        calls.clear()
        stream.advance(4, history, _history_owner=owner)
        assert calls == []
    finally:
        stream.close()


def test_pressure_stream_rejects_rewinding_its_fixed_market():
    bars, context = pressure_market()
    stream = PressureExitStream(bars, StrategyConfig())
    try:
        stream.advance(23, context)
        with pytest.raises(ValueError, match='exact current prefix'):
            stream.advance(22, context[:23])
    finally:
        stream.close()
