"""独立追加因果 N 达标证据，保持原结构反转与 N 路线各自的首次可知日。"""

from collections.abc import Mapping, Sequence
from typing import cast

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .n_trend_confirmation import (
    N_TARGET_CONFIRMATION, is_n_target_reversal, prepare_n_confirmation_context, qualified_n_source, qualify_n_trend,
)
from .trend_levels import _strip_source_license


def _order(point: Mapping[str, object]) -> tuple[int, int]:
    return cast(int, point['index']), cast(int, point.get('ordinal', 0))


def _known(point: Mapping[str, object]) -> int | str:
    value = point['available_at']
    if type(value) is int or isinstance(value, str):
        return value
    raise ValueError('N trend candidate needs a known session')


def _later(left: int | str, right: int | str) -> int | str:
    if isinstance(left, str) and isinstance(right, str):
        return max(left, right)
    if type(left) is int and type(right) is int:
        return max(left, right)
    raise ValueError('N candidate clocks must match')


def n_target_reversals(
    candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]],
    bars: Sequence[Bar], *, source_level: int,
    qualified_source: Sequence[Mapping[str, object]] | None = None,
    target_sink: list[dict[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> list[dict[str, object]]:
    """将已严格达标的 N 起点并入候选，不把每次局部摆动升级为新整波。

    连续同向起点保留整波极值，反向 N 则独立提供新的方向证据。同坐标
    已有更早普通反转或关键位升级时，保留其结构确认日期和 incoming
    历史，另附 n_target_confirmation 的独立方向及首次可知日期。
    一个 L 可先是已确认下降端点，随后才成为正 N 达标上涨起点；H 镜像。
    可选路线关闭时移除 N 附加证书，保留这个点原先已经成立的结构事实。
    """
    if not n_target_trend_confirmation_enabled:
        retained = []
        for point in candidates:
            if is_n_target_reversal(point):
                continue
            structural = dict(point)
            structural.pop('n_target_confirmation', None)
            retained.append(structural)
        return retained
    merged = {_order(point): dict(point) for point in candidates}
    if not bars:
        return list(merged.values())
    n_source = qualified_n_source(source, qualified_source) if qualified_source is not None else source
    context = prepare_n_confirmation_context(n_source, bars, len(bars) - 1)
    position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    for position, origin in enumerate(source):
        up = origin.get('kind') == 'L'
        proof = qualify_n_trend(n_source, n_source[position], position, bars, len(bars) - 1, up=up,
                                context=context, target_sink=target_sink)
        if proof is None:
            continue
        trigger = cast(Mapping[str, object], proof['confirmed_by'])
        sign = 1 if up else -1
        failure = next((index for index in range(cast(int, trigger['index']) + 1, len(bars))
                        if sign * ((bars[index].low if up else bars[index].high)
                                   - cast(float, origin['value'])) < 0), None)
        if failure is not None:
            peak_index = max(range(cast(int, origin['index']) + 1, failure),
                             key=lambda index: sign * (bars[index].high if up else bars[index].low))
            extremes = [(index, point) for index, point in enumerate(source)
                        if context.references[index] is not None
                        and _order(origin) < _order(point) and point['index'] == peak_index
                        and point.get('kind') == ('H' if up else 'L')
                        and point.get('state') not in ('seed', 'developing') and not point.get('display_only')]
            if extremes:
                peak_position, peak = max(extremes, key=lambda pair: sign * cast(float, pair[1]['value']))
                failure_known = failure if type(proof['available_at']) is int else session_date(bars[failure])
                peak_known = _later(_known(n_source[peak_position]), failure_known)
                previous_peak = merged.get(_order(peak))
                if previous_peak is None or _later(_known(previous_peak), peak_known) != peak_known:
                    merged[_order(peak)] = dict(_strip_source_license(peak), available_at=peak_known, state='reversal',
                        trend_level=source_level + 1, source_level=source_level,
                        confirmation_rule='n_origin_breach_confirms_wave_endpoint',
                        wave_direction_before='up' if up else 'down', wave_direction_after='down' if up else 'up',
                        flip='翻多为空' if up else '翻空为多', broken_key=proof['origin'],
                        confirmed_by=dict(index=failure, ordinal=0, time=session_date(bars[failure]),
                                          available_at=failure_known, kind='L' if up else 'H',
                                          value=bars[failure].low if up else bars[failure].high, label='N原点破位'),
                        **{position_field: peak_position})
        old = merged.get(_order(origin))
        known = _known(proof)
        if old is not None and _later(_known(old), known) == known:
            # 同坐标已经通过普通趋势或严格高低点升级，不代表后来发生的
            # 独立 N 达标证据不存在。保持原结构首次可知日及 incoming 方向，
            # 将 N 自己的方向和首次可知日单独附上，交给发布层判断后续发展。
            # 例如已确认下降端点 L，随后正 N 严格超 1P，可以从同一个 L
            # 开始上涨；不能把此前下降确认改写为更晚的 N 确认。
            attached = old.get('n_target_confirmation')
            if isinstance(attached, Mapping) and _later(_known(attached), known) == known:
                continue
            merged[_order(origin)] = dict(old, n_target_confirmation=dict(proof))
            continue
        # 新N点和N原点失守的新端点都跨越来源等级，必须先清除下级许可。
        # 上方同坐标old分支保留的是本级旧结构，不能误清其自身早已知证书。
        point = dict(_strip_source_license(origin), available_at=known, state='reversal',
                     trend_level=source_level + 1, source_level=source_level,
                     confirmation_rule=N_TARGET_CONFIRMATION, broken_key=proof['broken_key'],
                     confirmed_by=proof['confirmed_by'], wave_direction_before='down' if up else 'up',
                     wave_direction_after='up' if up else 'down', flip='翻空为多' if up else '翻多为空',
                     n_target_confirmation=proof, **{position_field: position})
        merged[_order(origin)] = point
    result: list[dict[str, object]] = []
    for point in sorted(merged.values(), key=_order):
        if result and point['kind'] == result[-1]['kind']:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) > 0:
                result[-1] = point
            continue
        if result:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) <= 0:
                continue
            point['available_at'] = _later(_known(point), _known(result[-1]))
        result.append(point)
    return result
