"""Rolling horizon inside the drawer configurations of the enumeration.

With a fixed number of drawers per box, the drawer box mapping is fixed for the
whole horizon, so the orders can be planned in windows: each window plans
ROLLING_PLANNED_ORDERS orders and keeps only the first ROLLING_COMMITTED_ORDERS,
then the next window starts from the boxes left in the drawers and from the group
of replenishments running until the cut. A small window is solved to optimality
in a few seconds, while the whole horizon may not even get a good solution
within the time limit.
"""

import logging
import math
import time
from ortools.sat.python import cp_model
from typing import Dict, List, Optional, Set, Tuple

from manufacturing import ManufacturingInstance, ManufacturingSolution
from manufacturing.dataclasses import solution
from manufacturing.factory import (
    ManufacturingSchedulingFactory,
    get_solution_with_granularity_fallback,
)

# orders planned by each window
ROLLING_PLANNED_ORDERS = 600
# orders of each window kept in the solution, the others are planned again by
# the next window
ROLLING_COMMITTED_ORDERS = 500

Drawer = Tuple[int, int]  # (box_idx, drawer_idx relative to the box)


class _Plan:
    """The replenishments (order_idx, box_idx, drawer_idx) and the drawer_idx
    used by each order planned so far with a configuration."""

    def __init__(self, config: Tuple[int, ...]):
        self.config = config
        self.replenishments: List[Tuple[int, int, int]] = []
        self.used_drawers: List[int] = []
        self.user_time = 0.0

    def num_groups(self, duration: int) -> int:
        """The number of groups of consecutive replenishments."""
        starts = sorted(order_idx for order_idx, _, _ in self.replenishments)
        return sum(
            1 for i, start in enumerate(starts) if i == 0 or starts[i - 1] + duration < start
        )


def get_solution_by_rolling_horizon(
    instance: ManufacturingInstance,
    time_limit: Optional[float] = None,
    planned_orders: int = ROLLING_PLANNED_ORDERS,
    committed_orders: int = ROLLING_COMMITTED_ORDERS,
) -> Optional[ManufacturingSolution]:
    """Plans the orders in windows for each drawer configuration of the
    enumeration, in its order, and returns the best plan.

    The time limit is global: each configuration can use all the remaining
    time, and the configurations whose lower bound is not better than the best
    plan are skipped. In each window, the replenishment granularity goes from
    box_constructions_per_replenishment down to 1 until the window has a
    solution: if it has none, the configuration is discarded. The solution is
    optimal only if it matches the lower bound of all the configurations.

    With at most planned_orders orders there is a single window, and the
    instance is solved by the enumeration."""
    start_time = time.time()
    duration = instance.box_constructions_per_replenishment
    if committed_orders % duration != 0 or not 0 < committed_orders < planned_orders:
        raise ValueError(
            f"committed_orders ({committed_orders}) must be a multiple of "
            f"box_constructions_per_replenishment ({duration}) smaller than "
            f"planned_orders ({planned_orders})"
        )
    if len(instance.orders.order) <= planned_orders:
        return get_solution_with_granularity_fallback(instance, time_limit, "enumerate")

    granularities = [g for g in range(duration, 0, -1) if duration % g == 0]
    bases = {
        g: ManufacturingSchedulingFactory(
            instance, replenishment_granularity=g, build_model=False
        )
        for g in granularities
    }
    # the finest granularity has the weakest, hence valid, lower bounds
    finest = bases[granularities[-1]]
    lower_bounds = {}
    for g in granularities:
        for config in bases[g].iter_drawer_configurations():
            if config not in lower_bounds:
                lower_bounds[config] = finest.configuration_lower_bound(config)
    configs = [c for c, lb in lower_bounds.items() if lb is not None]
    if not configs:
        logging.info("no drawer configuration")
        return None
    lower_bound = min(lower_bounds[c] for c in configs)

    deadline = None if time_limit is None else start_time + time_limit
    best: Optional[Tuple[int, _Plan]] = None
    for config in configs:
        if best is not None and lower_bounds[config] >= best[0]:
            logging.info(
                f"configuration {config}: skipped, lower bound {lower_bounds[config]}"
            )
            continue
        if deadline is not None and time.time() >= deadline:
            break
        plan = _plan_configuration(
            instance, config, deadline, planned_orders, committed_orders, granularities
        )
        if plan is None:
            logging.info(f"configuration {config}: discarded")
            continue
        num_groups = plan.num_groups(duration)
        logging.info(f"configuration {config}: {num_groups} groups")
        if best is None or num_groups < best[0]:
            best = (num_groups, plan)
        if num_groups == lower_bound:
            break

    if best is None:
        print("No solution found.")
        return None
    num_groups, plan = best
    base = bases[duration]
    return base.build_solution(
        plan.config,
        plan.replenishments,
        plan.used_drawers,
        solution.SolverInfo(num_groups, lower_bound, plan.user_time),
        num_groups == lower_bound,
    )


def _plan_configuration(
    instance: ManufacturingInstance,
    config: Tuple[int, ...],
    deadline: Optional[float],
    planned_orders: int,
    committed_orders: int,
    granularities: List[int],
) -> Optional[_Plan]:
    """Plans all the orders in windows with the given configuration, or returns
    None if a window has no solution within the time left."""
    duration = instance.box_constructions_per_replenishment
    num_orders = len(instance.orders.order)
    window_starts = [0]
    while window_starts[-1] + planned_orders < num_orders:
        window_starts.append(window_starts[-1] + committed_orders)

    capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}
    plan = _Plan(config)
    levels: Optional[Dict[Drawer, int]] = None
    open_group: Optional[Set[Drawer]] = None
    for window_idx, window_start in enumerate(window_starts):
        last = window_idx == len(window_starts) - 1
        window_end = num_orders if last else window_start + planned_orders
        cut = window_end - window_start if last else committed_orders
        # a replenishment cannot run across the cut, as the next window only
        # knows the boxes left in the drawers
        forbidden_starts = () if last else range(cut - duration + 1, cut)

        found = None
        for granularity in granularities:
            build_start = time.time()
            factory = ManufacturingSchedulingFactory(
                instance,
                replenishment_granularity=granularity,
                order_range=(window_start, window_end),
                config=config,
                initial_levels=levels,
                open_group=open_group,
                forbidden_starts=forbidden_starts,
            )
            if levels is None:
                # the first window starts with full drawers
                levels = {
                    (box_idx, drawer_idx): capacities[box.box]
                    for box_idx, box in enumerate(factory.boxes)
                    for drawer_idx in range(config[box_idx])
                }
            window_time_limit = None
            if deadline is not None:
                # the time left is shared by the windows left; building the
                # model is part of the time of the window
                window_time_limit = (deadline - build_start) / (
                    len(window_starts) - window_idx
                ) - (time.time() - build_start)
                if window_time_limit <= 0:
                    return None
            solver = factory._new_solver(window_time_limit)
            status = solver.solve(factory.model)
            plan.user_time += solver.user_time
            logging.info(
                f"configuration {config}, orders {window_start}-{window_end}, "
                f"granularity {granularity}: {solver.status_name(status)}, "
                f"{solver.wall_time:.1f}s"
            )
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                found = (factory, solver)
                break
            if status != cp_model.INFEASIBLE:
                return None
        if found is None:
            return None

        factory, solver = found
        replenishments, used_drawers = factory.solution_activities(solver)
        window_replenishments = sorted(
            (order_idx, box_idx, drawer_idx)
            for order_idx, box_idx, drawer_idx in replenishments
            if order_idx < cut
        )
        plan.replenishments.extend(
            (window_start + order_idx, box_idx, drawer_idx)
            for order_idx, box_idx, drawer_idx in window_replenishments
        )
        plan.used_drawers.extend(used_drawers[:cut])

        # the boxes left in the drawers at the cut: a replenishment fills the
        # drawer when it starts, and the drawer is not used until it ends
        starts = {
            order_idx: (box_idx, drawer_idx)
            for order_idx, box_idx, drawer_idx in window_replenishments
        }
        for order_idx in range(cut):
            if order_idx in starts:
                box_idx, _ = starts[order_idx]
                levels[starts[order_idx]] = factory.drawer_capacities[box_idx]
            order = factory.orders[order_idx]
            levels[(factory.box_index[order.box], used_drawers[order_idx])] -= 1

        # the group of replenishments running until the cut, if any, possibly
        # started in a previous window
        plan_starts = {
            order_idx: (box_idx, drawer_idx)
            for order_idx, box_idx, drawer_idx in plan.replenishments
        }
        open_group = None
        group_end = window_start + cut
        while group_end - duration in plan_starts:
            open_group = open_group or set()
            open_group.add(plan_starts[group_end - duration])
            group_end -= duration

    return plan
