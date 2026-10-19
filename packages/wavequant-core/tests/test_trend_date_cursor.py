"""内部稀疏日期读取只借用本次目标行，公共读取和历史读限保持独立。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest

import wavequant.domain.strategies.chart_entry_history as chart
from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows, _TrendDateCursor
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from wavequant.domain.strategies.market_calculations import MarketDay

from .test_chart_live_permission_reuse import _read, _strategy
from .test_chart_owned_transfer import _typed
from .test_staged_trend_pipeline import _multiple_path_bars


def test_cursor_reads_only_sparse_target_versions_and_never_walks_the_readonly_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded: list[int] = []

    def decode(value: object) -> dict[str, object]:
        row = cast(dict[str, object], value)
        decoded.append(cast(int, row['known']))
        return row

    with TrendChangeRows(decode, row_count=103) as history:
        for position in (0, 2, 10, 50, 100, 101):
            history.write_change(position, dict(known=position, evidence=[position]))
        history.read_limit = 101

        def forbid_pool(*_args: object) -> None:
            raise AssertionError('one consumer must not deep-charge each borrowed history row')

        monkeypatch.setattr(history._rows._readonly_cache, 'put', forbid_pool)
        with history._readonly_cursor() as cursor:
            first = cursor.at(0)
            assert cursor.at(1) is first
            assert cursor.at(9) == dict(known=2, evidence=[2])
            assert cursor.at(100) == dict(known=100, evidence=[100])
            assert decoded == [0, 2, 100]
            assert not history._rows._reading
        assert cursor._current is None
        with pytest.raises(RuntimeError, match='closed'):
            cursor.at(0)


def test_cursor_rechecks_growing_and_shrinking_read_limits_and_restores_the_writer() -> None:
    with TrendChangeRows(lambda value: value, row_count=12) as history:
        history.write_change(0, {'value': ['first']})
        history.read_limit = 1
        with history._readonly_cursor() as cursor:
            first = cursor.at(0)
            history.write_change(5, {'value': ['second']})
            history.read_limit = 10
            assert cursor.at(9) == {'value': ['second']}
            history.read_limit = 5
            with pytest.raises(ValueError, match='readable prefix'):
                cursor.at(9)
            assert cursor.at(4) == first
            history.read_limit = 6
            assert cursor.at(5) == {'value': ['second']}
            for invalid in (-1, 6, True, 1.5):
                with pytest.raises(ValueError, match='readable prefix'):
                    cursor.at(cast(int, invalid))
            history.write_change(10, {'value': ['third']})
            history.read_limit = 12
            assert cursor.at(11) == {'value': ['third']}


def test_cursor_is_independent_of_public_outputs_and_rejects_closed_history() -> None:
    with TrendChangeRows(lambda value: value, row_count=3) as history:
        history.write_change(0, {'value': [1]})
        with history._readonly_cursor() as cursor:
            borrowed = cursor.at(0)
            public = list(history.iter_rows())
            public[0]['value'].append(2)
            assert borrowed == {'value': [1]}
            assert cursor.at(2) is borrowed
            history.close()
            with pytest.raises(RuntimeError, match='closed'):
                cursor.at(0)


def test_cursor_decode_failure_releases_reader_and_forgets_the_previous_row() -> None:
    def decode(value: object) -> dict[str, object]:
        row = cast(dict[str, object], value)
        if row['known'] == 3:
            raise ValueError('broken target')
        return row

    with TrendChangeRows(decode, row_count=8) as history:
        history.write_change(0, dict(known=0))
        history.write_change(3, dict(known=3))
        history.read_limit = 4
        with history._readonly_cursor() as cursor:
            assert cursor.at(0) == dict(known=0)
            with pytest.raises(ValueError, match='broken target'):
                cursor.at(3)
            assert not history._rows._reading
            assert cursor._current is None
            history.write_change(6, dict(known=6))
            history.read_limit = 7
            assert cursor.at(6) == dict(known=6)


def test_cursor_keeps_the_existing_single_reader_constraint() -> None:
    with TrendChangeRows(lambda value: value, row_count=4) as history:
        history.write_change(0, {'known': 0})
        history.write_change(2, {'known': 2})
        history.read_limit = 4
        reader = history._rows.iter_rows(seal=False)
        try:
            next(reader)
            with history._readonly_cursor() as cursor:
                with pytest.raises(ValueError, match='one active'):
                    cursor.at(2)
                assert history._rows._reading
        finally:
            reader.close()
        assert not history._rows._reading


@pytest.mark.parametrize('fail_consumer', (False, True))
def test_chart_reads_geometry_only_on_existing_refresh_dates_and_closes_cursors(
    fail_consumer: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=8)
    last = bars[-1]
    bars.extend(replace(last, timestamp=last.timestamp + timedelta(days=offset)) for offset in range(1, 9))
    observed = False

    def consume(day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal observed
        if len(analysis.bars) != len(bars):
            return
        owner = analysis.calculations
        assert owner is not None
        current = replace(analysis, buy_days=(True,) * len(bars))
        read_dates: list[int] = []
        refresh_dates: list[int] = []
        cursors: list[_TrendDateCursor[object]] = []
        original_at = _TrendDateCursor.at
        original_attach = chart.attach_trend_permissions

        def at(cursor: _TrendDateCursor[object], position: int) -> object:
            cursors.append(cursor)
            read_dates.append(position)
            return original_at(cursor, position)

        def attach(prepared, market, **kwargs):
            refresh_dates.append(len(market) - 1)
            if fail_consumer:
                raise RuntimeError('permission consumer interrupted')
            return original_attach(prepared, market, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(_TrendDateCursor, 'at', at)
            patch.setattr(chart, 'attach_trend_permissions', attach)
            patch.setattr(owner, '_shared_permissions_at', lambda *_args, **_kwargs: None)
            if fail_consumer:
                with pytest.raises(RuntimeError, match='permission consumer interrupted'):
                    _read(current, None)
                assert cursors and all(cursor._closed and cursor._current is None for cursor in cursors)
                assert all(not stage._rows._reading for stage in current.levels)
                observed = True
                return
            actual = deepcopy(_read(current, None))
            assert read_dates == refresh_dates
            assert len(set(read_dates)) < len(bars)
            assert cursors and all(cursor._closed and cursor._current is None for cursor in cursors)
            # 非实时分支逐日顺序读取已保存几何，保留相同的原刷新与资格规则。
            events = {level: owner._lifecycle_events_since(level, 0) for level in (1, 2, 3)}
            patch.setattr(owner, '_lifecycle_events_since', lambda level, start: [
                event for event in events[level] if event['bar_index'] >= start])
            owner.reading_live_day = False
            try:
                expected = _read(current, None)
            finally:
                owner.reading_live_day = True
        assert _typed(actual) == _typed(expected)
        observed = True

    with prepare_backtest_analysis(bars, _strategy(), on_day=consume):
        pass
    assert observed
