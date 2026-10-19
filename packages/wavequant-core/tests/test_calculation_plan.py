"""后端步骤顺序调整必须遵守数据依赖及请求隔离。"""

import asyncio
from collections.abc import Sequence
from typing import cast

import pytest

from wavequant.domain.market_structure import calculation_plan
from wavequant.domain.market_structure.calculation_plan import (
    CalculationPlan,
    resolve_calculation_plan,
    using_calculation_order,
)


def test_default_plan_preserves_the_24_business_steps() -> None:
    plan = resolve_calculation_plan()
    assert set(plan.requested_order) == set(range(1, 25))
    assert plan.execution_order == (1, 3, 10, 17, 2, 24)


def test_geometry_can_precede_lifecycle_and_n_targets_can_run_later() -> None:
    plan = resolve_calculation_plan((17, 23, 16, 9, 2))
    assert plan.requested_order[:5] == (17, 23, 16, 9, 2)
    assert plan.execution_order == (1, 3, 10, 17, 2, 24)
    assert plan.execution_order[-1] == 24
    assert set(plan.execution_order) == {1, 2, 3, 10, 17, 24}


def test_reversed_preference_cannot_start_trading_before_all_market_steps() -> None:
    plan = resolve_calculation_plan(tuple(range(24, 0, -1)))
    assert plan.requested_order == tuple(range(24, 0, -1))
    assert plan.execution_order == (1, 3, 10, 17, 2, 24)


@pytest.mark.parametrize('order', [(), (24,), (22, 8, 12, 2), (3, 10, 17, 2), (9, 8, 7, 6, 5, 4)])
def test_every_plan_keeps_required_inputs_before_consumers(order: tuple[int, ...]) -> None:
    plan = resolve_calculation_plan(order)
    positions = {step: index for index, step in enumerate(plan.execution_order)}
    assert len(plan.requested_order) == 24
    assert len(plan.execution_order) == len(positions) == 6
    assert positions[1] < positions[2]
    assert positions[1] < positions[3] < positions[10] < positions[17]
    assert positions[17] < positions[2] < positions[24]


def test_lifecycle_outputs_have_no_artificial_mutual_dependencies() -> None:
    plan = resolve_calculation_plan((9, 8, 7, 6, 5, 4))
    assert plan.execution_order == (1, 3, 10, 17, 2, 24)


@pytest.mark.parametrize('order', [(1, 1), (True,), (False,), (0,), (-1,), (25,), (1.0,), ('3',)])
def test_invalid_step_numbers_are_rejected(order: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        resolve_calculation_plan(cast(Sequence[int], order))


@pytest.mark.parametrize('order', ['123', b'123', {1, 2}, {1: 2}, 3])
def test_non_sequence_configuration_is_rejected(order: object) -> None:
    with pytest.raises(ValueError):
        resolve_calculation_plan(cast(Sequence[int], order))


def test_backend_constant_is_the_default_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calculation_plan, 'BACKTEST_STEP_ORDER', (17, 2))
    plan = resolve_calculation_plan()
    assert plan.requested_order[:2] == (17, 2)
    assert plan.execution_order[:5] == (1, 3, 10, 17, 2)


def test_nested_override_restores_outer_plan_even_after_failure() -> None:
    baseline = resolve_calculation_plan()
    with using_calculation_order((17,)) as outer:
        assert resolve_calculation_plan() is outer
        with pytest.raises(RuntimeError, match='calculation interrupted'):
            with using_calculation_order((9,)) as inner:
                assert resolve_calculation_plan() is inner
                assert inner != outer
                raise RuntimeError('calculation interrupted')
        assert resolve_calculation_plan() is outer
    assert resolve_calculation_plan() == baseline


def test_override_validates_immediately_and_copies_mutable_input() -> None:
    with pytest.raises(ValueError):
        using_calculation_order((1, 1))
    order = [17]
    context = using_calculation_order(order)
    order[:] = [9]
    with context as plan:
        assert plan.requested_order[0] == 17
        assert resolve_calculation_plan() is plan


def test_explicit_resolution_does_not_change_active_request() -> None:
    with using_calculation_order((17,)) as active:
        assert resolve_calculation_plan((9,)).requested_order[0] == 9
        assert resolve_calculation_plan() is active


def test_concurrent_requests_keep_their_own_order() -> None:
    baseline = resolve_calculation_plan()

    async def observe(
        preference: tuple[int, ...], ready: asyncio.Event, release: asyncio.Event,
    ) -> CalculationPlan:
        with using_calculation_order(preference) as expected:
            ready.set()
            await release.wait()
            assert resolve_calculation_plan() is expected
            return resolve_calculation_plan()

    async def run_requests() -> tuple[CalculationPlan, CalculationPlan]:
        ready_first, ready_second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with asyncio.TaskGroup() as group:
            first = group.create_task(observe((17,), ready_first, release))
            second = group.create_task(observe((9,), ready_second, release))
            await asyncio.gather(ready_first.wait(), ready_second.wait())
            assert resolve_calculation_plan() == baseline
            release.set()
        return first.result(), second.result()

    first, second = asyncio.run(run_requests())
    assert first.requested_order[0] == 17
    assert second.requested_order[0] == 9
    assert resolve_calculation_plan() == baseline
