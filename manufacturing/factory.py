from manufacturing import ManufacturingInstance, ManufacturingSolution
from manufacturing.dataclasses.problem_data import SIZES

import collections
from typing import Tuple, List, Optional, Dict
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
        self.orders = self.get_machine_order_list()
        self.boxes: List[str] = [box.box for box in instance.boxes]
        self.box_index = {box: i for i, box in enumerate(self.boxes)}
        self.max_num_drawers = self.calculate_max_num_drawers()
        self.prev_order = self.calculate_prev_order()

        drawer_capacities = {
            dc.box: dc.capacity for dc in self.instance.drawer_capacities
        }
        self.drawer_capacities = [drawer_capacities[box] for box in self.boxes]

        # NOTE: assume replenish_duration is a multiple of box_construction_duration
        assert (
            self.instance.replenish_duration % self.instance.box_construction_duration
            == 0
        )
        self.num_orders_per_replenishment = (
            self.instance.replenish_duration // self.instance.box_construction_duration
        )

        (
            self.model,
            self.drawers_per_box_vars,
            self.replenish_vars,
            self.nbox_vars,
            self.is_used_vars,
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

    def calculate_max_num_drawers(self) -> List[int]:
        num_boxes_ge_size = {s: 0 for s in SIZES}
        for b in self.instance.boxes:
            for s in SIZES:
                if SIZES[b.size] >= SIZES[s]:
                    num_boxes_ge_size[s] += 1

        num_drawers_ge_size = {s: 0 for s in SIZES}
        for d in self.instance.drawers:
            for s in SIZES:
                if SIZES[d.size] >= SIZES[s]:
                    num_drawers_ge_size[s] += 1

        max_num_drawers = []
        for b in self.instance.boxes:
            max_num_drawers.append(
                num_drawers_ge_size[b.size] - num_boxes_ge_size[b.size] + 1
            )

        return max_num_drawers

    def calculate_prev_order(self) -> List[Optional[int]]:
        prev_order = []
        last_order_with_box = {b.box: None for b in self.instance.boxes}
        for order_idx, order in enumerate(self.orders):
            prev_order.append(last_order_with_box[order.box])
            last_order_with_box[order.box] = order_idx

        return prev_order

    def get_optimization_model(self):
        model = cp_model.CpModel()
        drawers_per_box_vars = self.define_drawers_per_box_vars(model)
        replenish_vars, any_replenish_vars = self.define_replenish_vars(
            model, drawers_per_box_vars
        )
        cumulative_replenish_vars = self.define_cumulative_replenish_vars(
            model, replenish_vars, any_replenish_vars
        )
        nbox_vars, is_used_vars = self.define_nbox_vars(
            model, replenish_vars, drawers_per_box_vars
        )

        self.add_quality_metric(model, any_replenish_vars)

        return model, drawers_per_box_vars, replenish_vars, nbox_vars, is_used_vars

    def define_drawers_per_box_vars(
        self, model: cp_model.CpModel
    ) -> List[cp_model.IntVar]:
        drawers_per_box_vars = []
        for box_idx, box in enumerate(self.instance.boxes):
            drawers_per_box_vars.append(
                model.new_int_var(
                    1,
                    self.max_num_drawers[box_idx],
                    f"drawers_containing_box{box.box}",
                )
            )

        for size in SIZES:
            assigned_drawers = sum(
                drawers_per_box_vars[box_idx]
                for box_idx, box in enumerate(self.instance.boxes)
                if SIZES[box.size] <= SIZES[size]
            )
            minimum_required_drawers = len(
                [
                    drawer
                    for drawer in self.instance.drawers
                    if SIZES[drawer.size] <= SIZES[size]
                ]
            )
            if size == "L":
                model.add(assigned_drawers == minimum_required_drawers)
            else:
                model.add(assigned_drawers >= minimum_required_drawers)

        return drawers_per_box_vars

    def define_replenish_vars(
        self, model: cp_model.CpModel, drawers_per_box_vars: List[cp_model.IntVar]
    ) -> Tuple[List[List[List[cp_model.IntVar]]], List[cp_model.IntVar]]:
        replenish_vars = [
            [[] for _ in range(self.max_num_drawers[box_idx])]
            for box_idx in range(len(self.instance.boxes))
        ]
        for box_idx, box in enumerate(self.instance.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                for order_idx, order in enumerate(self.orders):
                    if order_idx % self.num_orders_per_replenishment == 0:
                        rep_var = model.new_bool_var(
                            f"replenishment_box{box}_drawer{drawer_idx}@{order_idx}"
                        )
                        replenish_vars[box_idx][drawer_idx].append(rep_var)

                        if drawer_idx > 0:
                            # Replenishing the d-th drawer implies that the box has at least d drawers assigned
                            model.add(
                                drawers_per_box_vars[box_idx] >= drawer_idx + 1
                            ).only_enforce_if(rep_var)
                    else:
                        replenish_vars[box_idx][drawer_idx].append(
                            replenish_vars[box_idx][drawer_idx][-1]
                        )

        # at most one replenishment for each slot
        for order_idx, order in enumerate(self.orders):
            if order_idx % self.num_orders_per_replenishment == 0:
                slot_rep_vars = [
                    replenish_vars[box_idx][drawer_idx][order_idx]
                    for box_idx in range(len(self.instance.boxes))
                    for drawer_idx in range(self.max_num_drawers[box_idx])
                ]
                model.add_at_most_one(slot_rep_vars)

        any_replenish_vars = []
        for order_idx, order in enumerate(self.orders):
            if order_idx % self.num_orders_per_replenishment == 0:
                any_rep_var = model.new_bool_var(f"any_replenishment@{order_idx}")
                any_replenish_vars.append(any_rep_var)

                order_rep_vars = [
                    replenish_vars[box_idx][drawer_idx][order_idx]
                    for box_idx in range(len(self.instance.boxes))
                    for drawer_idx in range(self.max_num_drawers[box_idx])
                ]
                model.add_bool_and(any_rep_var.negated()).only_enforce_if(
                    v.negated() for v in order_rep_vars
                )
                for rep_var in order_rep_vars:
                    model.add_implication(rep_var, any_rep_var)

            else:
                any_replenish_vars.append(any_replenish_vars[-1])

        return replenish_vars, any_replenish_vars

    def define_cumulative_replenish_vars(
        self,
        model: cp_model.CpModel,
        replenish_vars: List[List[List[cp_model.IntVar]]],
        any_replenish_vars: List[cp_model.IntVar],
    ) -> List[List[List[cp_model.IntVar]]]:
        cumulative_replenish_vars = [
            [[] for _ in range(num_drawers)] for num_drawers in self.max_num_drawers
        ]
        for box_idx, box in enumerate(self.instance.boxes):
            for drawer_idx in range(self.max_num_drawers[box_idx]):
                for order_idx, order in enumerate(self.orders):
                    if order_idx % self.num_orders_per_replenishment == 0:
                        if order_idx == 0:
                            cumulative_replenish_vars[box_idx][drawer_idx].append(
                                replenish_vars[box_idx][drawer_idx][order_idx]
                            )
                        else:
                            crep_var = model.new_bool_var(
                                f"cumulative_replenishment_box{box}_drawer{drawer_idx}@{order_idx}"
                            )
                            cumulative_replenish_vars[box_idx][drawer_idx].append(
                                crep_var
                            )

                            model.add_implication(
                                replenish_vars[box_idx][drawer_idx][order_idx],
                                cumulative_replenish_vars[box_idx][drawer_idx][
                                    -2
                                ].negated(),
                            )

                            model.add_bool_and(crep_var).only_enforce_if(
                                replenish_vars[box_idx][drawer_idx][order_idx]
                            )
                            model.add_bool_and(crep_var).only_enforce_if(
                                cumulative_replenish_vars[box_idx][drawer_idx][-2],
                                any_replenish_vars[order_idx],
                            )
                            model.add_bool_or(
                                [
                                    replenish_vars[box_idx][drawer_idx][order_idx],
                                    cumulative_replenish_vars[box_idx][drawer_idx][-2],
                                ]
                            ).only_enforce_if(crep_var)
                            model.add_bool_or(
                                [
                                    replenish_vars[box_idx][drawer_idx][order_idx],
                                    any_replenish_vars[order_idx],
                                ]
                            ).only_enforce_if(crep_var)

        return cumulative_replenish_vars

    def define_nbox_vars(
        self,
        model: cp_model.CpModel,
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
        for box_idx, box in enumerate(self.instance.boxes):
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
                        for i in range(prev_order_idx + 1, order_idx + 1):
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
            # TODO
            # model.add_bool_or(
            model.add_exactly_one(
                is_used_vars[box_idx][drawer_idx][order_idx]
                for drawer_idx in range(self.max_num_drawers[box_idx])
            )

        return nbox_vars, is_used_vars

    def add_quality_metric(
        self, model: cp_model.CpModel, any_replenish_vars: List[cp_model.IntVar]
    ):
        """Sets the objective of the model to optimize."""

        replenishment_change_vars = [any_replenish_vars[0]]
        for i in range(1, len(any_replenish_vars)):
            replenishment_change = model.new_bool_var(f"replenishment_change{i}")
            model.add_bool_and(
                any_replenish_vars[i - 1].negated(),
                any_replenish_vars[i],
            ).only_enforce_if(replenishment_change)
            model.add_bool_or(
                any_replenish_vars[i - 1],
                any_replenish_vars[i].negated(),
            ).only_enforce_if(replenishment_change.negated())
            replenishment_change_vars.append(replenishment_change)

        model.minimize(sum(replenishment_change_vars))

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

            drawer_box_mapping = []
            relative_to_absolute_drawer = {}
            drawer_idx_offset = 0
            for box_idx, num_drawers_var in enumerate(self.drawers_per_box_vars):
                num_drawers = solver.value(num_drawers_var)
                assert 1 <= num_drawers
                for i in range(num_drawers):
                    drawer = self.instance.drawers[drawer_idx_offset + i]
                    box = self.instance.boxes[box_idx]
                    drawer_box_mapping.append(
                        solution.DrawerWithBox(drawer.drawer, box.box)
                    )
                    relative_to_absolute_drawer[(box_idx, i)] = drawer_idx_offset + i
                drawer_idx_offset += num_drawers
            drawer_box_mapping.sort(key=lambda d: d.drawer)

            replenishments = []
            for box_idx, box in enumerate(self.instance.boxes):
                for drawer_idx in range(self.max_num_drawers[box_idx]):
                    for order_idx in range(
                        0, len(self.orders), self.num_orders_per_replenishment
                    ):
                        if solver.value(
                            self.replenish_vars[box_idx][drawer_idx][order_idx]
                        ):
                            assert (box_idx, drawer_idx) in relative_to_absolute_drawer
                            drawer = self.instance.drawers[
                                relative_to_absolute_drawer[(box_idx, drawer_idx)]
                            ]
                            start = order_idx * self.instance.box_construction_duration
                            replenishments.append(
                                solution.Replenishment(
                                    drawer.drawer,
                                    box.box,
                                    start,
                                )
                            )

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
                    self.instance.drawers[
                        relative_to_absolute_drawer[(box_idx, used_drawer_idx)]
                    ].drawer
                )

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
