from manufacturing import (
    ManufacturingInstance,
    ManufacturingSolution,
    ManufacturingSchedulingFactory,
    get_solution_with_granularity_fallback,
)
from manufacturing.dataclasses.instance import SIZES
import pathlib
import os
from typing import Tuple, List, Optional, Dict
import pytest


def test_instance0():
    instance, solution = solve_instance(0)
    assert num_replenish_groups(solution) == 0
    validate_solution(solution)


@pytest.mark.parametrize("instance", [1, 2, 3])
def test_instance(instance: int):
    instance, solution = solve_instance(instance)
    validate_solution(solution)


def test_granularity_fallback_not_solvable():
    tests_path = pathlib.Path(__file__).parent.resolve()
    with open(os.path.join(tests_path, "instances/instance_not_solvable.json")) as f:
        instance = ManufacturingInstance.from_json(f.read())
    assert get_solution_with_granularity_fallback(instance) is None


def solve_instance(
    instance: int, time_limit: Optional[int] = None
) -> Tuple[ManufacturingInstance, ManufacturingSolution]:
    tests_path = pathlib.Path(__file__).parent.resolve()
    problem_data = os.path.join(tests_path, f"instances/instance{instance}.json")
    with open(problem_data) as f:
        instance: ManufacturingInstance = ManufacturingInstance.from_json(f.read())

    factory = ManufacturingSchedulingFactory(instance)
    solution: ManufacturingSolution = factory.get_solution(time_limit)
    assert solution is not None
    return instance, solution


def validate_solution(solution: ManufacturingSolution):
    ManufacturingSolution.from_json(solution.to_json())

    box_size = {b.box: SIZES[b.size] for b in solution.boxes}
    drawer_size = {
        d.drawer: SIZES[d.size]
        for d in solution.drawers.lower_level + solution.drawers.upper_level
        if d.enabled
    }

    boxes = [b.box for b in solution.boxes]
    drawers = [
        d.drawer
        for d in solution.drawers.lower_level + solution.drawers.upper_level
        if d.enabled
    ]

    box_filling_durations = dict(
        (bfd.box, bfd.filling_duration) for bfd in solution.box_filling_durations
    )

    orders = []
    for operator_order_list in solution.operator_order_lists:
        start = 0
        for order in operator_order_list.orders:
            orders.append(((start, operator_order_list.operator), order))
            start += box_filling_durations[order.box]
    orders.sort(key=lambda order: order[0])
    order_index = {order.id: i for i, (_, order) in enumerate(orders)}

    dbm_boxes = set(d.box for d in solution.drawer_box_mapping)
    assert set(boxes) == dbm_boxes

    dbm_drawers = [d.drawer for d in solution.drawer_box_mapping]
    assert len(dbm_drawers) == len(drawers) and set(dbm_drawers) == set(drawers)

    drawer_box_mapping = {dbm.drawer: dbm.box for dbm in solution.drawer_box_mapping}
    for drawer, box in drawer_box_mapping.items():
        assert drawer_size[drawer] >= box_size[box]

    for r in solution.replenishments:
        assert drawer_box_mapping[r.drawer] == r.box
        assert r.order_id in set(solution.orders.order)

    rep_indexes = [order_index[r.order_id] for r in solution.replenishments]
    rep_indexes.sort()
    for i in range(len(rep_indexes) - 1):
        assert (
            rep_indexes[i] + solution.box_constructions_per_replenishment
            <= rep_indexes[i + 1]
        )

    assert len(solution.box_constructions) == len(solution.orders.box)
    for i, drawer in enumerate(solution.box_constructions):
        assert drawer_box_mapping[drawer] == solution.orders.box[i]

    capacities = {c.box: c.capacity for c in solution.drawer_capacities}
    drawer_capacity = {d: capacities[b] for d, b in drawer_box_mapping.items()}

    activities = []
    drawer_activities = {
        d.drawer: []
        for d in solution.drawers.lower_level + solution.drawers.upper_level
        if d.enabled
    }
    for i, drawer in enumerate(solution.box_constructions):
        activities.append(
            (
                drawer,
                drawer_box_mapping[drawer],
                i,
                1,
                -1,
            )
        )
        drawer_activities[drawer].append((i, 1, -1))
    for r in solution.replenishments:
        activities.append(
            (
                r.drawer,
                r.box,
                order_index[r.order_id],
                solution.box_constructions_per_replenishment,
                drawer_capacity[r.drawer],
            )
        )
        drawer_activities[r.drawer].append(
            (
                order_index[r.order_id],
                solution.box_constructions_per_replenishment,
                drawer_capacity[r.drawer],
            )
        )

    for drawer, aa in drawer_activities.items():
        aa.sort(key=lambda a: a[0])
        assert not are_overlapped([a[:2] for a in aa])

        remaining_boxes = drawer_capacity[drawer]
        for start, duration, inc in aa:
            if inc < 0:
                remaining_boxes += inc
            else:
                # Ensure that the replenishment is performed when the drawer is not full
                assert remaining_boxes < drawer_capacity[drawer]
                remaining_boxes = drawer_capacity[drawer]

            # Ensure that the number of boxes remaining in each drawer never drops below zero
            assert 0 <= remaining_boxes <= drawer_capacity[drawer]

    # Ensure that the drawer selection policy is correctly enforced
    activities.sort(key=lambda a: (a[2] + a[3], a[4] > 0))
    remaining_boxes = {drawer: drawer_capacity[drawer] for drawer in drawers}
    for drawer, box, start, duration, inc in activities:
        if inc > 0:
            remaining_boxes[drawer] = inc
        else:
            for prev_drawer in previous_drawers(drawer, drawer_box_mapping):
                assert remaining_boxes[
                    prev_drawer
                ] == 0 or has_overlapping_replenishment(
                    solution, order_index, prev_drawer, start, start + duration
                )
            remaining_boxes[drawer] += inc


def are_overlapped(activities: List[Tuple[int, int]]):
    activities.sort(key=lambda x: x[0])
    for i in range(len(activities) - 1):
        if activities[i][0] + activities[i][1] > activities[i + 1][0]:
            return True
    return False


def previous_drawers(drawer: int, drawer_box_mapping: Dict[int, str]):
    for prev_drawer in drawer_box_mapping:
        if (
            prev_drawer < drawer
            and drawer_box_mapping[prev_drawer] == drawer_box_mapping[drawer]
        ):
            yield prev_drawer


def has_overlapping_replenishment(
    solution: ManufacturingSolution,
    order_index: Dict[int, int],
    drawer: int,
    start: int,
    end: int,
) -> bool:
    for r in solution.replenishments:
        rstart = order_index[r.order_id]
        rend = rstart + solution.box_constructions_per_replenishment
        if r.drawer == drawer and ((rstart <= start < rend) or (rstart < end <= rend)):
            return True
    return False


def num_replenish_groups(solution: ManufacturingSolution) -> int:
    if len(solution.replenishments) == 0:
        return 0

    box_filling_durations = dict(
        (bfd.box, bfd.filling_duration) for bfd in solution.box_filling_durations
    )

    orders = []
    for operator_order_list in solution.operator_order_lists:
        start = 0
        for order in operator_order_list.orders:
            orders.append(((start, operator_order_list.operator), order))
            start += box_filling_durations[order.box]
    orders.sort(key=lambda order: order[0])
    order_index = {order.id: i for i, (_, order) in enumerate(orders)}

    replenishments = sorted(
        solution.replenishments, key=lambda r: order_index[r.order_id]
    )

    groups = 1
    for i in range(1, len(replenishments)):
        if (
            order_index[replenishments[i].order_id]
            > order_index[replenishments[i - 1].order_id]
            + solution.box_constructions_per_replenishment
        ):
            groups += 1

    return groups
