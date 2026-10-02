"""Independent feasibility validator for scheduler solutions.

The validator works on the raw JSON dictionaries and simulates the plan step by
step, without relying on the solver or on the dataclasses. The rules are:

- Orders are assigned round robin: order i goes to operator i % operators. Each
  operator processes its orders one after the other from time 0, each for the
  filling duration of its box type. The boxes are constructed in order of start
  time (ties broken by lowest operator id): box_constructions and the positions
  of the replenishments refer to this sequence, not to the input order.
- Each enabled drawer holds exactly one box type for the whole plan, whose size
  must not exceed the drawer size (S < M < L). Disabled drawers are not used.
- Drawers start full; the capacity depends on the box type only.
- box_constructions[i] is the drawer from which the i-th box is taken.
  The drawer must hold that box type, must not be under replenishment and must
  not be empty. Every drawer of the same box type with a lower id must be empty
  or under replenishment.
- A replenishment with order_id X occupies the construction of X and the
  following box_constructions_per_replenishment - 1 ones. The drawer must not be
  full when the replenishment starts, is blocked while it runs, and is full when
  it ends. Replenishments never overlap, even on different drawers. A
  replenishment may run past the end of the plan.
- After every construction, the number of boxes of its type that can still be
  taken must be at least minimum_remaining_boxes: a drawer under replenishment
  counts as empty, as it cannot be used.
- The input fields reported in the output must be identical to the input.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

SIZE_RANK = {"S": 0, "M": 1, "L": 2}

INPUT_FIELDS = [
    "operators",
    "boxes",
    "drawers",
    "drawer_capacities",
    "box_constructions_per_replenishment",
    "box_filling_durations",
    "minimum_remaining_boxes",
    "orders",
]

OUTPUT_FIELDS = [
    "operator_order_lists",
    "drawer_box_mapping",
    "replenishments",
    "box_constructions",
]


@dataclass
class Violation:
    rule: str
    message: str
    step: Optional[int] = None

    def __str__(self) -> str:
        where = f" (step {self.step})" if self.step is not None else ""
        return f"[{self.rule}]{where} {self.message}"


class _InvalidInput(Exception):
    pass


def validate(input_data: Dict[str, Any], output_data: Dict[str, Any]) -> List[Violation]:
    """Return the list of violations of the plan; an empty list means feasible."""
    violations: List[Violation] = []

    def fail(rule: str, message: str, step: Optional[int] = None):
        violations.append(Violation(rule, message, step))

    try:
        problem = _parse_input(input_data)
    except _InvalidInput as e:
        fail("input", str(e))
        return violations

    for field in INPUT_FIELDS:
        if field not in output_data:
            fail("input-copy", f"field '{field}' is missing from the output")
        elif output_data[field] != input_data[field]:
            fail("input-copy", f"field '{field}' differs from the input")
    for field in OUTPUT_FIELDS:
        if field not in output_data:
            fail("structure", f"field '{field}' is missing from the output")
    if any(v.rule == "structure" for v in violations):
        return violations

    _check_operators(problem, output_data["operator_order_lists"], fail)
    mapping = _check_mapping(problem, output_data["drawer_box_mapping"], fail)
    starts = _check_replenishments(problem, mapping, output_data["replenishments"], fail)
    _simulate(problem, mapping, starts, output_data["box_constructions"], fail)
    return violations


def _parse_input(data: Dict[str, Any]) -> Dict[str, Any]:
    for field in INPUT_FIELDS:
        if field not in data:
            raise _InvalidInput(f"field '{field}' is missing from the input")

    box_size = {}
    for b in data["boxes"]:
        if b["size"] not in SIZE_RANK:
            raise _InvalidInput(f"box {b['box']} has unknown size {b['size']}")
        box_size[b["box"]] = b["size"]
    capacity = {c["box"]: c["capacity"] for c in data["drawer_capacities"]}
    filling = {f["box"]: f["filling_duration"] for f in data["box_filling_durations"]}
    for box in box_size:
        if box not in capacity:
            raise _InvalidInput(f"box {box} has no drawer capacity")
        if box not in filling:
            raise _InvalidInput(f"box {box} has no filling duration")

    drawer_size = {}
    enabled = set()
    for level in ("lower_level", "upper_level"):
        for d in data["drawers"][level]:
            if d["drawer"] in drawer_size:
                raise _InvalidInput(f"drawer {d['drawer']} is defined twice")
            if d["size"] not in SIZE_RANK:
                raise _InvalidInput(f"drawer {d['drawer']} has unknown size {d['size']}")
            drawer_size[d["drawer"]] = d["size"]
            if d["enabled"]:
                enabled.add(d["drawer"])

    order_ids = data["orders"]["order"]
    order_boxes = data["orders"]["box"]
    if len(order_ids) != len(order_boxes):
        raise _InvalidInput("orders.order and orders.box have different lengths")
    if len(set(order_ids)) != len(order_ids):
        raise _InvalidInput("order ids are not unique")
    for oid, box in zip(order_ids, order_boxes):
        if box not in box_size:
            raise _InvalidInput(f"order {oid} uses unknown box {box}")

    if data["operators"] < 1:
        raise _InvalidInput("there must be at least one operator")
    if data["box_constructions_per_replenishment"] < 1:
        raise _InvalidInput("box_constructions_per_replenishment must be positive")

    # Round robin assignment, then construction sequence by (start time, operator).
    operators = data["operators"]
    operator_orders: List[List[Dict[str, Any]]] = [[] for _ in range(operators)]
    free_at = [0] * operators
    starts = []
    for i, (oid, box) in enumerate(zip(order_ids, order_boxes)):
        op = i % operators
        operator_orders[op].append({"id": oid, "box": box})
        starts.append((free_at[op], op, i))
        free_at[op] += filling[box]
    sequence = [i for _, _, i in sorted(starts, key=lambda s: (s[0], s[1]))]
    order_ids = [order_ids[i] for i in sequence]
    order_boxes = [order_boxes[i] for i in sequence]

    return {
        "operators": operators,
        "operator_orders": operator_orders,
        "box_size": box_size,
        "capacity": capacity,
        "filling": filling,
        "drawer_size": drawer_size,
        "enabled": enabled,
        "order_ids": order_ids,
        "order_boxes": order_boxes,
        "order_index": {oid: i for i, oid in enumerate(order_ids)},
        "k": data["box_constructions_per_replenishment"],
        "min_remaining": data["minimum_remaining_boxes"],
    }


def _check_operators(problem, operator_order_lists, fail):
    expected = problem["operator_orders"]
    actual: Dict[int, List[Dict[str, Any]]] = {}
    for ol in operator_order_lists:
        op = ol["operator"]
        if not 0 <= op < problem["operators"]:
            fail("operators", f"operator {op} does not exist")
        elif op in actual:
            fail("operators", f"operator {op} is listed twice")
        else:
            actual[op] = ol["orders"]

    for op in range(problem["operators"]):
        got = actual.get(op, [])
        if got == expected[op]:
            continue
        for pos, (g, e) in enumerate(zip(got, expected[op])):
            if g != e:
                fail(
                    "operators",
                    f"operator {op}, position {pos}: expected {e}, got {g}",
                )
                break
        else:
            fail(
                "operators",
                f"operator {op} has {len(got)} orders, expected {len(expected[op])}",
            )


def _check_mapping(problem, drawer_box_mapping, fail) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for m in drawer_box_mapping:
        drawer, box = m["drawer"], m["box"]
        if drawer not in problem["drawer_size"]:
            fail("mapping", f"drawer {drawer} does not exist")
            continue
        if drawer not in problem["enabled"]:
            fail("mapping", f"drawer {drawer} is disabled")
            continue
        if drawer in mapping:
            fail("mapping", f"drawer {drawer} is mapped twice")
            continue
        if box not in problem["box_size"]:
            fail("mapping", f"drawer {drawer} holds unknown box {box}")
            continue
        if SIZE_RANK[problem["box_size"][box]] > SIZE_RANK[problem["drawer_size"][drawer]]:
            fail(
                "mapping",
                f"box {box} (size {problem['box_size'][box]}) does not fit drawer "
                f"{drawer} (size {problem['drawer_size'][drawer]})",
            )
        mapping[drawer] = box
    for drawer in sorted(problem["enabled"] - set(mapping)):
        fail("mapping", f"enabled drawer {drawer} has no box")
    return mapping


def _check_replenishments(problem, mapping, replenishments, fail) -> Dict[int, int]:
    """Return the drawer replenished starting at each step."""
    starts: Dict[int, int] = {}
    for r in replenishments:
        drawer, box, oid = r["drawer"], r["box"], r["order_id"]
        if drawer not in mapping:
            fail("replenishment", f"drawer {drawer} is not an enabled, mapped drawer")
            continue
        if mapping[drawer] != box:
            fail(
                "replenishment",
                f"drawer {drawer} holds box {mapping[drawer]}, replenished with {box}",
            )
        if oid not in problem["order_index"]:
            fail("replenishment", f"order {oid} does not exist")
            continue
        step = problem["order_index"][oid]
        if step in starts:
            fail(
                "replenishment-overlap",
                f"replenishments of drawers {starts[step]} and {drawer} both start "
                f"at order {oid}",
                step,
            )
            continue
        starts[step] = drawer

    ordered = sorted(starts)
    for prev, cur in zip(ordered, ordered[1:]):
        if prev + problem["k"] > cur:
            fail(
                "replenishment-overlap",
                f"replenishment of drawer {starts[cur]} starts at order "
                f"{problem['order_ids'][cur]} while the one of drawer {starts[prev]} "
                f"started at order {problem['order_ids'][prev]} is still running",
                cur,
            )
    return starts


def _simulate(problem, mapping, starts, box_constructions, fail):
    n = len(problem["order_ids"])
    if len(box_constructions) != n:
        fail(
            "constructions",
            f"there are {len(box_constructions)} box constructions for {n} orders",
        )
        n = min(n, len(box_constructions))

    capacity = {d: problem["capacity"][b] for d, b in mapping.items()}
    level = dict(capacity)
    blocked_until: Dict[int, int] = {}  # drawer -> first step it is available again
    drawers_of: Dict[str, List[int]] = {}
    for d in sorted(mapping):
        drawers_of.setdefault(mapping[d], []).append(d)

    def is_blocked(d: int, step: int) -> bool:
        return d in blocked_until and step < blocked_until[d]

    for step in range(n):
        for d, until in list(blocked_until.items()):
            if until == step:
                level[d] = capacity[d]
                del blocked_until[d]

        if step in starts and starts[step] in mapping:
            d = starts[step]
            if is_blocked(d, step):
                fail("replenishment", f"drawer {d} is already under replenishment", step)
            elif level[d] >= capacity[d]:
                fail("replenishment", f"drawer {d} is full when replenished", step)
            blocked_until[d] = step + problem["k"]

        oid, box = problem["order_ids"][step], problem["order_boxes"][step]
        d = box_constructions[step]
        if d not in mapping:
            fail(
                "constructions",
                f"order {oid} uses drawer {d}, which is not an enabled, mapped drawer",
                step,
            )
            continue
        if mapping[d] != box:
            fail(
                "constructions",
                f"order {oid} needs box {box}, drawer {d} holds box {mapping[d]}",
                step,
            )
            continue
        if is_blocked(d, step):
            fail(
                "constructions",
                f"order {oid} uses drawer {d}, which is under replenishment",
                step,
            )
            continue
        if level[d] <= 0:
            fail("constructions", f"order {oid} uses empty drawer {d}", step)
            continue
        for prev in drawers_of[box]:
            if prev >= d:
                break
            if not is_blocked(prev, step) and level[prev] > 0:
                fail(
                    "drawer-selection",
                    f"order {oid} uses drawer {d} while drawer {prev} of the same box "
                    f"still has {level[prev]} boxes",
                    step,
                )
                break
        level[d] -= 1

        total = sum(0 if is_blocked(x, step) else level[x] for x in drawers_of[box])
        if total < problem["min_remaining"]:
            fail(
                "minimum-remaining",
                f"box {box} has {total} boxes left, minimum is "
                f"{problem['min_remaining']}",
                step,
            )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the feasibility of a plan.")
    parser.add_argument("input", help="input JSON file")
    parser.add_argument("output", help="output JSON file produced by the scheduler")
    parser.add_argument(
        "--max-violations",
        type=int,
        default=50,
        help="maximum number of violations to print (0 prints all)",
    )
    args = parser.parse_args(argv)

    with open(args.input) as f:
        input_data = json.load(f)
    with open(args.output) as f:
        output_data = json.load(f)

    violations = validate(input_data, output_data)
    if not violations:
        print("Plan is feasible.")
        return 0

    shown = violations if args.max_violations == 0 else violations[: args.max_violations]
    for v in shown:
        print(v)
    if len(shown) < len(violations):
        print(f"... and {len(violations) - len(shown)} more")
    print(f"Plan is NOT feasible: {len(violations)} violations.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
