"""开发重载等待已运行回测结束，不以查询失败冒充空闲。"""

from io import BytesIO

import pytest

from wavequant_api import dev


@pytest.mark.parametrize('payload, expected', [
    (b'{"active": 2}', 2), (b'{"active": 0}', 0),
    (b'{"active": true}', None), (b'{}', None), (b'[]', None), (b'broken', None),
])
def test_active_backtests_requires_explicit_nonnegative_count(monkeypatch, payload, expected):
    def response(url, timeout):
        assert url == 'http://127.0.0.1:8766/api/backtest-jobs'
        assert timeout == 2
        return BytesIO(payload)

    monkeypatch.setattr(dev, 'urlopen', response)
    assert dev.active_backtests(8766) == expected


def test_reload_does_not_stop_busy_or_unknown_api(monkeypatch):
    children = []
    stopped = []
    observed = []
    snapshots = iter([('old',), ('new',), ('new',), ('new',)])
    ticks = iter([1.0, 4.0, 5.0, 6.0])
    counts = iter([2, None, 0])

    class Child:
        pid = 123

        def poll(self):
            return None

    def spawn(*args):
        child = Child()
        children.append(child)
        return child

    def pause(seconds):
        if len(observed) == 3:
            raise KeyboardInterrupt

    def active(port):
        count = next(counts)
        observed.append(count)
        assert stopped == []
        return count

    monkeypatch.setattr(dev.sys, 'argv', ['dev', '--api-only', '--port', '8766'])
    monkeypatch.setattr(dev, 'source_snapshot', lambda roots: next(snapshots))
    monkeypatch.setattr(dev, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(dev, 'sleep', pause)
    monkeypatch.setattr(dev, 'active_backtests', active)
    monkeypatch.setattr(dev.subprocess, 'Popen', spawn)
    monkeypatch.setattr(dev, 'stop_child', stopped.append)
    dev.main()
    assert observed == [2, None, 0]
    assert len(children) == 2
    assert stopped == children
