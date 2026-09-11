"""Causal, dependency-free research tools for the 主控波浪 framework."""

from .backtest import BacktestResult, run_backtest
from .config import StrategyConfig
from .features import compute_features
from .io import load_bars
from .strategy import generate_signals

__all__ = [
    "BacktestResult",
    "StrategyConfig",
    "compute_features",
    "generate_signals",
    "load_bars",
    "run_backtest",
]
