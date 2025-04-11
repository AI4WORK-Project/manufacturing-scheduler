from manufacturing import ManufacturingSchedulingFactory, ManufacturingSolution

from typing import Dict, List
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


def get_cmap(num_colors) -> List:
    colors = list(mcolors.TABLEAU_COLORS.values())
    remaining_colors = num_colors - len(colors)
    if remaining_colors > 0:
        cmap = plt.cm.get_cmap("hsv", remaining_colors + 1)
        colors += [cmap(i) for i in range(remaining_colors)]
        return colors
    return colors[:num_colors]


def get_drawer_capacity(
    factory: ManufacturingSchedulingFactory,
    drawer_box_mapping: Dict[int, str],
    drawer: int,
) -> int:
    box = drawer_box_mapping[drawer]
    return factory.drawer_capacities[factory.boxes.index(box)]


def plot_solution(
    factory: ManufacturingSchedulingFactory, solution: ManufacturingSolution
) -> None:
    """Plots the resulting schedule."""

    fig, ax = plt.subplots()
    ax.set_xlabel("Time")
    ax.set_ylabel("Machine")
    ax.set_xticks(
        range(
            0,
            len(solution.box_constructions) * factory.instance.box_construction_duration
            + 5,
            5,
        )
    )
    ax.set_ylim(bottom=-30, top=30)
    ax.grid(True)
    ax.set_axisbelow(True)

    colors = get_cmap(factory.num_drawers)
    drawer_to_index = dict(
        (drawer, idx) for idx, drawer in enumerate(factory.instance.drawers)
    )
    drawer_box_mapping = {
        drawer_to_index[drawer.drawer]: drawer.box
        for drawer in solution.drawer_box_mapping
    }

    remaining_boxes = [[] for drawer in range(factory.num_drawers)]
    for i, drawer in enumerate(solution.box_constructions):
        drawer_idx = drawer_to_index[drawer]
        box_construction_start = i * factory.instance.box_construction_duration
        ax.broken_barh(
            [
                (
                    box_construction_start,
                    factory.instance.box_construction_duration - 0.1,
                )
            ],
            (4, 2),
            facecolors=(colors[drawer_idx]),
        )
        ax.text(
            x=box_construction_start + factory.instance.box_construction_duration / 2,
            y=5,
            s=f"b{drawer_box_mapping[drawer_idx]},d{drawer}",
            ha="center",
            va="center",
            color="black",
        )
        remaining_boxes[drawer_idx].append(
            (
                box_construction_start,
                -1,
                box_construction_start,
                box_construction_start + factory.instance.box_construction_duration,
            )
        )

    for i, replenish_window in enumerate(factory.instance.replenish_windows):
        start_window, end_window = replenish_window.start, replenish_window.end
        ax.vlines(
            x=[start_window, end_window],
            ymin=-30,
            ymax=30,
            colors=colors[i % len(colors)],
            ls="dashed",
            lw=1.5,
        )

    for replenishment in solution.replenishments:
        ax.broken_barh(
            [(replenishment.start, factory.instance.replenish_duration)],
            (0, 2),
            facecolors=(colors[drawer_to_index[replenishment.drawer]]),
        )
        replenishment_end = replenishment.start + factory.instance.replenish_duration
        remaining_boxes[drawer_to_index[replenishment.drawer]].append(
            (
                replenishment_end,
                get_drawer_capacity(
                    factory, drawer_box_mapping, drawer_to_index[replenishment.drawer]
                ),
                replenishment.start,
                replenishment_end,
            )
        )

    for drawer_idx in range(factory.num_drawers):
        remaining_boxes[drawer_idx].sort(key=lambda e: e[2])
        boxes = get_drawer_capacity(factory, drawer_box_mapping, drawer_idx)
        for t, inc, start_activity, end_activity in remaining_boxes[drawer_idx]:
            if inc > 0:
                boxes = inc
                y = -1
            else:
                boxes += inc
                y = 3
            assert boxes >= 0

            ax.text(
                x=(start_activity + end_activity) / 2,
                y=y,
                s=str(boxes),
                ha="center",
                va="center",
                color="black",
            )

    plt.show()
