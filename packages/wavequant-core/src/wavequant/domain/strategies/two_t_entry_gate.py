"""首次观察到有效正 N 二吐目标的当日，所有入场渠道共用禁买规则。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from ..models.model import Bar


TWO_T_ENTRY_REACHED = 'positive_n_two_t_reached_no_entry'


@dataclass(frozen=True)
class TwoTEntryTarget:
    attack: int
    known_at: int
    one_p: float
    two_t: float
    source_id: str | None = None


def entry_targets_from_events(
    events: Sequence[Mapping[str, object]], index: int, *, at_close: bool,
) -> tuple[TwoTEntryTarget, ...]:
    """保留所有未失效测幅的入场风险；新下降段只交接主测幅，不解除旧目标风险。

    破位和资格关闭仍终止对应来源，盘中不能使用当日收盘才可知的事实。
    """
    cutoff = index if at_close else index - 1
    sources: list[TwoTEntryTarget] = []
    retired_ids: set[str] = set()
    retired_attacks: set[int] = set()
    for event in events:
        day = event.get('bar_index')
        known = event.get('known_at', day)
        if type(day) is not int or type(known) is not int or max(day, known) > cutoff:
            continue
        kind = event.get('event')
        identity = event.get('n_id')
        if kind in ('n_target_source_retired', 'n_invalidated'):
            if kind == 'n_target_source_retired' and event.get('reason') == 'new_confirmed_decline':
                continue
            if isinstance(identity, str):
                retired_ids.add(identity)
            elif type(attack := event.get('attack')) is int:
                retired_attacks.add(attack)
        elif (kind == 'n_completed' and event.get('direction') == 'up'
                and event.get('target_eligible') is True and event.get('target_primary') is not False):
            one_p, two_t = event.get('one_p'), event.get('two_t')
            if (type(one_p) not in (int, float) or type(two_t) not in (int, float)):
                continue
            # 类型边界不接受 bool、字符串或不完整目标。
            assert isinstance(one_p, (int, float)) and isinstance(two_t, (int, float))
            window = event.get('window_start')
            available = max(day, known, window if type(window) is int else 0)
            if available <= cutoff:
                sources.append(TwoTEntryTarget(day, available, float(one_p), float(two_t),
                    identity if isinstance(identity, str) else None))
    return tuple(sorted((source for source in sources
        if source.source_id not in retired_ids and source.attack not in retired_attacks),
        key=lambda value: (value.known_at, value.attack), reverse=True))


def two_t_entry_rejection(
    bars: Sequence[Bar], index: int, sources: Sequence[TwoTEntryTarget], *,
    observed_high: float, observed_close: float, at_close: bool,
) -> dict[str, object] | None:
    """任一仍有效的原 N 首次到二吐即拒绝，较新的未到价目标不能覆盖它。"""
    for source in sources:
        rejected = _target_rejection(bars, index, source,
            observed_high=observed_high, observed_close=observed_close, at_close=at_close)
        if rejected is not None:
            return rejected
    return None


def _target_rejection(
    bars: Sequence[Bar], index: int, source: TwoTEntryTarget, *,
    observed_high: float, observed_close: float, at_close: bool,
) -> dict[str, object] | None:
    """只使用决策时可见极值；首次可知日的目标只由收盘消费。

    已在此前交易日完成二吐的来源交回原有后续波段规则，不把到价变成永久禁买。
    只有当前已触及时才读取该来源此前的前缀，避免为未到价的每日候选重复扫描。
    """
    if not 0 <= source.attack <= source.known_at <= index < len(bars):
        return None
    if not all(isfinite(value) for value in (source.one_p, source.two_t, observed_high, observed_close)):
        return None
    if not 0 < source.one_p < source.two_t or (not at_close and source.known_at == index):
        return None
    observed = observed_close if source.known_at == index else observed_high
    if observed < source.two_t:
        return None
    if any((bars[day].close if day == source.known_at else bars[day].high) >= source.two_t
           for day in range(source.known_at, index)):
        return None
    return dict(reason=TWO_T_ENTRY_REACHED, target_source_attack=source.attack,
        target_source_date=bars[source.attack].timestamp.date().isoformat(),
        target_source_id=source.source_id, target_known_at=source.known_at,
        one_p=source.one_p, two_t=source.two_t, observed_high=observed_high,
        observed_close=observed_close, target_observed_price=observed,
        target_observation_basis='first_known_close' if source.known_at == index else 'observed_session_high')
