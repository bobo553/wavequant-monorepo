"""Execution replays use the same selected trend routes as signal generation."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import cast

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import chart_entry_history as chart_history_module
from wavequant.domain.strategies import hierarchical_entry as hierarchy_module
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result

from .test_n_trend_levels import _sample


@pytest.mark.parametrize('option', (None, False, True))
def test_intraday_wave_context_replay_uses_the_effective_strategy_option(
    monkeypatch: pytest.MonkeyPatch, option: bool | None,
) -> None:
    bars, _ = _sample()
    seen: list[bool] = []
    real_history = chart_history_module.chart_entry_history

    def replay(prefix: Sequence[Bar], *, audit: Sequence[Mapping[str, object]] = (),
               n_target_trend_confirmation_enabled: bool = False) -> object:
        seen.append(n_target_trend_confirmation_enabled)
        return real_history(prefix, audit=audit,
                            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)

    monkeypatch.setattr(chart_history_module, 'chart_entry_history', replay)
    ready = dict(event='wave_continuation_ready', bar_index=len(bars)-1,
                 origin_index=1, attack_index=4, squeeze_index=10,
                 origin=6.0, box_anchor=8.0, two_t=12.0, defense=6.5)
    generated = SystemResult([], [ready], {})
    strategy = SystemStrategy(buy_point_definition='whole_flip_wave_v3') if option is None else SystemStrategy(
        buy_point_definition='whole_flip_wave_v3', n_target_trend_confirmation_enabled=option,
    )
    resolved, executions, fallbacks = resolve_consolidation_entries(
        bars, generated, strategy, None, daily_fallback=True,
    )
    assert seen == [option is True]
    assert resolved.signals == generated.signals and resolved.audit == generated.audit
    assert executions == {} and fallbacks == []


@pytest.mark.parametrize('option', (None, False, True))
@pytest.mark.parametrize('consumer', ('portfolio', 'single_stock'))
def test_trend_flip_exit_replay_uses_the_selected_signal_confirmation_mode(
    monkeypatch: pytest.MonkeyPatch, option: bool | None, consumer: str,
) -> None:
    bars, _ = _sample()
    seen: list[bool] = []
    real_history = hierarchy_module.hierarchical_history

    def replay(prefix: Sequence[Bar], *, n_target_trend_confirmation_enabled: bool = False) -> object:
        seen.append(n_target_trend_confirmation_enabled)
        return real_history(prefix, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)

    monkeypatch.setattr(hierarchy_module, 'hierarchical_history', replay)
    execution = StrategyConfig(trend_flip_adverse_exit=True)
    if consumer == 'portfolio':
        result = (run_portfolio({bars[0].symbol: bars}, [], execution) if option is None else
                  run_portfolio({bars[0].symbol: bars}, [], execution,
                                n_target_trend_confirmation_enabled=option))
        assert not result.orders
    else:
        strategy = SystemStrategy() if option is None else SystemStrategy(n_target_trend_confirmation_enabled=option)
        result = single_stock_result(bars, asdict(strategy), asdict(execution), SystemResult([], [], {}))
        assert result['orders'] == []
    assert seen == [option is True]


@pytest.mark.parametrize('invalid', (None, 0, 1, 'false', 'true'))
def test_portfolio_rejects_non_boolean_trend_options(invalid: object) -> None:
    with pytest.raises(ValueError, match='must be a boolean'):
        run_portfolio({}, [], StrategyConfig(), n_target_trend_confirmation_enabled=cast(bool, invalid))
