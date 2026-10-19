"""策略变更直接终止旧引擎；停止失败时不得并行启动新引擎。"""

from types import SimpleNamespace

import pytest

from wavequant_api import dev


def test_reload_stops_running_child_before_starting_new_engine(monkeypatch):
    events = []
    children = []
    snapshots = iter([('old',), ('new',), ('new',)])
    ticks = iter([1.0, 1.0, 4.0])

    class Child:
        def poll(self):
            return None

    def spawn(*args, **kwargs):
        child = Child()
        child.pid = 100 + len(children)
        children.append(child)
        events.append(('start', child.pid))
        assert kwargs['start_new_session'] == (dev.os.name != 'nt')
        return child

    def pause(seconds):
        if len(children) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(dev.sys, 'argv', ['dev', '--api-only', '--port', '8766'])
    monkeypatch.setattr(dev, 'find_spec', lambda name: SimpleNamespace(submodule_search_locations=['core']))
    monkeypatch.setattr(dev, 'source_snapshot', lambda roots: next(snapshots))
    monkeypatch.setattr(dev, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(dev, 'sleep', pause)
    monkeypatch.setattr(dev.subprocess, 'Popen', spawn)
    monkeypatch.setattr(dev, 'stop_child', lambda child: events.append(('stop', child.pid)))
    dev.main()
    assert events == [('start', 100), ('stop', 100), ('start', 101), ('stop', 101)]


def test_reload_never_starts_new_engine_when_old_tree_cannot_stop(monkeypatch):
    children = []
    snapshots = iter([('old',), ('new',), ('new',)])
    ticks = iter([1.0, 1.0, 4.0])

    def spawn(*args, **kwargs):
        child = SimpleNamespace(pid=123, poll=lambda: None)
        children.append(child)
        return child

    def failed_stop(child):
        raise RuntimeError('owned process tree still running')

    monkeypatch.setattr(dev.sys, 'argv', ['dev', '--api-only'])
    monkeypatch.setattr(dev, 'find_spec', lambda name: SimpleNamespace(submodule_search_locations=['core']))
    monkeypatch.setattr(dev, 'source_snapshot', lambda roots: next(snapshots))
    monkeypatch.setattr(dev, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(dev, 'sleep', lambda seconds: None)
    monkeypatch.setattr(dev.subprocess, 'Popen', spawn)
    monkeypatch.setattr(dev, 'stop_child', failed_stop)
    with pytest.raises(RuntimeError, match='still running'):
        dev.main()
    assert len(children) == 1
