from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, config
from datetime import time, datetime
from typing import List


def parse_time_string(time_str: str) -> time:
    """Parse a time string like 'HH:MM:SS' into a datetime object with today's date."""
    return datetime.strptime(time_str, "%H:%M:%S").time()


def time_to_string(dt: time) -> str:
    """Convert a datetime.time object to a time string like HH:MM:SS."""
    return dt.strftime("%H:%M:%S")


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
    start_time: time = field(
        metadata=config(encoder=time_to_string, decoder=parse_time_string)
    )
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
