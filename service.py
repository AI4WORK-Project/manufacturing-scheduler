"""Scheduling shared by the HTTP API and the Kafka consumer, so that both
validate the requests and report the errors in the same way."""

import json
import logging
from dataclasses import dataclass
from typing import Any

from apiflask import Schema
from marshmallow import RAISE
from marshmallow_dataclass import class_schema

from manufacturing import (
    STRATEGIES,
    ManufacturingInstance,
    ManufacturingSolution,
    get_solution_with_granularity_fallback,
)

DEFAULT_STRATEGY = "enumerate"
NO_SOLUTION_MESSAGE = "No solution has been found for the given problem"


class UnknownFieldsSchema(Schema):
    """Base of the schema used only to find the unknown fields of a request."""

    class Meta:
        unknown = RAISE


InstanceUnknownFieldsSchema = class_schema(
    ManufacturingInstance, base_schema=UnknownFieldsSchema
)


def unknown_fields(data) -> list[str]:
    """Return the paths of the fields of the request that are not part of an
    instance, such as boxes.0.sise: they are ignored."""

    def paths(errors: dict, prefix: str):
        for key, value in errors.items():
            if value == ["Unknown field."]:
                yield f"{prefix}{key}"
            elif isinstance(value, dict):
                yield from paths(value, f"{prefix}{key}.")

    return sorted(paths(InstanceUnknownFieldsSchema().validate(data), ""))


def check_strategy(strategy: str):
    """Raise ValueError if the strategy is not one of STRATEGIES."""
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy {strategy}, expected one of {', '.join(STRATEGIES)}"
        )


@dataclass
class ScheduleResult:
    """Outcome of a request: the status code and the JSON body of the response,
    and the solution to visualize if one has been found."""

    status: int
    body: str
    schedule: tuple[ManufacturingSolution] | None = None


def json_body(body: dict) -> str:
    return json.dumps(body, separators=(",", ":"))


def internal_error(error: Exception) -> ScheduleResult:
    return ScheduleResult(500, json_body({"message": str(error)}))


def validation_error(error: Exception) -> ScheduleResult:
    if isinstance(error, KeyError):
        detail = f"Missing field {error}"
    else:
        detail = str(error) or type(error).__name__
    logging.error(f"Validation: {detail}")
    return ScheduleResult(
        422, json_body({"message": "Validation error", "detail": detail})
    )


def schedule(data: Any, time_limit: int | None, strategy: str) -> ScheduleResult:
    """Validate the JSON data of an instance and compute its schedule, as
    /schedule does. The strategy must have been checked with check_strategy."""
    try:
        instance: ManufacturingInstance = ManufacturingInstance.from_dict(data)
    except Exception as e:
        return validation_error(e)

    if ignored := unknown_fields(data):
        logging.warning(f"Ignoring unknown fields: {', '.join(ignored)}")
    logging.info(f"Time limit: {time_limit}")
    logging.info(f"Strategy: {strategy}")

    try:
        solution = get_solution_with_granularity_fallback(
            instance, time_limit=time_limit, strategy=strategy
        )
        if solution is None:
            logging.info(NO_SOLUTION_MESSAGE)
            return ScheduleResult(400, json_body({"message": NO_SOLUTION_MESSAGE}))
        logging.info("Solution found")

    except Exception as e:
        logging.exception("Error while computing the schedule")
        return internal_error(e)

    return ScheduleResult(200, solution.to_json(), (solution,))
