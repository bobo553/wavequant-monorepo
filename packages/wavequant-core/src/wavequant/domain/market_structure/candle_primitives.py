"""Strict two-candle price relations shared by trend and N-formation rules.

Every public observer takes the previous candle before the current candle and
rejects inconsistent OHLC, mismatched symbols and unordered timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models.model import Bar
from .price_action import _ordered_pair

__all__ = [
    "BarRelations", "observe_bar_relations", "shrinking_head", "shrinking_foot",
    "extending_head", "falling_tail", "sunrise", "sunset", "virtual_low", "virtual_high",
]


@dataclass(frozen=True)
class BarRelations:
    """Closed-candle facts; equal prices do not count as strict movement."""

    shrinking_head: bool
    shrinking_foot: bool
    extending_head: bool
    falling_tail: bool
    sunrise: bool
    sunset: bool
    inside: bool
    outside: bool
    equal_high: bool
    equal_low: bool


def observe_bar_relations(previous: Bar, current: Bar) -> BarRelations:
    """Observe all six lecture relations with one ordered-pair validation."""
    _ordered_pair(previous, current)
    lower_high = current.high < previous.high
    higher_low = current.low > previous.low
    higher_high = current.high > previous.high
    lower_low = current.low < previous.low
    return BarRelations(
        shrinking_head=lower_high,
        shrinking_foot=higher_low,
        extending_head=higher_high,
        falling_tail=lower_low,
        sunrise=higher_high and higher_low and current.close > previous.high,
        sunset=lower_high and lower_low and current.close < previous.low,
        inside=lower_high and higher_low,
        outside=higher_high and lower_low,
        equal_high=current.high == previous.high,
        equal_low=current.low == previous.low,
    )


def shrinking_head(previous: Bar, current: Bar) -> bool:
    """缩头: the current high is strictly below the previous high."""
    return observe_bar_relations(previous, current).shrinking_head


def shrinking_foot(previous: Bar, current: Bar) -> bool:
    """缩脚: the current low is strictly above the previous low."""
    return observe_bar_relations(previous, current).shrinking_foot


def extending_head(previous: Bar, current: Bar) -> bool:
    """出头: the current high is strictly above the previous high."""
    return observe_bar_relations(previous, current).extending_head


def falling_tail(previous: Bar, current: Bar) -> bool:
    """落尾: the current low is strictly below the previous low."""
    return observe_bar_relations(previous, current).falling_tail


def sunrise(previous: Bar, current: Bar) -> bool:
    """日出: both extremes rise and the close exceeds the previous high."""
    return observe_bar_relations(previous, current).sunrise


def sunset(previous: Bar, current: Bar) -> bool:
    """日落: both extremes fall and the close is below the previous low."""
    return observe_bar_relations(previous, current).sunset


def virtual_low(previous: Bar, current: Bar) -> float:
    """Include the previous close in the current low, preserving gap defense."""
    _ordered_pair(previous, current)
    return min(current.low, previous.close)


def virtual_high(previous: Bar, current: Bar) -> float:
    """Include the previous close in the current high, preserving gap defense."""
    _ordered_pair(previous, current)
    return max(current.high, previous.close)
