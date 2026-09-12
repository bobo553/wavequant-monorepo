"""Use cases that coordinate domain rules to produce research and trading results.

Application modules describe *what* WaveQuant does. External storage, market data
vendors, and user interfaces are supplied through infrastructure or interface
adapters instead of being embedded in the domain model.
"""

from wavequant.application.analytics.backtest import BacktestResult, run_backtest, run_portfolio

__all__ = ["BacktestResult", "run_backtest", "run_portfolio"]
