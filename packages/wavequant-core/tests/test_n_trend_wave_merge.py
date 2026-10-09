"""Optional N certificates retain their own identity alongside the wave display policy."""

from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_publication import trend_wave_identity

from .test_n_trend_levels import _points, _public, _records, _sample


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('up', (True, False))
def test_n_wave_identity_matches_the_published_live_wave_and_uses_complete_n_evidence(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    view = _public(level, bars, source, up=up)
    origin = next(point for point in _points(view) if point['index'] == 1)
    proof = cast(dict[str, object], origin['trend_confirmation'])
    tail = _records(view['developing_strokes'])[0]
    identity = trend_wave_identity(proof, trend_level=level, source_path=cast(str, tail['source_path']))
    assert identity is not None and tail['wave_id'] == identity
    relabeled = deepcopy(proof)
    cast(dict[str, object], relabeled['origin'])['label'] = 'display label only'
    assert trend_wave_identity(relabeled, trend_level=level, source_path=cast(str, tail['source_path'])) == identity
    assert trend_wave_identity(proof, trend_level=level, source_path='another-source') != identity
    for field in ('n_neckline', 'n_pullback', 'n_completion', 'box_anchor', 'one_p_target'):
        incomplete = dict(proof)
        incomplete.pop(field)
        assert trend_wave_identity(incomplete, trend_level=level, source_path=cast(str, tail['source_path'])) is None
