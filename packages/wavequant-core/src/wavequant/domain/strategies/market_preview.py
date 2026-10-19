"""Request-local read-only observation of completed market days."""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from ..models.model import Bar
from .market_calculations import MarketDay

MarketObserver = Callable[[Sequence[Bar], MarketDay, Mapping[str, object] | None], None]
_observer: ContextVar[MarketObserver | None] = ContextVar('market_preview', default=None)


@contextmanager
def observe_market_preview(observer: MarketObserver) -> Iterator[None]:
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def publish_market_preview(bars: Sequence[Bar], day: MarketDay,
                           result: Mapping[str, object] | None = None) -> None:
    observer = _observer.get()
    if observer is not None:
        observer(bars, day, result)
