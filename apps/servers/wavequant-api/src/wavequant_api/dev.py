"""Development-only process supervisor; reload source in a fresh interpreter."""

from importlib.util import find_spec
from argparse import ArgumentParser
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen


def source_snapshot(roots: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    rows = []
    for root in roots:
        for path in sorted(root.rglob('*.py')):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            rows.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


def stop_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    if os.name == 'nt':
        # Windows venv python.exe 可能另启解释器，必须结束本服务的整棵子进程树。
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def active_backtests(port: int) -> int | None:
    """状态未知不能视作空闲；只查询自己启动的 loopback API。"""
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/backtest-jobs', timeout=2) as response:
            data = json.load(response)
        active = data.get('active') if isinstance(data, dict) else None
        return active if type(active) is int and active >= 0 else None
    except (OSError, URLError, ValueError):
        return None


def main() -> None:
    args = sys.argv[1:]
    if '--api-only' not in args:
        raise SystemExit('Development reload requires --api-only; use wavequant_api.cli for other commands')
    parser = ArgumentParser(add_help=False)
    parser.add_argument('--port', type=int, default=8765)
    port = parser.parse_known_args(args)[0].port
    core = find_spec('wavequant')
    if core is None or not core.submodule_search_locations:
        raise SystemExit('wavequant-core source is unavailable')
    roots = (Path(__file__).resolve().parent, *(Path(path) for path in core.submodule_search_locations))
    snapshot = source_snapshot(roots)
    changed_at: float | None = None
    child = subprocess.Popen([sys.executable, '-m', 'wavequant_api.cli', *args])
    print(f'[wavequant-dev] API PID {child.pid}; watching API and core Python sources', flush=True)
    reported_exit = False
    waiting_for_idle = False
    try:
        while True:
            sleep(1)
            current = source_snapshot(roots)
            if current != snapshot:
                snapshot = current
                changed_at = monotonic()
            if changed_at is not None and monotonic() - changed_at >= 2:
                # 磁盘版本保护已阻止旧引擎接新策略，已有任务保留到结束。
                if child.poll() is None and active_backtests(port) != 0:
                    if not waiting_for_idle:
                        print('[wavequant-dev] Source changed; waiting for active backtests to finish before reload', flush=True)
                        waiting_for_idle = True
                    continue
                print('[wavequant-dev] Source changed; restarting API with a fresh engine', flush=True)
                stop_child(child)
                child = subprocess.Popen([sys.executable, '-m', 'wavequant_api.cli', *args])
                changed_at = None
                reported_exit = False
                waiting_for_idle = False
            elif child.poll() is not None and not reported_exit:
                print(f'[wavequant-dev] API exited ({child.returncode}); waiting for a source fix', flush=True)
                reported_exit = True
    except KeyboardInterrupt:
        pass
    finally:
        stop_child(child)


if __name__ == '__main__':
    main()
