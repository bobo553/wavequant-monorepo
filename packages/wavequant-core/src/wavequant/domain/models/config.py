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
    # V3 uses measured targets for entry sizing/reward, not automatic liquidation.
    exit_on_target: bool = True
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
    entry_at_close: bool = False
    allow_add_on: bool = False
    nonflat_limit_close_fill: bool = False
    consolidation_entry_intraday: bool = False
    entry_ttl_bars: int = 1
    max_entry_gap: float = 0.05
    minimum_commission: float = 5.0
    a_share_taxes: bool = True
    # Preserve sealed/library callers; the interactive backtest explicitly
    # selects its unchecked (False) default at the request boundary.
    net_reward_risk_filter: bool = True
    staged_exit_enabled: bool = False
    staged_exit_same_day: bool = False
    staged_exit_intraday: bool = False
    missing_minute_daily_fallback: bool = False
    inverse_n_after_reduction: bool = False
    inverse_n_close_reduce: bool = False
    wave_exhaustion_exit: bool = False
    wave_exhaustion_reduction: float = 0.8
    wave_exhaustion_min_range: float = 0.08
    wave_exhaustion_min_shadow: float = 0.2
    wave_engulf_min_body: float = 0.05
    pressure_adverse_exit: bool = False
    trend_flip_adverse_exit: bool = False
    pressure_lookback: int = 120
    pressure_body_min_fraction: float = 0.05
    pressure_volume_ratio: float = 2.0
    volume_inverse_n_clear: bool = False
    volume_down_exit: bool = False
    small_n_reduction: bool = False
    small_body_max_fraction: float = 0.01
    small_body_lookback: int = 10
    initial_reduction_fraction: float = 0.5

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
        if type(self.nonflat_limit_close_fill) is not bool:
            raise ValueError('nonflat_limit_close_fill must be boolean')
        for name, value in self.to_dict().items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name in ("entry_at_close", "allow_add_on", "consolidation_entry_intraday", "require_non_bearish_regime", "allow_same_day_exit", "a_share_taxes", "staged_exit_enabled", "net_reward_risk_filter", "staged_exit_same_day", "staged_exit_intraday", "missing_minute_daily_fallback", "inverse_n_after_reduction", "inverse_n_close_reduce", "volume_down_exit", "volume_inverse_n_clear", "pressure_adverse_exit", "trend_flip_adverse_exit", "wave_exhaustion_exit", "small_n_reduction", "exit_on_target"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        positive_ints = {
            "pressure_lookback": self.pressure_lookback,
            "small_body_lookback": self.small_body_lookback,
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
        if type(self.small_body_max_fraction) not in (int, float) or not 0 < self.small_body_max_fraction <= 1:
            raise ValueError("small_body_max_fraction must be in (0, 1]")
        if type(self.pressure_body_min_fraction) not in (int, float) or not 0 < self.pressure_body_min_fraction < 1:
            raise ValueError("pressure_body_min_fraction must be in (0, 1)")
        if type(self.pressure_volume_ratio) not in (int, float) or self.pressure_volume_ratio < 1:
            raise ValueError("pressure_volume_ratio must be >= 1")
        for name in ('wave_exhaustion_reduction', 'wave_exhaustion_min_range', 'wave_exhaustion_min_shadow', 'wave_engulf_min_body'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 < value < 1:
                raise ValueError(f'{name} must be in (0, 1)')
        if self.min_confirm_bars > self.confirm_window:
            raise ValueError("min_confirm_bars must not exceed confirm_window")
        if not 0.0 < self.max_retracement < 1.0:
            raise ValueError("max_retracement must be between 0 and 1")
        if type(self.initial_reduction_fraction) not in (int, float) or not 0 < self.initial_reduction_fraction < 1:
            raise ValueError("initial_reduction_fraction must be between 0 and 1")
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
