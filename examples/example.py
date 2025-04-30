from manufacturing import (
    ManufacturingConfiguration,
    ManufacturingProblemData,
    ManufacturingInstance,
    ManufacturingSolution,
    ManufacturingSchedulingFactory,
    plot_solution,
)
from manufacturing.dataclasses.instance import OperatorOrderList
from manufacturing.dataclasses.instance import Order
import pathlib
import os
from typing import List


def main():
    examples_path = pathlib.Path(__file__).parent.resolve()
    configuration_data = os.path.join(examples_path, "../configuration.json")
    problem_data = os.path.join(examples_path, "instances/instance.json")
    solution_data = os.path.join(examples_path, "instances/instance_solution.json")

    with open(configuration_data) as f:
        configuration: ManufacturingConfiguration = (
            ManufacturingConfiguration.from_json(f.read())
        )

    with open(problem_data) as f:
        problem_data: ManufacturingProblemData = ManufacturingProblemData.from_json(
            f.read()
        )

    operator_order_lists: List[OperatorOrderList] = [
        OperatorOrderList(operator, []) for operator in range(configuration.operators)
    ]
    for i, order_id in enumerate(problem_data.orders.order):
        operator_order_lists[i % configuration.operators].orders.append(
            Order(id=order_id, box=problem_data.orders.box[i])
        )

    instance: ManufacturingInstance = ManufacturingInstance(
        operators=configuration.operators,
        drawers=problem_data.drawers,
        drawer_capacities=configuration.drawer_capacities,
        replenish_windows=problem_data.replenish_windows,
        replenish_duration=configuration.replenish_duration,
        box_construction_duration=configuration.box_construction_duration,
        box_filling_durations=configuration.box_filling_durations,
        orders=problem_data.orders,
        operator_order_lists=operator_order_lists,
    )

    factory = ManufacturingSchedulingFactory(instance)

    solution: ManufacturingSolution = factory.get_solution()
    if solution is not None:
        with open(solution_data, "w") as f:
            f.write(solution.to_json())

        plot_solution(instance, solution)


if __name__ == "__main__":
    main()
