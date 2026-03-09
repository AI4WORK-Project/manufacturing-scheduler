from manufacturing import (
    ManufacturingInstance,
    ManufacturingSolution,
    ManufacturingSchedulingFactory,
    plot_solution,
)
import pathlib
import os
import json


def main():
    examples_path = pathlib.Path(__file__).parent.resolve()
    problem_data = os.path.join(examples_path, "instances/instance.json")
    solution_data = os.path.join(examples_path, "instances/instance_solution.json")

    with open(problem_data) as f:
        instance: ManufacturingInstance = ManufacturingInstance.from_dict(json.load(f))

    factory = ManufacturingSchedulingFactory(instance)
    solution: ManufacturingSolution = factory.get_solution()
    if solution is not None:
        with open(solution_data, "w") as f:
            f.write(json.dumps(json.loads(solution.to_json()), indent=4))

        plot_solution(instance, solution)


if __name__ == "__main__":
    main()
