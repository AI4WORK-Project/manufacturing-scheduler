from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, config
from typing import List
from datetime import time
from manufacturing.dataclasses.configuration import DrawerCapacity, BoxFillingDuration
from manufacturing.dataclasses.problem_data import (
    Box,
    Drawer,
    OrdersTable,
    parse_time_string,
    time_to_string,
)


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
    start_time: time = field(
        metadata=config(encoder=time_to_string, decoder=parse_time_string)
    )
    operators: int
    boxes: List[Box]
    drawers: List[Drawer]
    drawer_capacities: List[DrawerCapacity]
    replenish_duration: int
    box_construction_duration: int
    box_filling_durations: List[BoxFillingDuration]
    orders: OrdersTable
    operator_order_lists: List[OperatorOrderList]

    def __post_init__(self):
        self.validate()

    def validate(self):
        # FIXME: remove duplicated validations
        assert self.operators > 0, "The number of operators must be greater than zero"
        assert len(self.drawers) > 0, "The number of drawers must be greater than zero"
        # assert len(set(self.drawers)) == len(self.drawers), "The drawers must be unique"
        assert (
            self.replenish_duration > 0
        ), "The replenish duration must be greater than zero"
        assert (
            self.box_construction_duration > 0
        ), "The box construction duration must be greater than zero"

        assert self.operators == len(
            self.operator_order_lists
        ), f"Mismatch between number of operators ({self.operators}) and number of operator order lists ({len(self.operator_order_lists)})"

        # makespan = self.box_construction_duration * sum(
        #     len(orders_list.orders) for orders_list in self.operator_order_lists
        # )
        # assert all(
        #     window.start >= 0 and window.end <= makespan
        #     for window in self.replenish_windows
        # ), f"All replenish windows must be within [0, {makespan}]"

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
