"""Confirm a trend direction at a market break without freezing its live end."""

from collections.abc import Sequence
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

from ..models.model import Bar


class TrendReference(TypedDict):
    index: int
    time: str
    kind: str
    value: float
    available_at: str
    label: str
    ordinal: NotRequired[int]
    projection_count: NotRequired[int]
    projection_rank: NotRequired[int]


class DirectionConfirmation(TypedDict):
    available_at: str
    confirmation_rule: str
    direction: str
    broken_key: TrendReference
    origin: TrendReference
    confirmed_by: TrendReference


class ConfirmedDirection(TypedDict):
    confirmation: DirectionConfirmation
    endpoint: TrendReference


def source_trend_reversal(
    points: Sequence[TrendReference],
    direction: str,
    endpoint: TrendReference,
    end_index: int,
    asof: str,
    bars: Sequence[Bar],
) -> DirectionConfirmation | None:
    """Reverse only after breaking the source trend's frozen pre-extreme key.

    Locally lower highs and lows do not turn a whole rising trend while its
    last-rise low holds. Never roll that key to a later counter-swing low;
    falling trends mirror this rule. Reuse the existing causal market-break
    protocol, including known origins and ambiguous same-bar breaks.
    """
    key_kind = 'L' if direction == 'up' else 'H'
    formal_points = [point for point in points if point['index'] <= end_index and point['available_at'] <= asof
                     and point.get('state') not in ('seed', 'developing')]
    candidates = [(position, point) for position, point in enumerate(formal_points)
                  if point['kind'] == key_kind and point['index'] < endpoint['index']
                  and point['available_at'] <= endpoint['available_at']
                  and point['available_at'] <= asof and point.get('state') not in ('seed', 'developing')]
    if not candidates:
        return None
    position, key = candidates[-1]
    result = market_trend_confirmation(formal_points, key, position, bars, end_index)
    if result is None or result['confirmation']['direction'] == direction:
        return None
    return {**result['confirmation'], 'confirmation_rule': 'strict_source_trend_key_break'}


def session_date(bar: Bar) -> str:
    stamp = bar.timestamp
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
    return stamp.date().isoformat()


def source_reference(point: TrendReference) -> TrendReference:
    result: TrendReference = dict(index=point['index'], time=point['time'], kind=point['kind'], value=point['value'],
                                  available_at=point['available_at'], label=point['label'])
    if 'ordinal' in point:
        result['ordinal'] = point['ordinal']
    if 'projection_count' in point:
        result['projection_count'] = point['projection_count']
    if 'projection_rank' in point:
        result['projection_rank'] = point['projection_rank']
    return result


def market_trend_confirmation(
    points: Sequence[TrendReference],
    anchor: TrendReference,
    anchor_position: int,
    bars: Sequence[Bar],
    end_index: int,
) -> ConfirmedDirection | None:
    """Use the first strictly breaking bar and only source turns known by then.

    The opposite source extreme supplies the origin. A lower unconfirmed low
    (or higher unconfirmed high) prevents confirming a stale origin. Both sides
    breaking on one daily bar cannot establish their intraday order.
    """
    rising = anchor["kind"] == "H"
    origin_kind = "L" if rising else "H"
    candidates = sorted(
        (point for point in points[anchor_position + 1:] if point["kind"] == origin_kind),
        key=lambda point: (point["available_at"], point["index"]),
    )
    cursor = 0
    origin: TrendReference | None = None
    adverse = float("inf") if rising else float("-inf")
    for index in range(anchor["index"] + 1, end_index + 1):
        bar = bars[index]
        date = session_date(bar)
        adverse = min(adverse, bar.low) if rising else max(adverse, bar.high)
        while cursor < len(candidates) and candidates[cursor]["available_at"] <= date:
            point = candidates[cursor]
            if origin is None or (point["value"] < origin["value"] if rising else point["value"] > origin["value"]):
                origin = point
            cursor += 1
        if origin is None or date < anchor["available_at"] or index <= origin["index"]:
            continue
        stale_origin = adverse < origin["value"] if rising else adverse > origin["value"]
        if stale_origin:
            continue
        broken = bar.high > anchor["value"] if rising else bar.low < anchor["value"]
        if not broken:
            continue

        def reference(position: int) -> TrendReference:
            item = bars[position]
            return dict(
                index=position, time=session_date(item), kind="H" if rising else "L",
                value=item.high if rising else item.low, available_at=session_date(item),
                label="当前高点" if rising else "当前低点",
            )

        extreme = (max if rising else min)(
            range(origin["index"] + 1, end_index + 1),
            key=lambda position: bars[position].high if rising else bars[position].low,
        )
        confirmation: DirectionConfirmation = dict(
            available_at=date, confirmation_rule="strict_same_level_market_key_break",
            direction="up" if rising else "down", broken_key=source_reference(anchor), origin=source_reference(origin),
            confirmed_by=reference(index),
        )
        endpoint = reference(extreme)
        endpoint['available_at'] = max(date, endpoint['available_at'])
        return dict(confirmation=confirmation, endpoint=endpoint)
    return None
