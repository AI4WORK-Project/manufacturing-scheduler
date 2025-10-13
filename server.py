import os
import logging
import json
import pathlib
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from manufacturing import (
    ManufacturingProblemData,
    ManufacturingConfiguration,
    ManufacturingInstance,
    ManufacturingSchedulingFactory,
    plot_solution,
)
from manufacturing.dataclasses.instance import Order, OperatorOrderList
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("manufacturing-scheduler")

with open("configuration.json") as f:
    configuration: ManufacturingConfiguration = ManufacturingConfiguration.from_dict(
        json.load(f)
    )

plot_img_path = os.path.join(
    pathlib.Path(__file__).parent.resolve(), "saved_plots", "plot.png"
)
plot_html_path = os.path.join(
    pathlib.Path(__file__).parent.resolve(), "saved_plots", "plot.html"
)

app = FastAPI(
    title="Manufacturing-Scheduler-API",
    summary="FastAPI application serving the Manufacturing Scheduler service.",
    description="FastAPI application serving the Manufacturing Scheduler service.",
    openapi_tags=[
        {
            "name": "manufacturing-scheduler",
            "description": "APIs to interact with the ``manufacturing`` module.",
        }
    ],
)


@app.post(
    "/schedule",
    tags=["manufacturing-scheduler"],
    summary="Generate a schedule",
    description=(
        "Computes an optimized manufacturing schedule based on the provided problem data. "
        "Returns the scheduling solution in JSON format."
    ),
    responses={
        200: {"description": "Solution found and returned successfully."},
        400: {"description": "No solution has been found for the given problem."},
        500: {"description": "Internal server error."},
    },
)
def schedule(problem_data: ManufacturingProblemData, time_limit: Optional[int] = None):
    try:
        logger.info(f"Schedule request received, time limit {time_limit}")

        operator_order_lists: List[OperatorOrderList] = [
            OperatorOrderList(operator, [])
            for operator in range(configuration.operators)
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

        factory = ManufacturingSchedulingFactory(instance)
        solution = factory.get_solution(time_limit=time_limit)
        if solution is not None:
            logger.info(f"Solution found")
            plot_solution(
                instance,
                solution,
                plot_box_constructions=False,
                image_path=plot_img_path,
                html_path=plot_html_path,
            )
        else:
            logger.info("No solution has been found for the given problem")
            return JSONResponse(
                content={"message": "No solution has been found for the given problem"},
                status_code=400,
            )

    except Exception as e:
        logger.error("Exception occurred while scheduling", exc_info=True)
        return JSONResponse(
            content={"message": f"Exception occurred while scheduling:\n{str(e)}"},
            status_code=500,
        )

    return JSONResponse(content=solution.to_json(), status_code=200)


@app.get(
    "/last_schedule_gantt",
    tags=["manufacturing-scheduler"],
    summary="Get last generated schedule gantt chart",
    description="Returns the HTML file of the last generated manufacturing schedule Gantt chart.",
    responses={
        200: {
            "description": "The last generated Gantt chart HTML file is returned.",
            "content": {"text/html": {}},
        },
        404: {"description": "No Gantt chart has been generated yet."},
    },
)
def last_schedule_gantt():
    logger.info("Received request for the last generated schedule Gantt HTML.")
    if not os.path.exists(plot_html_path):
        logger.info("Last generated schedule Gantt HTML not found.")
        return JSONResponse(
            content={"message": "Last generated schedule Gantt HTML not found"},
            status_code=404,
        )
    logger.info("Returning the last generated schedule Gantt HTML file.")
    return FileResponse(path=plot_html_path, media_type="text/html")
