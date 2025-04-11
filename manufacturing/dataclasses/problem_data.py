from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List


@dataclass_json
@dataclass
class ReplenishWindow:
    start: int
    end: int

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert self.start >= 0, "The start value must be greater than or equal to zero"
        assert (
            self.end > self.start
        ), "The end value must be greater than the start value"


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
    drawers: List[int]
    replenish_windows: List[ReplenishWindow]
    orders: OrdersTable

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
        assert len(self.drawers) > 0, "The number of drawers must be greater than zero"
        assert len(set(self.drawers)) == len(self.drawers), "The drawers must be unique"

        assert (
            len(self.replenish_windows) > 0
        ), "At least one replenishment temporal window must be specified"

        assert (
            not self.are_replenish_windows_overlapped()
        ), "Replenish windows must not overlap"

        assert len(set(self.orders.order)) == len(
            self.orders.order
        ), "Duplicate order IDs detected in the order list. Each order must have a unique ID."
