### DIV Frappe Base

Extensions and utilities for Frappe.

### Features

#### Utilities

- `get_total_stock_quantity(item_code, inventory_dimensions=None)` — total actual qty across all warehouses from SLE, with optional inventory dimension filters.
- `get_child_list(doctype, parent, fields, pluck)` — whitelisted helper to fetch child table rows.
- `group_insert(docs, commit_each=False)` — batch-insert a list of documents.
- `get_item_variant(item_name=None, item=None)` — loads an Item variant with its template's attributes parsed into `item._attributes`.

**Note:** Inventory dimension setup (`setup_inventory_dimensions`) and carry-forward propagation (`propagate_inventory_dimensions`) have been moved to [BEAM](https://github.com/agritheory/beam) as of 2026-04-17. Apps should import from `beam.beam.inventory_dimension`.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app div_frappe_base
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/div_frappe_base
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
