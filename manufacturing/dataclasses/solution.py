from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List
from .instance import (
    Box,
    Drawers,
    DrawerCapacity,
    BoxFillingDuration,
    OrdersTable,
    OperatorOrderList,
)


@dataclass_json
@dataclass
class DrawerWithBox:
    drawer: int
    box: str


@dataclass_json
@dataclass
class Replenishment:
    drawer: int
    box: str
    box_construction_index: int


@dataclass_json
@dataclass
class SolverInfo:
    objective_value: float
    best_objective_bound: float
    user_time: float


@dataclass_json
@dataclass
class ManufacturingSolution:
    # instance data
    operators: int
    boxes: List[Box]
    drawers: Drawers
    drawer_capacities: List[DrawerCapacity]
    box_constructions_per_replenishment: int
    box_filling_durations: List[BoxFillingDuration]
    orders: OrdersTable
    operator_order_lists: List[OperatorOrderList]

    # solution data
    is_solution_optimal: bool
    drawer_box_mapping: List[DrawerWithBox]
    replenishments: List[Replenishment]
    box_constructions: List[int]
    solver: SolverInfo
