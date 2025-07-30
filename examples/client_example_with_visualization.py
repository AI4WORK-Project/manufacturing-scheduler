from manufacturing import (
    ManufacturingConfiguration,
    ManufacturingProblemData,
    ManufacturingInstance,
    ManufacturingSolution,
    plot_solution,
)
from manufacturing.dataclasses.instance import Order, OperatorOrderList
import json
import requests
import pathlib
import os
from typing import List


def main():
    url = "http://0.0.0.0:5000/schedule"
    examples_path = pathlib.Path(__file__).parent.resolve()
    configuration_data = os.path.join(examples_path, "../configuration.json")
    problem_data = os.path.join(examples_path, "instances/instance.json")

    with open(problem_data) as f:
        problem_data_json = json.load(f)
        problem_data: ManufacturingProblemData = ManufacturingProblemData.from_dict(
            problem_data_json
        )

    with open(configuration_data) as f:
        configuration: ManufacturingConfiguration = (
            ManufacturingConfiguration.from_json(f.read())
        )

    operator_order_lists: List[OperatorOrderList] = [
        OperatorOrderList(operator, []) for operator in range(configuration.operators)
    ]
    for i, order_id in enumerate(problem_data.orders.order):
        operator_order_lists[i % configuration.operators].orders.append(
            Order(id=order_id, box=problem_data.orders.box[i])
        )

    instance: ManufacturingInstance = ManufacturingInstance(
        start_time=problem_data.start_time,
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

    response = requests.post(url, params={"time_limit": 60}, json=problem_data_json)
    print("Status Code:", response.status_code)
    print("Response JSON:", response.json())

    if response.ok:
        solution: ManufacturingSolution = ManufacturingSolution.from_dict(
            response.json()
        )
        plot_solution(instance, solution)


if __name__ == "__main__":
    main()
