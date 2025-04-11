from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List


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
class ManufacturingConfiguration:
    operators: int
    drawer_capacities: List[DrawerCapacity]
    replenish_duration: int
    box_construction_duration: int
    box_filling_durations: List[BoxFillingDuration]

    def __post_init__(self):
        self.validate()

    def validate(self):
        assert self.operators > 0, "The number of operators must be greater than zero"
        assert (
            self.replenish_duration > 0
        ), "The replenish duration must be greater than zero"
        assert (
            self.box_construction_duration > 0
        ), "The box construction duration must be greater than zero"
