# Human-Aware Task Planner – Packaging Machine Management

Repository: `manufacturing-scheduler`

The Packaging Machine Management variant of the AI4Work Human-Aware Task Planner computes the replenishment schedule of a packaging machine: the assignment of box types to the machine drawers and the replenishments that the operators perform while the machine processes the production orders received from the ERP.
It is exposed as a REST service (`server.py`) on top of the `manufacturing` Python package, and it is distributed as a Docker image.

The schedule satisfies hard constraints and optimizes the working conditions of the replenishment operators:

- **Hard constraints**: each drawer holds one box type that fits its size, the machine never runs out of the boxes required by the orders, replenishments do not overlap and a drawer under replenishment cannot be used.
- **Robustness**: after every box construction, at least `minimum_remaining_boxes` boxes of its type are still available, so that the plan remains executable under operational variability.
- **Human-aware objective**: the number of groups of consecutive replenishments is minimized, which reduces the fragmentation of the operators' work, i.e. interruptions and task switching. The resulting `index_of_fragmentation` is reported in the output.

The optimization model is solved with the OR-Tools CP-SAT solver.

## Quick start

Run the published Docker image:

```sh
docker run -p 5000:5000 ghcr.io/ai4work-project/manufacturing-scheduler:latest
```

Or build the image from source:

```sh
docker build -t manufacturing-scheduler .
docker run -p 5000:5000 manufacturing-scheduler
```

Or install the package and run the server directly (Python 3.10 or later; the image and CI use 3.12):

```sh
pip install .
python server.py
```

The service listens on port 5000. The interactive API documentation is available at <http://localhost:5000/docs>.

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/schedule` | Computes a replenishment schedule for the given problem |
| `GET` | `/last_schedule_gantt` | HTML visualization of the last computed schedule |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive API documentation (Swagger UI) |
| `GET` | `/openapi.json` | OpenAPI specification |

### POST `/schedule`

Computes a replenishment schedule for the problem described by the JSON request body.

#### Query parameters

- `time_limit` (optional, int): time limit in seconds for the solver. If not provided, the solver runs without a time limit.
- `strategy` (optional, string): how the solver searches for a solution:
  - `full`: solves the whole model.
  - `enumerate` (default): solves a restricted model for each number of drawers per box, most pressured boxes first, with at most 2 drawers per box (more only if there are more than twice as many drawers as boxes), and returns the best solution.
  - `hint`: solves the restricted model for the first number of drawers per box, then solves the whole model using that solution as a hint.
  - `rolling`: for each number of drawers per box generated as in `enumerate`, plans the orders in windows of 600 orders and keeps the first 500 of each window, then returns the best plan. It scales to thousands of orders, but the solution is optimal only if it matches the lower bound. With at most 600 orders it is the same as `enumerate`.

Replenishments can start every `box_constructions_per_replenishment` orders. If no solution is found this way, the solver retries letting them start at a finer granularity (e.g. every 5, then 2, then 1 orders) and returns the first solution found; with `rolling`, each window retries on its own. The time limit covers all the attempts.

#### Request body

A JSON object describing the problem: operators, box types, drawers and their capacities, replenishment duration, box filling durations, minimum remaining boxes and the orders.
See:

- [instance_description.md](examples/instances/instance_description.md): description of the input and output fields;
- [instance.json](examples/instances/instance.json) and [instance_annotated.json](examples/instances/instance_annotated.json): example request;
- [instance_schema.json](examples/instances/instance_schema.json): JSON schema of the request.

Unknown fields are ignored.

#### Responses

- **200**: the input fields, augmented with the orders of each operator, the drawer box mapping, the replenishments, the sequence of drawers used for the box constructions, the index of fragmentation and the solver statistics. See [instance_solution.json](examples/instances/instance_solution.json), [instance_solution_annotated.json](examples/instances/instance_solution_annotated.json) and [instance_solution_schema.json](examples/instances/instance_solution_schema.json).
- **400**: no solution has been found: `{"message": "No solution has been found for the given problem"}`.
- **422**: the input or the strategy is not valid: `{"message": "Validation error", "detail": "<reason>"}`.
- **500**: internal server error: `{"message": "<reason>"}`.

### GET `/last_schedule_gantt`

Returns an HTML chart of the last schedule computed by `/schedule`: the boxes left in each drawer along the construction sequence, the replenishments of each drawer and the groups of consecutive replenishments.

- **200**: the HTML page, to be viewed in a browser.
- **404**: no schedule has been computed yet: `{"message": "Gantt html not found"}`.

### GET `/health`

Returns `{"status": "healthy", "version": "<version>"}` if the service is up and reachable.
The Docker image uses it as its `HEALTHCHECK`.

## Examples

With the server running:

```sh
# compute a schedule
curl -X POST --json @examples/instances/instance.json "http://localhost:5000/schedule?time_limit=60"

# save the visualization of the last schedule and open it in a browser
curl "http://localhost:5000/last_schedule_gantt" > schedule.html
```

The [examples](examples) folder also contains:

- [client_example.py](examples/client_example.py): sends a request to the server and prints the response;
- [client_example_with_visualization.py](examples/client_example_with_visualization.py): sends a request and shows the visualization of the schedule;
- [example.py](examples/example.py): solves an instance using the `manufacturing` package directly, without the server.

The client examples need `requests`: `pip install .[example]`.

## Development

```sh
pip install .[test]
python -m pytest tests
```

The API tests start the server on port 5000 themselves, so the port must be free. The validator tests take a few minutes.

The CI runs the tests on every push. Every push to `main` publishes the Docker image `ghcr.io/ai4work-project/manufacturing-scheduler:latest`, and every tag `vX.Y.Z` publishes `ghcr.io/ai4work-project/manufacturing-scheduler:X.Y.Z`.

## License

Copyright (c) 2025-2026 Fondazione Bruno Kessler.

This software is distributed under the [Eclipse Public License 2.0](LICENSE) (`EPL-2.0`). See [NOTICE](NOTICE).

## Acknowledgements

This software has been developed within the AI4Work project, which has received funding from the European Union's Horizon Europe research and innovation programme under grant agreement No 101135990.
