import os
import json
import logging
import pathlib
from flask import Flask, Response, request, send_file
from manufacturing import (
    ManufacturingInstance,
    STRATEGIES,
    get_solution_with_granularity_fallback,
    plot_solution,
)

logging.basicConfig(level=logging.INFO)

app = Flask("Manufacturing-API")

plot_img_path = os.path.join(
    pathlib.Path(__file__).parent.resolve(), "saved_plots", "plot.png"
)
plot_html_path = os.path.join(
    pathlib.Path(__file__).parent.resolve(), "saved_plots", "plot.html"
)


def json_response(body: dict, status: int) -> Response:
    return Response(
        json.dumps(body, separators=(",", ":")),
        mimetype="application/json",
        status=status,
    )


def validation_error_response(error: Exception) -> Response:
    if isinstance(error, KeyError):
        detail = f"Missing field {error}"
    else:
        detail = str(error) or type(error).__name__
    logging.error(f"Validation: {detail}")
    return json_response({"message": "Validation error", "detail": detail}, 422)


@app.route("/schedule", methods=["POST"])
def schedule():
    logging.info("Schedule request received!")

    time_limit = request.args.get("time_limit", None, type=int)
    logging.info(f"Time limit: {time_limit}")

    strategy = request.args.get("strategy", "enumerate", type=str)
    logging.info(f"Strategy: {strategy}")

    try:
        if strategy not in STRATEGIES:
            raise ValueError(
                f"Unknown strategy {strategy}, expected one of {', '.join(STRATEGIES)}"
            )
        instance: ManufacturingInstance = ManufacturingInstance.from_dict(request.json)
    except Exception as e:
        return validation_error_response(e)

    try:
        solution = get_solution_with_granularity_fallback(
            instance, time_limit=time_limit, strategy=strategy
        )
        if solution is not None:
            logging.info(f"Solution found")
            plot_solution(solution, image_path=plot_img_path, html_path=plot_html_path)
        else:
            logging.info("No solution has been found for the given problem")
            return json_response(
                {"message": "No solution has been found for the given problem"}, 400
            )

    except Exception as e:
        logging.exception("Error while computing the schedule")
        return json_response({"message": str(e)}, 500)

    return Response(solution.to_json(), mimetype="application/json", status=200)


@app.route("/last_schedule_gantt", methods=["GET"])
def last_schedule_gantt():
    logging.info("Received request for the last generated schedule plot html.")
    if not os.path.exists(plot_html_path):
        return json_response({"message": "Gantt html not found"}, 404)
    return send_file(plot_html_path, mimetype="text/html")


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="0.0.0.0", port=5000)
