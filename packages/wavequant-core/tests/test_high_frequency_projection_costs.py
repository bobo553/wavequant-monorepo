"""高频迭代和层级投影保留行情顺序、字段修订及历史版本。"""

from datetime import datetime, timedelta
from types import MappingProxyType

import pytest

from wavequant.domain.market_structure.trend_lines import _TrendPermissionCache
from wavequant.domain.market_structure.trend_lifecycle import _pivot
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars


def test_certified_market_iteration_bypasses_per_item_python_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2000, 1, 1)
    values = tuple(Bar(start + timedelta(days=index), 'TEST', 9., 10., 8., 9., 100.)
                   for index in range(6000))
    market = ValidatedBars(values)
    window = market[100:5900]

    def forbidden(*args: object) -> object:
        raise AssertionError('iteration must consume the certified immutable tuple')

    monkeypatch.setattr(ValidatedBars, '__getitem__', forbidden)
    assert list(market) == list(values)
    assert all(actual is expected for actual, expected in zip(window, values[100:5900], strict=True))


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('field,replacement', (
    ('index', 1), ('ordinal', 1), ('kind', 'L'), ('value', 20.), ('available_at', 6001),
))
def test_large_permission_projection_detects_each_real_field_revision(
    level: int, field: str, replacement: object,
) -> None:
    cache = _TrendPermissionCache(())
    rows = [dict(index=index, ordinal=0, kind='H', value=10., available_at=index)
            for index in range(6000)]
    first = cache.hierarchy_projection(level, 'path', rows)
    assert cache.hierarchy_projection(level, 'path', rows) is first
    revised = [*rows]
    revised[3000] = dict(rows[3000], **{field: replacement})
    following = cache.hierarchy_projection(level, 'path', revised)
    assert list(first) == rows
    assert list(following) == revised
    assert following.changed_from == 3000
    # 缺失 ordinal 与显式零保留原投影默认值语义。
    same = [dict(row) for row in revised]
    del same[100]['ordinal']
    assert cache.hierarchy_projection(level, 'path', same) is following


@pytest.mark.parametrize('wrapped', (False, True))
def test_pivot_fast_path_keeps_mapping_support_and_knowledge_boundary(wrapped: bool) -> None:
    row = dict(index=3, ordinal=0, value=10., kind='H', available_at=5)
    value = MappingProxyType(row) if wrapped else row
    assert _pivot(value, {}, 4) is None
    known = _pivot(value, {}, 5)
    assert known is not None and known.index == 3 and known.known == 5
    row['index'] = True
    assert _pivot(value, {}, 5) is None
