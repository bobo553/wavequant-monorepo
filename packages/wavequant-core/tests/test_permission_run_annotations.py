"""许可尾段修订不应重新标注独立的旧段，编号与日期修订仍严格失效。"""

from copy import deepcopy
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure.trend_readonly_cache import _ReadonlySnapshotCache


def _points() -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, time=str(index), available_at=index,
                 kind=kind, value=value, label=f'input-{index}')
            for index, (kind, value) in enumerate(zip('LHLHHLH', (10, 20, 12, 22, 25, 15, 27), strict=True))]


@pytest.mark.parametrize('level', [1, 2, 3])
def test_revised_last_permission_run_reuses_the_first_run(level: int) -> None:
    cache = _ReadonlySnapshotCache[list[dict[str, object]]](memory_bytes=1024 * 1024, max_entries=16)
    dates = {index: index for index in range(12)}
    points = _points()
    trend_lines._annotate_permissions(deepcopy(points), 'TEST', dates, trend_level=level,
                                     _cache=cache, _cache_key=(level, 'path'))
    points[-1]['value'] = 28
    expected, observed = deepcopy(points), deepcopy(points)
    trend_lines._annotate_permissions(expected, 'TEST', dates, trend_level=level)
    with patch.object(trend_lines, '_annotate_geometry', wraps=trend_lines._annotate_geometry) as annotate:
        trend_lines._annotate_permissions(observed, 'TEST', dates, trend_level=level,
                                         _cache=cache, _cache_key=(level, 'path'))
    assert observed == expected
    assert [len(call.args[0]) for call in annotate.call_args_list] == [3]


@pytest.mark.parametrize('revision', ['prefix', 'clock', 'boundary'])
def test_run_cache_preserves_clock_labels_and_changed_boundaries(revision: str) -> None:
    cache = _ReadonlySnapshotCache[list[dict[str, object]]](memory_bytes=1024 * 1024, max_entries=16)
    dates = {index: index for index in range(12)}
    points = _points()
    trend_lines._annotate_permissions(deepcopy(points), 'TEST', dates, trend_level=2,
                                     _cache=cache, _cache_key=(2, 'path'))
    if revision == 'prefix':
        points[0]['value'] = 9
    elif revision == 'clock':
        dates = {index: index + 1 for index in range(12)}
    else:
        points.pop(3)
    expected, observed = deepcopy(points), deepcopy(points)
    trend_lines._annotate_permissions(expected, 'TEST', dates, trend_level=2)
    trend_lines._annotate_permissions(observed, 'TEST', dates, trend_level=2,
                                     _cache=cache, _cache_key=(2, 'path'))
    assert observed == expected


