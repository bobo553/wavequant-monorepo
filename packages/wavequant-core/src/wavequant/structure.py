"""Causal daily proxies for the Notion notes, not hindsight Elliott-wave labels.

Two-sided pivots become usable only after their right-hand bars have closed.
Every pending setup freezes its anchors; later pivots never rewrite signals.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from .model import Bar, Signal


@dataclass(frozen=True)
class StructureConfig:
    pivot_width: int = 2
    max_pattern_bars: int = 66
    confirmation_bars: int = 5
    max_retracement: float = 0.5
    target_mode: str = 'risk'
    volume_filter: bool = False
    ma66_filter: bool = False
    weekly_filter: bool = False
    p_filter: bool = False
    minimum_reward_risk: float = 0.0

    def validate(self):
        for key in ('pivot_width', 'max_pattern_bars', 'confirmation_bars'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f'{key} must be a positive integer')
        for key in ('volume_filter', 'ma66_filter', 'weekly_filter', 'p_filter'):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f'{key} must be boolean')
        if not 0 < self.max_retracement < 1:
            raise ValueError('retracement must be in (0, 1)')
        if self.target_mode not in ('risk', 'tide', 'burst'):
            raise ValueError('unknown target mode')
        if not math.isfinite(self.minimum_reward_risk) or self.minimum_reward_risk < 0:
            raise ValueError('invalid minimum reward/risk')
        if self.minimum_reward_risk and self.target_mode == 'risk':
            raise ValueError('minimum reward/risk requires a fixed structural target')


def candle_evidence(bar: Bar, previous: Bar | None = None) -> dict:
    span = bar.high-bar.low
    clv = (bar.close-bar.low)/span if span else 0.5
    denominator = bar.high-bar.close
    return dict(close_location=clv,
                p_value=(bar.close-bar.low)/denominator if denominator else None,
                p_unbounded=bool(span and not denominator),
                burst=bool(span and clv > 0.8), burst_target=2*bar.close-bar.low,
                body_red=bar.close > bar.open,
                trend_red=previous is not None and bar.close > previous.close)


def completed_weekly_context(bars: list[Bar]) -> list[bool]:
    """Only earlier ISO weeks, even on Friday; observable next week's first bar.

    Initial observed week is discarded because its earlier sessions may be absent.
    This conservative delay also handles holidays without guessing a calendar.
    """
    result, closes = [], []
    current_week, last_close = None, None
    initial = True
    for bar in bars:
        week = bar.timestamp.isocalendar()[:2]
        if current_week is not None and week != current_week:
            if initial:
                initial = False
            else:
                closes.append(last_close)
        current_week, last_close = week, bar.close
        ok = (len(closes) >= 27 and
              statistics.mean(closes[-13:]) > statistics.mean(closes[-26:]) and
              statistics.mean(closes[-26:]) >= statistics.mean(closes[-27:-1]))
        result.append(ok)
    return result


def volume_votes(bars: list[Bar], i: int) -> int:
    if i < 22:
        return 0
    long = statistics.mean(b.volume for b in bars[i-21:i+1])
    short = statistics.mean(b.volume for b in bars[i-4:i+1])
    prev = statistics.mean(b.volume for b in bars[i-22:i])
    return sum((bars[i].volume > long, short > long, long > prev))


def structural_signals(bars: list[Bar], config: StructureConfig) -> list[Signal]:
    config.validate()
    if len({b.timestamp.date() for b in bars}) != len(bars):
        raise ValueError('structural strategy requires daily bars')
    if any(b.symbol != bars[0].symbol or (i and b.timestamp <= bars[i-1].timestamp)
           for i, b in enumerate(bars)):
        raise ValueError('one symbol, strictly ordered bars required')
    weekly = completed_weekly_context(bars) if config.weekly_filter else [False]*len(bars)
    pivots, signals, used = [], [], set()
    pending = None
    w = config.pivot_width
    for i, bar in enumerate(bars):
        if pending is not None:
            p = pending
            # Support must survive the entire post-breakout path, including today.
            if i-p['trigger'] > config.confirmation_bars or bar.low < p['neckline']:
                pending = None
            else:
                p['resistance'] |= bar.close <= bars[i-1].close or bar.low < bars[i-1].low
                if p['resistance'] and bar.close > p['peak']:
                    signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close,
                        p['stop'], 'notion_N_breakout_resistance_failed', bars[p['trigger']].timestamp,
                        p['retracement'], p['rvol'], 'structural_support_held',
                        p['target'], config.minimum_reward_risk))
                    pending = None
                else:
                    p['peak'] = max(p['peak'], bar.high)
        # A pivot at j is only learned at i=j+w, not on its historical date.
        if i < 2*w:
            continue
        j = i-w
        neighbors = bars[j-w:j]+bars[j+1:i+1]
        low = all(bars[j].low < b.low for b in neighbors)
        high = all(bars[j].high > b.high for b in neighbors)
        if low != high:  # ambiguous outside bars and tied extrema are ignored
            pivot = ('L' if low else 'H', j, bars[j].low if low else bars[j].high)
            if pivots and pivots[-1][0] == pivot[0]:
                if (low and pivot[2] < pivots[-1][2]) or (high and pivot[2] > pivots[-1][2]):
                    pivots[-1] = pivot
            else:
                pivots.append(pivot)
                pivots = pivots[-3:]
        if pending is not None or len(pivots) != 3 or [p[0] for p in pivots] != ['L', 'H', 'L']:
            continue
        origin, peak, pullback = pivots
        key = tuple(p[1] for p in pivots)
        amplitude = peak[2]-origin[2]
        if amplitude <= 0 or i-origin[1] > config.max_pattern_bars or key in used:
            continue
        q = (peak[2]-pullback[2])/amplitude
        # Strict '<': equality at half retracement is not the strict hypothesis.
        if not 0 < q < config.max_retracement or not bars[i-1].close <= peak[2] < bar.close:
            continue
        used.add(key)
        if i < 22:
            continue
        candle = candle_evidence(bar, bars[i-1])
        if config.p_filter and not candle['burst']:
            continue
        if config.volume_filter and volume_votes(bars, i) < 2:
            continue
        if config.ma66_filter and (i < 66 or bar.close <= statistics.mean(b.close for b in bars[i-65:i+1])
                                   or bar.close < bars[i-66].close):
            continue
        if config.weekly_filter and not weekly[i]:
            continue
        target = (2*peak[2]-origin[2] if config.target_mode == 'tide' else
                  candle['burst_target'] if config.target_mode == 'burst' else None)
        base_volume = statistics.median(b.volume for b in bars[i-20:i])
        pending = dict(trigger=i, neckline=peak[2], peak=bar.high, resistance=False,
                       stop=pullback[2]-0.01*bar.adjustment_factor, target=target,
                       retracement=q, rvol=bar.volume/base_volume if base_volume else None)
        if pending['stop'] <= 0:
            pending = None
    return signals
