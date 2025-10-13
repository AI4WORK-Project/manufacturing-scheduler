# Manufacturing Scheduler

This repository contains the `manufacturing` Python package and the REST API server ([server.py](server.py)). The application is containerized using Docker.

The scheduler uses a static configuration defined in [configuration.json](configuration.json) along with the problem data received via the REST API to compute a solution.
Make sure to modify the configuration file *before* building and running the Docker image.

## Docker Image
To build the Docker image, run:

```sh
docker build -t manufacturing-scheduler .
```

## Running the Container
To run the container and expose the application on port 5000, execute:

```sh
docker run --rm -it -p 5000:5000 manufacturing-scheduler
```

## Accessing the Application
Once the container is running, you can access the application at:

```
http://0.0.0.0:5000
```

## Calling the API

### POST `/schedule`

This endpoint accepts a JSON payload representing a scheduling problem and returns the solution.

#### Query Parameters
- `time_limit`: (Optional, int) The time limit in seconds for the solver. If not provided, the solver will run without a time constraint.

#### Request Body
A JSON object describing the scheduling problem. 
See [instance.json](examples/instances/instance.json) and [instance_annotated.json](examples/instances/instance_annotated.json) for an example.

#### Response
- **Success (200)**: If a solution is found, the response will contain a JSON object representing the solution.
See [instance_solution.json](examples/instances/instance_solution.json) and [instance_solution_annotated.json](examples/instances/instance_solution_annotated.json) for an example.

- **Error (400)**: If no solution is found for the given problem, a message will be returned indicating that no solution was found.

- **Error (500)**: If an internal server error occurs, an error message will be returned.

### GET `/last_schedule_gantt`

This endpoint returns the Gantt chart visualization of the last computed schedule as an HTML file.

#### Response
- **Success (200)**: Returns an HTML Gantt chart that can be viewed in a browser.
- **Error (404)**: If no schedule has been computed yet or the plot file is not found.

## Running an example
To test the API, you can run the `client_example.py` script:

```sh
python examples/client_example.py
```

This script sends a request to the running server and prints the response.

Alternatively, you can use `curl`:
```sh
curl -X POST --json @examples/instances/instance.json "http://0.0.0.0:5000/schedule?time_limit=60"
```

To view the Gantt chart visualization after computing a schedule:
```sh
curl "http://0.0.0.0:5000/last_schedule_gantt" > schedule.html
```
Then open `schedule.html` in your web browser.
