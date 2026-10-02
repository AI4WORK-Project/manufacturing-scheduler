from manufacturing import ManufacturingInstance, ManufacturingSolution
from manufacturing.dataclasses.instance import Order, SIZES
from manufacturing.dataclasses.solution import OperatorOrderList

from typing import Tuple, List, Optional, Dict, Iterator
from ortools.sat.python import cp_model
import heapq
import math
import logging
import time

from manufacturing.dataclasses import solution


STRATEGIES = ("full", "enumerate", "hint")

# time limit in seconds of the relaxed model of relaxed_lower_bound
RELAXED_BOUND_TIME_LIMIT = 2


def get_solution_with_granularity_fallback(
    instance: ManufacturingInstance,
    time_limit: Optional[float] = None,
    strategy: str = "enumerate",
) -> Optional[ManufacturingSolution]:
    """Solves the instance with the coarsest replenishment granularity that has
    a solution: it tries box_constructions_per_replenishment first, then its
    smaller divisors (e.g. 10, 5, 2, 1), and stops at the first solution found.
    A finer granularity has more replenishment slots, so it may find solutions
    that a coarser one cannot, but its model is bigger. The time limit is global."""
    duration = instance.box_constructions_per_replenishment
    granularities = [g for g in range(duration, 0, -1) if duration % g == 0]
    start_time = time.time()
    for granularity in granularities:
        remaining = None
        if time_limit is not None:
            remaining = time_limit - (time.time() - start_time)
            if remaining <= 0:
                break
        factory = ManufacturingSchedulingFactory(
            instance, replenishment_granularity=granularity
        )
        solution = factory.get_solution_by_strategy(strategy, remaining)
        if solution is not None:
            logging.info(
                f"solution found with replenishment granularity {granularity}"
            )
            return solution
        logging.info(f"no solution with replenishment granularity {granularity}")
    return None


class ManufacturingSchedulingFactory:

    def __init__(
        self,
        instance: ManufacturingInstance,
        replenishment_granularity: Optional[int] = None,
    ):
        """
        replenishment_granularity: a replenishment can start only at orders whose
        index is a multiple of this value. It must divide
        box_constructions_per_replenishment, which is also the default.
        """
        self.instance = instance
        replenishment_duration = self.instance.box_constructions_per_replenishment
        if replenishment_granularity is None:
            replenishment_granularity = replenishment_duration
        if (
            replenishment_granularity <= 0
            or replenishment_duration % replenishment_granularity != 0
        ):
            raise ValueError(
                f"replenishment_granularity ({replenishment_granularity}) must divide "
                f"box_constructions_per_replenishment ({replenishment_duration})"
            )
        self.replenishment_granularity = replenishment_granularity

        self.orders, self.operator_order_lists = self.get_machine_order_list()
        self.boxes = self.get_boxes()
        self.box_index = {box.box: i for i, box in enumerate(self.boxes)}
        self.drawers = self.get_drawers()
        self.prev_order = self.calculate_prev_order()

        drawer_capacities = {
            dc.box: dc.capacity for dc in self.instance.drawer_capacities
        }
        self.drawer_capacities = [drawer_capacities[box.box] for box in self.boxes]
        self.min_num_drawers = self.calculate_min_num_drawers()
        self.max_num_drawers = self.calculate_max_num_drawers()

        (
            self.model,
            self.drawers_per_box_vars,
            self.replenish_start_vars,
            self.nbox_vars,
            self.is_used_vars,
        ) = self.get_optimization_model()

    def get_boxes(self):
        boxes = list(self.instance.boxes)
        boxes.sort(key=lambda b: (SIZES[b.size], b.box))
        return boxes

    def get_drawers(self):
        drawers = self.instance.drawers.lower_level + self.instance.drawers.upper_level
        drawers = [d for d in drawers if d.enabled]
        drawers.sort(key=lambda d: (SIZES[d.size], d.drawer))
        return drawers

    def get_machine_order_list(
        self,
    ) -> Tuple[List[Order], List[OperatorOrderList]]:
        operator_order_lists = [
            OperatorOrderList(operator, [])
            for operator in range(self.instance.operators)
        ]
        for i, order_id in enumerate(self.instance.orders.order):
            operator_order_lists[i % self.instance.operators].orders.append(
                Order(id=order_id, box=self.instance.orders.box[i])
            )

        box_filling_durations = dict(
            (bfd.box, bfd.filling_duration)
            for bfd in self.instance.box_filling_durations
        )

        orders = []
        for operator_order_list in operator_order_lists:
            start = 0
            for order in operator_order_list.orders:
                orders.append(((start, operator_order_list.operator), order))
                start += box_filling_durations[order.box]

        orders.sort(key=lambda order: order[0])
        orders = [order for _, order in orders]
        return orders, operator_order_lists

    def calculate_max_num_drawers(self) -> List[int]:
        """Upper bound on the number of drawers of each box.

        For each size s up to the size of the box, the box and every other box of
        size at least s can only use drawers of size at least s, and every other box
        needs at least its minimum number of drawers. Sizes smaller than the box
        account for the drawers that smaller boxes take from larger sizes when the
        smaller drawers are not enough.
        """
        num_drawers_ge_size = {s: 0 for s in SIZES}
        for d in self.drawers:
            for s in SIZES:
                if SIZES[d.size] >= SIZES[s]:
                    num_drawers_ge_size[s] += 1

        max_num_drawers = []
        for box_idx, b in enumerate(self.boxes):
            available_drawers = min(
                num_drawers_ge_size[s]
                - sum(
                    self.min_num_drawers[other_idx]
                    for other_idx, other in enumerate(self.boxes)
                    if other_idx != box_idx and SIZES[other.size] >= SIZES[s]
                )
                for s in SIZES
                if SIZES[s] <= SIZES[b.size]
            )
            max_num_drawers.append(max(1, available_drawers))

        return max_num_drawers

    def calculate_drawer_pressure(self) -> List[float]:
        """Pressure on a single drawer of each box.

        With a single drawer, every order of the box must be served by it, so the
        drawer can only be replenished during a window of
        box_constructions_per_replenishment orders, starting at a multiple of the
        replenishment granularity, that contains no order of the box. Between the
        ends of two such windows, the drawer starts at most full and must never
        drop below the minimum remaining boxes. The pressure is the largest number
        of orders between two such window ends, divided by this margin: above 1,
        a single drawer is not enough.
        """
        duration = self.instance.box_constructions_per_replenishment
        granularity = self.replenishment_granularity
        min_remaining = max(self.instance.minimum_remaining_boxes, 0)

        pressure = []
        for box_idx, box in enumerate(self.boxes):
            margin = self.drawer_capacities[box_idx] - min_remaining
            free_window_ends = {
                start + duration
                for start in range(0, len(self.orders), granularity)
                if all(
                    order.box != box.box
                    for order in self.orders[start : start + duration]
                )
            }
            max_consumed = 0
            consumed = 0
            for order_idx, order in enumerate(self.orders):
                if order_idx in free_window_ends:
                    consumed = 0
                if order.box == box.box:
                    consumed += 1
                    max_consumed = max(max_consumed, consumed)
            if margin > 0:
                pressure.append(max_consumed / margin)
            else:
                pressure.append(math.inf if max_consumed > 0 else 0.0)

        return pressure

    def calculate_min_num_drawers(self) -> List[int]:
        """Lower bound on the number of drawers of each box.

        The smallest number of drawers passing `can_serve_box_orders`, a relaxation
        of the problem restricted to a single box. If no number of drawers passes,
        the bound exceeds the number of drawers.
        """
        self.drawer_pressure = self.calculate_drawer_pressure()

        granularity = self.replenishment_granularity
        steps_per_replenishment = (
            self.instance.box_constructions_per_replenishment // granularity
        )
        min_remaining = max(self.instance.minimum_remaining_boxes, 0)
        num_steps = math.ceil(len(self.orders) / granularity)
        step_demands = [[0] * num_steps for _ in self.boxes]
        for order_idx, order in enumerate(self.orders):
            step_demands[self.box_index[order.box]][order_idx // granularity] += 1

        min_num_drawers = []
        for box_idx, demands in enumerate(step_demands):
            num_drawers = 1
            while num_drawers <= len(self.drawers) and not self.can_serve_box_orders(
                demands,
                num_drawers,
                self.drawer_capacities[box_idx],
                min_remaining,
                steps_per_replenishment,
            ):
                num_drawers += 1
            min_num_drawers.append(num_drawers)

        return min_num_drawers

    @staticmethod
    def can_serve_box_orders(
        demands: List[int],
        num_drawers: int,
        capacity: int,
        min_remaining: int,
        steps_per_replenishment: int,
    ) -> bool:
        """Necessary condition for serving the orders of a box with num_drawers.

        `demands` holds the number of orders of the box in each step of
        replenishment_granularity orders; a replenishment starts at a step and lasts
        `steps_per_replenishment` steps, and at most one is in progress at a time.
        The drawers are relaxed to a single pool of boxes, starting full, and the
        highest reachable stock is tracked step by step, both when no drawer is
        being replenished and at each step of a replenishment:
        - no drawer is replenished: the orders are served by the stock, which must
          not drop below `min_remaining`;
        - one drawer is replenished: it is not full when it starts, so the stock is
          not full, and it cannot serve orders, so the orders are served by the
          other drawers, holding at most `(num_drawers - 1) * capacity` boxes,
          which must not drop below `min_remaining`. The replenished drawer adds a
          full drawer to the stock when the replenishment ends.
        All the transitions are monotone in the stock, so keeping the highest stock
        is exact for the relaxation. With a single drawer, the replenishment can
        only happen in windows without orders of the box.
        """
        full_stock = num_drawers * capacity
        # idle_stock: highest stock with no replenishment in progress;
        # replenishing[i]: highest stock of the other drawers after i steps of a
        # replenishment in progress
        idle_stock = full_stock
        replenishing = [None] * steps_per_replenishment

        def serve(stock, demand):
            if stock < demand or (demand > 0 and stock - demand < min_remaining):
                return None
            return stock - demand

        def keep_highest(current, candidate):
            if candidate is None or (current is not None and current >= candidate):
                return current
            return candidate

        for demand in demands:
            next_idle = None
            next_replenishing = [None] * steps_per_replenishment

            # replenishments in progress or starting at this step
            others = list(replenishing)
            if idle_stock is not None and idle_stock < full_stock:
                others[0] = keep_highest(
                    others[0], min(idle_stock, (num_drawers - 1) * capacity)
                )
            for steps_done, stock in enumerate(others):
                if stock is None:
                    continue
                served = serve(stock, demand)
                if served is None:
                    continue
                if steps_done + 1 == steps_per_replenishment:
                    next_idle = keep_highest(next_idle, served + capacity)
                else:
                    next_replenishing[steps_done + 1] = keep_highest(
                        next_replenishing[steps_done + 1], served
                    )

            # no replenishment
            if idle_stock is not None:
                next_idle = keep_highest(next_idle, serve(idle_stock, demand))

            if next_idle is None and all(s is None for s in next_replenishing):
                return False
            idle_stock, replenishing = next_idle, next_replenishing

        return True

    def _is_valid_configuration(self, config: Tuple[int, ...]) -> bool:
        """Whether the number of drawers per box satisfies the size constraints
        of define_drawers_per_box_vars."""
        for size in SIZES:
            assigned_drawers = sum(
                config[box_idx]
                for box_idx, box in enumerate(self.boxes)
                if SIZES[box.size] <= SIZES[size]
            )
            minimum_required_drawers = len(
                [d for d in self.drawers if SIZES[d.size] <= SIZES[size]]
            )
            if size == "L":
                if assigned_drawers != minimum_required_drawers:
                    return False
            elif assigned_drawers < minimum_required_drawers:
                return False
        return True

    def calculate_single_drawer_replenishments(self, box_idx: int) -> Optional[int]:
        """Minimum number of replenishments of the box if it has a single drawer,
        or None if a single drawer is not enough.

        The drawer starts full, can only be replenished during a window without
        orders of the box (see calculate_drawer_pressure) and must never drop
        below the minimum remaining boxes. Replenishing as late as possible, i.e.
        only when the drawer would not last until the next window, gives the
        minimum number of replenishments."""
        box = self.boxes[box_idx].box
        duration = self.instance.box_constructions_per_replenishment
        capacity = self.drawer_capacities[box_idx]
        min_remaining = max(self.instance.minimum_remaining_boxes, 0)
        num_orders = len(self.orders)

        # box_orders[j]: orders of the box before order j
        box_orders = [0]
        for order in self.orders:
            box_orders.append(box_orders[-1] + (order.box == box))
        free_window_starts = [
            start
            for start in range(0, num_orders, self.replenishment_granularity)
            if box_orders[min(start + duration, num_orders)] == box_orders[start]
        ]

        remaining = capacity
        replenishments = 0
        consumed_until = 0
        for i, start in enumerate(free_window_starts):
            remaining -= box_orders[start] - box_orders[consumed_until]
            consumed_until = start
            if remaining < min_remaining:
                return None
            next_start = (
                free_window_starts[i + 1]
                if i + 1 < len(free_window_starts)
                else num_orders
            )
            if remaining - (box_orders[next_start] - box_orders[start]) < min_remaining:
                remaining = capacity
                replenishments += 1
        remaining -= box_orders[num_orders] - box_orders[consumed_until]
        return replenishments if remaining >= min_remaining else None

    def configuration_lower_bound(self, config: Tuple[int, ...]) -> Optional[int]:
        """Lower bound on the objective (the number of groups of consecutive
        replenishments) with the given number of drawers per box, or None if the
        configuration has no solution.

        Each drawer is replenished at most once in a group, so a box with k
        drawers needs at least replenishments / k groups, and all the boxes
        together need at least total replenishments / #drawers groups. With a
        single drawer the minimum number of replenishments is exact (see
        calculate_single_drawer_replenishments); with more drawers it only
        counts the orders: the drawers start full and each replenishment adds at
        most a full drawer."""
        if not hasattr(self, "_single_drawer_replenishments"):
            self._single_drawer_replenishments = [
                self.calculate_single_drawer_replenishments(box_idx)
                for box_idx in range(len(self.boxes))
            ]
        min_remaining = max(self.instance.minimum_remaining_boxes, 0)
        box_orders = {box.box: 0 for box in self.boxes}
        for order in self.orders:
            box_orders[order.box] += 1

        lower_bound = 0
        total_replenishments = 0
        for box_idx, num_drawers in enumerate(config):
            if num_drawers == 1:
                replenishments = self._single_drawer_replenishments[box_idx]
                if replenishments is None:
                    return None
            else:
                capacity = self.drawer_capacities[box_idx]
                replenishments = max(
                    0,
                    -(
                        -(
                            box_orders[self.boxes[box_idx].box]
                            + min_remaining
                            - num_drawers * capacity
                        )
                        // capacity
                    ),
                )
            total_replenishments += replenishments
            lower_bound = max(lower_bound, -(-replenishments // num_drawers))
        return max(lower_bound, -(-total_replenishments // sum(config)))

    def relaxed_lower_bound(
        self, config: Tuple[int, ...], time_limit: float
    ) -> Tuple[int, bool]:
        """Lower bound on the objective with the given number of drawers per box,
        from a relaxed model with only the replenishments: at most one at a time,
        each drawer at most once in a group, and the drawers of the boxes with a
        single drawer replenished often enough, only in the windows without
        orders of the box. The replenishments of the boxes with more drawers are
        free: they can only join groups. Returns the bound and whether the
        relaxed model has been solved to optimality; the bound is valid anyway."""
        duration = self.instance.box_constructions_per_replenishment
        granularity = self.replenishment_granularity
        min_remaining = max(self.instance.minimum_remaining_boxes, 0)
        slots = list(range(0, len(self.orders), granularity))
        slots_per_replenishment = duration // granularity

        model = cp_model.CpModel()
        start_vars = []  # (box_idx, {slot_idx: var}) for each drawer
        for box_idx, num_drawers in enumerate(config):
            box = self.boxes[box_idx].box
            for _ in range(num_drawers):
                starts = {}
                for slot_idx, start in enumerate(slots):
                    window = self.orders[start : start + duration]
                    if num_drawers == 1 and any(order.box == box for order in window):
                        continue
                    starts[slot_idx] = model.new_bool_var("")
                start_vars.append((box_idx, starts))

        in_progress = [[] for _ in slots]
        for _, starts in start_vars:
            for slot_idx, var in starts.items():
                for i in range(
                    slot_idx, min(slot_idx + slots_per_replenishment, len(slots))
                ):
                    in_progress[i].append(var)
        any_replenish_vars = []
        for slot_vars in in_progress:
            any_replenish = model.new_bool_var("")
            model.add(sum(slot_vars) == any_replenish)
            any_replenish_vars.append(any_replenish)

        # each drawer is replenished at most once in each group
        for _, starts in start_vars:
            prev_cumulative = None
            for slot_idx in range(len(slots)):
                cumulative = model.new_bool_var("")
                start_var = starts.get(slot_idx)
                if start_var is not None:
                    model.add_implication(start_var, cumulative)
                    if prev_cumulative is not None:
                        model.add_implication(start_var, prev_cumulative.negated())
                if prev_cumulative is not None:
                    model.add_bool_or(
                        cumulative,
                        prev_cumulative.negated(),
                        any_replenish_vars[slot_idx].negated(),
                    )
                prev_cumulative = cumulative

        # a single drawer cannot serve more than capacity - minimum_remaining_boxes
        # orders of the box without a replenishment in between
        for box_idx, starts in start_vars:
            if config[box_idx] > 1:
                continue
            box = self.boxes[box_idx].box
            positions = [i for i, order in enumerate(self.orders) if order.box == box]
            width = self.drawer_capacities[box_idx] - min_remaining + 1
            for i in range(len(positions) - width + 1):
                first, last = positions[i], positions[i + width - 1]
                model.add_bool_or(
                    var
                    for slot_idx, var in starts.items()
                    if first < slots[slot_idx] <= last
                )

        group_start_vars = []
        for slot_idx, any_replenish in enumerate(any_replenish_vars):
            group_start = model.new_bool_var("")
            if slot_idx == 0:
                model.add_implication(any_replenish, group_start)
            else:
                model.add_bool_or(
                    group_start,
                    any_replenish.negated(),
                    any_replenish_vars[slot_idx - 1],
                )
            group_start_vars.append(group_start)
        model.minimize(sum(group_start_vars))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        status = solver.solve(model)
        if status == cp_model.INFEASIBLE:
            return math.inf, True
        return math.ceil(solver.best_objective_bound - 1e-6), status == cp_model.OPTIMAL

    def _configuration_priority(self, config: Tuple[int, ...]) -> Tuple:
        """A lower priority comes first: first the lower bound on the objective,
        then the pressure per drawer of each box, from the highest down, as the
        configurations that better relieve the most pressured boxes come first."""
        lower_bound = self.configuration_lower_bound(config)
        pressures = tuple(
            sorted(
                (
                    self.drawer_pressure[box_idx] / num_drawers
                    for box_idx, num_drawers in enumerate(config)
                ),
                reverse=True,
            )
        )
        return (math.inf if lower_bound is None else lower_bound, pressures)

    def iter_drawer_configurations(self) -> Iterator[Tuple[int, ...]]:
        """Generates, one at a time, the numbers of drawers per box that use all
        the drawers and satisfy the bounds and the size constraints, in order of
        priority (see _configuration_priority).

        Each box gets at most max(2, ceil(#drawers / #boxes)) drawers, unless its
        lower bound requires more: two drawers are enough to replenish one while
        the other serves the orders. If no configuration satisfies this cap, the
        cap is raised.

        The first configuration is built greedily, giving each extra drawer to
        the box with the highest pressure per drawer. The next ones are explored
        best-first, moving one drawer from a box to another, so the order is only
        approximately the order of priority."""
        num_drawers = len(self.drawers)
        num_boxes = len(self.boxes)
        if sum(self.min_num_drawers) > num_drawers or any(
            lb > ub for lb, ub in zip(self.min_num_drawers, self.max_num_drawers)
        ):
            return

        cap = max(2, -(-num_drawers // num_boxes))
        while True:
            upper = [
                min(ub, max(lb, cap))
                for lb, ub in zip(self.min_num_drawers, self.max_num_drawers)
            ]
            if sum(upper) >= num_drawers:
                config = list(self.min_num_drawers)
                for _ in range(num_drawers - sum(config)):
                    box_idx = max(
                        (b for b in range(num_boxes) if config[b] < upper[b]),
                        key=lambda b: self.drawer_pressure[b] / config[b],
                    )
                    config[box_idx] += 1
                config = tuple(config)

                found = False
                queue = [(self._configuration_priority(config), config)]
                visited = {config}
                while queue:
                    _, config = heapq.heappop(queue)
                    if self._is_valid_configuration(config):
                        found = True
                        yield config
                    for i in range(num_boxes):
                        if config[i] <= self.min_num_drawers[i]:
                            continue
                        for j in range(num_boxes):
                            if j == i or config[j] >= upper[j]:
                                continue
                            neighbour = list(config)
                            neighbour[i] -= 1
                            neighbour[j] += 1
                            neighbour = tuple(neighbour)
                            if neighbour not in visited:
                                visited.add(neighbour)
                                heapq.heappush(
                                    queue,
                                    (self._configuration_priority(neighbour), neighbour),
                                )
                if found:
                    return

            if cap >= max(self.max_num_drawers):
                return
            logging.info(
                f"no drawer configuration with at most {cap} drawers per box, "
                f"raising the cap to {cap + 1}"
            )
            cap += 1

    def restricted_model(
        self,
        config: Optional[Tuple[int, ...]],
        objective_ub: Optional[int] = None,
    ) -> cp_model.CpModel:
        """A copy of the model with, if config is given, the number of drawers of
        each box fixed and, if objective_ub is given, the objective at most
        objective_ub. The copy has the same variable indexes of the original
        model."""
        model = self.model.clone()
        for box_idx, num_drawers in enumerate(config or ()):
            var = model.get_int_var_from_proto_index(
                self.drawers_per_box_vars[box_idx].index
            )
            model.add(var == num_drawers)
        if objective_ub is not None:
            objective = model.proto.objective
            assert objective.scaling_factor in (0, 1)
            model.add(
                sum(
                    coeff * model.get_int_var_from_proto_index(var_idx)
                    for var_idx, coeff in zip(objective.vars, objective.coeffs)
                )
                + int(objective.offset)
                <= objective_ub
            )
        return model

    def calculate_prev_order(self) -> List[Optional[int]]:
        prev_order = []
        last_order_with_box = {b.box: None for b in self.boxes}
        for order_idx, order in enumerate(self.orders):
            prev_order.append(last_order_with_box[order.box])
            last_order_with_box[order.box] = order_idx

        return prev_order

    def get_optimization_model(self):
        model = cp_model.CpModel()
        drawers_per_box_vars = self.define_drawers_per_box_vars(model)
        replenish_start_vars, replenish_vars, any_replenish_vars = (
            self.define_replenish_vars(model, drawers_per_box_vars)
        )
        cumulative_replenish_vars = self.define_cumulative_replenish_vars(
            model, replenish_start_vars, any_replenish_vars
        )
        nbox_vars, is_used_vars = self.define_nbox_vars(
            model, replenish_start_vars, replenish_vars, drawers_per_box_vars
        )

        self.add_robustness_constraints(model, nbox_vars, replenish_vars)
        self.add_quality_metric(model, any_replenish_vars)

        return (
            model,
            drawers_per_box_vars,
            replenish_start_vars,
            nbox_vars,
            is_used_vars,
        )

    def define_drawers_per_box_vars(
        self, model: cp_model.CpModel
    ) -> List[cp_model.IntVar]:
        drawers_per_box_vars = []
        for box_idx, box in enumerate(self.boxes):
            min_num_drawers = self.min_num_drawers[box_idx]
            max_num_drawers = self.max_num_drawers[box_idx]
            drawers_per_box_vars.append(
                model.new_int_var(
                    min(min_num_drawers, max_num_drawers),
                    max_num_drawers,
                    f"drawers_containing_box{box.box}",
                )
            )
            if min_num_drawers > max_num_drawers:
                logging.warning(
                    f"Box {box.box} requires at least {min_num_drawers} "
                    f"drawers, but at most {max_num_drawers} can be assigned"
                )
                # an empty domain would make the model invalid instead of infeasible
                model.add(drawers_per_box_vars[-1] >= min_num_drawers)

        for size in SIZES:
            assigned_drawers = sum(
                drawers_per_box_vars[box_idx]
                for box_idx, box in enumerate(self.boxes)
                if SIZES[box.size] <= SIZES[size]
            )
            minimum_required_drawers = len(
                [drawer for drawer in self.drawers if SIZES[drawer.size] <= SIZES[size]]
            )
            if size == "L":
                model.add(assigned_drawers == minimum_required_drawers)
            else:
                model.add(assigned_drawers >= minimum_required_drawers)

        return drawers_per_box_vars

    def define_replenish_vars(
        self, model: cp_model.CpModel, drawers_per_box_vars: List[cp_model.IntVar]
    ) -> Tuple[
        List[List[Dict[int, cp_model.IntVar]]],
        List[List[List[cp_model.IntVar]]],
        List[cp_model.IntVar],
    ]:
        """
        Returns:
        - replenish_start_vars[b][d][j]: a replenishment of the drawer starts at
          order j (defined only when j is a multiple of the granularity)
        - replenish_vars[b][d][j]: the drawer is being replenished at order j
        - any_replenish_vars[j]: some drawer is being replenished at order j
        """
        duration = self.instance.box_constructions_per_replenishment
        granularity = self.replenishment_granularity

        replenish_start_vars = [
            [{} for _ in range(self.max_num_drawers[box_idx])]
            for box_idx in range(len(self.boxes))
        ]
        replenish_vars = [
            [[] for _ in range(self.max_num_drawers[box_idx])]
            for box_idx in range(len(self.boxes))
        ]
        for box_idx, box in enumerate(self.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                start_vars = replenish_start_vars[box_idx][drawer_idx]
                for order_idx, order in enumerate(self.orders):
                    if order_idx % granularity == 0:
                        start_var = model.new_bool_var(
                            f"replenishment_box{box}_drawer{drawer_idx}@{order_idx}"
                        )
                        start_vars[order_idx] = start_var

                        if drawer_idx >= self.min_num_drawers[box_idx]:
                            # Replenishing the d-th drawer implies that the box has at least d drawers assigned
                            model.add(
                                drawers_per_box_vars[box_idx] >= drawer_idx + 1
                            ).only_enforce_if(start_var)

                        if granularity == duration:
                            rep_var = start_var
                        else:
                            # the drawer is being replenished if a replenishment started in the last duration orders
                            rep_var = model.new_bool_var(
                                f"replenishing_box{box}_drawer{drawer_idx}@{order_idx}"
                            )
                            model.add(
                                rep_var
                                == sum(
                                    start_vars[i]
                                    for i in range(
                                        max(order_idx - duration + granularity, 0),
                                        order_idx + 1,
                                        granularity,
                                    )
                                )
                            )
                        replenish_vars[box_idx][drawer_idx].append(rep_var)
                    else:
                        replenish_vars[box_idx][drawer_idx].append(
                            replenish_vars[box_idx][drawer_idx][-1]
                        )

        # at most one replenishment in progress at each time
        for order_idx, order in enumerate(self.orders):
            if order_idx % granularity == 0:
                slot_rep_vars = [
                    replenish_vars[box_idx][drawer_idx][order_idx]
                    for box_idx in range(len(self.boxes))
                    for drawer_idx in range(self.max_num_drawers[box_idx])
                ]
                model.add_at_most_one(slot_rep_vars)

        any_replenish_vars = []
        for order_idx, order in enumerate(self.orders):
            if order_idx % granularity == 0:
                any_rep_var = model.new_bool_var(f"any_replenishment@{order_idx}")
                any_replenish_vars.append(any_rep_var)

                order_rep_vars = [
                    replenish_vars[box_idx][drawer_idx][order_idx]
                    for box_idx in range(len(self.boxes))
                    for drawer_idx in range(self.max_num_drawers[box_idx])
                ]
                model.add_bool_and(any_rep_var.negated()).only_enforce_if(
                    v.negated() for v in order_rep_vars
                )
                for rep_var in order_rep_vars:
                    model.add_implication(rep_var, any_rep_var)

            else:
                any_replenish_vars.append(any_replenish_vars[-1])

        return replenish_start_vars, replenish_vars, any_replenish_vars

    def define_cumulative_replenish_vars(
        self,
        model: cp_model.CpModel,
        replenish_start_vars: List[List[Dict[int, cp_model.IntVar]]],
        any_replenish_vars: List[cp_model.IntVar],
    ) -> List[List[List[cp_model.IntVar]]]:
        # each drawer is replenished at most once in each group of consecutive replenishments
        cumulative_replenish_vars = [
            [[] for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        for box_idx, box in enumerate(self.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                start_vars = replenish_start_vars[box_idx][drawer_idx]
                for order_idx, order in enumerate(self.orders):
                    if order_idx % self.replenishment_granularity == 0:
                        if order_idx == 0:
                            cumulative_replenish_vars[box_idx][drawer_idx].append(
                                start_vars[order_idx]
                            )
                        else:
                            crep_var = model.new_bool_var(
                                f"cumulative_replenishment_box{box}_drawer{drawer_idx}@{order_idx}"
                            )
                            cumulative_replenish_vars[box_idx][drawer_idx].append(
                                crep_var
                            )

                            model.add_implication(
                                start_vars[order_idx],
                                cumulative_replenish_vars[box_idx][drawer_idx][
                                    -2
                                ].negated(),
                            )

                            model.add_bool_and(crep_var).only_enforce_if(
                                start_vars[order_idx]
                            )
                            model.add_bool_and(crep_var).only_enforce_if(
                                cumulative_replenish_vars[box_idx][drawer_idx][-2],
                                any_replenish_vars[order_idx],
                            )
                            # TODO: not necessary
                            # model.add_bool_or(
                            #     [
                            #         replenish_vars[box_idx][drawer_idx][order_idx],
                            #         cumulative_replenish_vars[box_idx][drawer_idx][-2],
                            #     ]
                            # ).only_enforce_if(crep_var)
                            # model.add_bool_or(
                            #     [
                            #         replenish_vars[box_idx][drawer_idx][order_idx],
                            #         any_replenish_vars[order_idx],
                            #     ]
                            # ).only_enforce_if(crep_var)

        # force groups of at least two replenishments
        # for order_idx in range(0, len(self.orders), self.instance.box_constructions_per_replenishment):
        #     if 0 < order_idx and order_idx + self.instance.box_constructions_per_replenishment < len(
        #         self.orders
        #     ):
        #         model.add_implication(
        #             any_replenish_vars[order_idx],
        #             any_replenish_vars[order_idx + self.instance.box_constructions_per_replenishment],
        #         ).only_enforce_if(
        #             any_replenish_vars[
        #                 order_idx - self.instance.box_constructions_per_replenishment
        #             ].negated()
        #         )

        return cumulative_replenish_vars

    def define_nbox_vars(
        self,
        model: cp_model.CpModel,
        replenish_start_vars: List[List[Dict[int, cp_model.IntVar]]],
        replenish_vars: List[List[List[cp_model.IntVar]]],
        drawers_per_box_vars: List[cp_model.IntVar],
    ) -> Tuple[
        List[List[Dict[int, cp_model.IntVar]]], List[List[Dict[int, cp_model.IntVar]]]
    ]:
        nbox_vars = [
            [{} for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        is_empty_vars = [
            [{} for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        for box_idx, num_drawers in enumerate(self.max_num_drawers):
            for drawer_idx in range(num_drawers):
                drawer_capacity_var = model.new_int_var_from_domain(
                    cp_model.Domain.FromValues([0, self.drawer_capacities[box_idx]]),
                    f"capacity_box{box_idx}_drawer{drawer_idx}",
                )
                nbox_vars[box_idx][drawer_idx][-1] = drawer_capacity_var
                drawer_contains_box_var = model.new_bool_var(
                    f"drawer{drawer_idx}_contains_box{box_idx}"
                )
                is_empty_vars[box_idx][drawer_idx][
                    -1
                ] = drawer_contains_box_var.negated()
                # TODO: drawer_contains_box_var can be used in function define_replenish_vars
                model.add(
                    drawers_per_box_vars[box_idx] >= drawer_idx + 1
                ).only_enforce_if(drawer_contains_box_var)
                model.add(
                    drawers_per_box_vars[box_idx] < drawer_idx + 1
                ).only_enforce_if(drawer_contains_box_var.negated())
                model.add(
                    drawer_capacity_var == self.drawer_capacities[box_idx]
                ).only_enforce_if(drawer_contains_box_var)
                model.add(drawer_capacity_var == 0).only_enforce_if(
                    drawer_contains_box_var.negated()
                )

        can_be_used_vars = [
            [{} for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        for order_idx, order in enumerate(self.orders):
            box_idx = self.box_index[order.box]
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                if order_idx > 0 and self.orders[order_idx - 1].box != order.box:
                    indexes = [order_idx - 1, order_idx]
                else:
                    indexes = [order_idx]

                for i in indexes:
                    nbox_vars[box_idx][drawer_idx][i] = model.new_int_var(
                        0,
                        self.drawer_capacities[box_idx],
                        f"nbox_var_box{order.box}_drawer{drawer_idx}@order{i}",
                    )

                    if i + 1 < len(self.orders) and self.orders[i + 1].box == order.box:
                        is_empty_var = model.new_bool_var(
                            f"is_empty_box{order.box}_drawer{drawer_idx}@order{i}",
                        )
                        is_empty_vars[box_idx][drawer_idx][i] = is_empty_var
                        model.add(
                            nbox_vars[box_idx][drawer_idx][i] == 0
                        ).only_enforce_if(is_empty_var)
                        model.add(
                            nbox_vars[box_idx][drawer_idx][i] > 0
                        ).only_enforce_if(is_empty_var.negated())

                can_be_used_var = model.new_bool_var(
                    f"can_be_used_box{order.box}_drawer{drawer_idx}@order{order_idx}",
                )
                can_be_used_vars[box_idx][drawer_idx][order_idx] = can_be_used_var
                model.add_bool_and(
                    is_empty_vars[box_idx][drawer_idx][order_idx - 1].negated(),
                    replenish_vars[box_idx][drawer_idx][order_idx].negated(),
                ).only_enforce_if(can_be_used_var)
                model.add_bool_or(
                    is_empty_vars[box_idx][drawer_idx][order_idx - 1],
                    replenish_vars[box_idx][drawer_idx][order_idx],
                ).only_enforce_if(can_be_used_var.negated())

        is_used_vars = [
            [{} for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        for box_idx, box in enumerate(self.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                for order_idx in nbox_vars[box_idx][drawer_idx]:
                    if order_idx == -1:
                        continue

                    nbox_var = nbox_vars[box_idx][drawer_idx][order_idx]
                    if box.box == self.orders[order_idx].box:
                        is_used_var = model.new_bool_var(
                            f"is_used_var{box_idx}{drawer_idx}{order_idx}"
                        )
                        is_used_vars[box_idx][drawer_idx][order_idx] = is_used_var
                        model.add_bool_and(
                            [can_be_used_vars[box_idx][drawer_idx][order_idx]]
                            + [
                                can_be_used_vars[box_idx][d][order_idx].negated()
                                for d in range(drawer_idx)
                            ],
                        ).only_enforce_if(is_used_var)
                        model.add_bool_or(
                            [can_be_used_vars[box_idx][drawer_idx][order_idx].negated()]
                            + [
                                can_be_used_vars[box_idx][d][order_idx]
                                for d in range(drawer_idx)
                            ],
                        ).only_enforce_if(is_used_var.negated())

                        # rep(b, d, j) => nbox(b, d, j) = capacity(b)
                        model.add(
                            nbox_var == self.drawer_capacities[box_idx]
                        ).only_enforce_if(
                            replenish_vars[box_idx][drawer_idx][order_idx]
                        )
                        # not rep(b, d, j) and nbox(b, d, j − 1) > 0 and [nbox(b, d', j − 1) = 0 or rep(b, d', j) foreach d' < d]
                        #   => nbox(b, d, j) = nbox(b, d, j − 1) − 1
                        model.add(
                            nbox_var
                            == nbox_vars[box_idx][drawer_idx][order_idx - 1] - 1
                        ).only_enforce_if(is_used_var)
                        # otherwise => nbox(b, d, j) = nbox(b, d, j − 1)
                        model.add(
                            nbox_var == nbox_vars[box_idx][drawer_idx][order_idx - 1]
                        ).only_enforce_if(
                            replenish_vars[box_idx][drawer_idx][order_idx].negated(),
                            is_used_var.negated(),
                        )

                    else:
                        prev_order_idx = self.prev_order[order_idx + 1]
                        if prev_order_idx is None:
                            prev_order_idx = -1
                        negated_rep_vars = []
                        start = prev_order_idx + 1
                        start -= start % self.replenishment_granularity
                        for i in range(
                            start,
                            order_idx + 1,
                            self.replenishment_granularity,
                        ):
                            negated_rep_vars.append(
                                replenish_vars[box_idx][drawer_idx][i].negated()
                            )
                            # rep(b, d, i) => nbox(b, d, j) = capacity(b)
                            model.add(
                                nbox_var == self.drawer_capacities[box_idx]
                            ).only_enforce_if(replenish_vars[box_idx][drawer_idx][i])

                        model.add(
                            nbox_var == nbox_vars[box_idx][drawer_idx][prev_order_idx]
                        ).only_enforce_if(negated_rep_vars)

        for order_idx, order in enumerate(self.orders):
            box_idx = self.box_index[order.box]
            # exactly one drawer used for each order
            # TODO: both are equivalent
            # model.add_bool_or(
            model.add_exactly_one(
                is_used_vars[box_idx][drawer_idx][order_idx]
                for drawer_idx in range(self.max_num_drawers[box_idx])
            )

        # prevent replenishment when drawer is full
        for box_idx, box in enumerate(self.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                start_vars = replenish_start_vars[box_idx][drawer_idx]
                prev_nbox = nbox_vars[box_idx][drawer_idx][-1]
                prev_nbox_idx = -1
                for order_idx in range(len(self.orders)):
                    if order_idx % self.replenishment_granularity == 0:
                        model.add(
                            prev_nbox < self.drawer_capacities[box_idx]
                        ).only_enforce_if(start_vars[order_idx])
                        # prev_nbox does not see replenishments started after it: the drawer
                        # is full if it has been replenished since then, as it was not used
                        granularity = self.replenishment_granularity
                        first_start = -(-(prev_nbox_idx + 1) // granularity) * granularity
                        for i in range(first_start, order_idx, granularity):
                            model.add_bool_or(
                                start_vars[order_idx].negated(),
                                start_vars[i].negated(),
                            )

                    if order_idx in nbox_vars[box_idx][drawer_idx]:
                        prev_nbox = nbox_vars[box_idx][drawer_idx][order_idx]
                        prev_nbox_idx = order_idx

        return nbox_vars, is_used_vars

    def add_robustness_constraints(
        self,
        model: cp_model.CpModel,
        nbox_vars: List[List[Dict[int, cp_model.IntVar]]],
        replenish_vars: List[List[List[cp_model.IntVar]]],
    ):
        if self.instance.minimum_remaining_boxes > 0:
            # after each order, the boxes left in the drawers of its box that are
            # not being replenished are at least minimum_remaining_boxes: a drawer
            # being replenished is full but cannot be used
            for order_idx, order in enumerate(self.orders):
                box_idx = self.box_index[order.box]
                capacity = self.drawer_capacities[box_idx]
                model.add(
                    sum(
                        nbox_vars[box_idx][drawer_idx][order_idx]
                        - capacity * replenish_vars[box_idx][drawer_idx][order_idx]
                        for drawer_idx in range(self.max_num_drawers[box_idx])
                    )
                    >= self.instance.minimum_remaining_boxes
                )

    def add_quality_metric(
        self, model: cp_model.CpModel, any_replenish_vars: List[cp_model.IntVar]
    ):
        """Sets the objective of the model to optimize."""

        replenishment_change_vars = [any_replenish_vars[0]]
        for i in range(1, len(any_replenish_vars)):
            replenishment_change = model.new_bool_var(f"replenishment_change{i}")
            # TODO: not necessary
            # model.add_bool_and(
            #     any_replenish_vars[i - 1].negated(),
            #     any_replenish_vars[i],
            # ).only_enforce_if(replenishment_change)
            model.add_bool_or(
                any_replenish_vars[i - 1],
                any_replenish_vars[i].negated(),
            ).only_enforce_if(replenishment_change.negated())
            replenishment_change_vars.append(replenishment_change)

        model.minimize(sum(replenishment_change_vars))
        # model.minimize(
        #     1000 * sum(replenishment_change_vars)
        #     + sum(
        #         any_replenish_vars[i]
        #         for i in range(0, len(self.orders), self.instance.box_constructions_per_replenishment)
        #     )
        # )

    def _new_solver(self, time_limit: Optional[float] = None) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        if time_limit is not None:
            solver.parameters.max_time_in_seconds = time_limit
        # solver.parameters.num_search_workers = 20
        # solver.parameters.log_search_progress = True

        # # deterministic search, usually way slower than the non deterministic version
        # # see: https://groups.google.com/g/or-tools-discuss/c/lPb1FzhTMt0
        # solver.parameters.interleave_search = True
        # solver.parameters.share_binary_clauses = False
        # solver.parameters.interleave_batch_size = 32
        # solver.parameters.num_workers = 16

        # solver.parameters.cp_model_presolve = False
        # solver.parameters.max_presolve_iterations = 1
        # solver.parameters.cp_model_probing_level = 1
        # solver.parameters.presolve_probing_deterministic_time_limit = 5
        # solver.parameters.probing_deterministic_time_limit = 5

        # class SolutionCallback(cp_model.CpSolverSolutionCallback):
        #     def __init__(self):
        #         cp_model.CpSolverSolutionCallback.__init__(self)

        #     def on_solution_callback(self):
        #         logging.info(
        #             f"solution: user_time {self.user_time}, objective_value {self.objective_value}, best_objective_bound {self.best_objective_bound}"
        #         )
        #         self.StopSearch()

        # solution_callback = SolutionCallback()
        # status = solver.solve(self.model, solution_callback=solution_callback)

        return solver

    def get_solution_by_strategy(
        self, strategy: str, time_limit: Optional[float] = None
    ) -> Optional[ManufacturingSolution]:
        """Solves the model with one of STRATEGIES."""
        if strategy == "full":
            return self.get_solution(time_limit)
        if strategy == "enumerate":
            return self.get_solution_by_enumeration(time_limit)
        if strategy == "hint":
            return self.get_solution_with_hint(time_limit)
        raise ValueError(f"Unknown strategy {strategy}")

    def get_solution(
        self, time_limit: Optional[int] = None
    ) -> Optional[ManufacturingSolution]:
        solver = self._new_solver(time_limit)
        status = solver.solve(self.model)
        return self._extract_solution(solver, status)

    def get_solution_by_enumeration(
        self, time_limit: Optional[float] = None
    ) -> Optional[ManufacturingSolution]:
        """Solves a restricted model for each number of drawers per box generated
        by iter_drawer_configurations, in its order, and returns the best
        solution. The time limit is global: each restricted model can use all the
        remaining time. Each restricted model only looks for solutions better than
        the best one so far: the configurations whose configuration_lower_bound
        is not better than the best solution are skipped. The solution is optimal among the generated configurations if
        all of them have been solved to optimality, proven infeasible or skipped."""
        start_time = time.time()
        best = None
        all_closed = True
        use_relaxed_bound = True
        user_time = 0.0
        for config in self.iter_drawer_configurations():
            # only look for solutions strictly better than the best one so far:
            # a configuration that cannot improve is proven infeasible
            objective_ub = None
            if best is not None:
                objective_ub = round(best[0].objective_value) - 1
            objective_lb = self.configuration_lower_bound(config)
            if objective_lb is None or (
                objective_ub is not None and objective_lb > objective_ub
            ):
                logging.info(
                    f"configuration {config}: skipped, lower bound {objective_lb}"
                )
                continue
            # the relaxed lower bound is only computed to skip a configuration
            # that cannot improve the best solution, and it is no longer tried
            # once it does not close within its time limit
            if objective_ub is not None and use_relaxed_bound:
                relaxed_time_limit = RELAXED_BOUND_TIME_LIMIT
                if time_limit is not None:
                    relaxed_time_limit = min(
                        relaxed_time_limit,
                        time_limit - (time.time() - start_time),
                    )
                if relaxed_time_limit > 0:
                    relaxed_lb, closed = self.relaxed_lower_bound(
                        config, relaxed_time_limit
                    )
                    use_relaxed_bound = closed
                    if relaxed_lb > objective_ub:
                        logging.info(
                            f"configuration {config}: skipped, relaxed lower bound "
                            f"{relaxed_lb}"
                        )
                        continue

            config_time_limit = None
            if time_limit is not None:
                remaining = time_limit - (time.time() - start_time)
                if remaining <= 0:
                    all_closed = False
                    break
                config_time_limit = remaining

            solver = self._new_solver(config_time_limit)
            # the lower bound is not added to the model: on these instances it
            # slows down CP-SAT
            status = solver.solve(self.restricted_model(config, objective_ub))
            user_time += solver.user_time
            logging.info(
                f"configuration {config}: {solver.status_name(status)}, "
                f"objective {solver.objective_value if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None}, "
                f"{solver.wall_time:.1f}s"
            )
            if status not in (cp_model.OPTIMAL, cp_model.INFEASIBLE):
                all_closed = False
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) and (
                best is None or solver.objective_value < best[0].objective_value
            ):
                best = (solver, status)

        if best is None:
            print("No solution found.")
            return None
        solver, status = best
        return self._extract_solution(
            solver, status, is_optimal=all_closed, user_time=user_time
        )

    def get_solution_with_hint(
        self, time_limit: Optional[float] = None
    ) -> Optional[ManufacturingSolution]:
        """Solves the restricted model of the first drawer configuration,
        then solves the full model using that solution as a hint and looking only
        for solutions at least as good. The solution of the restricted model is
        also a solution of the full model, so the hint is a valid solution, and it
        is returned if the full model finds nothing. The time limit is global:
        the full model gets the time left by the restricted one."""
        config = next(self.iter_drawer_configurations(), None)
        if config is None:
            print("No solution found.")
            return None

        start_time = time.time()
        hint_solver = self._new_solver(time_limit)
        hint_status = hint_solver.solve(self.restricted_model(config))
        logging.info(
            f"hint configuration {config}: "
            f"{hint_solver.status_name(hint_status)}, {hint_solver.wall_time:.1f}s"
        )
        has_hint = hint_status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        user_time = hint_solver.user_time

        remaining = None
        if time_limit is not None:
            remaining = time_limit - (time.time() - start_time)
        if remaining is None or remaining > 0:
            if has_hint:
                model = self.restricted_model(
                    None, round(hint_solver.objective_value)
                )
                for var_idx, value in enumerate(hint_solver.response_proto.solution):
                    model.add_hint(model.get_int_var_from_proto_index(var_idx), value)
            else:
                model = self.model

            solver = self._new_solver(remaining)
            status = solver.solve(model)
            user_time += solver.user_time
            logging.info(
                f"full model with hint: {solver.status_name(status)}, "
                f"{solver.wall_time:.1f}s"
            )
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return self._extract_solution(solver, status, user_time=user_time)

        if has_hint:
            return self._extract_solution(
                hint_solver, hint_status, is_optimal=False, user_time=user_time
            )
        print("No solution found.")
        return None

    def _extract_solution(
        self,
        solver: cp_model.CpSolver,
        status,
        is_optimal: Optional[bool] = None,
        user_time: Optional[float] = None,
    ) -> Optional[ManufacturingSolution]:
        if is_optimal is None:
            is_optimal = status == cp_model.OPTIMAL
        if user_time is None:
            user_time = solver.user_time
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(
                f"{'Optimal' if status == cp_model.OPTIMAL else 'Feasible'} solution found."
            )

            box_to_drawers = {box.box: [] for box in self.boxes}
            drawer_box_mapping = []
            drawer_idx_offset = 0
            for box_idx, num_drawers_var in enumerate(self.drawers_per_box_vars):
                num_drawers = solver.value(num_drawers_var)
                assert 1 <= num_drawers
                for i in range(num_drawers):
                    drawer = self.drawers[drawer_idx_offset + i]
                    box = self.boxes[box_idx]
                    drawer_box_mapping.append(
                        solution.DrawerWithBox(drawer.drawer, box.box)
                    )
                    box_to_drawers[box.box].append(drawer.drawer)
                drawer_idx_offset += num_drawers
            drawer_box_mapping.sort(key=lambda d: d.drawer)

            relative_to_absolute_drawer = {}
            for box_idx, box in enumerate(self.boxes):
                box_to_drawers[box.box].sort()
                for i, drawer in enumerate(box_to_drawers[box.box]):
                    relative_to_absolute_drawer[(box_idx, i)] = drawer

            replenishments = []
            for box_idx, box in enumerate(self.boxes):
                for drawer_idx in range(self.max_num_drawers[box_idx]):
                    start_vars = self.replenish_start_vars[box_idx][drawer_idx]
                    for order_idx, start_var in start_vars.items():
                        if solver.value(start_var):
                            drawer = relative_to_absolute_drawer[(box_idx, drawer_idx)]
                            replenishments.append(
                                solution.Replenishment(
                                    drawer, box.box, self.orders[order_idx].id
                                )
                            )
            # replenishments.sort(key=lambda r: (r.start, r.drawer))

            box_constructions = []
            for order_idx, order in enumerate(self.orders):
                box_idx = self.box_index[order.box]
                used_drawer_idx = None
                for drawer_idx in range(self.max_num_drawers[box_idx]):
                    if solver.value(self.is_used_vars[box_idx][drawer_idx][order_idx]):
                        assert used_drawer_idx is None
                        used_drawer_idx = drawer_idx

                assert used_drawer_idx is not None
                box_constructions.append(
                    relative_to_absolute_drawer[(box_idx, used_drawer_idx)]
                )

            replenish_groups_ub = len(self.orders) // (
                2 * self.instance.box_constructions_per_replenishment
            )
            replenish_groups_lb = math.ceil(
                len(self.orders)
                / float(max(self.drawer_capacities) * len(self.drawers))
                - 1
            )
            # replenish_groups_lb = max(replenish_groups_lb, solver.best_objective_bound)
            index_of_fragmentation = (
                (solver.objective_value - replenish_groups_lb)
                / (replenish_groups_ub - replenish_groups_lb)
                * 100
            )

            solver_info = solution.SolverInfo(
                solver.objective_value, solver.best_objective_bound, user_time
            )

            return ManufacturingSolution(
                self.instance.operators,
                self.instance.boxes,
                self.instance.drawers,
                self.instance.drawer_capacities,
                self.instance.box_constructions_per_replenishment,
                self.instance.box_filling_durations,
                self.instance.minimum_remaining_boxes,
                self.instance.orders,
                self.operator_order_lists,
                is_optimal,
                index_of_fragmentation,
                drawer_box_mapping,
                replenishments,
                box_constructions,
                solver_info,
            )

        else:
            print("No solution found.")
            return None
