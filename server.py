import os
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


@app.route("/schedule", methods=["POST"])
def schedule():
    try:
        logging.info("Schedule request received!")

        instance: ManufacturingInstance = ManufacturingInstance.from_dict(request.json)

        time_limit = request.args.get("time_limit", None, type=int)
        logging.info(f"Time limit: {time_limit}")

        strategy = request.args.get("strategy", "enumerate", type=str)
        logging.info(f"Strategy: {strategy}")
        if strategy not in STRATEGIES:
            return Response(
                '{"message":"Unknown strategy %s"}' % strategy,
                mimetype="application/json",
                status=400,
            )
        solution = get_solution_with_granularity_fallback(
            instance, time_limit=time_limit, strategy=strategy
        )
        if solution is not None:
            logging.info(f"Solution found")
            plot_solution(solution, image_path=plot_img_path, html_path=plot_html_path)
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


@app.route("/last_schedule_gantt", methods=["GET"])
def last_schedule_gantt():
    logging.info("Received request for the last generated schedule plot html.")
    if not os.path.exists(plot_html_path):
        return Response(
            '{"message":"Gantt html not found"}',
            mimetype="application/json",
            status=404,
        )
    return send_file(plot_html_path, mimetype="text/html")


if __name__ == "__main__":
    from waitress import serve

    serve(app, host="0.0.0.0", port=5000)
