from manufacturing import ManufacturingInstance, ManufacturingSolution

import collections
from typing import List, Optional, Union
from ortools.sat.python import cp_model
import logging

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

        # self.num_drawers = len(instance.drawers)
        self.orders = self.get_machine_order_list()
        self.makespan = self.instance.box_construction_duration * len(self.orders)
        self.boxes: List[str] = [box.box for box in instance.boxes]
        # self.max_drawers_per_box = self.num_drawers - len(self.boxes) + 1

        drawer_capacities = {
            dc.box: dc.capacity for dc in self.instance.drawer_capacities
        }
        self.drawer_capacities = [drawer_capacities[box] for box in self.boxes]

        (
            self.model,
            self.drawer_contains_box_vars,
            self.num_boxes_vars,
            self.replenish_vars,
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

    def add_quality_metric(
        self, model: cp_model.CpModel, replenish_vars: List[List[cp_model.IntVar]]
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
        # model.minimize(sum(r for rr in replenish_vars for r in rr))

        step = (
            self.instance.replenish_duration // self.instance.box_construction_duration
        )

        replenishment_performed_vars = []
        for order_idx in range(0, len(self.orders), step):
            replenishment_performed = model.new_bool_var(
                f"replenishment_performed{order_idx}"
            )
            model.add_bool_or(
                replenish_vars[order_idx][drawer_idx]
                for drawer_idx, drawer in enumerate(self.instance.drawers)
            ).only_enforce_if(replenishment_performed)
            model.add_bool_and(
                replenish_vars[order_idx][drawer_idx].negated()
                for drawer_idx, drawer in enumerate(self.instance.drawers)
            ).only_enforce_if(replenishment_performed.negated())
            replenishment_performed_vars.append(replenishment_performed)

        cumulative_replenish_vars = [
            [
                replenish_vars[0][drawer_idx]
                for drawer_idx, drawer in enumerate(self.instance.drawers)
            ]
        ]
        for i in range(1, len(replenishment_performed_vars)):
            order_idx = i * step
            cumulative_replenish_vars.append([])
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                cumulative_replenish_vars[i].append(
                    model.new_bool_var(f"cumulative_replenish{order_idx}{drawer_idx}")
                )

                # replenish_vars[i][j] -> not(cumulative_replenish_vars[i - 1][j])
                model.add_implication(
                    replenish_vars[order_idx][drawer_idx],
                    cumulative_replenish_vars[i - 1][drawer_idx].negated(),
                )

                tmp_var = model.new_bool_var(f"tmp{order_idx}{drawer_idx}")
                model.add_bool_and(
                    cumulative_replenish_vars[i - 1][drawer_idx],
                    replenishment_performed_vars[i],
                ).only_enforce_if(tmp_var)
                model.add_bool_or(
                    cumulative_replenish_vars[i - 1][drawer_idx].negated(),
                    replenishment_performed_vars[i].negated(),
                ).only_enforce_if(tmp_var.negated())

                # cumulative_replenish_vars[i][j] <-> replenish_vars[i][j] || (cumulative_replenish_vars[i - 1] && replenishment_performed_vars[i])
                model.add_bool_or(
                    replenish_vars[order_idx][drawer_idx], tmp_var
                ).only_enforce_if(cumulative_replenish_vars[i][drawer_idx])
                model.add_bool_and(
                    replenish_vars[order_idx][drawer_idx].negated(), tmp_var.negated()
                ).only_enforce_if(cumulative_replenish_vars[i][drawer_idx].negated())

        replenishment_change_vars = [replenishment_performed_vars[0]]
        for i in range(len(replenishment_performed_vars) - 1):
            replenishment_change = model.new_bool_var(f"replenishment_change{i}")
            model.add_bool_and(
                replenishment_performed_vars[i].negated(),
                replenishment_performed_vars[i + 1],
            ).only_enforce_if(replenishment_change)
            model.add_bool_or(
                replenishment_performed_vars[i],
                replenishment_performed_vars[i + 1].negated(),
            ).only_enforce_if(replenishment_change.negated())
            replenishment_change_vars.append(replenishment_change)

        # model.add(sum(replenishment_change_vars) == 1)
        model.minimize(sum(replenishment_change_vars))

    def define_drawer_contains_box_vars(
        self, model: cp_model.CpModel
    ) -> List[List[cp_model.IntVar]]:
        drawer_contains_box_vars = []

        false_var = model.new_bool_var("false_var")
        model.add_bool_and(false_var.negated())
        for drawer_idx, drawer in enumerate(self.instance.drawers):
            drawer_contains_box_vars.append([])
            for box_idx, box in enumerate(self.instance.boxes):
                if box.fits_in_drawer(drawer):
                    drawer_contains_box_vars[drawer_idx].append(
                        model.new_bool_var(
                            f"drawer{drawer.drawer}_contains_box{box.box}"
                        )
                    )
                else:
                    drawer_contains_box_vars[drawer_idx].append(false_var)

            # enforce each drawer contains exactly one box
            model.add_exactly_one(
                v
                for v in drawer_contains_box_vars[drawer_idx]
                if not isinstance(v, bool)
            )

        # enforce an ordering of the boxes assigned to drawers to reduce
        # the number of equivalent solutions
        for drawer in range(len(drawer_contains_box_vars) - 1):
            for i in range(1, len(drawer_contains_box_vars[drawer])):
                if not isinstance(drawer_contains_box_vars[drawer][i], bool):
                    model.add_bool_or(
                        [drawer_contains_box_vars[drawer][i].negated()]
                        + drawer_contains_box_vars[drawer + 1][i:]
                    )

        return drawer_contains_box_vars

    def initialize_num_boxes_vars(
        self,
        model: cp_model.CpModel,
        drawer_contains_box_vars: List[List[cp_model.IntVar]],
    ) -> List[List[cp_model.IntVar]]:
        num_boxes_vars = [[]]
        # set the drawer initial capacity to full
        for drawer_idx, drawer in enumerate(self.instance.drawers):
            initial_capacity = model.new_int_var(
                0,
                max(self.drawer_capacities),
                f"initial_capacity_drawer{drawer.drawer}",
            )
            num_boxes_vars[0].append(initial_capacity)

            # drawer initial capacity depends on box contained
            for box in range(len(self.boxes)):
                model.add(
                    initial_capacity == self.drawer_capacities[box]
                ).only_enforce_if(drawer_contains_box_vars[drawer_idx][box])

        for order_idx, order in enumerate(self.orders):
            num_boxes_vars.append([])
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                num_boxes = model.new_int_var(
                    0,
                    max(self.drawer_capacities),
                    f"num_boxes{drawer.drawer}@order{order_idx}",
                )
                num_boxes_vars[order_idx + 1].append(num_boxes)

        return num_boxes_vars

    def initialize_replenish_vars(
        self, model: cp_model.CpModel
    ) -> List[List[cp_model.IntVar]]:
        # NOTE: assume replenish_duration is a multiple of box_construction_duration
        # TODO
        assert (
            self.instance.replenish_duration % self.instance.box_construction_duration
            == 0
        )

        replenish_vars = []
        for i in range(
            len(self.orders)
            * self.instance.box_construction_duration
            // self.instance.replenish_duration
        ):
            replenish_vars.append([])
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                replenish_vars[i].append(
                    model.new_bool_var(
                        f"replenishment{drawer.drawer}@{i * self.instance.replenish_duration}"
                    )
                )

            # at most one replenishment among all the drawers
            model.add_at_most_one(replenish_vars[i])

        replenish_vars_extended = []
        for drawers_replenish_vars in replenish_vars:
            for i in range(
                self.instance.replenish_duration
                // self.instance.box_construction_duration
            ):
                replenish_vars_extended.append(drawers_replenish_vars)

        return replenish_vars_extended

    def define_is_empty_vars(
        self, model: cp_model.CpModel, num_boxes_vars: List[List[cp_model.IntVar]]
    ) -> List[List[cp_model.IntVar]]:
        is_empty_vars = []
        for order_idx, order in enumerate(self.orders):
            is_empty_vars.append([])
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                is_empty_var = model.new_bool_var(
                    f"is_empty_drawer{drawer_idx}@order{order_idx}"
                )
                model.add(num_boxes_vars[order_idx][drawer_idx] == 0).only_enforce_if(
                    is_empty_var
                )
                model.add(num_boxes_vars[order_idx][drawer_idx] > 0).only_enforce_if(
                    is_empty_var.negated()
                )
                is_empty_vars[order_idx].append(is_empty_var)
        return is_empty_vars

    def get_optimization_model(self):
        model = cp_model.CpModel()
        drawer_contains_box_vars = self.define_drawer_contains_box_vars(model)
        num_boxes_vars = self.initialize_num_boxes_vars(model, drawer_contains_box_vars)
        replenish_vars = self.initialize_replenish_vars(model)

        is_empty_vars = self.define_is_empty_vars(model, num_boxes_vars)

        order_uses_drawer_vars: List[List[cp_model.IntVar]] = []
        for order_idx, order in enumerate(self.orders):
            box_idx = self.boxes.index(order.box)

            prev_drawer_not_used: List[cp_model.IntVar] = []
            for drawer_idx in range(len(self.instance.drawers) - 1):
                prev_drawer_not_used.append(
                    model.new_bool_var(f"prev_drawer_not_used{drawer_idx}{order_idx}")
                )
                model.add_bool_or(
                    drawer_contains_box_vars[drawer_idx][box_idx].negated(),
                    is_empty_vars[order_idx][drawer_idx],
                    replenish_vars[order_idx - 1][drawer_idx],
                ).only_enforce_if(prev_drawer_not_used[drawer_idx])
                model.add_bool_and(
                    drawer_contains_box_vars[drawer_idx][box_idx],
                    is_empty_vars[order_idx][drawer_idx].negated(),
                    replenish_vars[order_idx - 1][drawer_idx].negated(),
                ).only_enforce_if(prev_drawer_not_used[drawer_idx].negated())

            order_uses_drawer_vars.append([])
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                model.add(
                    num_boxes_vars[order_idx + 1][drawer_idx]
                    == num_boxes_vars[0][drawer_idx]
                ).only_enforce_if(replenish_vars[order_idx][drawer_idx])

                model.add(
                    num_boxes_vars[order_idx + 1][drawer_idx]
                    == num_boxes_vars[order_idx][drawer_idx]
                ).only_enforce_if(
                    [
                        replenish_vars[order_idx][drawer_idx].negated(),
                        drawer_contains_box_vars[drawer_idx][box_idx].negated(),
                    ]
                )

                big_and_condition_var = model.new_bool_var(
                    f"big_and_condition_var{order_idx}{drawer_idx}"
                )
                model.add_bool_and(
                    [
                        prev_drawer_not_used[drawer_idx2]
                        for drawer_idx2 in range(drawer_idx)
                    ]
                    + [is_empty_vars[order_idx][drawer_idx].negated()]
                ).only_enforce_if(big_and_condition_var)
                model.add_bool_or(
                    [
                        prev_drawer_not_used[drawer_idx2].negated()
                        for drawer_idx2 in range(drawer_idx)
                    ]
                    + [is_empty_vars[order_idx][drawer_idx]]
                ).only_enforce_if(big_and_condition_var.negated())

                model.add(
                    num_boxes_vars[order_idx + 1][drawer_idx]
                    == num_boxes_vars[order_idx][drawer_idx]
                ).only_enforce_if(
                    [
                        replenish_vars[order_idx][drawer_idx].negated(),
                        drawer_contains_box_vars[drawer_idx][box_idx],
                        big_and_condition_var.negated(),
                    ]
                )

                order_uses_drawer_vars[order_idx].append(
                    model.new_bool_var(f"order{order_idx}_uses_drawer{drawer_idx}")
                )
                model.add_bool_and(
                    replenish_vars[order_idx][drawer_idx].negated(),
                    drawer_contains_box_vars[drawer_idx][box_idx],
                    big_and_condition_var,
                ).only_enforce_if(order_uses_drawer_vars[order_idx][drawer_idx])
                model.add_bool_or(
                    replenish_vars[order_idx][drawer_idx],
                    drawer_contains_box_vars[drawer_idx][box_idx].negated(),
                    big_and_condition_var.negated(),
                ).only_enforce_if(
                    order_uses_drawer_vars[order_idx][drawer_idx].negated()
                )

                model.add(
                    num_boxes_vars[order_idx + 1][drawer_idx]
                    == num_boxes_vars[order_idx][drawer_idx] - 1
                ).only_enforce_if(order_uses_drawer_vars[order_idx][drawer_idx])

            # enfore exactly one drawer used for an order
            model.add_exactly_one(order_uses_drawer_vars[order_idx])

        self.add_quality_metric(model, replenish_vars)

        return (
            model,
            drawer_contains_box_vars,
            num_boxes_vars,
            replenish_vars,
            order_uses_drawer_vars,
        )

    def get_solution(
        self, time_limit: Optional[int] = None
    ) -> Optional[ManufacturingSolution]:
        solver = cp_model.CpSolver()
        if time_limit is not None:
            solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.log_search_progress = True
        # solver.parameters.num_workers = 1
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
        #         # self.StopSearch()

        # solution_callback = SolutionCallback()
        # status = solver.solve(self.model, solution_callback=solution_callback)

        status = solver.solve(self.model)
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            print(
                f"{'Optimal' if status == cp_model.OPTIMAL else 'Feasible'} solution found."
            )

            drawer_to_box = {}
            drawer_box_mapping = []
            for drawer_idx, drawer in enumerate(self.instance.drawers):
                for box_idx, drawer_contains_box in enumerate(
                    self.drawer_contains_box_vars[drawer_idx]
                ):
                    if solver.value(drawer_contains_box):
                        box = self.boxes[box_idx]
                        drawer_to_box[drawer.drawer] = box
                        drawer_box_mapping.append(solution.Drawer(drawer.drawer, box))
                        break

            replenishments = []
            processed_replenishments = set()
            for order_idx, order in enumerate(self.orders):
                for drawer_idx, drawer in enumerate(self.instance.drawers):
                    replenish_var = self.replenish_vars[order_idx][drawer_idx]
                    if (
                        replenish_var.name not in processed_replenishments
                        and solver.value(replenish_var)
                    ):
                        processed_replenishments.add(replenish_var.name)
                        start = order_idx * self.instance.box_construction_duration
                        replenishments.append(
                            solution.Replenishment(
                                drawer.drawer,
                                drawer_to_box[drawer.drawer],
                                start,
                            )
                        )

            box_constructions = []
            for order_idx, order in enumerate(self.orders):
                drawer = -1
                for j, bool_var in enumerate(self.order_uses_drawer_vars[order_idx]):
                    if solver.value(bool_var):
                        assert drawer == -1
                        drawer = j
                assert drawer != -1

                box_constructions.append(self.instance.drawers[drawer].drawer)

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
