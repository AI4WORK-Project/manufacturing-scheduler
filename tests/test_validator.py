import copy
import json
import os
import pathlib

import pytest

from manufacturing import (
    STRATEGIES,
    ManufacturingInstance,
    get_solution_with_granularity_fallback,
)
from manufacturing.validator import validate

# Two operators, box A (S) in drawers 1 and 2, box B (M) in drawer 3, drawer 4
# disabled. Orders are assigned round robin: 10, 12 and 14 go to operator 0, 11,
# 13 and 15 go to operator 1. By start time the boxes are constructed in the
# sequence 10, 11, 12, 13, 15, 14 (14 starts at time 3, after the box B of 12).
INPUT = {
    "operators": 2,
    "boxes": [{"box": "A", "size": "S"}, {"box": "B", "size": "M"}],
    "drawers": {
        "lower_level": [{"drawer": 1, "size": "L", "enabled": True}],
        "upper_level": [
            {"drawer": 2, "size": "S", "enabled": True},
            {"drawer": 3, "size": "M", "enabled": True},
            {"drawer": 4, "size": "S", "enabled": False},
        ],
    },
    "drawer_capacities": [{"box": "A", "capacity": 3}, {"box": "B", "capacity": 3}],
    "box_constructions_per_replenishment": 2,
    "box_filling_durations": [
        {"box": "A", "filling_duration": 1},
        {"box": "B", "filling_duration": 2},
    ],
    "minimum_remaining_boxes": 0,
    "orders": {
        "date": ["2026-01-01"] * 6,
        "order": [10, 11, 12, 13, 14, 15],
        "item": [1, 2, 3, 4, 5, 6],
        "height": [1] * 6,
        "width": [1] * 6,
        "depth": [1] * 6,
        "box": ["A", "A", "B", "A", "A", "A"],
    },
}

OUTPUT = {
    **copy.deepcopy(INPUT),
    "operator_order_lists": [
        {
            "operator": 0,
            "orders": [{"id": 10, "box": "A"}, {"id": 12, "box": "B"}, {"id": 14, "box": "A"}],
        },
        {
            "operator": 1,
            "orders": [{"id": 11, "box": "A"}, {"id": 13, "box": "A"}, {"id": 15, "box": "A"}],
        },
    ],
    "is_solution_optimal": True,
    "index_of_fragmentation": 0.0,
    "drawer_box_mapping": [
        {"drawer": 1, "box": "A"},
        {"drawer": 2, "box": "A"},
        {"drawer": 3, "box": "B"},
    ],
    "replenishments": [],
    "box_constructions": [1, 1, 3, 1, 2, 2],
    "solver": {"objective_value": 0, "best_objective_bound": 0, "user_time": 0},
}


def rules(output, input_data=INPUT):
    return {v.rule for v in validate(input_data, output)}


def with_replenishment(order_id, constructions, drawer=1):
    output = copy.deepcopy(OUTPUT)
    output["replenishments"] = [{"drawer": drawer, "box": "A", "order_id": order_id}]
    output["box_constructions"] = constructions
    return output


def test_feasible_plan():
    assert validate(INPUT, OUTPUT) == []


def test_feasible_plan_with_replenishment():
    # Drawer 1 is blocked for orders 13 and 15 and full again for order 14.
    output = with_replenishment(13, [1, 1, 3, 2, 2, 1])
    assert validate(INPUT, output) == []


def test_replenishment_past_the_end_of_the_plan():
    # 14 is the last box constructed.
    assert validate(INPUT, with_replenishment(14, [1, 1, 3, 1, 2, 2])) == []


def test_operator_assignment():
    output = copy.deepcopy(OUTPUT)
    lists = output["operator_order_lists"]
    lists[0]["orders"][2], lists[1]["orders"][2] = lists[1]["orders"][2], lists[0]["orders"][2]
    assert rules(output) == {"operators"}


def test_constructions_follow_the_start_times():
    # With order 15 of box B, the boxes are constructed in the sequence
    # 10 A, 11 A, 12 B, 13 A, 15 B, 14 A, not in the input order.
    input_data = copy.deepcopy(INPUT)
    input_data["orders"]["box"][5] = "B"
    output = {**copy.deepcopy(OUTPUT), **copy.deepcopy(input_data)}
    output["operator_order_lists"][1]["orders"][2]["box"] = "B"
    output["box_constructions"] = [1, 1, 3, 1, 3, 2]
    assert validate(input_data, output) == []
    output["box_constructions"] = [1, 1, 3, 1, 2, 3]
    assert rules(output, input_data) == {"constructions"}


def test_box_does_not_fit_drawer():
    output = copy.deepcopy(OUTPUT)
    output["drawer_box_mapping"][1] = {"drawer": 2, "box": "B"}
    output["drawer_box_mapping"][2] = {"drawer": 3, "box": "A"}
    assert "mapping" in rules(output)


def test_disabled_drawer_mapped():
    output = copy.deepcopy(OUTPUT)
    output["drawer_box_mapping"].append({"drawer": 4, "box": "A"})
    assert rules(output) == {"mapping"}


def test_enabled_drawer_not_mapped():
    output = copy.deepcopy(OUTPUT)
    output["drawer_box_mapping"].pop(1)
    assert "mapping" in rules(output)


def test_construction_with_wrong_box():
    output = copy.deepcopy(OUTPUT)
    output["box_constructions"][2] = 1
    assert "constructions" in rules(output)


def test_wrong_number_of_constructions():
    output = copy.deepcopy(OUTPUT)
    output["box_constructions"].pop()
    assert rules(output) == {"constructions"}


def test_empty_drawer():
    output = copy.deepcopy(OUTPUT)
    output["box_constructions"] = [1, 1, 3, 1, 1, 2]
    assert rules(output) == {"constructions"}


def test_drawer_selection_policy():
    output = copy.deepcopy(OUTPUT)
    output["box_constructions"] = [2, 1, 3, 1, 1, 2]
    assert rules(output) == {"drawer-selection"}


def test_construction_from_blocked_drawer():
    output = with_replenishment(13, [1, 1, 3, 1, 2, 1])
    assert "constructions" in rules(output)


def test_replenishment_of_full_drawer():
    output = with_replenishment(10, [2, 2, 3, 1, 1, 1])
    assert rules(output) == {"replenishment"}


def test_replenishment_with_wrong_box():
    output = with_replenishment(13, [1, 1, 3, 2, 2, 1])
    output["replenishments"][0]["box"] = "B"
    assert rules(output) == {"replenishment"}


def test_replenishment_of_unknown_order():
    output = with_replenishment(99, [1, 1, 3, 1, 2, 2])
    assert rules(output) == {"replenishment"}


def test_overlapping_replenishments():
    output = with_replenishment(13, [1, 1, 3, 2, 2, 1])
    output["replenishments"].append({"drawer": 2, "box": "A", "order_id": 15})
    assert "replenishment-overlap" in rules(output)


def test_consecutive_replenishments():
    # Drawer 1 is blocked for orders 11 and 12, drawer 2 for orders 13 and 15.
    output = with_replenishment(11, [1, 2, 3, 1, 1, 1])
    output["replenishments"].append({"drawer": 2, "box": "A", "order_id": 13})
    assert validate(INPUT, output) == []


def test_minimum_remaining_boxes():
    input_data = copy.deepcopy(INPUT)
    input_data["minimum_remaining_boxes"] = 2
    output = {**copy.deepcopy(OUTPUT), **copy.deepcopy(input_data)}
    assert rules(output, input_data) == {"minimum-remaining"}
    # A drawer under replenishment counts as full.
    output["replenishments"] = [{"drawer": 1, "box": "A", "order_id": 13}]
    output["box_constructions"] = [1, 1, 3, 2, 2, 1]
    assert validate(input_data, output) == []


def test_input_copy_differs():
    output = copy.deepcopy(OUTPUT)
    output["orders"]["item"][0] = 42
    assert rules(output) == {"input-copy"}


INSTANCES = [
    "instance0",
    "instance1",
    "instance2",
    "instance3",
    "large_9drawers_1",
    "large_9drawers_2",
    "large_8drawers_1",
    "large_8drawers_2",
]

# Combinations that do not terminate within 15 minutes.
SLOW = {("full", "large_9drawers_2"), ("hint", "large_9drawers_2")}


# The large instances have 4000 orders; with 8 drawers the solver needs the
# granularity fallback.
@pytest.mark.parametrize(
    "strategy, instance",
    [(s, i) for s in STRATEGIES for i in INSTANCES if (s, i) not in SLOW],
)
def test_solver_solutions_are_feasible(strategy: str, instance: str):
    tests_path = pathlib.Path(__file__).parent.resolve()
    with open(os.path.join(tests_path, f"instances/{instance}.json")) as f:
        raw = f.read()
    solution = get_solution_with_granularity_fallback(
        ManufacturingInstance.from_json(raw), strategy=strategy
    )
    assert solution is not None
    violations = validate(json.loads(raw), json.loads(solution.to_json()))
    assert violations == [], "\n".join(str(v) for v in violations)
