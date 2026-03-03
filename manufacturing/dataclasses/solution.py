from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List
from .instance import OperatorOrderList
from .problem_data import OrdersTable


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
    start: int


@dataclass_json
@dataclass
class SolverInfo:
    objective_value: float
    best_objective_bound: float
    user_time: float


@dataclass_json
@dataclass
class ManufacturingSolution:
    is_solution_optimal: bool
    drawer_box_mapping: List[DrawerWithBox]
    replenishments: List[Replenishment]
    box_constructions: List[int]
    orders: OrdersTable
    operator_order_lists: List[OperatorOrderList]
    solver: SolverInfo
