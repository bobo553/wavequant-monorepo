"""Changed source semantics cannot resume old geometry or causal checkpoints."""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache, canonical
from wavequant.interfaces.charts.chart_geometry import cached_geometry


class _Cache(Protocol):
    def get(self, namespace: str, inputs: Mapping[str, object]) -> object: ...
    def put(self, namespace: str, inputs: Mapping[str, object], data: object) -> bool: ...


def _bars() -> list[Bar]:
    rows = ((12., 14., 10., 13.), (14., 16., 12., 15.), (13., 14., 11., 12.),
            (12., 16.1, 10., 12.), (13., 17., 12., 15.), (13., 14., 9., 10.),
            (14., 18., 13., 16.), (13., 14., 8., 9.))
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'CACHE', *row, 1000.)
            for index, row in enumerate(rows)]


@pytest.mark.parametrize('old_version', ('v118', 'v119'))
def test_hierarchy_rejects_old_checkpoint_even_with_an_identical_bar_prefix(old_version: str) -> None:
    bars = _bars()
    cache: dict[str, object] = {}
    structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    expected = hierarchical_history(bars, prefix_cache=cache, structure_sink=structures)
    key = cache['key']
    assert isinstance(key, tuple)
    current = 'level_first_polyline_owned_completed_source_geometry_and_independent_permissions_v120'
    assert key[0] == current
    stale: dict[str, object] = dict(cache, key=(current.removesuffix('v120') + old_version, *key[1:]),
                                    checkpoint=object())
    replayed: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    assert hierarchical_history(bars, prefix_cache=stale, structure_sink=replayed) == expected
    assert replayed == structures
    assert stale['key'] == key


@pytest.mark.parametrize('n_enabled', (False, True))
def test_persistent_geometry_ignores_v119_artifact_without_deleting_it(
    tmp_path: Path, n_enabled: bool,
) -> None:
    bars = _bars()
    cache = cast(Callable[[Path], _Cache], ArtifactCache)(tmp_path)
    rows = [dict(asdict(bar), timestamp=bar.timestamp.isoformat()) for bar in bars]
    canonical_rows = cast(Callable[[object], str], canonical)(rows)
    old_key = dict(bars_sha256=hashlib.sha256(canonical_rows.encode()).hexdigest(),
                   price_basis='raw_unadjusted', engine='same-engine',
                   computation_policy='complete_level_polyline_owned_closed_source_v119',
                   n_target_trend_confirmation_enabled=n_enabled)
    old_result = {'obsolete_source': 'outside_doji_anchor_crossing_reset'}
    assert cache.put('geometry', old_key, old_result)
    result = cached_geometry(cache, bars, 'raw_unadjusted', 'same-engine',
                             n_target_trend_confirmation_enabled=n_enabled)
    assert result != old_result
    current_key = dict(old_key, computation_policy='complete_level_polyline_owned_closed_source_v120')
    assert cache.get('geometry', current_key) == result
    assert cached_geometry(cache, bars, 'raw_unadjusted', 'same-engine',
                           n_target_trend_confirmation_enabled=n_enabled) == result
    assert cache.get('geometry', old_key) == old_result
