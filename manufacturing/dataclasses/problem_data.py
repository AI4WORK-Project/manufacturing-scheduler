from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, config
from datetime import time, datetime
from typing import List, Literal
from functools import total_ordering


def parse_time_string(time_str: str) -> time:
    """Parse a time string like 'HH:MM:SS' into a datetime object with today's date."""
    return datetime.strptime(time_str, "%H:%M:%S").time()


def time_to_string(dt: time) -> str:
    """Convert a datetime.time object to a time string like HH:MM:SS."""
    return dt.strftime("%H:%M:%S")


# @total_ordering
# @dataclass_json
# @dataclass(frozen=True)
# class Size:
#     value: str

#     # Define allowed values and their order
#     _order = {"S": 0, "M": 1, "L": 2}

#     def __post_init__(self):
#         if self.value not in self._order:
#             raise ValueError(
#                 f"Invalid size: {self.value}. Must be one of {list(self._order.keys())}"
#             )

#     # Comparisons
#     def __eq__(self, other):
#         if not isinstance(other, Size):
#             return NotImplemented
#         return self.value == other.value

#     def __lt__(self, other):
#         if not isinstance(other, Size):
#             return NotImplemented
#         return self._order[self.value] < self._order[other.value]

Size = Literal["L", "M", "S"]
SIZES = {"S": 0, "M": 1, "L": 2}


@dataclass_json
@dataclass
class Drawer:
    drawer: int
    size: Size


@dataclass_json
@dataclass
class Box:
    box: str
    size: Size

    def fits_in_drawer(self, drawer: Drawer) -> bool:
        return SIZES[drawer.size] >= SIZES[self.size]


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
class ManufacturingProblemData:
    start_time: time = field(
        metadata=config(encoder=time_to_string, decoder=parse_time_string)
    )
    boxes: List[Box]
    drawers: List[Drawer]
    orders: OrdersTable

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert len(self.drawers) > 0, "The number of drawers must be greater than zero"
        # assert len(set(self.drawers)) == len(self.drawers), "The drawers must be unique"

        assert len(set(self.orders.order)) == len(
            self.orders.order
        ), "Duplicate order IDs detected in the order list. Each order must have a unique ID."
