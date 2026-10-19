"""公开图表出口必须能直接进入 HTTP/ArtifactCache 的严格 JSON 编码。"""

import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
from wavequant.domain.market_structure.trend_pipeline import calculate_trend_hierarchy
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache, canonical
from wavequant.interfaces.charts.visualization import ChartRepository
from tests.test_chart_target_windows import market
from tests.test_staged_trend_pipeline import _nested_bars


@pytest.mark.parametrize('n_enabled', [False, True])
def test_public_hierarchy_is_json_serializable_and_cacheable(tmp_path: Path, n_enabled: bool) -> None:
    with owned_evidence_scope():
        hierarchy = calculate_trend_hierarchy(_nested_bars(),
            n_target_trend_confirmation_enabled=n_enabled)
    restored = json.loads(json.dumps(hierarchy, allow_nan=False))
    assert set(restored) == set(hierarchy)
    cache = ArtifactCache(tmp_path)
    key = {'n_enabled': n_enabled}
    cache.put('geometry', key, hierarchy)
    assert cache.get('geometry', key) == restored
    for name in ('reversal_trends', 'secondary_trends', 'tertiary_trends'):
        for field in ('strokes', 'source_strokes', 'candidate_strokes'):
            for stroke in hierarchy[name][field]:
                assert type(stroke['points']) is list


def test_supplied_owned_geometry_is_exported_without_mutating_internal_versions(tmp_path: Path) -> None:
    bars = market()
    point = dict(index=0, ordinal=0, time='2026-01-01', kind='L', value=8.,
                 available_at=1, evidence={'known_at': 1})
    candidates = PointVersion([point]).seal()
    legs = PointVersion([dict(points=[point, dict(point, kind='H', value=12.)])]).seal()
    with owned_evidence_scope() as owner:
        sources = CertifiedSource([point], [b'first'], owner, None)
        geometry = dict(reversal_trends=dict(
            candidate_strokes=[dict(points=candidates)],
            source_strokes=[dict(points=sources)],
            strokes=[dict(points=candidates, structural_legs=legs)]), tertiary_trends={})
        theory = ChartRepository.render_theory(None, bars, SystemStrategy(), SystemResult([], [], {}),
            bars[-1].timestamp.date().isoformat(), geometry=geometry)
        cache = ArtifactCache(tmp_path)
        cache.put('adjusted_theory', {'symbol': 'TEST'}, theory)
        assert cache.get('adjusted_theory', {'symbol': 'TEST'}) == json.loads(canonical(theory))
        exported = theory['reversal_trends']['candidate_strokes'][0]['points']
        assert type(exported) is list and exported == list(candidates)
        exported[0]['evidence']['known_at'] = 99
        assert cast(dict[str, object], candidates[0]['evidence'])['known_at'] == 1
        assert geometry['reversal_trends']['candidate_strokes'][0]['points'] is candidates
        assert type(theory['reversal_trends']['source_strokes'][0]['points']) is list
        assert type(theory['reversal_trends']['strokes'][0]['structural_legs']) is list
