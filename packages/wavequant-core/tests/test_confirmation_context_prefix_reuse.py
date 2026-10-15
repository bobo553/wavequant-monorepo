"""静态解析复用必须保留每个真实市场前缀的证书和拒绝边界。"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.trend_confirmation import (
    SAME_LEVEL_KEY_BREAK,
    SOURCE_CYCLE_CONFIRMATION,
    ConfirmationContext,
    _SuccessfulConfirmations,
    prepare_confirmation_context,
    qualify_downtrend,
    qualify_uptrend,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex


Clock = Literal['integer', 'date', 'mixed']
Kind = Literal['H', 'L']
START = datetime(2024, 1, 1)


def _reference(bars: Sequence[Bar], index: int, kind: Kind, known: int, clock: Clock) -> dict[str, object]:
    date_clock = clock == 'date' or clock == 'mixed' and index % 2 == 1
    return dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(), kind=kind,
                value=bars[index].high if kind == 'H' else bars[index].low,
                available_at=bars[known].timestamp.date().isoformat() if date_clock else known,
                state='confirmed', label=f'{kind}{index}')


def _sample(*, clock: Clock, up: bool = True, n: bool = False) -> tuple[list[Bar], list[dict[str, object]]]:
    # 沿用全局许可/N 许可测试已验证的最小行情：普通周期于 index7 转向；N 的
    # one-P=10 在 index9 仅相等，到 index10 才严格突破。无需完整历史或外部数据。
    prices = ([(20., 19., 19.5), (6., 5., 5.5), (6.3, 5.5, 6.), (7., 6.4, 6.7),
               (6.8, 6.2, 6.5), (6.5, 6., 6.2), (7., 6.2, 6.6), (7.5, 6.6, 7.2),
               (9.8, 7., 9.5), (10., 9., 9.8), (10.1, 9., 9.9)] if n else
              [(10., 8., 9.), (7., 5., 6.), (9., 6., 8.), (11., 8., 10.5), (10.8, 9., 10.2),
               (9.5, 8., 9.), (10.5, 8.5, 10.), (12., 9., 11.5), (11.8, 9.5, 11.3)])
    bars = [Bar(START + timedelta(days=index), 'sz.test', (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    if not up:
        bars = [Bar(bar.timestamp, bar.symbol, 30 - bar.open, 30 - bar.low, 30 - bar.high,
                    30 - bar.close, bar.volume) for bar in bars]
    anchors: tuple[tuple[int, Kind, int], ...] = ((0, 'H', 0), (1, 'L', 2), (3, 'H', 4), (5, 'L', 6))
    source = [_reference(bars, index, kind if up else ('L' if kind == 'H' else 'H'), known, clock)
              for index, kind, known in anchors]
    return bars, source


def _evidence(proof: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = proof[name]
    assert isinstance(value, Mapping)
    return value


def _assert_same_prefix(
    source: Sequence[Mapping[str, object]], bars: Sequence[Bar] | None, cutoff: int,
    snapshot: ConfirmationContext, *, up: bool,
    key: Mapping[str, object] | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None, enabled: bool = False,
) -> tuple[ConfirmationContext, dict[str, object] | None]:
    """比较旧入口、独立前缀上下文与复用投影的完整公开证书。"""
    cold = prepare_confirmation_context(source, bars, cutoff, n_source=n_source,
                                        n_target_trend_confirmation_enabled=enabled)
    reused = prepare_confirmation_context(source, bars, cutoff, n_source=n_source,
                                          n_target_trend_confirmation_enabled=enabled, parsed_context=snapshot)
    assert reused.end_index == cold.end_index == cutoff
    assert reused.bars == cold.bars
    assert reused.session_indices == cold.session_indices
    assert reused.references == cold.references
    assert reused.ordered == cold.ordered
    # N 仍独立观察真实前缀；比较其可观察状态，不能因 ValidatedBars 包装的对象
    # 身份不同而误判，也不能仅比较最终 None 掩盖内部使用了完整市场快照。
    if cold.n_context is None:
        assert reused.n_context is None
    else:
        assert reused.n_context is not None
        assert reused.n_context.end_index == cold.n_context.end_index == cutoff
        assert tuple(reused.n_context.market) == tuple(cold.n_context.market)
        assert reused.n_context.sessions == cold.n_context.sessions
        assert reused.n_context.references == cold.n_context.references
        assert reused.n_context.ordered == cold.n_context.ordered
        assert reused.n_context.low_failures == cold.n_context.low_failures
        assert reused.n_context.high_failures == cold.n_context.high_failures
    qualify = qualify_uptrend if up else qualify_downtrend
    legacy = qualify(source, source[1], 1, key, bars, cutoff, n_source=n_source,
                     n_target_trend_confirmation_enabled=enabled)
    assert qualify(source, source[1], 1, key, bars, cutoff, context=cold, n_source=n_source,
                   n_target_trend_confirmation_enabled=enabled) == legacy
    assert qualify(source, source[1], 1, key, bars, cutoff, context=reused, n_source=n_source,
                   n_target_trend_confirmation_enabled=enabled) == legacy
    return reused, legacy


@pytest.mark.parametrize('clock', ('integer', 'date', 'mixed'))
@pytest.mark.parametrize('up', (False, True))
@pytest.mark.parametrize('own_key', (False, True))
@pytest.mark.parametrize('enabled', (False, True))
def test_each_prefix_preserves_the_first_own_key_or_complete_cycle_certificate(
    clock: Clock, up: bool, own_key: bool, enabled: bool,
) -> None:
    bars, source = _sample(clock=clock, up=up)
    snapshot = prepare_confirmation_context(source, bars, len(bars) - 1,
                                            n_target_trend_confirmation_enabled=enabled)
    first = 3 if own_key else 7
    for cutoff in range(len(bars)):
        _, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=up,
                                       key=source[0] if own_key else None, enabled=enabled)
        if cutoff < first:
            assert proof is None
            continue
        assert proof is not None
        assert proof['confirmation_rule'] == (SAME_LEVEL_KEY_BREAK if own_key else SOURCE_CYCLE_CONFIRMATION)
        assert proof['direction'] == ('up' if up else 'down')
        assert _evidence(proof, 'confirmed_by')['index'] == first
        date_clock = clock in ('date', 'mixed')
        assert proof['available_at'] == (bars[first].timestamp.date().isoformat() if date_clock else first)
        assert _evidence(proof, 'broken_key')['index'] == 0
        if not own_key:
            assert _evidence(proof, 'flip_high' if up else 'flip_low')['index'] == 3
            assert _evidence(proof, 'alternation_low' if up else 'alternation_high')['index'] == 5
            assert proof['retracement_ratio'] == .5


def test_duplicate_session_dates_are_resolved_against_each_real_prefix() -> None:
    bars, source = _sample(clock='date')
    bars[5] = replace(bars[5], timestamp=bars[4].timestamp + timedelta(hours=12))
    # Jan5 在 index4/index5 各出现一次。完整快照将 Jan5 映射到5，但截止4时
    # 其已知位置必须是4；简单过滤 full.known 会把已知 H3 错当成未来点。
    source[2]['available_at'] = bars[4].timestamp.date().isoformat()
    source[3]['time'] = bars[5].timestamp.date().isoformat()
    snapshot = prepare_confirmation_context(source, bars, 8)
    full_high = snapshot.references[2]
    assert full_high is not None and full_high.known == 5
    for cutoff in range(len(bars)):
        projected, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=True)
        if cutoff == 4:
            high = projected.references[2]
            assert high is not None and high.known == 4
        if cutoff >= 7:
            assert proof is not None and _evidence(proof, 'confirmed_by')['index'] == 7


@pytest.mark.parametrize('clock', ('integer', 'date'))
@pytest.mark.parametrize('disorder', ('position', 'known'))
def test_later_known_disorder_cannot_poison_an_earlier_legal_prefix(clock: Clock, disorder: str) -> None:
    bars, source = _sample(clock=clock)
    if disorder == 'position':
        source.append(_reference(bars, 2, 'H', 8, clock))
    else:
        source[2]['available_at'] = bars[8].timestamp.date().isoformat() if clock == 'date' else 8
    snapshot = prepare_confirmation_context(source, bars, 8)
    assert snapshot.ordered is False
    for cutoff in range(len(bars)):
        projected, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=True)
        if cutoff < 8:
            assert projected.ordered is True
        else:
            assert projected.ordered is False and proof is None
        if disorder == 'position' and cutoff == 7:
            assert proof is not None and _evidence(proof, 'confirmed_by')['index'] == 7
        if disorder == 'known':
            assert proof is None


@pytest.mark.parametrize('malformed', (
    {'index': True}, {'index': -1}, {'index': 20}, {'ordinal': -1}, {'ordinal': True},
    {'kind': 'K'}, {'value': True}, {'value': float('nan')}, {'value': 0},
    {'display_only': True}, {'state': 'developing'}, {'available_at': True},
    {'available_at': 2}, {'available_at': 20}, {'available_at': '2024-1-05'},
    {'available_at': '2024-01-20'}, {'available_at': None},
))
def test_invalid_or_future_flip_never_becomes_a_cycle_certificate(malformed: dict[str, object]) -> None:
    bars, source = _sample(clock='integer')
    source[2].update(malformed)
    snapshot = prepare_confirmation_context(source, bars, 8)
    for cutoff in range(len(bars)):
        _, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=True)
        assert proof is None


@pytest.mark.parametrize('clock', ('integer', 'date', 'mixed'))
@pytest.mark.parametrize('up', (False, True))
@pytest.mark.parametrize('enabled', (False, True))
def test_no_market_keeps_only_the_known_source_own_key_route(clock: Clock, up: bool, enabled: bool) -> None:
    bars, source = _sample(clock=clock, up=up)
    snapshot = prepare_confirmation_context(source, None, 8, n_target_trend_confirmation_enabled=enabled)
    for cutoff in range(9):
        projected, cycle = _assert_same_prefix(source, None, cutoff, snapshot, up=up, enabled=enabled)
        assert projected.bars is None and projected.n_context is None
        assert cycle is None
        _, own = _assert_same_prefix(source, None, cutoff, snapshot, up=up, key=source[0], enabled=enabled)
        # 无市场时 date 点必须由调用方提供已知前缀，既有入口不会把日期猜成整数
        # 截止日。仍按 occurrence index 过滤，且不能补出 source-cycle/N 许可证。
        first = 4 if clock == 'integer' else 3
        if cutoff < first:
            assert own is None
        else:
            assert own is not None and own['confirmation_rule'] == SAME_LEVEL_KEY_BREAK
            assert _evidence(own, 'confirmed_by')['index'] == 3
            date_high = clock != 'integer'
            assert own['available_at'] == (bars[4].timestamp.date().isoformat() if date_high else 4)


@pytest.mark.parametrize('clock', ('integer', 'date'))
@pytest.mark.parametrize('up', (False, True))
@pytest.mark.parametrize('enabled', (False, True))
@pytest.mark.parametrize('separate_n_source', (False, True))
def test_n_switch_and_distinct_source_preserve_the_strict_target_and_its_first_date(
    clock: Clock, up: bool, enabled: bool, separate_n_source: bool,
) -> None:
    bars, source = _sample(clock=clock, up=up, n=True)
    n_source = [dict(point) for point in source] if separate_n_source else None
    snapshot = prepare_confirmation_context(source, bars, 10, n_source=n_source,
                                            n_target_trend_confirmation_enabled=enabled)
    for cutoff in range(len(bars)):
        _, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=up, n_source=n_source, enabled=enabled)
        if not enabled or cutoff < 10:
            assert proof is None
        else:
            assert proof is not None and proof['confirmation_rule'] == N_TARGET_CONFIRMATION
            assert proof['direction'] == ('up' if up else 'down')
            assert _evidence(proof, 'confirmed_by')['index'] == 10
            assert proof['available_at'] == ('2024-01-11' if clock == 'date' else 10)
            assert proof['one_p_target'] == (10 if up else 20)


@pytest.mark.parametrize('clock', ('integer', 'date'))
@pytest.mark.parametrize('up', (False, True))
def test_a_later_disordered_n_source_does_not_suppress_the_earlier_target(clock: Clock, up: bool) -> None:
    bars, source = _sample(clock=clock, up=up, n=True)
    bars.append(replace(bars[-1], timestamp=START + timedelta(days=11)))
    n_source = [dict(point) for point in source]
    n_source.append(_reference(bars, 2, 'H' if up else 'L', 11, clock))
    snapshot = prepare_confirmation_context(source, bars, 11, n_source=n_source,
                                            n_target_trend_confirmation_enabled=True)
    assert snapshot.ordered is True
    assert snapshot.n_context is not None and snapshot.n_context.ordered is False
    # 几何来源仍有序，但独立 N 来源在截止索引11才收到旧位置点。截止索引10已取得的
    # N 目标不能被完整快照中的未来乱序抑制；截止索引11仍保留旧入口拒绝乱序的门禁。
    for cutoff in range(len(bars)):
        projected, proof = _assert_same_prefix(source, bars, cutoff, snapshot, up=up,
                                               n_source=n_source, enabled=True)
        assert projected.n_context is not None
        assert projected.n_context.ordered is (cutoff < 11)
        if cutoff == 10:
            assert proof is not None and proof['confirmation_rule'] == N_TARGET_CONFIRMATION
            assert _evidence(proof, 'confirmed_by')['index'] == 10
        else:
            assert proof is None


@pytest.mark.parametrize('enabled', (False, True))
@pytest.mark.parametrize('binding', ('source', 'bars', 'n_source', 'flag', 'cutoff', 'none_market'))
def test_a_parsed_snapshot_rejects_another_identity_mode_or_later_cutoff(enabled: bool, binding: str) -> None:
    bars, source = _sample(clock='integer', n=True)
    n_source = [dict(point) for point in source]
    snapshot = prepare_confirmation_context(source, bars, 7, n_source=n_source,
                                            n_target_trend_confirmation_enabled=enabled)
    source_arg: Sequence[Mapping[str, object]] = [dict(point) for point in source] if binding == 'source' else source
    bars_arg: Sequence[Bar] | None = list(bars) if binding == 'bars' else None if binding == 'none_market' else bars
    n_arg = [dict(point) for point in n_source] if binding == 'n_source' else n_source
    flag = not enabled if binding == 'flag' else enabled
    cutoff = 8 if binding == 'cutoff' else 6
    with pytest.raises(ValueError, match='context'):
        prepare_confirmation_context(source_arg, bars_arg, cutoff, n_source=n_arg,
                                     n_target_trend_confirmation_enabled=flag, parsed_context=snapshot)


@pytest.mark.parametrize('cutoff', (-1, True, 11))
def test_reuse_does_not_bypass_an_invalid_market_cutoff(cutoff: int) -> None:
    bars, source = _sample(clock='integer', n=True)
    snapshot = prepare_confirmation_context(source, bars, 10)
    for parsed in (None, snapshot):
        with pytest.raises(ValueError, match='prefix'):
            prepare_confirmation_context(source, bars, cutoff, parsed_context=parsed)


def test_a_projected_context_is_still_bound_to_its_exact_qualification_cutoff() -> None:
    bars, source = _sample(clock='date')
    snapshot = prepare_confirmation_context(source, bars, 8)
    projected, proof = _assert_same_prefix(source, bars, 7, snapshot, up=True)
    assert proof is not None and proof['available_at'] == '2024-01-08'
    # parsed_context 可以投影较短前缀，不代表最终资格context也能跨日期使用。
    # 先得到7日证书，再直接把该上下文用于6/8截止，必须沿用原身份拒绝契约。
    for cutoff in (6, 8):
        with pytest.raises(ValueError, match='context'):
            qualify_uptrend(source, source[1], 1, None, bars, cutoff, context=projected)


@pytest.mark.parametrize('up', (False, True))
def test_terminal_failure_is_reused_only_while_its_actual_source_prefix_is_unchanged(
    up: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars, source = _sample(clock='integer', up=up)
    bars[6] = (replace(bars[6], high=12., close=11.5) if up else
               replace(bars[6], low=18., close=18.5))
    bars[7] = replace(bars[7], low=4.) if up else replace(bars[7], high=26.)
    owner = _MarketRangeIndex.build(tuple(bars))
    assert owner is not None
    qualify = qualify_uptrend if up else qualify_downtrend

    def indexed(end: int) -> tuple[tuple[Bar, ...], ConfirmationContext]:
        prefix = owner.bars[:end+1]
        context = prepare_confirmation_context(source, prefix, end,
            _market_index=_MarketPrefixIndex(owner, prefix))
        return prefix, context

    prefix, context = indexed(7)
    assert qualify(source, source[1], 1, None, prefix, 7, context=context) is None
    reuse = owner.confirmation_reuse
    assert isinstance(reuse, _SuccessfulConfirmations) and reuse.rejections
    prefix, context = indexed(8)
    with monkeypatch.context() as guarded:
        def no_rescan(*args: object, **kwargs: object) -> None:
            raise AssertionError('an unchanged terminal failure was scanned again')
        guarded.setattr(owner, 'first_extreme', no_rescan)
        assert qualify(source, source[1], 1, None, prefix, 8, context=context) is None

    source[3]['available_at'] = 5
    prefix, context = indexed(8)
    actual = qualify(source, source[1], 1, None, prefix, 8, context=context)
    expected = qualify(source, source[1], 1, None, prefix, 8)
    assert actual == expected and actual is not None
    assert _evidence(actual, 'confirmed_by')['index'] == 6


@pytest.mark.parametrize('clock', ('integer', 'date'))
def test_cross_day_reference_parsing_keeps_shorter_cutoffs_and_source_revisions(clock: Clock) -> None:
    bars, source = _sample(clock=clock)
    owner = _MarketRangeIndex.build(tuple(bars))
    assert owner is not None
    for end in (8, 2, 6, 4, 8):
        prefix = owner.bars[:end+1]
        actual = prepare_confirmation_context(source, prefix, end,
            _market_index=_MarketPrefixIndex(owner, prefix))
        expected = prepare_confirmation_context(source, prefix, end)
        assert actual.references == expected.references and actual.ordered == expected.ordered
    source[2].update(value=10.5, label='revised', available_at=7 if clock == 'integer' else '2024-01-08')
    for end in (6, 8):
        prefix = owner.bars[:end+1]
        actual = prepare_confirmation_context(source, prefix, end,
            _market_index=_MarketPrefixIndex(owner, prefix))
        expected = prepare_confirmation_context(source, prefix, end)
        assert actual.references == expected.references and actual.ordered == expected.ordered
