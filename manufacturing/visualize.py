from manufacturing import ManufacturingInstance, ManufacturingSolution
from typing import List, Optional
import plotly.graph_objects as go
import plotly.colors as colors
from datetime import datetime, timedelta


def get_cmap(num_colors) -> List:
    # Use plotly's qualitative color palette
    if num_colors <= len(colors.qualitative.Plotly):
        return colors.qualitative.Plotly[:num_colors]
    else:
        # Generate additional colors using plotly's sample_colorscale
        base_colors = colors.qualitative.Plotly
        additional_colors = colors.sample_colorscale(
            "hsv",
            [
                i / (num_colors - len(base_colors))
                for i in range(num_colors - len(base_colors))
            ],
        )
        return base_colors + additional_colors


def plot_solution(
    instance: ManufacturingInstance,
    solution: ManufacturingSolution,
    plot_box_constructions: bool = False,
    image_path: Optional[str] = None,
    html_path: Optional[str] = None,
) -> None:
    """Plots the resulting schedule."""

    fig = go.Figure()

    color_palette = get_cmap(len(instance.drawers))
    start_datetime = datetime.combine(datetime.today().date(), instance.start_time)
    drawer_to_index = dict((drawer, idx) for idx, drawer in enumerate(instance.drawers))
    drawer_box_mapping = {
        drawer_to_index[drawer.drawer]: drawer.box
        for drawer in solution.drawer_box_mapping
    }
    drawer_capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}

    remaining_boxes = [[] for drawer in range(len(instance.drawers))]

    # Add box constructions
    for i, drawer in enumerate(solution.box_constructions):
        drawer_idx = drawer_to_index[drawer]
        box_construction_start = i * instance.box_construction_duration

        if plot_box_constructions:
            # Add box construction bar
            fig.add_trace(
                go.Scatter(
                    x=[
                        box_construction_start,
                        box_construction_start + instance.box_construction_duration,
                        box_construction_start + instance.box_construction_duration,
                        box_construction_start,
                        box_construction_start,
                    ],
                    y=[4, 4, 6, 6, 4],
                    fill="toself",
                    fillcolor=color_palette[drawer_idx],
                    line=dict(color=color_palette[drawer_idx]),
                    mode="lines",
                    name=f"Construction [Box {drawer_box_mapping[drawer_idx]}, Drawer {drawer}]",
                    showlegend=False,
                )
            )

            # Add text annotation
            fig.add_annotation(
                x=box_construction_start + instance.box_construction_duration / 2,
                y=5,
                text=f"{drawer_box_mapping[drawer_idx]}{drawer}",
                showarrow=False,
                font=dict(color="black"),
            )

        remaining_boxes[drawer_idx].append(
            (
                box_construction_start,
                -1,
                box_construction_start,
                box_construction_start + instance.box_construction_duration,
            )
        )

    # Add replenish windows
    for i, replenish_window in enumerate(instance.replenish_windows):
        start_window, end_window = replenish_window.start, replenish_window.end

        # Add vertical lines for windows
        fig.add_vline(
            x=start_window,
            line=dict(
                color=color_palette[i % len(color_palette)], dash="dash", width=1.5
            ),
        )
        fig.add_vline(
            x=end_window,
            line=dict(
                color=color_palette[i % len(color_palette)], dash="dash", width=1.5
            ),
        )

    # Add replenishments
    for replenishment in solution.replenishments:
        drawer_idx = drawer_to_index[replenishment.drawer]
        start = (start_datetime + timedelta(seconds=replenishment.start)).strftime(
            "%H:%M"
        )

        # Add replenishment bar
        fig.add_trace(
            go.Scatter(
                x=[
                    replenishment.start,
                    replenishment.start + instance.replenish_duration,
                    replenishment.start + instance.replenish_duration,
                    replenishment.start,
                    replenishment.start,
                ],
                y=[0, 0, 2, 2, 0],
                fill="toself",
                fillcolor=color_palette[drawer_idx],
                line=dict(color=color_palette[drawer_idx]),
                mode="lines",
                name=f"Replenishment [Box {drawer_box_mapping[drawer_idx]}, Drawer {replenishment.drawer}, Start {start}]",
                showlegend=False,
            )
        )

        # Add text annotation
        fig.add_annotation(
            x=replenishment.start + instance.replenish_duration / 2,
            y=1,
            text=f"{drawer_box_mapping[drawer_idx]}{replenishment.drawer}",
            showarrow=False,
            font=dict(color="black"),
        )

        replenishment_end = replenishment.start + instance.replenish_duration
        remaining_boxes[drawer_idx].append(
            (
                replenishment_end,
                drawer_capacities[drawer_box_mapping[drawer_idx]],
                replenishment.start,
                replenishment_end,
            )
        )

    # # Add remaining boxes text
    # for drawer_idx in range(len(instance.drawers)):
    #     remaining_boxes[drawer_idx].sort(key=lambda e: e[2])
    #     boxes = drawer_capacities[drawer_box_mapping[drawer_idx]]
    #     for t, inc, start_activity, end_activity in remaining_boxes[drawer_idx]:
    #         if inc > 0:
    #             boxes = inc
    #             y = -1
    #         else:
    #             boxes += inc
    #             y = 3
    #         assert boxes >= 0

    #         fig.add_annotation(
    #             x=(start_activity + end_activity) / 2,
    #             y=y,
    #             text=str(boxes),
    #             showarrow=False,
    #             font=dict(color="black"),
    #         )

    tick_vals = []
    # Add start and end times of each replenishment window
    for replenish_window in instance.replenish_windows:
        tick_vals += [replenish_window.start, replenish_window.end]

    # Convert elapsed seconds to actual clock times based on instance.start_time
    tick_text = [
        (start_datetime + timedelta(seconds=t)).strftime("%H:%M:%S") for t in tick_vals
    ]

    # Update layout
    fig.update_layout(
        # title="Manufacturing Schedule",
        xaxis_title="Time",
        xaxis=dict(
            tickmode="array",
            tickvals=tick_vals,
            ticktext=tick_text,
        ),
        yaxis=dict(range=[-4, 6], visible=False),
        showlegend=False,
        width=1200,
        height=600,
        plot_bgcolor="white",
    )

    # Add grid
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="lightgray")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="lightgray")

    if image_path is None and html_path is None:
        fig.show()
    else:
        fig.write_image(image_path, width=1200, height=600, scale=4)
        fig.write_html(html_path, include_plotlyjs="cdn")
