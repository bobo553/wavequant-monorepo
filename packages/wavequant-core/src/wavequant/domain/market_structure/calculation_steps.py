"""Request-local observation ports; recording never changes trading decisions."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from ..models.model import Bar
from .calculation_plan import CalculationPlan


STEP_TITLES = (
    '算技术折线', '计算 N 字目标位',
    '计算一级趋势线', '一级·空翻多位置', '一级·空多交替位置', '一级·确认转多位置',
    '一级·多翻空位置', '一级·多空交替位置', '一级·确认转空位置',
    '计算二级趋势线', '二级·空翻多位置', '二级·空多交替位置', '二级·确认转多位置',
    '二级·多翻空位置', '二级·多空交替位置', '二级·确认转空位置',
    '计算三级趋势线', '三级·空翻多位置', '三级·空多交替位置', '三级·确认转多位置',
    '三级·多翻空位置', '三级·多空交替位置', '三级·确认转空位置',
    '多级协同与 B/S 信号及回测',
)
LIFECYCLE_STEPS = tuple(i for i in range(4, 24) if i not in (10, 17))
STATE_NAMES = ('空翻多', '空多交替', '转多确认', '多翻空', '多空交替', '转空确认')


class CalculationSteps(Protocol):
    def bind(self, bars: Sequence[Bar]) -> bool: ...
    def set_plan(self, plan: CalculationPlan) -> None: ...
    def begin(self, step: int) -> None: ...
    def append(self, step: int, row: Mapping[str, object]) -> None: ...
    def complete(self, step: int) -> None: ...


_current: ContextVar[CalculationSteps | None] = ContextVar('calculation_steps', default=None)


@contextmanager
def observe_steps(recorder: CalculationSteps) -> Iterator[None]:
    token = _current.set(recorder)
    try:
        yield
    finally:
        _current.reset(token)


def calculation_steps(bars: Sequence[Bar]) -> CalculationSteps | None:
    recorder = _current.get()
    return recorder if recorder is not None and recorder.bind(bars) else None


def record_lifecycle(
    recorder: CalculationSteps, events: Sequence[Mapping[str, object]], *, only_step: int | None = None,
) -> None:
    """按稳定业务编号保存事件；调度顺序不能改变状态所属的缓存。"""
    if only_step is not None and (type(only_step) is not int or only_step not in LIFECYCLE_STEPS):
        raise ValueError('only_step must be a lifecycle step')
    for event in events:
        level, state = event.get('trend_level'), event.get('state')
        if type(level) is not int or level not in (1, 2, 3):
            continue
        if isinstance(state, str) and state in STATE_NAMES:
            number = 4 + (level - 1) * 7 + STATE_NAMES.index(state)
            if only_step is None or number == only_step:
                recorder.append(number, event)
        elif event.get('event') == 'trend_lifecycle_invalidated':
            for offset in range(6):
                number = 4 + (level - 1) * 7 + offset
                if only_step is None or number == only_step:
                    recorder.append(number, event)
