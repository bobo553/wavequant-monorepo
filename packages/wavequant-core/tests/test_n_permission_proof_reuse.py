from copy import deepcopy
from dataclasses import replace

import pytest

from wavequant.domain.market_structure import n_trend_confirmation as module
from wavequant.domain.market_structure.n_trend_confirmation import (
    _NPermissionProofs, _reuse_n_permissions, qualify_n_trend,
)
from wavequant.domain.market_structure.trend_lines import _TrendPermissionCache
from .test_n_trend_confirmation import sample


@pytest.mark.parametrize('dates', [False, True])
@pytest.mark.parametrize('up', [False, True])
def test_confirmed_n_proof_survives_stopped_demand_without_recomputing_n(monkeypatch, dates, up):
    bars, source = sample(dates=dates, up=up)
    proofs = _NPermissionProofs()
    with _reuse_n_permissions(proofs, bars[:11], allow_new=True):
        expected = qualify_n_trend(source, source[1], 1, bars[:11], 10, up=up)
    assert expected is not None

    def forbidden(*args, **kwargs):
        raise AssertionError('an already confirmed N must not be replayed')

    monkeypatch.setattr(module, 'prepare_n_confirmation_context', forbidden)
    monkeypatch.setattr(module, '_observation', forbidden)
    revised_container = deepcopy(source)
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        actual = qualify_n_trend(revised_container, revised_container[1], 1, bars, 11, up=up)
        assert actual == expected
        assert qualify_n_trend(source, source[0], 0, bars, 11, up=not up) is None
    actual['one_p_target'] = -1
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 11, up=up) == expected


def test_n_work_does_not_start_before_this_level_requests_confirmation(monkeypatch):
    bars, source = sample()

    def forbidden(*args, **kwargs):
        raise AssertionError('no pending own-level bull flip')

    monkeypatch.setattr(module, 'prepare_n_confirmation_context', forbidden)
    with _reuse_n_permissions(_NPermissionProofs(), bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


@pytest.mark.parametrize('revision', ['source_price', 'source_clock', 'source_visibility', 'market'])
def test_n_proof_rejects_revised_source_and_market_without_starting_unrequested_work(revision):
    bars, source = sample()
    proofs = _NPermissionProofs()
    with _reuse_n_permissions(proofs, bars, allow_new=True):
        assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is not None
    if revision == 'source_price':
        source[3]['value'] = 6.1
    elif revision == 'source_clock':
        source[3]['available_at'] = 8
    elif revision == 'source_visibility':
        source[3]['state'] = 'unqualified'
    else:
        bars[10] = replace(bars[10], low=4.9)
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 11, up=True) is None


def test_n_proof_reuse_keeps_first_target_observations_and_prefix_boundary():
    bars, source = sample()
    proofs = _NPermissionProofs()
    original_targets = []
    with _reuse_n_permissions(proofs, bars, allow_new=True):
        proof = qualify_n_trend(source, source[1], 1, bars, 10, up=True, target_sink=original_targets)
    assert proof is not None and original_targets
    repeated_targets = []
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 9, up=True) is None
        assert qualify_n_trend(source, source[1], 1, bars, 11, up=True,
            target_sink=repeated_targets) == proof
    assert repeated_targets == original_targets


def test_later_source_append_cannot_bypass_the_original_source_order_contract():
    bars, source = sample()
    proofs = _NPermissionProofs()
    with _reuse_n_permissions(proofs, bars, allow_new=True):
        assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is not None
    # 后知但更早的坐标不在旧证明前缀内；它仍使本次完整来源不合法。
    source.append(dict(source[2], available_at=11))
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 11, up=True) is None


def test_closed_permission_cache_does_not_cross_own_level_n_demand_modes():
    bars, _source = sample()
    cache = _TrendPermissionCache(bars)
    prefix = cache.prefix(bars)
    assert prefix is not None
    cache.put((1, 'path'), [], [], None, prefix, len(bars), True, ([], [], [], []), n_demand=True)
    assert cache.get((1, 'path'), [], [], None, prefix, len(bars), True, n_demand=True) is not None
    assert cache.get((1, 'path'), [], [], None, prefix, len(bars), True, n_demand=False) is None
    assert cache.get((1, 'path'), [], [], None, prefix, len(bars), True) is None


def test_partial_tail_revision_keeps_proofs_known_before_the_revised_day(monkeypatch):
    bars, source = sample()
    proofs = _NPermissionProofs()
    with _reuse_n_permissions(proofs, bars, allow_new=True):
        expected = qualify_n_trend(source, source[1], 1, bars, 10, up=True)
    assert expected is not None
    bars[11] = replace(bars[11], close=bars[11].open)

    def forbidden(*args, **kwargs):
        raise AssertionError('a later partial candle cannot erase a prior confirmed proof')

    monkeypatch.setattr(module, '_qualify_n_trend', forbidden)
    with _reuse_n_permissions(proofs, bars, allow_new=False):
        assert qualify_n_trend(source, source[1], 1, bars, 11, up=True) == expected
