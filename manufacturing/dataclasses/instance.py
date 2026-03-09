from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, config
from typing import List, Literal
from datetime import time, datetime


def parse_time_string(time_str: str) -> time:
    """Parse a time string like 'HH:MM:SS' into a datetime object with today's date."""
    return datetime.strptime(time_str, "%H:%M:%S").time()


def time_to_string(dt: time) -> str:
    """Convert a datetime.time object to a time string like HH:MM:SS."""
    return dt.strftime("%H:%M:%S")


Size = Literal["L", "M", "S"]
SIZES = {"S": 0, "M": 1, "L": 2}


@dataclass_json
@dataclass
class Box:
    box: str
    size: Size

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert self.size in SIZES


@dataclass_json
@dataclass
class Drawer:
    drawer: int
    size: Size


@dataclass_json
@dataclass
class Drawers:
    lower_level: List[Drawer]
    upper_level: List[Drawer]


@dataclass_json
@dataclass
class DrawerCapacity:
    box: str
    capacity: int

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert self.capacity > 0, "The capacity must be greater than zero"


@dataclass_json
@dataclass
class BoxFillingDuration:
    box: str
    filling_duration: int

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert (
            self.filling_duration > 0
        ), "The box filling duration must be greater than zero"


@dataclass_json
@dataclass
class OrdersTable:
    date: List[str]
    order: List[int]
    item: List[int]
    height: List[int]
    width: List[int]
    depth: List[int]
    box: List[str]


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
    boxes: List[Box]
    drawers: Drawers
    drawer_capacities: List[DrawerCapacity]
    box_constructions_per_replenishment: int
    box_filling_durations: List[BoxFillingDuration]
    minimum_remaining_boxes: int
    orders: OrdersTable
    operator_order_lists: List[OperatorOrderList] = field(init=False)

    def __post_init__(self):
        self.validate()

        self.operator_order_lists = [
            OperatorOrderList(operator, []) for operator in range(self.operators)
        ]
        for i, order_id in enumerate(self.orders.order):
            self.operator_order_lists[i % self.operators].orders.append(
                Order(id=order_id, box=self.orders.box[i])
            )

    def validate(self):
        # TODO: add validation checks
        assert self.operators > 0, "The number of operators must be greater than zero"
        # assert len(self.drawers) > 0, "The number of drawers must be greater than zero"

        # assert self.operators == len(
        #     self.operator_order_lists
        # ), f"Mismatch between number of operators ({self.operators}) and number of operator order lists ({len(self.operator_order_lists)})"

        # assert set(
        #     orders_list.operator for orders_list in self.operator_order_lists
        # ) == set(
        #     range(self.operators)
        # ), "Mismatch between the operators in the `operator_order_lists` and the number of operators"

        # assert len(
        #     set(
        #         order.id
        #         for orders_list in self.operator_order_lists
        #         for order in orders_list.orders
        #     )
        # ) == sum(
        #     len(orders_list.orders) for orders_list in self.operator_order_lists
        # ), "Duplicate order IDs detected in the order lists. Each order must have a unique ID."

        # boxes = set(
        #     order.box
        #     for operator_list in self.operator_order_lists
        #     for order in operator_list.orders
        # )

        # assert boxes.issubset(
        #     set(c.box for c in self.drawer_capacities)
        # ), "Some boxes referenced in orders are not present in `drawer_capacities`. All boxes in orders must exist in `drawer_capacities`."

        # assert boxes.issubset(
        #     set(duration.box for duration in self.box_filling_durations)
        # ), "Some boxes referenced in orders are not present in `box_filling_durations`. All boxes in orders must exist in `box_filling_durations`."
