from manufacturing import ManufacturingSolution
from typing import Dict, List, Optional
import plotly.graph_objects as go
from plotly.colors import qualitative
from plotly.subplots import make_subplots

REPLENISHMENT_COLOR = "rgba(192,57,43,0.85)"


def plot_solution(
    solution: ManufacturingSolution,
    image_path: Optional[str] = None,
    html_path: Optional[str] = None,
):
    """Plots the boxes left in each drawer along the construction sequence, the
    replenishments of each drawer and the groups of consecutive replenishments.
    The figure is shown if no path is given."""
    k = solution.box_constructions_per_replenishment
    order_ids = construction_sequence(solution)
    order_index = {order_id: i for i, order_id in enumerate(order_ids)}
    num_orders = len(order_ids)

    box_size = {b.box: b.size for b in solution.boxes}
    capacity = {c.box: c.capacity for c in solution.drawer_capacities}
    drawer_size = {
        d.drawer: d.size
        for d in solution.drawers.lower_level + solution.drawers.upper_level
    }
    mapping = {m.drawer: m.box for m in solution.drawer_box_mapping}
    drawers = sorted(mapping)
    boxes = sorted(set(mapping.values()))
    palette = qualitative.Plotly
    color = {b: palette[i % len(palette)] for i, b in enumerate(boxes)}

    starts: Dict[int, List[int]] = {}
    for r in solution.replenishments:
        starts.setdefault(order_index[r.order_id], []).append(r.drawer)

    # a replenishment fills the drawer when it starts, and the drawer is not used
    # until it ends
    level = {d: capacity[mapping[d]] for d in drawers}
    levels: Dict[int, List[int]] = {d: [] for d in drawers}
    for i in range(num_orders):
        for d in starts.get(i, []):
            level[d] = capacity[mapping[d]]
        level[solution.box_constructions[i]] -= 1
        for d in drawers:
            levels[d].append(level[d])

    # groups of consecutive replenishments: [start, end, replenishments]
    groups = []
    for s in sorted(starts):
        if groups and s == groups[-1][1]:
            groups[-1][1] = s + k
            groups[-1][2] += len(starts[s])
        else:
            groups.append([s, s + k, len(starts[s])])

    # the replenishment groups, then one row per drawer
    groups_row = 1
    titles = ["Replenishment groups"] + [
        f"Drawer {d} ({drawer_size[d]}) · box {mapping[d]} ({box_size[mapping[d]]})"
        for d in drawers
    ]
    fig = make_subplots(
        rows=len(drawers) + 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        subplot_titles=titles,
        row_heights=[0.6] + [1] * len(drawers),
    )

    for start, end, count in groups:
        fig.add_trace(
            go.Scatter(
                x=[start, start, end, end, start],
                y=[0, 1, 1, 0, 0],
                fill="toself",
                mode="lines",
                line=dict(width=0),
                fillcolor=REPLENISHMENT_COLOR,
                hovertemplate=f"group: orders {start}–{end}<br>"
                f"{count} replenishments<extra></extra>",
            ),
            row=groups_row,
            col=1,
        )
    fig.update_yaxes(visible=False, range=[0, 1], row=groups_row, col=1)

    for row, d in enumerate(drawers, start=2):
        box = mapping[d]
        fig.add_trace(
            go.Scatter(
                x=list(range(num_orders)),
                y=levels[d],
                mode="lines",
                line=dict(shape="hv", width=1, color=color[box]),
                fill="tozeroy",
                opacity=0.6,
                hovertemplate=f"drawer {d}<br>order %{{x}}<br>"
                "%{y} boxes<extra></extra>",
            ),
            row=row,
            col=1,
        )
        xs, ys = [], []
        for s, replenished in sorted(starts.items()):
            if d in replenished:
                xs += [s, s, s + k, s + k, s, None]
                ys += [0, capacity[box], capacity[box], 0, 0, None]
        if xs:
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    fill="toself",
                    mode="lines",
                    line=dict(width=0),
                    fillcolor=REPLENISHMENT_COLOR,
                    hoverinfo="skip",
                ),
                row=row,
                col=1,
            )
        fig.update_yaxes(
            range=[0, capacity[box] * 1.05],
            tickvals=[0, capacity[box]],
            row=row,
            col=1,
        )

    fig.update_xaxes(range=[0, num_orders], showgrid=False)
    fig.update_xaxes(
        title_text="order in the construction sequence",
        row=len(drawers) + 1,
        col=1,
    )
    fig.update_annotations(font_size=11, xanchor="left", x=0)
    fig.update_layout(
        height=100 + 110 * (len(drawers) + 1),
        plot_bgcolor="white",
        hovermode="closest",
        showlegend=False,
        margin=dict(l=60, r=20, t=40, b=50),
    )

    if image_path is None and html_path is None:
        fig.show()
    if image_path is not None:
        fig.write_image(image_path, width=1200, height=fig.layout.height, scale=2)
    if html_path is not None:
        fig.write_html(html_path, include_plotlyjs="cdn")


def construction_sequence(solution: ManufacturingSolution) -> List[int]:
    """The order ids in construction order: the orders are assigned to the
    operators round robin, then sorted by (start time, operator)."""
    box_filling_durations = dict(
        (bfd.box, bfd.filling_duration) for bfd in solution.box_filling_durations
    )
    orders = []
    for operator_order_list in solution.operator_order_lists:
        start = 0
        for order in operator_order_list.orders:
            orders.append(((start, operator_order_list.operator), order.id))
            start += box_filling_durations[order.box]
    orders.sort(key=lambda order: order[0])
    return [order_id for _, order_id in orders]
