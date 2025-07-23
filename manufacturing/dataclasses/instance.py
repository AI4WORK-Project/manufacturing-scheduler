from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List
from manufacturing.dataclasses.configuration import DrawerCapacity, BoxFillingDuration
from manufacturing.dataclasses.problem_data import ReplenishWindow
from manufacturing.dataclasses.problem_data import OrdersTable


@dataclass_json
@dataclass
class Order:
    id: int
    box: str


@dataclass_json
@dataclass
class OperatorOrderList:
    operator: int
    orders: List[Order]


@dataclass_json
@dataclass
class ManufacturingInstance:
    operators: int
    drawers: List[int]
    drawer_capacities: List[DrawerCapacity]
    replenish_windows: List[ReplenishWindow]
    replenish_duration: int
    box_construction_duration: int
    box_filling_durations: List[BoxFillingDuration]
    orders: OrdersTable
    operator_order_lists: List[OperatorOrderList]

    def __post_init__(self):
        self.validate()

    def are_replenish_windows_overlapped(self):
        windows = sorted(self.replenish_windows, key=lambda window: window.start)
        for i in range(len(windows) - 1):
            w = windows[i]
            w_next = windows[i + 1]
            if w.end > w_next.start:
                return True
        return False

    def validate(self):
        # FIXME: remove duplicated validations
        assert self.operators > 0, "The number of operators must be greater than zero"
        assert len(self.drawers) > 0, "The number of drawers must be greater than zero"
        assert len(set(self.drawers)) == len(self.drawers), "The drawers must be unique"
        assert (
            self.replenish_duration > 0
        ), "The replenish duration must be greater than zero"
        assert (
            self.box_construction_duration > 0
        ), "The box construction duration must be greater than zero"

        assert self.operators == len(
            self.operator_order_lists
        ), f"Mismatch between number of operators ({self.operators}) and number of operator order lists ({len(self.operator_order_lists)})"

        assert (
            len(self.replenish_windows) > 0
        ), "At least one replenishment temporal window must be specified"
        for window in self.replenish_windows:
            assert (
                window.end - window.start >= self.replenish_duration
            ), "A replenish window must be at least as long as the replenish duration"

        assert (
            not self.are_replenish_windows_overlapped()
        ), "Replenish windows must not overlap"

        makespan = self.box_construction_duration * sum(
            len(orders_list.orders) for orders_list in self.operator_order_lists
        )
        assert all(
            window.start >= 0 and window.end <= makespan
            for window in self.replenish_windows
        ), f"All replenish windows must be within [0, {makespan}]"

        assert set(
            orders_list.operator for orders_list in self.operator_order_lists
        ) == set(
            range(self.operators)
        ), "Mismatch between the operators in the `operator_order_lists` and the number of operators"

        assert len(
            set(
                order.id
                for orders_list in self.operator_order_lists
                for order in orders_list.orders
            )
        ) == sum(
            len(orders_list.orders) for orders_list in self.operator_order_lists
        ), "Duplicate order IDs detected in the order lists. Each order must have a unique ID."

        boxes = set(
            order.box
            for operator_list in self.operator_order_lists
            for order in operator_list.orders
        )

        assert boxes.issubset(
            set(c.box for c in self.drawer_capacities)
        ), "Some boxes referenced in orders are not present in `drawer_capacities`. All boxes in orders must exist in `drawer_capacities`."

        assert boxes.issubset(
            set(duration.box for duration in self.box_filling_durations)
        ), "Some boxes referenced in orders are not present in `box_filling_durations`. All boxes in orders must exist in `box_filling_durations`."
