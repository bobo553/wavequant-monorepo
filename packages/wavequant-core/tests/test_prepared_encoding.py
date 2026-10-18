import pickle

from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.market_structure.trend_lines import _prepared_payload, _PreparedPathReference


def test_prepared_payload_preserves_closed_references_and_aliases():
    point = {'proof': [1, 2, 3]}
    reference = _PreparedPathReference('owner', 3)
    row = {'left': point, 'right': point, 'closed': reference}
    payload = _prepared_payload(row)
    assert payload is not None
    restored = pickle.loads(payload)
    assert restored == row
    assert restored['left'] is restored['right']


def test_encoded_changes_keep_random_access_and_independent_public_results():
    with TrendChangeRows(lambda value: value, row_count=4) as rows:
        for position in (0, 2):
            payload = _prepared_payload({'position': position, 'proof': [1, 2, 3]})
            assert payload is not None
            rows._write_encoded_change(position, payload)
        results = list(rows.iter_rows())
        assert [row['position'] for row in results] == [0, 0, 2, 2]
        results[0]['proof'].append(4)
        assert results[1]['proof'] == [1, 2, 3]
        assert rows._at(3) == {'position': 2, 'proof': [1, 2, 3]}


def test_encoding_refuses_unknown_hooks_before_original_fallback():
    called = []
    class Unknown:
        def __reduce__(self):
            called.append(True)
            return int, (1,)
    assert _prepared_payload({'audit': Unknown()}) is None
    assert called == []
