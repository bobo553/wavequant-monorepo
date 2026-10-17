"""追加交易账本保持原全量稳定排序，活跃准备事实仍允许修订。"""

from datetime import datetime, timedelta
from typing import cast

from wavequant.domain.models.model import Signal
from wavequant.domain.strategies.integrated_strategy import DailySignalState


def _event(day: int, label: str, source: str) -> dict[str, object]:
    return dict(bar_index=day, event=label, source=source)


def test_append_order_matches_full_sort_with_ties_archives_and_preparation_revisions() -> None:
    state = DailySignalState()
    for day in range(8):
        when = datetime(2026, 1, 1) + timedelta(days=day)
        for side in ('LONG', 'EXIT', 'LONG'):
            state.signals.append(Signal(when, 'TEST', day, side, 10., 9., str(day), when, .2, None, 'bull'))
        state.audit.extend((_event(day, 'z', 'decision'), _event(day, 'a', 'decision')))
        if day % 2:
            state.archived_preparation.extend((_event(day - 1, 'a', 'archive'), _event(day, 'a', 'archive')))
        state.preparation_audit = [_event(day, 'a', 'active'), _event(max(0, day - 2), 'x', str(day))]
        rows = [*state.archived_preparation, *state.preparation_audit, *state.audit]
        actual = state.ordered_result({'days': day + 1})
        assert actual.signals == sorted(state.signals, key=lambda value: (value.timestamp, value.side))
        assert actual.audit == sorted(rows, key=lambda row: (cast(int, row['bar_index']), cast(str, row['event'])))
        assert actual.counts == {'days': day + 1}
        assert len(actual.audit) == len(rows)
        assert state.ordered_result({'days': day + 1}) == actual


def test_returned_list_containers_do_not_modify_append_order() -> None:
    state = DailySignalState(audit=[_event(2, 'a', 'decision'), _event(1, 'b', 'decision')])
    first = state.ordered_result({})
    first.audit.clear()
    second = state.ordered_result({})
    assert [row['bar_index'] for row in second.audit] == [1, 2]
    assert len(state.audit) == 2
