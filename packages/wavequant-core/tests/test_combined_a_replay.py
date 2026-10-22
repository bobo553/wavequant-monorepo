from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.combined_a_entry import GeometryLevel, combined_a_contexts_from_geometry
from wavequant.domain.strategies.combined_a_replay import CombinedAReplay


@pytest.mark.parametrize('spill', (False, True))
def test_geometry_archive_recomputes_nonempty_context_after_n_knowledge_revision(spill, request) -> None:
    ranges = [(4., 6.)] * 13
    for index, prices in {0: (1., 2.), 1: (1., 2.), 2: (2.5, 3.), 3: (2., 2.8),
                          4: (3., 4.), 6: (5., 8.), 7: (6., 7.), 8: (5., 7.),
                          9: (6., 8.), 10: (6., 9.), 11: (6., 8.), 12: (7., 8.)}.items():
        ranges[index] = prices
    bars = tuple(Bar(datetime(2025, 1, 1) + timedelta(days=index), 'TEST',
        (low + high) / 2, high, low, (low + high) / 2, 100) for index, (low, high) in enumerate(ranges))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}

    def point(index: int, kind: str) -> dict[str, object]:
        return dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(), kind=kind,
            value=bars[index].high if kind == 'H' else bars[index].low,
            available_at=bars[index].timestamp.date().isoformat(), state='confirmed',
            flip='翻多为空' if kind == 'H' else '翻空为多', observations=[{'unused': object()}])

    drawing: GeometryLevel = {'strokes': []}
    first = cast(GeometryLevel, dict(strokes=[dict(id='first', points=[point(i, kind)
        for i, kind in ((0, 'H'), (1, 'L'), (2, 'H'), (3, 'L'), (4, 'H'), (6, 'H'), (8, 'L'), (10, 'H'))])]))
    second = cast(GeometryLevel, dict(strokes=[dict(id='second', source_path='first',
        points=[point(6, 'H'), point(8, 'L'), point(10, 'H')])]))
    third: GeometryLevel = {'strokes': []}
    audit = [dict(event='n_completed', direction='up', origin=1, neckline=2, pullback=3,
                  bar_index=4, known_at=4, one_p=7., two_t=10.)]
    expected = combined_a_contexts_from_geometry(bars, drawing, first, second, third, dates, 12, audit)
    assert expected
    archive = CombinedAReplay(spill=spill)
    request.addfinalizer(archive.close)
    archive.remember(12, (drawing, first, second, third))
    days = (False,) * 12 + (True,)
    assert archive.recompute(bars, dates, days, 13, audit, None) == {12: expected}
    revised = [dict(audit[0], known_at=13)]
    assert archive.recompute(bars, dates, days, 13, revised, None) == {12: ()}
    # 最后一天先前可能是不完整版本，重放后不能继续保留其旧几何。
    archive.remember(12, (drawing, drawing, drawing, drawing))
    assert archive.recompute(bars, dates, days, 13, audit, None) == {12: ()}
    assert archive.recompute(bars, dates, (True,) * 13, 13, audit, None) is None


def test_geometry_archive_budget_and_unsupported_consumed_field_refuse_repair() -> None:
    archive = CombinedAReplay(memory_bytes=0)
    empty: GeometryLevel = {'strokes': []}
    archive.remember(0, (empty, empty, empty, empty))
    assert not archive._rows
    archive = CombinedAReplay()
    archive.remember(0, (empty, empty, empty, empty))
    unknown = cast(GeometryLevel, {'strokes': [{'id': 'path', 'points': [{'index': object()}]}]})
    archive.remember(0, (unknown, empty, empty, empty))
    assert not archive._rows


def test_attack_metadata_ignores_superseded_unknown_proof_and_bounds_records() -> None:
    from hashlib import sha256

    from wavequant.domain.strategies.backtest_history_reuse import _certificate_bytes
    from wavequant.domain.strategies.combined_a_replay import attack_certificates, later_attack_revision

    last = dict(event='n_completed', direction='up', bar_index=4, known_at=4, proof={'version': 2})
    replaced = dict(last, proof=object())
    saved = attack_certificates([replaced, last], 8)
    assert saved == {4: sha256(_certificate_bytes(last)).digest()}
    assert later_attack_revision([replaced, dict(last, known_at=9)], saved, 8)
    assert not later_attack_revision([replaced, dict(last, known_at=7)], saved, 8)
    assert attack_certificates([dict(last, bar_index=index) for index in range(4097)], 4096) is None


def test_full_archive_stops_transforming_future_rows_and_refuses_partial_repair(monkeypatch) -> None:
    import wavequant.domain.strategies.combined_a_replay as replay_module

    archive = CombinedAReplay(memory_bytes=4700)
    empty: GeometryLevel = {'strokes': []}
    archive.remember(0, (empty, empty, empty, empty))
    assert archive._rows
    archive.remember(1, (empty, empty, empty, empty))
    assert not archive._rows

    def forbidden(*args: object) -> None:
        raise AssertionError('an incomplete archive retried geometry projection after its budget filled')

    monkeypatch.setattr(replay_module, '_geometry', forbidden)
    archive.remember(2, (empty, empty, empty, empty))
    assert archive.recompute((), {}, (), 0, (), None) is None


def test_cold_replays_keep_budget_refusal_without_reencoding_old_prefix(monkeypatch) -> None:
    import wavequant.domain.strategies.combined_a_replay as replay_module

    archive = CombinedAReplay(memory_bytes=4700)
    empty: GeometryLevel = {'strokes': []}
    archive.remember(0, (empty,) * 4)
    archive.remember(1, (empty,) * 4)
    assert not archive._rows

    def forbidden(*args: object) -> None:
        raise AssertionError('cold replay rebuilt an archive already refused in this request')

    monkeypatch.setattr(replay_module, '_geometry', forbidden)
    for _ in range(3):
        reused = CombinedAReplay.for_replay(archive, resume_start=0)
        assert reused is archive
        reused.remember(0, (empty,) * 4)
        assert reused.recompute((), {}, (), 0, (), None) is None
    assert CombinedAReplay.for_replay(None, resume_start=0) is not archive


def test_cold_replay_replaces_admitted_history_and_warm_replay_keeps_it() -> None:
    archive = CombinedAReplay()
    assert CombinedAReplay.for_replay(archive, resume_start=1) is archive
    assert CombinedAReplay.for_replay(archive, resume_start=0) is not archive


def test_disk_archive_keeps_large_rows_with_bounded_index_and_closes():
    empty: GeometryLevel = {'strokes': []}
    points = [{'index': i, 'kind': 'H', 'value': float(i), 'available_at': str(i)} for i in range(2000)]
    large = cast(GeometryLevel, {'strokes': [{'id': 'path', 'points': points}]})
    archive = CombinedAReplay(memory_bytes=8192, spill=True)
    try:
        for index in range(4):
            archive.remember(index, (large, empty, empty, empty))
        assert archive._accepting
        assert len(archive._rows) == 4
        assert archive._bytes <= 8192
        assert archive._disk_bytes > 8192
        archive.remember(1, (empty,) * 4)
        assert archive.recompute((), {}, (False, True), 2, (), None) == {1: ()}
        stream = archive._stream
        assert stream is not None and not stream.closed
    finally:
        archive.close()
    assert stream.closed
    assert archive.recompute((), {}, (), 0, (), None) is None


def test_disk_archive_caps_storage_and_closes_on_refusal():
    archive = CombinedAReplay(spill=True)
    archive._disk_limit = 1
    archive.remember(0, ({'strokes': []},) * 4)
    assert not archive._accepting
    assert archive._stream is not None and archive._stream.closed
    assert not archive._rows


@pytest.mark.parametrize('fail', (False, True))
def test_request_scope_closes_replay_stream_on_success_and_failure(fail):
    from wavequant.domain.strategies.market_calculations import market_calculations

    archive = CombinedAReplay(spill=True)
    bars = [Bar(datetime(2025, 1, 1), 'TEST', 5., 6., 4., 5., 100)]
    try:
        with market_calculations(bars) as calculation:
            calculation._strategy_resources.callback(archive.close)
            if fail:
                raise RuntimeError('consumer failed')
    except RuntimeError as error:
        assert fail and str(error) == 'consumer failed'
    assert archive._stream is not None and archive._stream.closed

@pytest.mark.parametrize('failure', ('create', 'write', 'truncated'))
def test_optional_disk_failure_falls_back_without_partial_geometry(monkeypatch, failure):
    import wavequant.domain.strategies.combined_a_replay as module

    empty: GeometryLevel = {'strokes': []}
    if failure == 'create':
        def unavailable(**kwargs):
            raise OSError('temporary storage unavailable')
        monkeypatch.setattr(module, 'TemporaryFile', unavailable)
    archive = CombinedAReplay(spill=True)
    try:
        if failure == 'write':
            def failed_write(payload):
                raise OSError('temporary storage full')
            assert archive._stream is not None
            monkeypatch.setattr(archive._stream, 'write', failed_write)
        archive.remember(0, (empty,) * 4)
        if failure == 'create':
            assert archive._stream is None
            assert archive.recompute((), {}, (True,), 1, (), None) == {0: ()}
        elif failure == 'write':
            assert not archive._accepting
            assert archive._stream is not None and archive._stream.closed
        else:
            assert archive._stream is not None
            archive._stream.truncate(1)
            assert archive.recompute((), {}, (True,), 1, (), None) is None
            assert not archive._accepting and archive._stream.closed
    finally:
        archive.close()
