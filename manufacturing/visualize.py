from manufacturing import ManufacturingSolution
from typing import Optional
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def plot_solution(
    solution: ManufacturingSolution,
    image_path: Optional[str] = None,
    html_path: Optional[str] = None,
):
    fig = go.Figure()

    box_filling_durations = dict(
        (bfd.box, bfd.filling_duration) for bfd in solution.box_filling_durations
    )

    orders = []
    for operator_order_list in solution.operator_order_lists:
        start = 0
        for order in operator_order_list.orders:
            orders.append(((start, operator_order_list.operator), order))
            start += box_filling_durations[order.box]

    orders.sort(key=lambda order: order[0])
    orders = [order for _, order in orders]
    order_index = {o.id: i for i, o in enumerate(orders)}

    drawer_index = {
        drawer.drawer: idx for idx, drawer in enumerate(solution.drawer_box_mapping)
    }

    activities = []

    # Add box constructions
    for i, drawer in enumerate(solution.box_constructions):
        activities.append(
            dict(
                Task=f"Drawer {drawer}",
                Drawer=f"Drawer {drawer}",
                StartIdx=i,
                EndIdx=i + 1,
            )
        )

    # Add replenishments
    for replenishment in solution.replenishments:
        i = order_index[replenishment.order_id]
        activities.append(
            dict(
                Task=f"Replenishment",
                Drawer=f"Drawer {replenishment.drawer}",
                StartIdx=i,
                EndIdx=i + solution.box_constructions_per_replenishment,
            )
        )

    tasks = [f"Drawer {d}" for d in sorted(drawer_index.keys())] + ["Replenishment"]
    tasks.reverse()

    df = pd.DataFrame(activities)
    reference_date = pd.Timestamp.now()
    df["Start"] = reference_date + pd.to_timedelta(df["StartIdx"], unit="s")
    df["End"] = reference_date + pd.to_timedelta(df["EndIdx"], unit="s")

    # Create timeline plot
    fig = px.timeline(df, x_start="Start", x_end="End", y="Task", color="Drawer")
    fig.update_xaxes(showticklabels=False)
    fig.update_yaxes(categoryorder="array", categoryarray=tasks)
    fig.update_layout(
        plot_bgcolor="white",
        height=400,
        xaxis_title="",
        yaxis_title="",
        showlegend=False,
    )
    fig.update_traces(hovertemplate=None, hoverinfo="skip")

    # Map drawer names to colors
    color_map = {}
    for trace in fig.data:
        drawer_name = trace.name
        color_map[drawer_name] = trace.marker.color

    # Update y-axis tick labels with colors
    y_ticks = fig.layout.yaxis.categoryarray
    fig.update_yaxes(
        tickvals=y_ticks,
        ticktext=[
            f"<span style='color:{color_map.get(t, 'black')}'>{t}</span>"
            for t in y_ticks
        ],
    )

    if image_path is None and html_path is None:
        fig.show()
    else:
        fig.write_image(image_path, width=1200, height=600, scale=4)
        fig.write_html(html_path, include_plotlyjs="cdn")
