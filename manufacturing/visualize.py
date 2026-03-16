from manufacturing import ManufacturingInstance, ManufacturingSolution
from typing import List, Optional
import plotly.graph_objects as go
import plotly.colors as colors


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

    color_palette = get_cmap(len(solution.drawer_box_mapping))
    drawer_index = dict(
        (drawer.drawer, idx) for idx, drawer in enumerate(solution.drawer_box_mapping)
    )
    drawer_box_mapping = {
        drawer.drawer: drawer.box for drawer in solution.drawer_box_mapping
    }
    drawer_capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}

    box_filling_durations = dict(
        (bfd.box, bfd.filling_duration) for bfd in instance.box_filling_durations
    )

    orders = []
    for operator_order_list in solution.operator_order_lists:
        start = 0
        for order in operator_order_list.orders:
            orders.append(((start, operator_order_list.operator), order))
            start += box_filling_durations[order.box]
    orders.sort(key=lambda order: order[0])
    order_index = {order.id: i for i, (_, order) in enumerate(orders)}

    remaining_boxes = [[] for drawer in range(len(solution.drawer_box_mapping))]

    # Add box constructions
    for i, drawer in enumerate(solution.box_constructions):
        drawer_idx = drawer_index[drawer]

        if plot_box_constructions:
            # Add box construction bar
            fig.add_trace(
                go.Scatter(
                    x=[i, i + 1, i + 1, i, i],
                    y=[4, 4, 6, 6, 4],
                    fill="toself",
                    fillcolor=color_palette[drawer_idx],
                    line=dict(color=color_palette[drawer_idx]),
                    mode="lines",
                    name=f"Construction [Box {drawer_box_mapping[drawer]}, Drawer {drawer}]",
                    showlegend=False,
                )
            )

            # Add text annotation
            fig.add_annotation(
                x=i + 0.5,
                y=5,
                text=f"{drawer_box_mapping[drawer]}{drawer}",
                showarrow=False,
                font=dict(color="black"),
            )

        remaining_boxes[drawer_idx].append((i, -1, i, i + 1))

    # Add replenishments
    for replenishment in solution.replenishments:
        drawer_idx = drawer_index[replenishment.drawer]
        order_idx = order_index[replenishment.order_id]

        # Add replenishment bar
        fig.add_trace(
            go.Scatter(
                x=[
                    order_idx,
                    order_idx + instance.box_constructions_per_replenishment,
                    order_idx + instance.box_constructions_per_replenishment,
                    order_idx,
                    order_idx,
                ],
                y=[0, 0, 2, 2, 0],
                fill="toself",
                fillcolor=color_palette[drawer_idx],
                line=dict(color=color_palette[drawer_idx]),
                mode="lines",
                name=f"Replenishment [Box {drawer_box_mapping[replenishment.drawer]}, Drawer {replenishment.drawer}]",
                showlegend=False,
                hoverinfo="text",
            )
        )

        # Add text annotation
        fig.add_annotation(
            x=order_idx + instance.box_constructions_per_replenishment / 2,
            y=1,
            text=f"{drawer_box_mapping[replenishment.drawer]}{replenishment.drawer}",
            showarrow=False,
            font=dict(color="black"),
        )

        replenishment_end = order_idx + instance.box_constructions_per_replenishment
        remaining_boxes[drawer_idx].append(
            (
                replenishment_end,
                drawer_capacities[drawer_box_mapping[replenishment.drawer]],
                order_idx,
                replenishment_end,
            )
        )

    # # Add remaining boxes text
    # for drawer in solution.drawer_box_mapping:
    #     drawer_idx = drawer_index[drawer.drawer]
    #     remaining_boxes[drawer_idx].sort(key=lambda e: e[2])
    #     boxes = drawer_capacities[drawer_box_mapping[drawer.drawer]]
    #     for t, inc, start_activity, end_activity in remaining_boxes[drawer_idx]:
    #         if inc > 0:
    #             # replenishment
    #             assert boxes < drawer_capacities[drawer_box_mapping[drawer.drawer]]
    #             boxes = inc
    #             y = -1
    #         else:
    #             # box construction
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

    # Update layout
    # FIXME
    fig.update_layout(
        # title="Manufacturing Schedule",
        xaxis_title="Replenishments",
        xaxis=dict(range=[0, len(instance.orders.order)], visible=False),
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
