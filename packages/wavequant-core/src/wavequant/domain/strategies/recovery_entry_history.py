"""Expose shared lifecycle alternation proofs to the existing entry selectors."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

from ..models.model import Bar
from .hierarchical_entry import EntryContext
from .trading_intervals import TradingEligibility, confirmed_alternation_event


def merge_recovery_entry_history(
    bars: Sequence[Bar], history: Mapping[int, Sequence[EntryContext]], events: Sequence[Mapping[str, object]],
) -> dict[int, tuple[EntryContext, ...]]:
    """只适配已发布事实；确认N可参与当天买点，未确认日期不补许可。"""
    dated: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for event in events:
        index = event.get('bar_index')
        if type(index) is int and 0 <= index < len(bars):
            dated[index].append(event)
    gates = {level: TradingEligibility(level) for level in (1, 2, 3)}
    active: dict[tuple[int, str], EntryContext] = {}
    result = {}
    for now, bar in enumerate(bars):
        for event in dated[now]:
            level, path = event.get('trend_level'), event.get('source_path')
            if type(level) is not int or level not in gates or not isinstance(path, str):
                continue
            changed = gates[level].observe(event)
            key = level, path
            if changed is False:
                active.pop(key, None)
            if event.get('event') != 'trend_lifecycle_state':
                continue
            if event.get('state') == '转多确认' and key in active:
                active[key] = replace(active[key], maturity_index=now)
            if (event.get('state') != '空多交替' or not confirmed_alternation_event(event)
                    or not any(window.source_path == path and window.start_flip_index == event.get('flip_index')
                               for window in gates[level].active_intervals)):
                continue
            # 同一开放区间仍可确认更晚的交替低点；仅更新当日起的策略上下文，
            # 不能要求资格重新开窗，也不能回写先前日期的测幅和买入依据。
            proof = event.get('confirmation_evidence')
            if event.get('special_bull_recovery') is not True and isinstance(proof, Mapping):
                event = dict(event, recovery_key_index=proof.get('key_index'), recovery_key_price=proof.get('key_price'))
            indices = ('origin_index', 'recovery_key_index', 'cycle_started_index', 'flip_index', 'counter_index')
            prices = ('origin_price', 'recovery_key_price', 'breakout_level', 'counter_price')
            if any(type(event.get(name)) is not int for name in indices):
                continue
            if any(type(event.get(name)) not in (int, float) for name in prices):
                continue
            attack = proof.get('attack') if isinstance(proof, Mapping) else None
            active[key] = EntryContext(level, cast(int, event['cycle_started_index']),
                cast(int, event['origin_index']), cast(int, event['recovery_key_index']),
                cast(float, event['recovery_key_price']), cast(int, event['cycle_started_index']),
                cast(int, event['flip_index']), cast(float, event['breakout_level']),
                cast(int, event['origin_index']), cast(float, event['origin_price']), now,
                cast(int, event['counter_index']), cast(float, event['counter_price']),
                confirmation_attack=attack if type(attack) is int else None)
        active = {key: context for key, context in active.items()
                  if bar.low >= context.origin_price and (context.maturity_index is not None
                      or bar.low >= context.alternation_low_price)}
        # Only replace the matching cycle, leaving unrelated levels/episodes available.
        identities = {(context.trend_level, context.origin_index, context.flip_high_index)
                      for context in active.values()}
        ordinary = tuple(context for context in history.get(now, ())
            if (context.trend_level, context.origin_index, context.flip_high_index) not in identities)
        result[now] = (*ordinary, *active.values())
    return result
