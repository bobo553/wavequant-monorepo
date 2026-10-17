"""同一次同步计算的趋势选项；线程与嵌套请求各自隔离。"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from typing import ParamSpec, TypeVar

_P = ParamSpec('_P')
_R = TypeVar('_R')


_ORDINARY_COMPLETION: ContextVar[bool] = ContextVar('ordinary_trend_completion', default=False)


def ordinary_completion_enabled() -> bool:
    return _ORDINARY_COMPLETION.get()


@contextmanager
def ordinary_trend_completion(enabled: bool) -> Iterator[None]:
    """仅显式勾选才允许普通升降确认整波；退出及异常时恢复外层选择。"""
    if type(enabled) is not bool:
        raise ValueError('ordinary_trend_completion_enabled must be a boolean')
    token = _ORDINARY_COMPLETION.set(enabled)
    try:
        yield
    finally:
        _ORDINARY_COMPLETION.reset(token)


def with_trend_options(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """同步图表入口接收可选覆盖；未传参的内部调用继承请求作用域。"""
    parameters = signature(function)

    @wraps(function)
    def call(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        selected = parameters.bind(*args, **kwargs).arguments.get('ordinary_trend_completion_enabled')
        enabled = ordinary_completion_enabled() if selected is None else selected
        if not isinstance(enabled, bool):
            raise ValueError('ordinary_trend_completion_enabled must be a boolean')
        with ordinary_trend_completion(enabled):
            return function(*args, **kwargs)
    return call


def with_strategy_trend_options(parameter: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """同步策略入口以本次显式配置绑定选项，不修改封存配置或进程全局值。"""
    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        parameters = signature(function)

        @wraps(function)
        def call(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            config = parameters.bind(*args, **kwargs).arguments[parameter]
            enabled = (config.get('ordinary_trend_completion_enabled', False) if isinstance(config, Mapping)
                       else getattr(config, 'ordinary_trend_completion_enabled', False))
            with ordinary_trend_completion(enabled):
                return function(*args, **kwargs)
        return call
    return decorate
