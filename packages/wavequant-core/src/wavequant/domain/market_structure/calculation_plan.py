"""后端回测步骤偏好及其满足数据依赖的执行计划。"""

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


# 前四项是同一交易日内的传播关系，不是四次全历史扫描。
STREAM_STEPS = (1, 3, 10, 17)
BACKTEST_STEP_ORDER = (*STREAM_STEPS, 2, 24)


@dataclass(frozen=True)
class CalculationPlan:
    """保留旧偏好用于审计；实际几何按同日依赖传播，六态无独立任务。"""

    requested_order: tuple[int, ...]
    execution_order: tuple[int, ...]


_active_plan: ContextVar[CalculationPlan | None] = ContextVar('backtest_calculation_plan', default=None)


def resolve_calculation_plan(order: Sequence[int] | None = None) -> CalculationPlan:
    """兼容旧编号配置，但不再允许把六态或各级历史拆成独立串行任务。

    原 24 个编号继续标识缓存产物。市场流固定按折线→一级→二级→三级
    同日传播，并在该日完成 N 测幅、策略及账户；旧请求顺序只保留作审计元数据。
    """
    if order is None:
        active = _active_plan.get()
        if active is not None:
            return active
        order = BACKTEST_STEP_ORDER
    if not isinstance(order, Sequence) or isinstance(order, (str, bytes, bytearray)):
        raise ValueError('calculation order must be a sequence of step numbers')

    requested: list[int] = []
    seen: set[int] = set()
    for step in order:
        if type(step) is not int or not 1 <= step <= 24:
            raise ValueError('calculation step must be an integer from 1 through 24')
        if step in seen:
            raise ValueError(f'duplicate calculation step: {step}')
        requested.append(step)
        seen.add(step)
    requested.extend(step for step in range(1, 25) if step not in seen)

    return CalculationPlan(tuple(requested), (*STREAM_STEPS, 2, 24))


@contextmanager
def _using_plan(plan: CalculationPlan) -> Iterator[CalculationPlan]:
    token = _active_plan.set(plan)
    try:
        yield plan
    finally:
        _active_plan.reset(token)


def using_calculation_order(order: Sequence[int]) -> AbstractContextManager[CalculationPlan]:
    """为当前执行上下文记录配置，退出或异常时恢复；不能拆开事件传播链。

    调用本函数时立即验证并复制编号，因此尚未进入 with 也能拒绝非法配置，
    调用方后续修改列表不会改变已构造计划。覆盖不修改模块默认配置。
    """
    return _using_plan(resolve_calculation_plan(order))
