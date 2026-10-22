"""国芳集团真实逐日前缀只发布每个一级趋势的一个底部成功 N。"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.daily_strategy import daily_signals
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.interfaces.charts.visualization import ChartRepository


_POLICY = 'level_one_successful_bottom_n_v2'
_DAYS = ('2024-03-11', '2024-03-12')
_TARGET_STAGES = {'one_p', 'two_t', 'five_top', 'ten_full'}


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, dict) for item in value)
    return cast(list[Mapping[str, object]], list(value))


def _positive_ns(chart: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [row for row in _records(chart['events'])
            if row['event'] == 'n_completed' and row['direction'] == 'up']


def _selected(chart: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [row for row in _positive_ns(chart) if row['structural_target_selected'] is True]


def _render(bars: Sequence[Bar], strategy: SystemStrategy, result: SystemResult) -> dict[str, object]:
    return cast(dict[str, object], ChartRepository.render_theory(
        None, bars, strategy, result, bars[-1].timestamp.date().isoformat(),
        geometry={'tertiary_trends': {
            'n_target_trend_confirmation_enabled': strategy.n_target_trend_confirmation_enabled,
        }},
    ))


@dataclass(frozen=True)
class _GuofangReplay:
    bars: tuple[Bar, ...]
    strategy: SystemStrategy
    result: SystemResult
    charts: Mapping[str, Mapping[str, object]]


@pytest.fixture(scope='module')
def guofang_replay() -> _GuofangReplay:
    # 复用既有冻结完整前缀；不注入 N、趋势、交易资格或预先选中的目标。
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2024_squeeze_confirmation.json')
                     .read_text(encoding='utf-8'))
    bars = tuple(Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
                 for row in raw['bars'] if row['timestamp'][:10] <= _DAYS[-1])
    strategy = SystemStrategy(**raw['strategy'])
    charts: dict[str, Mapping[str, object]] = {}

    def capture(day: MarketDay, analysis: BacktestAnalysis, generated: SystemResult,
                state: DailySignalState) -> None:
        index = day.frame.index
        asof = bars[index].timestamp.date().isoformat()
        if asof in _DAYS:
            charts[asof] = _render(bars[:index + 1], strategy, generated)

    result = daily_signals(bars, strategy, on_day=capture)
    return _GuofangReplay(bars, strategy, result, charts)


def test_guofang_frozen_quote_is_the_reported_march_12_candle(guofang_replay: _GuofangReplay) -> None:
    bar = guofang_replay.bars[-1]
    assert guofang_replay.bars[0].timestamp.date().isoformat() == '2018-01-02'
    assert bar.symbol == 'sh.601086' and bar.timestamp.date().isoformat() == '2024-03-12'
    assert (bar.open, bar.high, bar.low, bar.close) == pytest.approx((5.68, 5.81, 5.64, 5.81), abs=.005)
    assert bar.volume == 11_487_500
    assert bar.close / bar.adjustment_factor == pytest.approx(4.45)


@pytest.mark.parametrize('asof', _DAYS)
def test_real_trend_publishes_one_selected_n_and_one_pair_of_targets(
    guofang_replay: _GuofangReplay, asof: str,
) -> None:
    chart = guofang_replay.charts[asof]
    assert chart['structural_n_policy'] == _POLICY
    positive_ns = _positive_ns(chart)
    selected = _selected(chart)
    assert positive_ns and selected
    # 保证本次二月低点后的案例被覆盖，不能靠过滤掉所有近期 N 通过唯一性断言。
    assert any(str(row['available_at']) >= '2024-02-01' for row in selected)
    owners: Counter[str] = Counter()
    for row in positive_ns:
        assert type(row['structural_target_selected']) is bool
        levels = [level for level in _records(row['levels']) if level.get('stage') in _TARGET_STAGES]
        if not row['structural_target_selected']:
            assert not levels
            continue
        assert isinstance(row['structural_trend_id'], str) and row['structural_trend_id']
        assert isinstance(row['structural_target_owner'], str) and row['structural_target_owner']
        owners[row['structural_trend_id']] += 1
        stages = Counter(str(level['stage']) for level in levels)
        assert stages['one_p'] == stages['two_t'] == 1
        assert str(row['available_at']) <= asof
        assert all(str(level['available_at']) <= asof for level in levels)
    assert all(count == 1 for count in owners.values())


def test_later_close_cannot_reselect_or_reveal_a_future_n_in_march_11_history(
    guofang_replay: _GuofangReplay,
) -> None:
    earlier = guofang_replay.charts[_DAYS[0]]
    replayed = _render(guofang_replay.bars[:-1], guofang_replay.strategy, guofang_replay.result)
    assert replayed['structural_n_policy'] == _POLICY
    assert _selected(replayed) == _selected(earlier)
    assert all(str(row['available_at']) <= _DAYS[0] for row in _positive_ns(replayed))
