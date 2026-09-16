"""Validated strategy parameters shared by domain rules and application use cases."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategyConfig:
    """Parameters are hypotheses, not market constants.

    A grid or walk-forward process should test them.  The defaults are only a
    conservative starting point for daily or 5-minute research.
    """

    breakout_lookback: int = 20
    impulse_lookback: int = 20
    atr_period: int = 14
    rvol_lookback: int = 20
    regime_lookback: int = 20
    confirm_window: int = 5
    min_confirm_bars: int = 1

    breakout_buffer_bps: float = 0.0
    min_rvol: float = 1.20
    min_close_location: float = 0.65
    max_retracement: float = 0.50
    stop_atr_buffer: float = 0.25

    regime_min_efficiency: float = 0.25
    regime_strong_move_atr: float = 2.50
    require_non_bearish_regime: bool = True

    max_hold_bars: int = 20
    take_profit_r: float = 2.0
    commission_bps_per_side: float = 3.0
    slippage_bps_per_side: float = 5.0
    allow_same_day_exit: bool = False
    initial_capital: float = 1_000_000.0
    max_positions: int = 5
    max_position_weight: float = 0.20
    risk_fraction: float = 0.01
    lot_size: int = 100
    # Exchange minimum for a new position.  It is separate from ``lot_size``:
    # STAR/BSE allow increments smaller than their minimum opening order.
    minimum_entry_shares: int = 1
    liquidity_lookback: int = 20
    max_participation: float = 0.01
    entry_ttl_bars: int = 1
    max_entry_gap: float = 0.05
    minimum_commission: float = 5.0
    a_share_taxes: bool = True

    @classmethod
    def from_json(cls, path: str | Path) -> "StrategyConfig":
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown config fields: {sorted(unknown)}")
        result = cls(**raw)
        result.validate()
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        for name, value in self.to_dict().items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name in ("require_non_bearish_regime", "allow_same_day_exit", "a_share_taxes"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        positive_ints = {
            "breakout_lookback": self.breakout_lookback,
            "impulse_lookback": self.impulse_lookback,
            "atr_period": self.atr_period,
            "rvol_lookback": self.rvol_lookback,
            "regime_lookback": self.regime_lookback,
            "confirm_window": self.confirm_window,
            "min_confirm_bars": self.min_confirm_bars,
            "max_hold_bars": self.max_hold_bars,
            "max_positions": self.max_positions,
            "lot_size": self.lot_size,
            "minimum_entry_shares": self.minimum_entry_shares,
            "liquidity_lookback": self.liquidity_lookback,
            "entry_ttl_bars": self.entry_ttl_bars,
        }
        for name, value in positive_ints.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.min_confirm_bars > self.confirm_window:
            raise ValueError("min_confirm_bars must not exceed confirm_window")
        if not 0.0 < self.max_retracement < 1.0:
            raise ValueError("max_retracement must be between 0 and 1")
        if not 0.0 <= self.min_close_location <= 1.0:
            raise ValueError("min_close_location must be between 0 and 1")
        if self.min_rvol < 0.0:
            raise ValueError("min_rvol must be >= 0")
        for name in ("initial_capital", "take_profit_r", "regime_strong_move_atr"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        for name in ("max_position_weight", "risk_fraction", "max_participation"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for name in ("breakout_buffer_bps", "stop_atr_buffer", "minimum_commission",
                     "commission_bps_per_side", "slippage_bps_per_side", "max_entry_gap"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.slippage_bps_per_side >= 10000 or self.commission_bps_per_side >= 10000:
            raise ValueError("cost rates must be below 10000 bps")
        if not 0 <= self.regime_min_efficiency <= 1:
            raise ValueError("regime_min_efficiency must be in [0, 1]")
