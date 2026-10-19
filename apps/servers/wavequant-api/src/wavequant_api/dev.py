"""Development-only process supervisor; reload source in a fresh interpreter."""

from importlib.util import find_spec
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep


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
        result = subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if result.returncode != 0 and child.poll() is None:
            raise RuntimeError('Unable to stop the owned API process tree; refusing overlapping engines')
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            child.kill()
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=5)


def main() -> None:
    args = sys.argv[1:]
    if '--api-only' not in args:
        raise SystemExit('Development reload requires --api-only; use wavequant_api.cli for other commands')
    core = find_spec('wavequant')
    if core is None or not core.submodule_search_locations:
        raise SystemExit('wavequant-core source is unavailable')
    roots = (Path(__file__).resolve().parent, *(Path(path) for path in core.submodule_search_locations))
    snapshot = source_snapshot(roots)
    changed_at: float | None = None
    child = subprocess.Popen([sys.executable, '-m', 'wavequant_api.cli', *args], start_new_session=os.name != 'nt')
    print(f'[wavequant-dev] API PID {child.pid}; watching API and core Python sources', flush=True)
    reported_exit = False
    try:
        while True:
            sleep(1)
            current = source_snapshot(roots)
            if current != snapshot:
                snapshot = current
                changed_at = monotonic()
            if changed_at is not None and monotonic() - changed_at >= 2:
                # 旧规则的在途计算不再有用；先结束所属进程树再启动新版本，避免两套引擎并跑。
                print('[wavequant-dev] Source changed; stopping old backtests and restarting API with a fresh engine', flush=True)
                stop_child(child)
                child = subprocess.Popen([sys.executable, '-m', 'wavequant_api.cli', *args], start_new_session=os.name != 'nt')
                changed_at = None
                reported_exit = False
            elif child.poll() is not None and not reported_exit:
                print(f'[wavequant-dev] API exited ({child.returncode}); waiting for a source fix', flush=True)
                reported_exit = True
    except KeyboardInterrupt:
        pass
    finally:
        stop_child(child)


if __name__ == '__main__':
    main()
