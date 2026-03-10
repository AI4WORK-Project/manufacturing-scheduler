from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List, Literal


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
class ManufacturingInstance:
    operators: int
    boxes: List[Box]
    drawers: Drawers
    drawer_capacities: List[DrawerCapacity]
    box_constructions_per_replenishment: int
    box_filling_durations: List[BoxFillingDuration]
    minimum_remaining_boxes: int
    orders: OrdersTable

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert self.operators > 0, "The number of operators must be greater than zero"

        boxes = [b.box for b in self.boxes]
        assert len(boxes) == len(set(boxes)), "Box identifiers must be unique"
        assert all(b.size in SIZES for b in self.boxes), "Invalid box size detected"

        drawers = [
            d.drawer for d in self.drawers.lower_level + self.drawers.upper_level
        ]
        assert len(drawers) == len(set(drawers)), "Drawer identifiers must be unique"
        assert len(drawers) > 0, "The number of drawers must be greater than zero"
        assert all(
            d.size in SIZES for d in self.drawers.lower_level + self.drawers.upper_level
        ), "Invalid drawer size detected"

        dc_boxes = [dc.box for dc in self.drawer_capacities]
        assert len(dc_boxes) == len(boxes) and set(boxes) == set(
            dc_boxes
        ), "Drawer capacities must be defined for all box types"

        assert (
            self.box_constructions_per_replenishment > 0
        ), "box_constructions_per_replenishment must be greater than zero"
        assert (
            self.minimum_remaining_boxes >= 0
        ), "minimum_remaining_boxes must be non-negative"

        fd_boxes = [fd.box for fd in self.box_filling_durations]
        assert len(fd_boxes) == len(boxes) and set(boxes) == set(
            fd_boxes
        ), "Filling durations must be defined for all box types"

        assert len(set(self.orders.order)) == len(
            self.orders.order
        ), "Duplicate order IDs detected in the order list"

        assert set(boxes) == set(
            self.orders.box
        ), "Orders must reference exactly the box types defined in boxes"
        num_orders = [
            len(self.orders.date),
            len(self.orders.order),
            len(self.orders.item),
            len(self.orders.height),
            len(self.orders.width),
            len(self.orders.depth),
            len(self.orders.box),
        ]
        assert (
            len(set(num_orders)) == 1
        ), "All columns in the orders table must have the same number of rows"
