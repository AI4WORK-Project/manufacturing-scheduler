from manufacturing import ManufacturingInstance, ManufacturingSolution

import collections
from typing import List, Tuple, Dict, Any, Optional, Iterator
from ortools.sat.python import cp_model

from manufacturing.dataclasses import solution


order_type = collections.namedtuple(
    "order_type", "id box operator filling_duration start"
)
optional_activity_type = collections.namedtuple(
    "optional_activity_type", "start duration interval is_present params"
)


class ManufacturingSchedulingFactory:

    def __init__(self, instance: ManufacturingInstance):
        self.instance = instance

        self.num_drawers = len(instance.drawers)
        self.orders = self.get_machine_order_list()
        self.makespan = self.instance.box_construction_duration * len(self.orders)
        self.boxes: List[str] = sorted(list(set(order.box for order in self.orders)))
        self.max_drawers_per_box = self.num_drawers - len(self.boxes) + 1

        drawer_capacities = {
            dc.box: dc.capacity for dc in self.instance.drawer_capacities
        }
        self.drawer_capacities = [drawer_capacities[box] for box in self.boxes]
        self.first_eligible_empty_drawer_orders = (
            self.get_first_eligible_empty_drawer_orders()
        )

        self.count_used_drawer_cache = {}

        (
            self.model,
            self.replenishments,
            self.replenishment_position_vars,
            self.drawer_contains_box_vars,
            self.order_uses_drawer_vars,
        ) = self.get_optimization_model()

    def get_machine_order_list(self) -> List[order_type]:
        box_filling_durations = dict(
            (bfd.box, bfd.filling_duration)
            for bfd in self.instance.box_filling_durations
        )

        orders = []
        for operator_order_list in self.instance.operator_order_lists:
            start = 0
            for order in operator_order_list.orders:
                orders.append(
                    order_type(
                        id=order.id,
                        box=order.box,
                        operator=operator_order_list.operator,
                        filling_duration=box_filling_durations[order.box],
                        start=start,
                    )
                )
                start += box_filling_durations[order.box]

        orders.sort(key=lambda order: (order.start, order.operator))
        return orders

    def get_first_eligible_empty_drawer_orders(self) -> List[int]:
        counter = [0] * len(self.boxes)
        first_eligible_empty_drawer_orders = [-1] * len(self.boxes)
        for order_idx, order in enumerate(self.orders):
            box_idx = self.boxes.index(order.box)
            if counter[box_idx] == self.drawer_capacities[box_idx]:
                first_eligible_empty_drawer_orders[box_idx] = order_idx
            counter[box_idx] += 1
        return first_eligible_empty_drawer_orders

    def add_replenishment(
        self,
        model: cp_model.CpModel,
        name: str,
        window: Tuple[int, int],
        params: Dict[str, Any] = {},
    ) -> optional_activity_type:
        """Adds an optional replenishment activity within a specified time window."""

        # start_window, end_window = window
        # start_var = model.new_int_var(
        #     start_window, end_window - self.instance.replenish_duration, "start_" + name
        # )
        # start_var = model.new_int_var_from_domain(
        #     cp_model.Domain.from_values(
        #         [
        #             start
        #             for start, end in self.replenishment_intervals_in_window2(window)
        #         ]
        #     ),
        #     "start_" + name,
        # )
        is_present_var = model.new_bool_var("is_present_" + name)
        # interval_var = model.new_optional_fixed_size_interval_var(
        #     start_var,
        #     self.instance.replenish_duration,
        #     is_present_var,
        #     "interval_" + name,
        # )

        return optional_activity_type(
            start=None,
            duration=self.instance.replenish_duration,
            interval=None,
            is_present=is_present_var,
            params=params,
        )

    def replenishment_intervals_in_window(
        self, window: Tuple[int, int]
    ) -> Iterator[Tuple[int, int]]:
        """
        Yields all time intervals within the specified window during which replenishment
        can be performed.
        """

        def next_tp(tp):
            t = 0
            for order in self.orders:
                if t > tp:
                    return t
                if (t + self.instance.box_construction_duration) > tp:
                    return t + self.instance.box_construction_duration
                t += self.instance.box_construction_duration
            return None

        start_window, end_window = window
        if (end_window - start_window) < self.instance.replenish_duration:
            return

        start_prev = start_window
        start_lb = start_window
        start_ub = next_tp(start_prev)
        end_prev = start_window + self.instance.replenish_duration
        end_lb = start_lb + self.instance.replenish_duration
        # if end_prev is a timepoint, end_ub = end_lb
        end_ub = next_tp(end_prev - 1)

        while (
            end_window - start_lb
        ) >= self.instance.replenish_duration and end_ub is not None:
            yield start_lb, min(end_window, end_ub)

            if (start_ub - start_lb) <= (end_ub - end_lb):
                start_prev = start_ub
                start_lb = start_ub
                start_ub = next_tp(start_ub)
                end_lb = start_lb + self.instance.replenish_duration - 1
            else:
                end_prev = end_ub
                end_lb = end_ub
                end_ub = next_tp(end_ub)
                start_lb = end_lb - self.instance.replenish_duration + 1

    def replenishment_intervals_in_window2(
        self, window: Tuple[int, int]
    ) -> Iterator[Tuple[int, int]]:
        """
        Yields all time intervals within the specified window during which replenishment
        can be performed.
        """

        start_window, end_window = window
        for start in range(
            start_window,
            end_window + 1 - self.instance.replenish_duration,
            self.instance.replenish_duration,
        ):
            yield start, start + self.instance.replenish_duration

    def count_used_drawer(
        self,
        box_idx: int,
        drawer: int,
        start: int,
        end: int,
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
    ) -> cp_model.LinearExpr:
        """Counts the number of times a specified drawer is used within a given time interval."""

        cache_key = (box_idx, drawer, start, end)
        if cache_key in self.count_used_drawer_cache:
            return self.count_used_drawer_cache[cache_key]

        start_idx = start // self.instance.box_construction_duration
        if start % self.instance.box_construction_duration != 0:
            start_idx += 1

        end_idx = end // self.instance.box_construction_duration
        if end % self.instance.box_construction_duration == 0:
            end_idx -= 1
        end_idx = min(len(self.orders) - 1, end_idx)

        order_idxs = []
        for order_idx in range(start_idx, end_idx + 1):
            if self.orders[order_idx].box == self.boxes[box_idx]:
                order_idxs.append(order_idx)
        self.count_used_drawer_cache[cache_key] = sum(
            order_uses_drawer_vars[order_idx][drawer] for order_idx in order_idxs
        )

        return self.count_used_drawer_cache[cache_key]

    def orders_within_time_window(
        self, window: Tuple[int, int]
    ) -> List[Tuple[int, order_type]]:
        """
        Returns the box construction activities that use the specified
        box type within the time window.
        """

        start_window, end_window = window
        start_idx = start_window // self.instance.box_construction_duration
        end_idx = end_window // self.instance.box_construction_duration
        if end_window % self.instance.box_construction_duration == 0:
            end_idx -= 1
        end_idx = min(len(self.orders) - 1, end_idx)

        return [(i, self.orders[i]) for i in range(start_idx, end_idx + 1)]

    def add_quality_metric(
        self,
        model: cp_model.CpModel,
        replenishments: List[List[optional_activity_type]],
    ):
        """Sets the objective of the model to optimize."""

        # model.maximize(
        #     sum(drawer_vars[-1] for drawer_vars in self.remaining_boxes_vars)
        # )

        # w1, w2 = 10, 1
        # model.minimize(
        #     w1
        #     * sum(
        #         r
        #         for window_replenishments in self.replenishments
        #         for replenishments in window_replenishments
        #         for r in replenishments
        #         if r is not None
        #     )
        #     - w2 * sum(drawer_vars[-1] for drawer_vars in self.remaining_boxes_vars)
        # )

        # minimize the number of replenishments
        model.minimize(
            sum(
                replenishment.is_present
                for window_replenishments in replenishments
                for replenishment in window_replenishments
            )
        )

    def define_drawer_contains_box_vars(
        self, model: cp_model.CpModel
    ) -> List[List[cp_model.IntVar]]:
        drawer_contains_box_vars = []
        for box_idx, box in enumerate(self.boxes):
            drawer_contains_box_vars.append([])
            for drawer in range(self.max_drawers_per_box):
                drawer_contains_box_vars[box_idx].append(
                    model.new_bool_var(f"drawer{drawer}_contains_box{box}")
                )

            # TODO: remove variable since it is True
            model.add_bool_and(drawer_contains_box_vars[-1][0])

            for drawer in range(1, self.max_drawers_per_box):
                model.add_implication(
                    drawer_contains_box_vars[box_idx][drawer],
                    drawer_contains_box_vars[box_idx][drawer - 1],
                )

        all_vars = []
        for vv in drawer_contains_box_vars:
            all_vars += vv
        model.add(sum(all_vars) == self.num_drawers)

        return drawer_contains_box_vars

    def define_order_uses_drawer_vars(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
    ) -> List[List[cp_model.IntVar]]:
        order_uses_drawer_vars = []
        for order_idx, order in enumerate(self.orders):
            order_uses_drawer_vars.append([])
            for drawer in range(self.max_drawers_per_box):
                order_uses_drawer = model.new_bool_var(
                    f"order{order_idx}_uses_drawer{drawer}"
                )
                model.add_implication(
                    order_uses_drawer,
                    drawer_contains_box_vars[self.boxes.index(order.box)][drawer],
                )
                order_uses_drawer_vars[order_idx].append(order_uses_drawer)

            # enforce only one drawer used by each order
            model.add_exactly_one(order_uses_drawer_vars[-1])

        return order_uses_drawer_vars

    def initialize_remaining_boxes_vars(self) -> List[List[List[cp_model.IntVar]]]:
        remaining_boxes_vars = []
        for box_idx, box in enumerate(self.boxes):
            remaining_boxes_vars.append([])
            for drawer in range(self.max_drawers_per_box):
                remaining_boxes_vars[box_idx].append([self.drawer_capacities[box_idx]])

        return remaining_boxes_vars

    def enforce_drawer_selection_policy(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
        remaining_boxes_vars: List[List[List[cp_model.IntVar]]],
        replenishments: List[List[optional_activity_type]],
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]],
    ):
        drawer_is_empty_vars = self.get_drawer_empty_vars_outside_replenish_windows(
            model, order_uses_drawer_vars, remaining_boxes_vars
        )
        drawer_is_empty_vars.update(
            self.get_drawer_empty_vars_during_replenish_windows(
                model,
                order_uses_drawer_vars,
                remaining_boxes_vars,
                replenishments,
                replenishment_position_vars,
            )
        )

        for order_idx, order in enumerate(self.orders):
            box_idx = self.boxes.index(order.box)
            for drawer in range(self.max_drawers_per_box):
                order_uses_drawer = order_uses_drawer_vars[order_idx][drawer]
                drawer_contains_box = drawer_contains_box_vars[box_idx][drawer]

                prev_drawers_empty = [
                    drawer_is_empty_vars[(prev_drawer, order_idx)]
                    for prev_drawer in range(drawer)
                ]

                prev_drawers_are_not_empty = any(
                    map(lambda v: isinstance(v, bool), prev_drawers_empty)
                )
                if prev_drawers_are_not_empty:
                    continue

                if isinstance(drawer_is_empty_vars[(drawer, order_idx)], bool):
                    drawer_is_not_empty = True
                else:
                    drawer_is_not_empty = drawer_is_empty_vars[
                        (drawer, order_idx)
                    ].negated()

                order_start = self.instance.box_construction_duration * order_idx
                order_end = order_start + self.instance.box_construction_duration
                replenishment, pos_vars = self.overlapping_replenishment(
                    (order_start, order_end),
                    box_idx,
                    drawer,
                    replenishments,
                    replenishment_position_vars,
                )

                if replenishment is None:
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [drawer_contains_box, drawer_is_not_empty] + prev_drawers_empty
                    )
                else:
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [
                            drawer_contains_box,
                            drawer_is_not_empty,
                            replenishment.is_present.negated(),
                        ]
                        + prev_drawers_empty
                    )
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [
                            drawer_contains_box,
                            drawer_is_not_empty,
                            replenishment.is_present,
                        ]
                        + [pos_var.negated() for pos_var in pos_vars]
                        + prev_drawers_empty
                    )

    def overlapping_replenishment(
        self,
        window: Tuple[int, int],
        box_idx: int,
        drawer: int,
        replenishments: List[List[optional_activity_type]],
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]],
    ) -> Tuple[Optional[optional_activity_type], List[cp_model.IntVar]]:
        start, end = window
        position_vars = []
        for window_idx, window_replenishments in enumerate(replenishments):
            replenishment = self.get_replenishment(
                replenishments, window_idx, box_idx, drawer
            )
            for pos_var, lb, ub in replenishment_position_vars[
                replenishment.is_present.name
            ]:
                if (lb <= start < ub) or (start <= lb < end):
                    position_vars.append(pos_var)

            if len(position_vars) > 0:
                return replenishment, position_vars

        return None, []

    def get_replenishment(
        self,
        replenishments: List[List[optional_activity_type]],
        window_idx: int,
        box_idx: int,
        drawer: int,
    ) -> optional_activity_type:
        for replenishment in replenishments[window_idx]:
            if (
                replenishment.params["box_idx"] == box_idx
                and replenishment.params["drawer"] == drawer
            ):
                return replenishment
        return None

    def get_drawer_empty_vars_outside_replenish_windows(
        self,
        model: cp_model.CpModel,
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
        remaining_boxes_vars: List[List[List[cp_model.IntVar]]],
    ) -> Dict[Tuple[int, int], cp_model.IntVar]:
        drawer_is_empty_vars = dict()

        prev_window_end = 0
        for window_idx, (start_window, end_window) in enumerate(
            [(w.start, w.end) for w in self.instance.replenish_windows]
            + [(self.makespan, 0)]
        ):
            for i in range(
                (start_window - prev_window_end)
                // self.instance.box_construction_duration
            ):
                order_idx = (
                    prev_window_end // self.instance.box_construction_duration + i
                )
                if order_idx >= len(self.orders):
                    break

                box_idx = self.boxes.index(self.orders[order_idx].box)
                for drawer in range(self.max_drawers_per_box):
                    if order_idx < self.first_eligible_empty_drawer_orders[box_idx]:
                        drawer_is_empty_vars[(drawer, order_idx)] = False
                    else:
                        count = self.count_used_drawer(
                            box_idx,
                            drawer,
                            start=prev_window_end,
                            end=order_idx * self.instance.box_construction_duration,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )

                        drawer_empty = model.new_bool_var(
                            f"drawer{drawer}_is_empty@order{order_idx}"
                        )
                        drawer_is_empty_vars[(drawer, order_idx)] = drawer_empty
                        model.add(
                            (remaining_boxes_vars[box_idx][drawer][window_idx] - count)
                            == 0
                        ).only_enforce_if(drawer_empty)
                        model.add(
                            (remaining_boxes_vars[box_idx][drawer][window_idx] - count)
                            > 0
                        ).only_enforce_if(drawer_empty.negated())

            if end_window % self.instance.box_construction_duration == 0:
                prev_window_end = end_window
            else:
                prev_window_end = (
                    end_window
                    + self.instance.box_construction_duration
                    - (end_window % self.instance.box_construction_duration)
                )

        return drawer_is_empty_vars

    def get_drawer_empty_vars_during_replenish_windows(
        self,
        model: cp_model.CpModel,
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
        remaining_boxes_vars: List[List[List[cp_model.IntVar]]],
        replenishments: List[List[optional_activity_type]],
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]],
    ) -> Dict[Tuple[int, int], cp_model.IntVar]:
        drawer_is_empty_vars = dict()
        prev_window_end = 0
        for window_idx, replenish_window in enumerate(self.instance.replenish_windows):
            for order_idx, order in self.orders_within_time_window(
                (replenish_window.start, replenish_window.end)
            ):
                box_idx = self.boxes.index(order.box)
                for drawer in range(self.max_drawers_per_box):
                    if order_idx < self.first_eligible_empty_drawer_orders[box_idx]:
                        drawer_empty = False
                    else:
                        drawer_empty = model.new_bool_var(
                            f"drawer{drawer}_is_empty@order{order_idx}"
                        )
                    drawer_is_empty_vars[(drawer, order_idx)] = drawer_empty

            for box_idx, box in enumerate(self.boxes):
                for drawer in range(self.max_drawers_per_box):
                    replenishment = self.get_replenishment(
                        replenishments, window_idx, box_idx, drawer
                    )

                    for order_idx, order in self.orders_within_time_window(
                        (replenish_window.start, replenish_window.end)
                    ):
                        if order.box != box:
                            continue

                        order_start = (
                            self.instance.box_construction_duration * order_idx
                        )
                        order_end = (
                            order_start + self.instance.box_construction_duration
                        )
                        drawer_empty = drawer_is_empty_vars[(drawer, order_idx)]
                        drawer_empty_negated = (
                            True
                            if isinstance(drawer_empty, bool)
                            else drawer_empty.negated()
                        )

                        count1 = self.count_used_drawer(
                            box_idx,
                            drawer,
                            start=prev_window_end,
                            end=order_start,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )
                        remaining_boxes1 = (
                            remaining_boxes_vars[box_idx][drawer][window_idx] - count1
                        )

                        model.add(remaining_boxes1 == 0).only_enforce_if(
                            drawer_empty, replenishment.is_present.negated()
                        )
                        model.add(remaining_boxes1 > 0).only_enforce_if(
                            drawer_empty_negated, replenishment.is_present.negated()
                        )

                        for i, (pos_var, lb, ub) in enumerate(
                            replenishment_position_vars[replenishment.is_present.name]
                        ):
                            remaining_boxes = None
                            if order_end <= lb:
                                remaining_boxes = remaining_boxes1
                            elif order_start >= ub:
                                count2 = self.count_used_drawer(
                                    box_idx,
                                    drawer,
                                    start=ub,
                                    end=order_start,
                                    order_uses_drawer_vars=order_uses_drawer_vars,
                                )
                                remaining_boxes = (
                                    self.drawer_capacities[box_idx] - count2
                                )

                            if remaining_boxes is not None:
                                model.add(remaining_boxes == 0).only_enforce_if(
                                    drawer_empty, replenishment.is_present, pos_var
                                )
                                model.add(remaining_boxes > 0).only_enforce_if(
                                    drawer_empty_negated,
                                    replenishment.is_present,
                                    pos_var,
                                )

            prev_window_end = replenish_window.end

        return drawer_is_empty_vars

    def ensure_sufficient_boxes_after_final_replenish_window(
        self,
        model: cp_model.CpModel,
        remaining_boxes_vars: List[List[List[cp_model.IntVar]]],
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
    ):
        """
        Ensures that each drawer has a sufficient number of remaining boxes
        to fulfill orders from the final replenishment window until the end of the plan.
        """

        for box_idx, box in enumerate(self.boxes):
            for drawer in range(self.max_drawers_per_box):
                last_window_end = self.instance.replenish_windows[-1].end
                prev_r_boxes_var = remaining_boxes_vars[box_idx][drawer][-1]
                used_boxes = self.count_used_drawer(
                    box_idx,
                    drawer,
                    start=last_window_end,
                    end=self.makespan,
                    order_uses_drawer_vars=order_uses_drawer_vars,
                )
                last_r_boxes_var = model.new_int_var(
                    0,
                    self.drawer_capacities[box_idx],
                    f"remaining_boxes_box{box}_drawer{drawer}@end",
                )
                remaining_boxes_vars[drawer].append(last_r_boxes_var)
                model.add(last_r_boxes_var == (prev_r_boxes_var - used_boxes))

    def get_optimization_model(self):
        model = cp_model.CpModel()
        drawer_contains_box_vars = self.define_drawer_contains_box_vars(model)
        order_uses_drawer_vars = self.define_order_uses_drawer_vars(
            model, drawer_contains_box_vars
        )
        remaining_boxes_vars = self.initialize_remaining_boxes_vars()

        replenishments: List[List[optional_activity_type]] = []
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]] = {}
        prev_end_window = 0
        for window_idx, replenish_window in enumerate(self.instance.replenish_windows):
            start_window, end_window = replenish_window.start, replenish_window.end
            replenishments.append([])

            for box_idx, box in enumerate(self.boxes):
                for drawer in range(self.max_drawers_per_box):
                    # create a new replenishment activity
                    replenishment_name = f"replenishment_box{box}_drawer{drawer}({start_window},{end_window})"
                    replenishment = self.add_replenishment(
                        model,
                        replenishment_name,
                        (start_window, end_window),
                        params={"box_idx": box_idx, "drawer": drawer},
                    )
                    model.add_implication(
                        replenishment.is_present,
                        drawer_contains_box_vars[box_idx][drawer],
                    )
                    replenishments[window_idx].append(replenishment)

                    prev_r_boxes_var = remaining_boxes_vars[box_idx][drawer][-1]
                    # create a new variable to track the remaining boxes in the drawer at the end of the window
                    r_boxes_var = model.new_int_var(
                        0,
                        self.drawer_capacities[box_idx],
                        f"remaining_boxes_box{box}_drawer{drawer}@{end_window}",
                    )
                    remaining_boxes_vars[box_idx][drawer].append(r_boxes_var)

                    replenishment_position_vars[replenishment.is_present.name] = []
                    for lb, ub in self.replenishment_intervals_in_window2(
                        (start_window, end_window)
                    ):
                        pos_var = model.new_bool_var(f"{replenishment_name}<{lb},{ub}>")
                        replenishment_position_vars[
                            replenishment.is_present.name
                        ].append((pos_var, lb, ub))

                        # enforce relpenishment is performed between [lb, ub]
                        # model.add(replenishment.start >= lb).only_enforce_if(pos_var)
                        # model.add(
                        #     (replenishment.start + self.instance.replenish_duration) <= ub
                        # ).only_enforce_if(pos_var)

                        # enforce used boxes before replenishment do not exceed drawer ramaining capacity
                        used_boxes_before = self.count_used_drawer(
                            box_idx,
                            drawer,
                            start=prev_end_window,
                            end=lb,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )
                        model.add(
                            prev_r_boxes_var >= used_boxes_before
                        ).only_enforce_if(pos_var)

                        # calculate the remaining boxes at window end
                        used_boxes_after = self.count_used_drawer(
                            box_idx,
                            drawer,
                            start=ub,
                            end=end_window,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )
                        model.add(
                            r_boxes_var
                            == (
                                remaining_boxes_vars[box_idx][drawer][0]
                                - used_boxes_after
                            )
                        ).only_enforce_if(pos_var)

                        # enforce activities in [lb, ub] do not use that drawer
                        model.add_bool_and(
                            order_uses_drawer_vars[order_idx][drawer].negated()
                            for order_idx, order in self.orders_within_time_window(
                                (lb, ub)
                            )
                            if order.box == self.boxes[box_idx]
                        ).only_enforce_if(pos_var)

                    # BUG: add_exactly_one with only_enforce_if doesn't work
                    # model.add_exactly_one(
                    #     p[0]
                    #     for p in replenishment_position_vars[replenishment.is_present.name]
                    # ).only_enforce_if(replenishment.is_present)

                    # enforce exactly one position var when replenishment is performed
                    model.add(
                        sum(
                            pos_var
                            for pos_var, lb, ub in replenishment_position_vars[
                                replenishment.is_present.name
                            ]
                        )
                        == 1
                    ).only_enforce_if(replenishment.is_present)
                    model.add_bool_and(
                        pos_var.negated()
                        for pos_var, lb, ub in replenishment_position_vars[
                            replenishment.is_present.name
                        ]
                    ).only_enforce_if(replenishment.is_present.negated())

                    # update remaining boxes var when replenishment is not performed
                    all_used_boxes = self.count_used_drawer(
                        box_idx,
                        drawer,
                        start=prev_end_window,
                        end=end_window,
                        order_uses_drawer_vars=order_uses_drawer_vars,
                    )
                    model.add(
                        r_boxes_var == (prev_r_boxes_var - all_used_boxes)
                    ).only_enforce_if(replenishment.is_present.negated())

            # no overlap between replenishments
            # model.add_no_overlap(
            #     map(lambda act: act.interval, replenishments[window_idx])
            # )
            for i, _ in enumerate(
                self.replenishment_intervals_in_window2((start_window, end_window))
            ):
                model.add_at_most_one(
                    [
                        replenishment_position_vars[replenishment.is_present.name][i][0]
                        for replenishment in replenishments[window_idx]
                    ]
                )

            prev_end_window = end_window

        self.ensure_sufficient_boxes_after_final_replenish_window(
            model, remaining_boxes_vars, order_uses_drawer_vars
        )
        self.enforce_drawer_selection_policy(
            model,
            drawer_contains_box_vars,
            order_uses_drawer_vars,
            remaining_boxes_vars,
            replenishments,
            replenishment_position_vars,
        )
        self.add_quality_metric(model, replenishments)

        return (
            model,
            replenishments,
            replenishment_position_vars,
            drawer_contains_box_vars,
            order_uses_drawer_vars,
        )

    def get_solution(
        self, time_limit: Optional[int] = None
    ) -> Optional[ManufacturingSolution]:
        solver = cp_model.CpSolver()
        if time_limit is not None:
            solver.parameters.max_time_in_seconds = time_limit
        # solver.parameters.log_search_progress = True
        # solver.parameters.num_workers = 1
        # solver.parameters.cp_model_presolve = False
        # solver.parameters.max_presolve_iterations = 1
        # solver.parameters.cp_model_probing_level = 1
        # solver.parameters.presolve_probing_deterministic_time_limit = 5
        # solver.parameters.probing_deterministic_time_limit = 5

        status = solver.solve(self.model)
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(
                f"{'Optimal' if status == cp_model.OPTIMAL else 'Feasible'} solution found."
            )

            drawer_box_mapping = []
            box_drawer_to_drawer = []
            for box_idx, box in enumerate(self.boxes):
                box_drawer_to_drawer.append([])
                for drawer in range(self.max_drawers_per_box):
                    if solver.value(self.drawer_contains_box_vars[box_idx][drawer]):
                        box_drawer_to_drawer[box_idx].append(len(drawer_box_mapping))
                        drawer_box_mapping.append(
                            solution.Drawer(
                                self.instance.drawers[
                                    box_drawer_to_drawer[box_idx][drawer]
                                ],
                                box,
                            )
                        )

            replenishments = []
            for window_idx, window in enumerate(self.instance.replenish_windows):
                for replenishment in self.replenishments[window_idx]:
                    if solver.value(replenishment.is_present):
                        start = None
                        for i, (lb, ub) in enumerate(
                            self.replenishment_intervals_in_window2(
                                (window.start, window.end)
                            )
                        ):
                            if solver.value(
                                self.replenishment_position_vars[
                                    replenishment.is_present.name
                                ][i][0]
                            ):
                                start = lb
                                break
                        assert start is not None
                        drawer = box_drawer_to_drawer[replenishment.params["box_idx"]][
                            replenishment.params["drawer"]
                        ]
                        replenishments.append(
                            solution.Replenishment(
                                self.instance.drawers[drawer],
                                self.boxes[replenishment.params["box_idx"]],
                                start,
                            )
                        )

            box_constructions = []
            for order_idx, order in enumerate(self.orders):
                for drawer in range(self.max_drawers_per_box):
                    if solver.value(self.order_uses_drawer_vars[order_idx][drawer]):
                        drawer_idx = box_drawer_to_drawer[self.boxes.index(order.box)][
                            drawer
                        ]
                        box_constructions.append(self.instance.drawers[drawer_idx])
                        break

            solver_info = solution.SolverInfo(
                solver.objective_value, solver.best_objective_bound, solver.user_time
            )

            return ManufacturingSolution(
                status == cp_model.OPTIMAL,
                drawer_box_mapping,
                replenishments,
                box_constructions,
                self.instance.orders,
                self.instance.operator_order_lists,
                solver_info,
            )

        else:
            print("No solution found.")
            return None
