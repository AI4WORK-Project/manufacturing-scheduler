import logging
import json
from flask import Flask, request, Response
from manufacturing import (
    ManufacturingProblemData,
    ManufacturingConfiguration,
    ManufacturingInstance,
    ManufacturingSchedulingFactory,
)
from manufacturing.dataclasses.instance import Order
from manufacturing.dataclasses.instance import OperatorOrderList
from typing import List

logging.basicConfig(level=logging.INFO)

app = Flask("Manufacturing-API")


@app.route("/schedule", methods=["POST"])
def schedule():
    try:
        logging.info("Request received!")

        with open("configuration.json") as f:
            configuration: ManufacturingConfiguration = (
                ManufacturingConfiguration.from_dict(json.load(f))
            )

        problem_data: ManufacturingProblemData = ManufacturingProblemData.from_dict(
            request.json
        )

        operator_order_lists: List[OperatorOrderList] = [
            OperatorOrderList(operator, [])
            for operator in range(configuration.operators)
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

        time_limit = request.args.get("time_limit", None, type=int)
        logging.info(f"Time limit: {time_limit}")

        solution = factory.get_solution(time_limit=time_limit)
        if solution is not None:
            logging.info(f"Solution found")
        else:
            logging.info("No solution has been found for the given problem")
            return Response(
                '{"message":"No solution has been found for the given problem"}',
                mimetype="application/json",
                status=400,
            )

    except Exception as e:
        return Response(
            '{"message":"%s"}' % str(e), mimetype="application/json", status=500
        )

    return Response(solution.to_json(), mimetype="application/json", status=200)


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="0.0.0.0", port=5000)
