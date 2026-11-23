import json
import logging
import threading
from importlib.metadata import PackageNotFoundError, version

from apiflask import APIFlask, HTTPError, Schema
from apiflask.fields import Integer, String
from apiflask.validators import OneOf
from flask import Response, request
from flask_cors import CORS
from marshmallow import EXCLUDE
from marshmallow_dataclass import class_schema
from werkzeug.exceptions import BadRequest

from manufacturing import (
    ManufacturingInstance,
    ManufacturingSolution,
    STRATEGIES,
    get_solution_with_granularity_fallback,
    solution_figure,
)

logging.basicConfig(level=logging.INFO)

try:
    __version__ = version("manufacturing")
except PackageNotFoundError:
    __version__ = "unknown"

app = APIFlask(
    "Manufacturing-API",
    title="Human-Aware Task Planner – Packaging Machine Management",
    version=__version__,
)
app.openapi_version = "3.0.2"
CORS(app)


class DocSchema(Schema):
    """The schemas only document the API: the input is parsed and validated by
    the dataclasses, so validation is disabled and unknown fields are ignored."""

    class Meta:
        unknown = EXCLUDE


InstanceSchema = class_schema(ManufacturingInstance, base_schema=DocSchema)
SolutionSchema = class_schema(ManufacturingSolution, base_schema=DocSchema)


class ScheduleQuerySchema(Schema):
    time_limit = Integer(
        required=False,
        metadata={
            "description": "Time limit in seconds for the solver, covering all the "
            "attempts. If not provided, the solver runs without a time limit."
        },
    )
    strategy = String(
        required=False,
        load_default="enumerate",
        validate=OneOf(STRATEGIES),
        metadata={
            "description": "How the solver searches for a solution: full, "
            "enumerate (default), hint or rolling. See the README for details."
        },
    )


class HealthSchema(Schema):
    status = String(metadata={"example": "healthy"})
    version = String(metadata={"example": __version__})


ERROR_RESPONSES = {
    400: "No solution has been found for the given problem",
    422: 'Invalid input: {"message": "Validation error", "detail": "..."}',
    500: 'Internal server error: {"message": "..."}',
}



class LastScheduleGantt:
    """HTML visualization of the last computed schedule, kept in memory."""

    def __init__(self):
        self._lock = threading.Lock()
        self._html = None

    def update(self, solution):
        try:
            html = solution_figure(solution).to_html(include_plotlyjs="cdn")
        except Exception:
            logging.warning("Could not visualize the schedule", exc_info=True)
            html = None
        with self._lock:
            self._html = html

    def get(self):
        with self._lock:
            return self._html


last_schedule_gantt_html = LastScheduleGantt()


def json_response(body: dict, status: int) -> Response:
    return Response(
        json.dumps(body, separators=(",", ":")),
        mimetype="application/json",
        status=status,
    )


@app.get("/health")
@app.output(HealthSchema)
@app.doc(summary="Check if the service is alive and reachable")
def health_check():
    logging.debug("Health check pinged.")
    return json_response({"status": "healthy", "version": __version__}, 200)


def validation_error_response(error: Exception) -> Response:
    if isinstance(error, KeyError):
        detail = f"Missing field {error}"
    else:
        detail = str(error) or type(error).__name__
    logging.error(f"Validation: {detail}")
    return json_response({"message": "Validation error", "detail": detail}, 422)


@app.errorhandler(BadRequest)
def bad_request(error: BadRequest) -> Response:
    # the request parser attaches the decoding error to the 400 it raises for a
    # body that is not valid JSON: report it as invalid input, as documented
    decoding_error = getattr(error, "exc", None)
    if isinstance(decoding_error, (json.JSONDecodeError, UnicodeDecodeError)):
        return validation_error_response(ValueError(f"Invalid JSON: {decoding_error}"))
    return app.error_callback(HTTPError(400, message=error.name))


@app.post("/schedule")
@app.input(ScheduleQuerySchema, location="query", validation=False)
@app.input(InstanceSchema, location="json", validation=False)
@app.output(SolutionSchema)
@app.doc(summary="Compute a replenishment schedule", responses=ERROR_RESPONSES)
def schedule(query_data, json_data):
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
        if solution is None:
            logging.info("No solution has been found for the given problem")
            return json_response(
                {"message": "No solution has been found for the given problem"}, 400
            )
        logging.info("Solution found")

    except Exception as e:
        logging.exception("Error while computing the schedule")
        return json_response({"message": str(e)}, 500)

    last_schedule_gantt_html.update(solution)
    return Response(solution.to_json(), mimetype="application/json", status=200)


@app.get("/last_schedule_gantt")
@app.doc(
    summary="Visualize the last computed schedule",
    description="HTML chart of the boxes left in each drawer along the "
    "construction sequence, the replenishments of each drawer and the groups of "
    "consecutive replenishments in the last schedule computed by /schedule.",
    responses={
        200: {"description": "HTML visualization", "content": {"text/html": {}}},
        404: "No schedule has been computed yet",
    },
)
def last_schedule_gantt():
    logging.info("Received request for the last generated schedule plot html.")
    html = last_schedule_gantt_html.get()
    if html is None:
        return json_response({"message": "Gantt html not found"}, 404)
    return Response(html, mimetype="text/html")


def start():
    logging.info("Starting...")
    from waitress import serve

    serve(app, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    start()
