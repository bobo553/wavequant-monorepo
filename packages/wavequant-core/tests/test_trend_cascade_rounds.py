"""每个日期仍从空升级证明收敛，轮次缓存不得互相改写来源版本。"""

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_cascade import _TrendCascadeCache, prepare_trend_cascade
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache, polyline_high_low_points
from wavequant.domain.market_structure.trend_options import ordinary_trend_completion
from wavequant.domain.models.model import Bar


def _source(*, mirror: bool = False) -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50)
    start = datetime(2026, 1, 1)
    return [dict(index=index, ordinal=0, time=(start + timedelta(days=index)).date().isoformat(),
        available_at=index + 1, kind=('H' if mirror else 'L') if index % 2 == 0 else ('L' if mirror else 'H'),
        value=100 - value if mirror else value, trend_level=0, state='reversal', source_path='sample')
        for index, value in enumerate(prices)]


def _real_bars() -> list[Bar]:
    fixture = json.loads((Path(__file__).parent / 'fixtures/huaci_2024_key_break.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(row[0]), fixture['symbol'], *row[1:])
            for row in fixture['bars'] if row[0] <= '2024-04-30']


def _cold_runs(workers: Sequence[_TrendJudgementCache]) -> list[dict[tuple[int, str], tuple[object, int]]]:
    return [{key: (state, state.cold_runs) for key, state in worker._states.items()} for worker in workers]


@pytest.mark.parametrize('max_rounds', (1, 3))
def test_real_promotions_do_not_force_the_next_date_first_round_back_to_cold(max_rounds: int) -> None:
    bars = _real_bars()
    source = polyline_high_low_points(lecture_drawing(bars))
    before = deepcopy(source)
    expected = prepare_trend_cascade(source, symbol='sz.001216', bars=bars)
    workers = [_TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024) for _ in range(3)]
    with _TrendCascadeCache(workers, max_rounds=max_rounds) as rounds:
        actual = prepare_trend_cascade(source, symbol='sz.001216', bars=bars,
            caches=workers, _round_caches=rounds)
        assert actual == expected
        initial = _cold_runs(workers)
        assert any(initial)
        if max_rounds > 1:
            assert len(rounds._rounds) > 1
        repeated = prepare_trend_cascade(deepcopy(source), symbol='sz.001216', bars=bars,
            caches=workers, _round_caches=rounds)
        assert repeated == expected
        # 第零轮来源再次相同；后续轮次的旧端点证明不应迫使它重放前史。
        assert _cold_runs(workers) == initial
    assert source == before


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('ordinary', (False, True))
def test_round_cache_matches_every_cold_prefix_rollback_and_earlier_revision(mirror: bool, ordinary: bool) -> None:
    source = _source(mirror=mirror)
    workers = [_TrendJudgementCache(tail_reuse=True) for _ in range(3)]
    with ordinary_trend_completion(ordinary), _TrendCascadeCache(workers) as rounds:
        for end in (*range(1, len(source) + 1), 10, len(source)):
            prefix = deepcopy(source[:end])
            expected = prepare_trend_cascade(prefix, symbol='ROUND')
            actual = prepare_trend_cascade(prefix, symbol='ROUND', caches=workers, _round_caches=rounds)
            assert actual == expected
            assert prefix == source[:end]
        revised = deepcopy(source)
        revised[0]['audit'] = {'earlier_revision': ['retained']}
        assert prepare_trend_cascade(revised, symbol='ROUND', caches=workers, _round_caches=rounds) == (
            prepare_trend_cascade(revised, symbol='ROUND'))


def test_extra_rounds_keep_one_path_share_exports_and_release_owned_state() -> None:
    source = _source()
    base = _TrendJudgementCache(tail_reuse=True)
    base.judge(source, path='base', trend_level=1, _owned_source=True)
    with _TrendCascadeCache([base]) as rounds:
        first_extra = rounds.workers_for(1)
        second_extra = rounds.workers_for(2)
        assert first_extra is not None and second_extra is not None
        sibling = first_extra[0]
        assert sibling._finite is base._finite
        assert sibling._point_exports is base._point_exports
        assert rounds.workers_for(3) is None
        assert rounds.workers_for(1000) is None
        for path in ('closed-a', 'closed-b', 'active'):
            assert sibling.judge(source, path=path, trend_level=1, _owned_source=True) == judge_trend(source)
            assert set(sibling._states) == {(1, path)}
            assert set(sibling._source_owners) == {(1, path)}
            assert set(sibling._version_results) == {(1, path)}
        assert len(rounds._rounds) == 3
    assert not sibling._states
    assert not sibling._source_owners
    assert not sibling._version_results
    assert (1, 'base') in base._states
    with pytest.raises(RuntimeError, match='closed'):
        rounds.workers_for(0)


def test_source_flip_freezes_each_round_own_version_without_sharing_checkpoints() -> None:
    source = _source()
    base = _TrendJudgementCache(tail_reuse=True)
    with _TrendCascadeCache([base]) as rounds:
        states = []
        for round_index in range(3):
            workers = rounds.workers_for(round_index)
            assert workers is not None
            version = deepcopy(source)
            version[0]['audit'] = {'round': round_index}
            workers[0].judge(version, path='sample', trend_level=1, _owned_source=True)
            states.append(workers[0]._states[1, 'sample'])
        rounds.remember_source_flip(1, 'sample')
        for state in states:
            assert state._flip_checkpoint is not None
            assert state._flip_checkpoint[0] == state._source_keys
        assert len({state._source_keys for state in states}) == 3


def test_rule_switch_invalidates_each_round_without_losing_shared_export_budget() -> None:
    source = _source()
    base = _TrendJudgementCache(tail_reuse=True)
    with _TrendCascadeCache([base]) as rounds:
        siblings = rounds.workers_for(1)
        assert siblings is not None
        sibling = siblings[0]
        for ordinary in (False, True, False):
            with ordinary_trend_completion(ordinary):
                assert sibling.judge(source, path='sample', trend_level=1, _owned_source=True) == judge_trend(source)
                assert sibling._finite is base._finite
                assert sibling._point_exports is base._point_exports
                assert sibling._max_paths == 1
