from manufacturing import (
    ManufacturingInstance,
    ManufacturingSolution,
    get_solution_with_granularity_fallback,
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

    # same default strategy as the REST API
    solution: ManufacturingSolution = get_solution_with_granularity_fallback(
        instance, time_limit=60, strategy="enumerate"
    )
    if solution is not None:
        with open(solution_data, "w") as f:
            f.write(json.dumps(json.loads(solution.to_json()), indent=4))

        plot_solution(solution)
    else:
        print("No solution.")


if __name__ == "__main__":
    main()
