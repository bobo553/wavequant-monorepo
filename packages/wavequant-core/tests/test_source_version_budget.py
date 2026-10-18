"""来源证书预算保持原阈值，追加不会再次计量全部可见前缀。"""

from collections.abc import Callable
from sys import getsizeof

import pytest

from wavequant.domain.market_structure import trend_judgement
from wavequant.domain.market_structure.trend_judgement import _SourceVersionOwner


def _source(count: int) -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, time=str(index), available_at=index+1,
                 kind='H' if index % 2 else 'L', value=20+index if index % 2 else 10+index,
                 state='confirmed', trend_level=0) for index in range(count)]


def test_appended_source_keeps_the_original_certificate_budget_boundary() -> None:
    source = _source(40)
    measure = _SourceVersionOwner(1, 8*1024*1024)
    assert measure.prepare(source[:-1]) is not None
    version = measure.prepare(source)
    assert version is not None
    required = 4 * sum(getsizeof(value) for value in (*measure.keys, *version.certificates))
    for limit, accepted in ((required-1, False), (required, True)):
        owner = _SourceVersionOwner(1, 8*1024*1024)
        assert owner.prepare(source[:-1]) is not None
        owner.limit = limit
        result = owner.prepare(source)
        assert (result is not None) is accepted
        if accepted:
            assert result is not None
            assert result.source == version.source
            assert result.certificates == version.certificates
        else:
            assert owner.current is None and owner.keys == ()
            assert owner._certificate_prefix_bytes == [0]


def test_append_measures_only_new_visible_certificates(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(100)
    owner = _SourceVersionOwner(1, 8*1024*1024)
    assert owner.prepare(source[:-1]) is not None
    measured_bytes = 0
    original: Callable[[object], int] = trend_judgement.getsizeof

    def record(value: object) -> int:
        nonlocal measured_bytes
        if type(value) is bytes:
            measured_bytes += 1
        return original(value)

    monkeypatch.setattr(trend_judgement, 'getsizeof', record)
    assert owner.prepare(source) is not None
    # 原始可变来源仍须逐点全文认证；可见证书只计相邻边界与新增的一点。
    assert measured_bytes == len(source)+2


def test_source_revision_and_truncation_replace_certificate_prefix_costs() -> None:
    source = _source(20)
    owner = _SourceVersionOwner(1, 8*1024*1024)
    for selected in (source, [dict(point, audit=['revision']) for point in source[:9]], source[:12], source):
        version = owner.prepare(selected)
        assert version is not None
        assert len(owner._certificate_prefix_bytes) == len(version.certificates)+1
        assert owner._certificate_prefix_bytes[-1] == sum(getsizeof(value) for value in version.certificates)
