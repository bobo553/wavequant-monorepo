"""逐日信号消费时钟；策略状态由消费函数的闭包持有。"""

from collections.abc import Callable


class SignalClock:
    """拒绝跳日、重复和倒序，避免重复消耗同一买点或丢失目标达成状态。"""

    def __init__(self, size: int, consume: Callable[[int], None]) -> None:
        if type(size) is not int or size < 0:
            raise ValueError('signal clock requires a nonnegative market size')
        self._size = size
        self._consume = consume
        self._next = 0
        self._failed = False
        self._advancing = False

    def advance(self, index: int) -> None:
        if self._failed:
            raise RuntimeError('a failed signal clock cannot be reused')
        if self._advancing:
            raise RuntimeError('a signal clock cannot advance recursively')
        if type(index) is not int or index != self._next or index >= self._size:
            raise ValueError('signal clock requires consecutive market dates')
        self._advancing = True
        try:
            self._consume(index)
        except Exception:
            self._failed = True
            raise
        finally:
            self._advancing = False
        self._next += 1

    def finish(self) -> None:
        if self._failed or self._advancing or self._next != self._size:
            raise RuntimeError('signal clock has not completed its market history')
