from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    buyable: bool = True
    sellable: bool = True
    # adjusted price / raw price. Quantity in the engine is an adjusted-price
    # equivalent unit; entry lot size and volume capacity use actual shares.
    adjustment_factor: float = 1.0


@dataclass(frozen=True)
class BarFeature:
    bar: Bar
    index: int
    previous_high: float | None
    previous_low: float | None
    atr: float | None
    rvol: float | None
    close_location: float
    efficiency: float | None
    move_in_atr: float | None
    regime: str


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    symbol: str
    bar_index: int
    side: str
    reference_price: float
    invalidation_price: float
    reason: str
    trigger_timestamp: datetime
    retracement: float
    rvol: float | None
    regime: str
    target_price: float | None = None
    minimum_reward_risk: float = 0.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    quantity: float
    gross_return: float
    net_return: float
    bars_held: int
    entry_reason: str
    exit_reason: str
    pnl: float = 0.0
    fees: float = 0.0
