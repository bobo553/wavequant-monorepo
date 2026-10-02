"""Confirm an independently known secondary/primary low chain at its breakout."""

from collections.abc import Sequence
from dataclasses import asdict

from ..models.model import Bar
from .hierarchical_entry import EntryContext


def nested_alternation_breakout(
    bars: Sequence[Bar], before: Sequence[EntryContext], current: Sequence[EntryContext],
    *, now: int, origin: int, pullback: int,
) -> dict[str, object] | None:
    """Require both lows before today, held support, and a fresh volume/body break.

    The N must measure the primary wave containing those lows. Its completion
    supplies the existing measured targets and defense; no later squeeze is
    needed to reconfirm the already known pair.
    """
    if not 0 < now < len(bars):
        return None
    bar, previous = bars[now], bars[now - 1]
    body, upper = bar.close - bar.open, bar.high - max(bar.open, bar.close)
    if body <= 0 or previous.volume <= 0 or bar.volume <= previous.volume:
        return None
    if upper > 0 and upper >= body and upper >= (bar.high - bar.low) / 3:
        return None
    live = {ctx.episode for ctx in current}
    primaries = [ctx for ctx in before if ctx.trend_level == 1 and ctx.episode in live
                 and ctx.origin_index == origin and ctx.alternation_low_index == pullback]
    secondaries = [ctx for ctx in before if ctx.trend_level == 2 and ctx.episode in live]
    for primary in sorted(primaries, key=lambda ctx: ctx.alternation_index, reverse=True):
        if not (
            0 <= primary.origin_index < primary.flip_high_index < primary.alternation_low_index
            <= primary.alternation_index < now
        ):
            continue
        for secondary in sorted(secondaries, key=lambda ctx: ctx.alternation_index, reverse=True):
            if not (
                0 <= secondary.origin_index < secondary.flip_high_index < secondary.alternation_low_index
                <= primary.origin_index < primary.alternation_low_index
                and secondary.flip_index <= secondary.alternation_index < primary.alternation_index
                and primary.flip_index <= primary.alternation_index
                and secondary.alternation_low_price <= primary.origin_price < primary.alternation_low_price
                and primary.alternation_low_price < primary.flip_high_price
            ):
                continue
            if (
                min(b.low for b in bars[secondary.alternation_low_index : now + 1]) < secondary.alternation_low_price
                or min(b.low for b in bars[primary.alternation_low_index : now + 1]) < primary.alternation_low_price
            ):
                continue
            high = max(primary.flip_high_price, *(b.high for b in bars[primary.alternation_low_index : now]))
            if bar.close <= high:
                continue
            return dict(
                asdict(primary), buy_point_type="nested_alternation_breakout",
                definition="secondary_primary_alternation_breakout_v1", priority=3,
                counter_filter_applied=False,
                counter_ratio=(primary.flip_high_price - primary.alternation_low_price)
                              / (primary.flip_high_price - primary.origin_price),
                secondary_low_index=secondary.alternation_low_index,
                secondary_low_price=secondary.alternation_low_price,
                secondary_known_index=secondary.alternation_index,
                secondary_low_date=str(bars[secondary.alternation_low_index].timestamp.date()),
                secondary_known_date=str(bars[secondary.alternation_index].timestamp.date()),
                primary_low_index=primary.alternation_low_index,
                primary_low_price=primary.alternation_low_price,
                primary_known_index=primary.alternation_index,
                primary_low_date=str(bars[primary.alternation_low_index].timestamp.date()),
                primary_known_date=str(bars[primary.alternation_index].timestamp.date()),
                breakout_high=high, confirmation_close=bar.close, confirmation_low=bar.low,
                confirmation_volume=bar.volume, previous_volume=previous.volume,
            )
    return None
