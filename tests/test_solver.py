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
from typing import Tuple, List, Optional, Dict
import pytest


@pytest.mark.parametrize("instance", [1, 2, 3, 4, 5, 6])
def test_instance(instance: int):
    instance, solution = solve_instance(instance, time_limit=60)
    validate_solution(instance, solution)


def test_instance0():
    instance, solution = solve_instance(0)

    assert len(solution.replenishments) == 3
    validate_solution(instance, solution)


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
        start_time=problem_data.start_time,
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
    solution: ManufacturingSolution = factory.get_solution(time_limit)
    assert solution is not None
    return instance, solution


def validate_solution(instance: ManufacturingInstance, solution: ManufacturingSolution):
    drawer_to_index = dict((drawer, idx) for idx, drawer in enumerate(instance.drawers))
    drawer_box_mapping = {
        drawer_to_index[drawer.drawer]: drawer.box
        for drawer in solution.drawer_box_mapping
    }
    box_drawers_mapping = {dc.box: [] for dc in instance.drawer_capacities}
    for drawer, box in drawer_box_mapping.items():
        box_drawers_mapping[box].append(drawer)
    for box in box_drawers_mapping:
        box_drawers_mapping[box].sort()
    drawer_capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}

    # # Ensure that each box construction in solution matches the drawer_box_mapping mapping
    # for drawer in solution.box_constructions:
    #     assert drawer_box_mapping[drawer_to_index[drawer.drawer]] == drawer.box

    activities = []
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
        activities.append(
            (
                drawer,
                drawer_box_mapping[drawer],
                box_construction_start,
                box_construction_end,
                -1,
            )
        )

    replenishments = []
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

        r = (
            replenishment.drawer,
            replenishment.box,
            replenishment.start,
            replenishment.start + instance.replenish_duration,
            drawer_capacities[
                drawer_box_mapping[drawer_to_index[replenishment.drawer]]
            ],
        )
        replenishments.append(r)
        activities.append(r)

    # Ensure that the number of boxes remaining in each drawer never drops below zero
    for drawer_idx in range(len(instance.drawers)):
        drawer_activities[drawer_idx].sort(key=lambda e: e[0])
        boxes = drawer_capacities[drawer_box_mapping[drawer_idx]]
        for start, end, inc in drawer_activities[drawer_idx]:
            if inc > 0:
                boxes = inc
            else:
                boxes += inc
                assert boxes >= 0

    # Ensure that no two activities on the same drawer overlap in time
    for drawer_idx in range(len(instance.drawers)):
        assert not are_overlapped(
            [(start, end) for start, end, _ in drawer_activities[drawer_idx]]
        )

    activities.sort(key=lambda a: (a[3], a[4] > 0))
    remaining_boxes = [
        drawer_capacities[drawer_box_mapping[drawer_idx]]
        for drawer_idx in range(len(instance.drawers))
    ]
    # Ensure that the drawer selection policy is correctly enforced
    for i, (drawer, box, start, end, inc) in enumerate(activities):
        drawer_idx = drawer_to_index[drawer]
        if inc > 0:
            remaining_boxes[drawer_idx] = inc
            continue

        if box_drawers_mapping[box][0] != drawer_idx:
            for prev_drawer in previous_drawers(drawer_idx, box, box_drawers_mapping):
                assert remaining_boxes[
                    prev_drawer
                ] == 0 or is_replenishment_overlapping(
                    instance.drawers[prev_drawer], start, end, replenishments
                )

        remaining_boxes[drawer_idx] += inc


def are_overlapped(activities: List[Tuple[int, int]]):
    activities.sort(key=lambda x: x[0])
    for i in range(len(activities) - 1):
        if activities[i][1] > activities[i + 1][0]:
            return True
    return False


def previous_drawers(
    drawer_idx: int, box: str, box_drawers_mapping: Dict[str, List[int]]
):
    for prev_drawer in box_drawers_mapping[box]:
        if prev_drawer < drawer_idx:
            yield prev_drawer


def is_replenishment_overlapping(
    drawer: int, start: int, end: int, replenishments: List[Tuple]
) -> bool:
    for drawer2, box2, start2, end2, inc2 in replenishments:
        if drawer2 == drawer and ((start2 <= start < end2) or (start2 < end <= end2)):
            return True
    return False
