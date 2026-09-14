"""Bounded, lazy access to the optional AkShare package."""

from __future__ import annotations

from queue import Queue
from threading import BoundedSemaphore, Thread
from typing import Any, Callable


class AkShareUnavailable(RuntimeError):
    """The optional provider is missing, busy, timed out, or failed upstream."""


class AkShareProvider:
    """Keep third-party imports and network calls outside domain code.

    Timed-out calls cannot be killed safely in CPython, so each call retains one
    of the small fixed number of slots until its daemon worker actually exits.
    This prevents repeated requests from creating an unbounded thread pile-up.
    """

    def __init__(self, client: Any | None = None, *, timeout: float = 20.0, concurrency: int = 2):
        if not 1 <= timeout <= 60:
            raise ValueError("AkShare timeout must be between 1 and 60 seconds")
        if not 1 <= concurrency <= 4:
            raise ValueError("AkShare concurrency must be between 1 and 4")
        self._client = client
        self.timeout = float(timeout)
        self._slots = BoundedSemaphore(concurrency)

    def _load_client(self) -> Any:
        if self._client is None:
            try:
                import akshare
            except ImportError as exc:
                raise AkShareUnavailable("AkShare 数据源未安装") from exc
            self._client = akshare
        return self._client

    @property
    def version(self) -> str:
        return str(getattr(self._load_client(), "__version__", "unknown"))

    def call(self, name: str, *, call_timeout: float | None = None, **kwargs: Any) -> Any:
        timeout = self.timeout if call_timeout is None else float(call_timeout)
        if not 1 <= timeout <= self.timeout:
            raise ValueError("AkShare call timeout must be between 1 second and the provider timeout")
        if not self._slots.acquire(timeout=timeout):
            raise AkShareUnavailable("AkShare 数据源繁忙，请稍后重试")
        result: Queue[tuple[bool, Any]] = Queue(maxsize=1)

        def run() -> None:
            try:
                method: Callable[..., Any] = getattr(self._load_client(), name)
                result.put((True, method(**kwargs)))
            except Exception as exc:  # provider errors are normalized at this boundary
                result.put((False, exc))
            finally:
                self._slots.release()

        worker = Thread(target=run, daemon=True, name=f"akshare-{name}")
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise AkShareUnavailable("AkShare 上游请求超时，请稍后重试")
        ok, value = result.get_nowait()
        if not ok:
            raise AkShareUnavailable("AkShare 上游请求失败，请稍后重试") from value
        return value
