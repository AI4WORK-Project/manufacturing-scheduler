import json
import requests
import pathlib
import os
from typing import Dict


LAST_SCHEDULE_GANTT_ENDPOINT = "http://0.0.0.0:5000/last_schedule_gantt"
SCHEDULE_ENDPOINT = "http://0.0.0.0:5000/schedule"
INSTANCES_PATH = os.path.join(pathlib.Path(__file__).parent.resolve(), "instances")


def load_instance_json(instance_name: str) -> Dict:
    instance_path = os.path.join(INSTANCES_PATH, instance_name)
    with open(instance_path, "r") as f:
        instance = json.load(f)
    return instance


def test_invalid_instance():
    response = requests.post(SCHEDULE_ENDPOINT, json="")
    assert response.status_code == 422
    assert len(response.json()["detail"]) > 0


def test_instance_not_solvable():
    instance = load_instance_json("instance_not_solvable.json")
    response = requests.post(SCHEDULE_ENDPOINT, json=instance)
    assert response.status_code == 400
    assert len(response.json()["message"]) > 0


def test_valid_instance():
    instance = load_instance_json("instance0.json")
    response = requests.post(
        SCHEDULE_ENDPOINT, params={"time_limit": 10 * 60}, json=instance
    )
    assert response.status_code == 200


def test_last_schedule_gantt():
    response = requests.get(LAST_SCHEDULE_GANTT_ENDPOINT)
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
