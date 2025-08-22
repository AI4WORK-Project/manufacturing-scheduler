from manufacturing import ManufacturingInstance, ManufacturingSolution

import collections
from typing import List, Tuple, Dict, Any, Optional
from ortools.sat.python import cp_model

from manufacturing.dataclasses import solution
from datetime import datetime


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

        self.count_used_drawer_cache = {}

        (
            self.model,
            self.replenishments,
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

    def add_replenishment(
        self,
        model: cp_model.CpModel,
        name: str,
        window: Tuple[int, int],
        params: Dict[str, Any] = None,
    ) -> optional_activity_type:
        """Adds an optional replenishment activity within a specified time window."""

        start_window, end_window = window
        start_var = model.new_int_var(
            start_window, end_window - self.instance.replenish_duration, "start_" + name
        )
        is_present_var = model.new_bool_var("is_present_" + name)
        interval_var = model.new_optional_fixed_size_interval_var(
            start_var,
            self.instance.replenish_duration,
            is_present_var,
            "interval_" + name,
        )

        return optional_activity_type(
            start=start_var,
            duration=self.instance.replenish_duration,
            interval=interval_var,
            is_present=is_present_var,
            params=params,
        )

    def replenishment_intervals_in_window(self, window: Tuple[int, int]):
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

    def replenishment_intervals_in_window2(self, window: Tuple[int, int]):
        """
        Yields all time intervals within the specified window during which replenishment
        can be performed.
        """

        start_window, end_window = window
        for start in range(
            start_window,
            end_window + 1 - self.instance.replenish_duration,
            self.instance.box_construction_duration,
        ):
            yield start, start + self.instance.replenish_duration

    def count_used_drawer(
        self,
        drawer: int,
        start: int,
        end: int,
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
    ):
        """Counts the number of times a specified drawer is used within a given time interval."""

        cache_key = (drawer, start, end)
        if cache_key in self.count_used_drawer_cache:
            return self.count_used_drawer_cache[cache_key]

        start_idx = start // self.instance.box_construction_duration
        if start % self.instance.box_construction_duration != 0:
            start_idx += 1

        end_idx = end // self.instance.box_construction_duration
        if end % self.instance.box_construction_duration == 0:
            end_idx -= 1

        self.count_used_drawer_cache[cache_key] = sum(
            order_uses_drawer[drawer]
            for order_uses_drawer in order_uses_drawer_vars[start_idx : end_idx + 1]
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
        for drawer in range(self.num_drawers):
            drawer_contains_box_vars.append(
                [
                    model.new_bool_var(f"drawer{drawer}_contains_box{box}")
                    for box in self.boxes
                ]
            )
            # enforce each drawer contains exactly one box
            model.add_exactly_one(drawer_contains_box_vars[-1])

        # TODO
        # for drawer in range(self.num_drawers):
        #     for i in range(drawer + 1, len(self.boxes)):
        #         model.add_bool_and(drawer_contains_box_vars[drawer][i].negated())

        # enforce max drawers per box
        print("self.max_drawers_per_box", self.max_drawers_per_box)
        for i in range(len(self.boxes)):
            model.add(
                sum(drawer_contains_box_vars[d][i] for d in range(self.num_drawers))
                <= self.max_drawers_per_box
            )

        # enforce an ordering of the boxes assigned to drawers to reduce
        # the number of equivalent solutions
        for drawer in range(self.num_drawers - 1):
            for i in range(1, len(drawer_contains_box_vars[drawer])):
                # drawer_contains_box_vars[drawer][i] => Or(drawer_contains_box_vars[drawer + 1][i:])
                model.add_bool_or(
                    [drawer_contains_box_vars[drawer][i].negated()]
                    + drawer_contains_box_vars[drawer + 1][i:]
                )

        return drawer_contains_box_vars

    def define_order_uses_drawer_vars(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
    ) -> List[List[cp_model.IntVar]]:
        order_uses_drawer_vars = []
        for i, order in enumerate(self.orders):
            order_uses_drawer_vars.append([])
            for drawer in range(self.num_drawers):
                order_uses_drawer = model.new_bool_var(f"order{i}_uses_drawer{drawer}")
                drawer_contains_box = drawer_contains_box_vars[drawer][
                    self.boxes.index(order.box)
                ]
                model.add_implication(order_uses_drawer, drawer_contains_box)
                order_uses_drawer_vars[-1].append(order_uses_drawer)

            # enforce only one drawer used by each order
            model.add_exactly_one(order_uses_drawer_vars[-1])

        return order_uses_drawer_vars

    def initialize_remaining_boxes_vars(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
    ) -> List[List[cp_model.IntVar]]:
        remaining_boxes_vars = [[] for drawer in range(self.num_drawers)]
        # set the drawer initial capacity to full
        for drawer in range(self.num_drawers):
            initial_capacity = model.new_int_var(
                0,
                max(self.drawer_capacities),
                f"initial_capacity_drawer{drawer}",
            )
            remaining_boxes_vars[drawer].append(initial_capacity)

            # drawer initial capacity depends on box contained
            for box in range(len(self.boxes)):
                model.add(
                    initial_capacity == self.drawer_capacities[box]
                ).only_enforce_if(drawer_contains_box_vars[drawer][box])

        return remaining_boxes_vars

    def enforce_drawer_selection_policy(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
        remaining_boxes_vars: List[List[cp_model.IntVar]],
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
        print("enforce_drawer_selection_policy2:", datetime.now())
        cache = dict()

        for order_idx, order in enumerate(self.orders):
            for drawer in range(self.num_drawers):
                order_uses_drawer = order_uses_drawer_vars[order_idx][drawer]
                drawer_contains_box = drawer_contains_box_vars[drawer][
                    self.boxes.index(order.box)
                ]

                prev_drawers_empty = []
                for prev_drawer in range(
                    max(0, drawer - self.max_drawers_per_box + 1), drawer
                ):
                    # for prev_drawer in range(drawer):
                    prev_drawer_empty = drawer_is_empty_vars[(prev_drawer, order_idx)]
                    prev_drawer_contains_box = drawer_contains_box_vars[prev_drawer][
                        self.boxes.index(order.box)
                    ]
                    cache_key = (
                        id(prev_drawer_contains_box),
                        id(prev_drawer_empty),
                    )
                    if cache_key not in cache:
                        bool_var = model.new_bool_var(
                            f"{id(prev_drawer_contains_box)}_{id(prev_drawer_empty)}"
                        )
                        cache[cache_key] = bool_var
                        # bool_var <=> (prev_drawer_contains_box => prev_drawer_empty)
                        model.add_implication(
                            prev_drawer_contains_box, prev_drawer_empty
                        ).only_enforce_if(bool_var)
                        model.add_bool_and(
                            prev_drawer_contains_box, prev_drawer_empty.negated()
                        ).only_enforce_if(bool_var.negated())
                    else:
                        bool_var = cache[cache_key]

                    prev_drawers_empty.append(bool_var)

                order_start = self.instance.box_construction_duration * order_idx
                order_end = order_start + self.instance.box_construction_duration
                replenishment, pos_vars = self.overlapping_replenishment(
                    (order_start, order_end),
                    drawer,
                    replenishments,
                    replenishment_position_vars,
                )

                if replenishment is None:
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [
                            drawer_contains_box,
                            drawer_is_empty_vars[(drawer, order_idx)].negated(),
                        ]
                        + prev_drawers_empty
                    )
                else:
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [
                            drawer_contains_box,
                            drawer_is_empty_vars[(drawer, order_idx)].negated(),
                            replenishment.is_present.negated(),
                        ]
                        + prev_drawers_empty
                    )
                    model.add_bool_and(order_uses_drawer).only_enforce_if(
                        [
                            drawer_contains_box,
                            drawer_is_empty_vars[(drawer, order_idx)].negated(),
                            replenishment.is_present,
                        ]
                        + [pos_var.negated() for pos_var in pos_vars]
                        + prev_drawers_empty
                    )

                # model.add_bool_and(order_uses_drawer).only_enforce_if(
                #     [
                #         drawer_contains_box,
                #         drawer_is_empty_vars[(drawer, order_idx)].negated(),
                #     ]
                #     + prev_drawers_empty
                # )

    def overlapping_replenishment(
        self,
        window: Tuple[int, int],
        drawer: int,
        replenishments: List[List[optional_activity_type]],
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]],
    ) -> Tuple[Optional[optional_activity_type], List[cp_model.IntVar]]:
        start, end = window
        position_vars = []
        for window_replenishments in replenishments:
            replenishment = window_replenishments[drawer]
            for pos_var, lb, ub in replenishment_position_vars[
                replenishment.interval.name
            ]:
                if (lb <= start < ub) or (start <= lb < end):
                    position_vars.append(pos_var)

            if len(position_vars) > 0:
                return replenishment, position_vars

        return None, []

    def get_drawer_empty_vars_outside_replenish_windows(
        self,
        model: cp_model.CpModel,
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
        remaining_boxes_vars: List[List[cp_model.IntVar]],
    ) -> Dict[Tuple[int, int], cp_model.IntVar]:
        print("get_drawer_empty_vars_outside_replenish_windows:", datetime.now())
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

                for drawer in range(self.num_drawers):
                    count = self.count_used_drawer(
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
                        (remaining_boxes_vars[drawer][window_idx] - count) == 0
                    ).only_enforce_if(drawer_empty)
                    model.add(
                        (remaining_boxes_vars[drawer][window_idx] - count) > 0
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
        remaining_boxes_vars: List[List[cp_model.IntVar]],
        replenishments: List[List[optional_activity_type]],
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]],
    ) -> Dict[Tuple[int, int], cp_model.IntVar]:
        print("get_drawer_empty_vars_during_replenish_windows:", datetime.now())
        counter = 0

        drawer_is_empty_vars = dict()
        prev_window_end = 0
        for window_idx, replenish_window in enumerate(self.instance.replenish_windows):
            print(f"window {window_idx}:", datetime.now())

            print(f"drawer_empty:", datetime.now())
            for drawer in range(self.num_drawers):
                for order_idx, order in self.orders_within_time_window(
                    (replenish_window.start, replenish_window.end)
                ):
                    drawer_empty = model.new_bool_var(
                        f"drawer{drawer}_is_empty@order{order_idx}"
                    )
                    drawer_is_empty_vars[(drawer, order_idx)] = drawer_empty

            print(f"constraints:", datetime.now())
            for drawer in range(self.num_drawers):
                print(f"drawer:", datetime.now())
                replenishment = replenishments[window_idx][drawer]
                print(
                    "len(replenishment_position_vars[replenishment.interval.name])",
                    len(replenishment_position_vars[replenishment.interval.name]),
                )
                for i, (pos_var, lb, ub) in enumerate(
                    replenishment_position_vars[replenishment.interval.name]
                ):
                    for order_idx, order in self.orders_within_time_window(
                        (replenish_window.start, replenish_window.end)
                    ):
                        counter += 1
                        order_start = (
                            self.instance.box_construction_duration * order_idx
                        )
                        order_end = (
                            order_start + self.instance.box_construction_duration
                        )
                        drawer_empty = drawer_is_empty_vars[(drawer, order_idx)]

                        count1 = self.count_used_drawer(
                            drawer,
                            start=prev_window_end,
                            end=order_start,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )
                        remaining_boxes1 = (
                            remaining_boxes_vars[drawer][window_idx] - count1
                        )
                        count2 = self.count_used_drawer(
                            drawer,
                            start=ub,
                            end=order_start,
                            order_uses_drawer_vars=order_uses_drawer_vars,
                        )
                        remaining_boxes2 = remaining_boxes_vars[drawer][0] - count2
                        remaining_boxes = None
                        if order_end <= lb:
                            remaining_boxes = remaining_boxes1
                        elif order_start >= ub:
                            remaining_boxes = remaining_boxes2

                        if remaining_boxes is not None:
                            model.add(remaining_boxes == 0).only_enforce_if(
                                drawer_empty, replenishment.is_present, pos_var
                            )
                            model.add(remaining_boxes > 0).only_enforce_if(
                                drawer_empty.negated(),
                                replenishment.is_present,
                                pos_var,
                            )

                        if i == 0:
                            model.add(remaining_boxes1 == 0).only_enforce_if(
                                drawer_empty, replenishment.is_present.negated()
                            )
                            model.add(remaining_boxes1 > 0).only_enforce_if(
                                drawer_empty.negated(),
                                replenishment.is_present.negated(),
                            )

            prev_window_end = replenish_window.end

        print("counter", counter)
        return drawer_is_empty_vars

    def ensure_sufficient_boxes_after_final_replenish_window(
        self,
        model: cp_model.CpModel,
        remaining_boxes_vars: List[List[cp_model.IntVar]],
        order_uses_drawer_vars: List[List[cp_model.IntVar]],
    ):
        """
        Ensures that each drawer has a sufficient number of remaining boxes
        to fulfill orders from the final replenishment window until the end of the plan.
        """

        for drawer in range(self.num_drawers):
            last_window_end = self.instance.replenish_windows[-1].end
            prev_r_boxes_var = remaining_boxes_vars[drawer][-1]
            used_boxes = self.count_used_drawer(
                drawer,
                start=last_window_end,
                end=self.makespan,
                order_uses_drawer_vars=order_uses_drawer_vars,
            )
            last_r_boxes_var = model.new_int_var(
                0,
                max(self.drawer_capacities),
                f"remaining_boxes{drawer}@end",
            )
            remaining_boxes_vars[drawer].append(last_r_boxes_var)
            model.add(last_r_boxes_var == (prev_r_boxes_var - used_boxes))

    def get_optimization_model(self):
        print("get_optimization_model:", datetime.now())
        model = cp_model.CpModel()
        drawer_contains_box_vars = self.define_drawer_contains_box_vars(model)
        order_uses_drawer_vars = self.define_order_uses_drawer_vars(
            model, drawer_contains_box_vars
        )
        remaining_boxes_vars = self.initialize_remaining_boxes_vars(
            model, drawer_contains_box_vars
        )

        replenishments: List[List[optional_activity_type]] = []
        replenishment_position_vars: Dict[str, Tuple[cp_model.IntVar, int, int]] = {}
        prev_end_window = 0
        for window_idx, replenish_window in enumerate(self.instance.replenish_windows):
            start_window, end_window = replenish_window.start, replenish_window.end
            replenishments.append([])

            for drawer in range(self.num_drawers):
                # create a new replenishment activity
                replenishment_name = (
                    f"replenishment_drawer{drawer}({start_window},{end_window})"
                )
                replenishment = self.add_replenishment(
                    model, replenishment_name, (start_window, end_window)
                )
                replenishments[window_idx].append(replenishment)

                prev_r_boxes_var = remaining_boxes_vars[drawer][-1]
                # create a new variable to track the remaining boxes in the drawer at the end of the window
                r_boxes_var = model.new_int_var(
                    0,
                    max(self.drawer_capacities),
                    f"remaining_boxes{drawer}@{end_window}",
                )
                remaining_boxes_vars[drawer].append(r_boxes_var)

                replenishment_position_vars[replenishment.interval.name] = []
                for lb, ub in self.replenishment_intervals_in_window2(
                    (start_window, end_window)
                ):
                    pos_var = model.new_bool_var(f"{replenishment_name}<{lb},{ub}>")
                    replenishment_position_vars[replenishment.interval.name].append(
                        (pos_var, lb, ub)
                    )

                    # enforce relpenishment is performed between [lb, ub]
                    model.add(replenishment.start >= lb).only_enforce_if(pos_var)
                    model.add(
                        (replenishment.start + self.instance.replenish_duration) <= ub
                    ).only_enforce_if(pos_var)

                    # enforce used boxes before replenishment do not exceed drawer ramaining capacity
                    used_boxes_before = self.count_used_drawer(
                        drawer,
                        start=prev_end_window,
                        end=lb,
                        order_uses_drawer_vars=order_uses_drawer_vars,
                    )
                    model.add(prev_r_boxes_var >= used_boxes_before).only_enforce_if(
                        [replenishment.is_present, pos_var]
                    )

                    # calculate the remaining boxes at window end
                    used_boxes_after = self.count_used_drawer(
                        drawer,
                        start=ub,
                        end=end_window,
                        order_uses_drawer_vars=order_uses_drawer_vars,
                    )
                    model.add(
                        r_boxes_var
                        == (remaining_boxes_vars[drawer][0] - used_boxes_after)
                    ).only_enforce_if([replenishment.is_present, pos_var])

                    # enforce activities in [lb, ub] do not use that drawer
                    for order_idx, order in self.orders_within_time_window((lb, ub)):
                        model.add_bool_and(
                            order_uses_drawer_vars[order_idx][drawer].negated()
                        ).only_enforce_if([replenishment.is_present, pos_var])

                # BUG: add_exactly_one with only_enforce_if doesn't work
                # model.add_exactly_one(replenishment_position_vars).only_enforce_if(
                #     replenishment.is_present
                # )
                # enforce exactly one position var when replenishment is performed
                model.add(
                    sum(
                        p[0]
                        for p in replenishment_position_vars[
                            replenishment.interval.name
                        ]
                    )
                    == 1
                ).only_enforce_if(replenishment.is_present)

                # update remaining boxes var when replenishment is not performed
                all_used_boxes = self.count_used_drawer(
                    drawer,
                    start=prev_end_window,
                    end=end_window,
                    order_uses_drawer_vars=order_uses_drawer_vars,
                )
                model.add(
                    r_boxes_var == (prev_r_boxes_var - all_used_boxes)
                ).only_enforce_if(replenishment.is_present.negated())

            # no overlap between replenishments
            model.add_no_overlap(
                map(lambda act: act.interval, replenishments[window_idx])
            )
            prev_end_window = end_window

        self.ensure_sufficient_boxes_after_final_replenish_window(
            model, remaining_boxes_vars, order_uses_drawer_vars
        )
        print("enforce_drawer_selection_policy2:", datetime.now())
        self.enforce_drawer_selection_policy(
            model,
            drawer_contains_box_vars,
            order_uses_drawer_vars,
            remaining_boxes_vars,
            replenishments,
            replenishment_position_vars,
        )
        print("add_quality_metric:", datetime.now())
        self.add_quality_metric(model, replenishments)

        return model, replenishments, drawer_contains_box_vars, order_uses_drawer_vars

    def get_solution(
        self, time_limit: Optional[int] = None
    ) -> Optional[ManufacturingSolution]:
        # TODO: check remaining boxes at the end of each window

        solver = cp_model.CpSolver()
        if time_limit is not None:
            solver.parameters.max_time_in_seconds = time_limit

        status = solver.solve(self.model)
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(
                f"{'Optimal' if status == cp_model.OPTIMAL else 'Feasible'} solution found."
            )

            drawer_to_box = []
            drawer_box_mapping = []
            for drawer in range(len(self.drawer_contains_box_vars)):
                for box_idx, drawer_contains_box in enumerate(
                    self.drawer_contains_box_vars[drawer]
                ):
                    if solver.value(drawer_contains_box):
                        box = self.boxes[box_idx]
                        drawer_to_box.append(box)
                        drawer_box_mapping.append(
                            solution.Drawer(self.instance.drawers[drawer], box)
                        )
                        break

            replenishments = []
            for window_replenishments in self.replenishments:
                for drawer, replenishment in enumerate(window_replenishments):
                    if solver.value(replenishment.is_present):
                        start = solver.value(replenishment.start)
                        replenishments.append(
                            solution.Replenishment(
                                self.instance.drawers[drawer],
                                drawer_to_box[drawer],
                                start,
                            )
                        )

            box_constructions = []
            for i, order in enumerate(self.orders):
                drawer = -1
                for j, bool_var in enumerate(self.order_uses_drawer_vars[i]):
                    if solver.value(bool_var):
                        assert drawer == -1
                        drawer = j
                assert drawer != -1

                box_constructions.append(self.instance.drawers[drawer])

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
