from manufacturing import ManufacturingInstance, ManufacturingSolution

from typing import List, Optional
import matplotlib
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


def plot_solution(
    instance: ManufacturingInstance,
    solution: ManufacturingSolution,
    plot_box_constructions: bool = True,
    destination_path: Optional[str] = None,
) -> None:
    """Plots the resulting schedule."""

    if destination_path is None:
        matplotlib.use("tkagg")
    else:
        matplotlib.use("agg")
    fig, ax = plt.subplots()
    ax.set_xlabel("Time")
    # ax.set_ylabel("Machine")
    ax.set_ylim(bottom=-30, top=30)
    ax.grid(True)
    ax.set_axisbelow(True)

    colors = get_cmap(len(instance.drawers))
    drawer_to_index = dict((drawer.drawer, idx) for idx, drawer in enumerate(instance.drawers))
    drawer_box_mapping = {
        drawer_to_index[drawer.drawer]: drawer.box
        for drawer in solution.drawer_box_mapping
    }
    drawer_capacities = {dc.box: dc.capacity for dc in instance.drawer_capacities}

    remaining_boxes = [[] for drawer in range(len(instance.drawers))]
    for i, drawer in enumerate(solution.box_constructions):
        drawer_idx = drawer_to_index[drawer]
        box_construction_start = i * instance.box_construction_duration
        ax.broken_barh(
            [
                (
                    box_construction_start,
                    instance.box_construction_duration - 0.1,
                )
            ],
            (4, 2),
            facecolors=(colors[drawer_idx]),
        )
        ax.text(
            x=box_construction_start + instance.box_construction_duration / 2,
            y=5,
            s=f"{drawer_box_mapping[drawer_idx]}{drawer}",
            ha="center",
            va="center",
            color="black",
        )
        remaining_boxes[drawer_idx].append(
            (
                box_construction_start,
                -1,
                box_construction_start,
                box_construction_start + instance.box_construction_duration,
            )
        )

    # for i, replenish_window in enumerate(instance.replenish_windows):
    #     start_window, end_window = replenish_window.start, replenish_window.end
    #     ax.vlines(
    #         x=[start_window, end_window],
    #         ymin=-30,
    #         ymax=30,
    #         colors=colors[i % len(colors)],
    #         ls="dashed",
    #         lw=1.5,
    #     )

    for replenishment in solution.replenishments:
        ax.broken_barh(
            [(replenishment.start, instance.replenish_duration)],
            (0, 2),
            facecolors=(colors[drawer_to_index[replenishment.drawer]]),
        )
        ax.text(
            x=replenishment.start + instance.replenish_duration / 2,
            y=1,
            s=f"{drawer_box_mapping[drawer_to_index[replenishment.drawer]]}{replenishment.drawer}",
            ha="center",
            va="center",
            color="black",
        )
        replenishment_end = replenishment.start + instance.replenish_duration
        remaining_boxes[drawer_to_index[replenishment.drawer]].append(
            (
                replenishment_end,
                drawer_capacities[
                    drawer_box_mapping[drawer_to_index[replenishment.drawer]]
                ],
                replenishment.start,
                replenishment_end,
            )
        )

    for drawer_idx in range(len(instance.drawers)):
        remaining_boxes[drawer_idx].sort(key=lambda e: e[2])
        boxes = drawer_capacities[drawer_box_mapping[drawer_idx]]
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

    if destination_path is None:
        plt.show()
    else:
        fig.set_size_inches(24, 16)
        plt.savefig(destination_path, dpi=300)
