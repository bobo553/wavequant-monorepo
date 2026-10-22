"""暂存加速只复用字节；每次读取仍有独立证据容器和解码行为。"""

from typing import cast

import pytest

import wavequant.domain.market_structure.trend_staging as staging


def _identity(value: object) -> object:
    return value


def _row(index: int) -> dict[str, object]:
    shared = [index, ('clock', index), -0.0]
    return {'first': shared, 'second': shared, 'padding': 'proof ' * 20000}


def test_live_read_reuses_written_bytes_but_restores_independent_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decompressions = 0
    decodes = 0
    decompress = staging.decompress

    def counted_decompress(payload: bytes) -> bytes:
        nonlocal decompressions
        decompressions += 1
        return decompress(payload)

    def decode(value: object) -> object:
        nonlocal decodes
        decodes += 1
        return value

    monkeypatch.setattr(staging, 'decompress', counted_decompress)
    with staging.TrendStageRows(decode, memory_bytes=1) as stage:
        source = _row(1)
        stage.write(source)
        first = cast(dict[str, object], list(stage.iter_rows(seal=False))[0])
        assert first['first'] is first['second']
        cast(list[object], first['first']).clear()
        cast(list[object], source['first']).append('changed after write')
        second = cast(dict[str, object], list(stage.iter_rows(seal=False))[0])
        assert second == _row(1)
        assert second['first'] is not first['first']
        assert type(cast(list[object], second['first'])[1]) is tuple
        stage.write(_row(2))
        assert list(stage.iter_rows(seal=False, start_row=1)) == [_row(2)]
        assert decodes == 3
        assert decompressions == 0
    assert not stage._payload_cache
    assert stage._payload_cache_size == 0


@pytest.mark.parametrize('cache_bytes', [0, 64, 2 * 1024 * 1024])
def test_cache_disabled_over_budget_and_enabled_keep_identical_suffixes(cache_bytes: int) -> None:
    with staging.TrendStageRows(_identity, memory_bytes=1, payload_cache_bytes=cache_bytes) as stage:
        for index in range(6):
            stage.write(_row(index))
        assert list(stage.iter_rows(start_row=3)) == [_row(index) for index in range(3, 6)]
        assert list(stage.iter_rows(start_row=4)) == [_row(index) for index in range(4, 6)]
        assert len(stage._payload_cache) <= 2
        if stage._payload_cache:
            assert stage._payload_cache_size + 1024 <= cache_bytes
        else:
            assert stage._payload_cache_size == 0


def test_cached_payload_does_not_hide_a_truncated_stage() -> None:
    with staging.TrendStageRows(_identity) as stage:
        stage.write(_row(1))
        stage._stream.truncate(stage.stored_bytes - 1)
        with pytest.raises(ValueError, match='incomplete row payload'):
            list(stage.iter_rows())


def test_decode_failure_releases_reader_and_keeps_fresh_decoding() -> None:
    calls = 0

    def decode(value: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError('decoder failed')
        return value

    with staging.TrendStageRows(decode) as stage:
        stage.write(_row(1))
        with pytest.raises(ValueError, match='decoder failed'):
            list(stage.iter_rows(seal=False))
        assert list(stage.iter_rows(seal=False)) == [_row(1)]
        stage.write(_row(2))
        assert list(stage.iter_rows(seal=False, start_row=1)) == [_row(2)]


@pytest.mark.parametrize('cache_bytes', [-1, True, 1.5])
def test_payload_cache_rejects_invalid_limits(cache_bytes: object) -> None:
    with pytest.raises(ValueError, match='nonnegative integer'):
        staging.TrendStageRows(_identity, payload_cache_bytes=cast(int, cache_bytes))


def test_private_readonly_versions_are_reused_without_changing_public_reads() -> None:
    decodes = 0

    def decode(value: object) -> object:
        nonlocal decodes
        decodes += 1
        return value

    with staging.TrendStageRows(decode) as stage:
        stage.write(_row(1))
        first = list(stage.iter_rows(seal=False, _borrow_readonly=True))[0]
        again = list(stage.iter_rows(seal=False, _borrow_readonly=True))[0]
        assert first is again
        assert decodes == 1
        public = cast(dict[str, object], list(stage.iter_rows(seal=False))[0])
        cast(list[object], public['first']).clear()
        assert first == _row(1)
        assert list(stage.iter_rows(seal=False))[0] == _row(1)
        stage.write(_row(2))
        latest = list(stage.iter_rows(seal=False, start_row=1, _borrow_readonly=True))[0]
        assert latest == _row(2) and latest is not first
        assert 0 < stage._readonly_cache.memory_used <= 4 * 1024 * 1024
        stage._stream.truncate(stage.stored_bytes - 1)
        with pytest.raises(ValueError, match='incomplete row payload'):
            list(stage.iter_rows(seal=False, start_row=1, _borrow_readonly=True))
    assert stage._readonly_cache.memory_used == 0


def test_readonly_cache_checks_same_length_payload_revisions() -> None:
    with staging.TrendStageRows(_identity) as stage:
        stage.write({'value': 1})
        first = list(stage.iter_rows(seal=False, _borrow_readonly=True))[0]
        replacement = staging.dumps({'value': 2}, protocol=staging.HIGHEST_PROTOCOL)
        assert len(replacement) + staging._HEADER.size == stage.stored_bytes
        stage._stream.seek(staging._HEADER.size)
        stage._stream.write(replacement)
        stage._stream.seek(stage.stored_bytes)
        second = list(stage.iter_rows(seal=False, _borrow_readonly=True))[0]
        assert first == {'value': 1}
        assert second == {'value': 2}


def test_unchanged_serialized_blocks_are_not_compressed_again(monkeypatch):
    calls = []
    original = staging.compress

    def record(payload, **kwargs):
        calls.append(payload)
        return original(payload, **kwargs)

    monkeypatch.setattr(staging, 'compress', record)
    shared = [1, ('clock', 2), -0.0]
    first = dict(padding=b'x' * (staging._BLOCK_BYTES * 4), first=shared, second=shared, tail=1)
    second = dict(first, tail=2)
    with staging.TrendStageRows(_identity, memory_bytes=1) as stage:
        stage.write(first)
        prior_calls = len(calls)
        stage.write(second)
        assert len(calls) - prior_calls < 4
        actual = cast(list[dict[str, object]], list(stage.iter_rows()))
        assert actual == [first, second]
        assert actual[0]['first'] is actual[0]['second']
        assert actual[0]['first'] is not actual[1]['first']
        assert stage._compression_bytes <= stage._compression_limit
        assert stage._compression_bytes + stage._payload_cache_size <= staging._PAYLOAD_CACHE_BYTES
    assert not stage._compression_cache
    assert stage._compression_bytes == 0


@pytest.mark.parametrize('payload', (b'x', staging._BLOCK_HEADER.pack(65537, 1) + b'x'))
def test_invalid_compressed_block_records_are_rejected(payload):
    with pytest.raises(ValueError, match='block'):
        staging.TrendStageRows._decompress_blocks(payload)


def test_long_row_scan_retains_compression_prefix_instead_of_thrashing(monkeypatch):
    blocks = [bytes([i]) * staging._BLOCK_BYTES for i in range(20)]
    calls = []
    original = staging.compress

    def record(payload, **kwargs):
        calls.append(payload)
        return original(payload, **kwargs)

    monkeypatch.setattr(staging, 'compress', record)
    with staging.TrendStageRows(_identity, payload_cache_bytes=512 * 1024) as stage:
        first = b''.join(blocks)
        encoded = stage._compress_blocks(first)
        assert stage._decompress_blocks(encoded) == first
        calls.clear()
        following = b''.join([*blocks[:-1], b'z' * staging._BLOCK_BYTES])
        encoded = stage._compress_blocks(following)
        assert stage._decompress_blocks(encoded) == following
        assert blocks[0] not in calls
        assert 2 * stage._compression_bytes + stage._payload_cache_size <= 512 * 1024
