"""内部点外壳仍拒绝所有非 exact str 键，不因批量导出跳过输入边界。"""

from typing import cast

import pytest

from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from wavequant.domain.market_structure.trend_point_version import PointVersion


class _StringKey(str):
    pass


@pytest.mark.parametrize('key', (1, True, _StringKey('field')))
def test_point_version_and_owned_export_reject_non_exact_string_keys(key: object) -> None:
    point = cast(dict[str, object], {key: 'invalid'})
    with pytest.raises(ValueError, match='string keys'):
        PointVersion([point])
    with pytest.raises(ValueError, match='string keys'):
        _TrendJudgementCache().export_owned([point], _owned_result=True)


def test_empty_and_many_field_points_preserve_public_validation() -> None:
    points = [{}, {f'field_{field}': field for field in range(100)}]
    version = PointVersion(points).seal()
    assert list(version) == points
    with pytest.raises(TypeError, match='immutable'):
        version.append({'index': 1})
