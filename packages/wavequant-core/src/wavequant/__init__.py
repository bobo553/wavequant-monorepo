"""Causal, dependency-free research tools for the 主控波浪 framework."""

from wavequant.application.analytics.backtest import BacktestResult, run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.strategies.features import compute_features
from wavequant.domain.strategies.strategy import generate_signals
from wavequant.infrastructure.market_data.io import load_bars

__all__ = [
    "BacktestResult",
    "StrategyConfig",
    "compute_features",
    "generate_signals",
    "load_bars",
    "run_backtest",
]
