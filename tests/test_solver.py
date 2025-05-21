from manufacturing import (
    ManufacturingProblemData,
    ManufacturingConfiguration,
    ManufacturingInstance,
    ManufacturingSolution,
    ManufacturingSchedulingFactory,
)
from manufacturing.dataclasses.instance import OperatorOrderList, Order
import pathlib
import os
from typing import Tuple, List, Optional
import pytest


def solve_instance(
    instance: int, time_limit: Optional[int] = None
) -> Tuple[ManufacturingInstance, ManufacturingSolution]:
    tests_path = pathlib.Path(__file__).parent.resolve()
    configuration_data = os.path.join(tests_path, "../configuration.json")
    problem_data = os.path.join(tests_path, f"instances/instance{instance}.json")

    with open(configuration_data) as f:
        configuration: ManufacturingConfiguration = (
            ManufacturingConfiguration.from_json(f.read())
        )

    with open(problem_data) as f:
        problem_data: ManufacturingProblemData = ManufacturingProblemData.from_json(
            f.read()
        )

    operator_order_lists: List[OperatorOrderList] = [
        OperatorOrderList(operator, []) for operator in range(configuration.operators)
    ]
    for i, order_id in enumerate(problem_data.orders.order):
        operator_order_lists[i % configuration.operators].orders.append(
            Order(id=order_id, box=problem_data.orders.box[i])
        )

    instance: ManufacturingInstance = ManufacturingInstance(
        operators=configuration.operators,
        drawers=problem_data.drawers,
        drawer_capacities=configuration.drawer_capacities,
        replenish_windows=problem_data.replenish_windows,
        replenish_duration=configuration.replenish_duration,
        box_construction_duration=configuration.box_construction_duration,
        box_filling_durations=configuration.box_filling_durations,
        orders=problem_data.orders,
        operator_order_lists=operator_order_lists,
    )

    factory = ManufacturingSchedulingFactory(instance)
    solution: ManufacturingSolution = factory.get_solution()
    assert solution is not None
    return instance, solution


def validate_replenishments(
    instance: ManufacturingInstance, solution: ManufacturingSolution
):
    drawer_to_index = dict((drawer, idx) for idx, drawer in enumerate(instance.drawers))
    drawer_box_mapping = {
        drawer_to_index[drawer.drawer]: drawer.box
        for drawer in solution.drawer_box_mapping
    }
    drawer_capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}

    drawer_activities = [[] for _ in range(len(instance.drawers))]
    for i, drawer in enumerate(solution.box_constructions):
        drawer_idx = drawer_to_index[drawer]
        box_construction_start = i * instance.box_construction_duration
        box_construction_end = (
            box_construction_start + instance.box_construction_duration
        )
        drawer_activities[drawer_idx].append(
            (box_construction_start, box_construction_end, -1)
        )

    for replenishment in solution.replenishments:
        drawer_activities[drawer_to_index[replenishment.drawer]].append(
            (
                replenishment.start,
                replenishment.start + instance.replenish_duration,
                drawer_capacities[
                    drawer_box_mapping[drawer_to_index[replenishment.drawer]]
                ],
            )
        )

    for drawer_idx in range(len(instance.drawers)):
        drawer_activities[drawer_idx].sort(key=lambda e: e[0])
        boxes = drawer_capacities[drawer_box_mapping[drawer_idx]]
        for start, end, inc in drawer_activities[drawer_idx]:
            if inc > 0:
                boxes = inc
            else:
                boxes += inc
                assert boxes >= 0

    for drawer_idx in range(len(instance.drawers)):
        assert not are_overlapped(
            [(start, end) for start, end, _ in drawer_activities[drawer_idx]]
        )


def are_overlapped(activities: List[Tuple[int, int]]):
    activities.sort(key=lambda x: x[0])
    for i in range(len(activities) - 1):
        if activities[i][1] > activities[i + 1][0]:
            return True
    return False


@pytest.mark.parametrize("instance", [1, 2, 3, 4, 5, 6, 7])
def test_instance(instance: int):
    instance, solution = solve_instance(0, time_limit=60)
    validate_replenishments(instance, solution)


def test_instance0():
    instance, solution = solve_instance(0)

    assert len(solution.replenishments) == 12
    validate_replenishments(instance, solution)
