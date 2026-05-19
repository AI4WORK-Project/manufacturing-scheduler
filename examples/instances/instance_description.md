# Input and Output API Description

This document details the structure and fields of the scheduler’s input and output JSON files.

## Input JSON Structure

The input file defines the configuration of the packaging machine and the set of orders to be processed.

| Field | Type | Description |
| :--- | :--- | :--- |
| **operators** | Integer | The number of packaging operators available in the system. |
| **boxes** | Array | A list of available box types and their associated sizes (i.e., "L", "M", "S"). |
| **drawers** | Object | Contains arrays for `lower_level` and `upper_level` drawers, defining their ID, size, and whether they are `enabled`. |
| **drawer_capacities** | Array | Defines the maximum capacity for each box type within a drawer. |
| **box_constructions_per_replenishment** | Integer | The duration of one replenishment measured in the number of box constructions. |
| **box_filling_durations** | Array | The time required to fill each specific box type. |
| **minimum_remaining_boxes** | Integer | The safety stock level to be maintained in drawers to ensure robustness. |
| **orders** | Object | The list of orders received from the ERP system. |


## Output JSON Structure

The output file preserves all data from the input file and augments it with the computed assignment of box types to drawers, the generated replenishment activities, and the fragmentation index.

| Field | Type | Description |
| :--- | :--- | :--- |
| **operator_order_lists** | Array | The list of orders assigned to each operator. |
| **is_solution_optimal** | Boolean | Indicates if the generated solution is optimal or just feasible. |
| **index_of_fragmentation** | Float | A percentage (0-100%) representing the degree of fragmentation among replenishment groups. |
| **drawer_box_mapping** | Array | The mapping of specific box types to designated drawers. |
| **replenishments** | Array | A list of replenishment activities, including the drawer, box type, and associated order ID. |
| **box_constructions** | Array | A sequence of drawer IDs used for box construction activities. |
| **solver** | Object | Contains performance statistics including `objective_value`, `best_objective_bound`, and `user_time`. |

