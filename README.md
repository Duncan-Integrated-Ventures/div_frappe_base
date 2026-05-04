<!-- Copyright (c) 2026,  and contributors
For license information, please see license.txt-->

Extensions and utilities for Frappe.

### Features

#### Utilities

- `get_total_stock_quantity(item_code, inventory_dimensions=None)` — total actual qty across all warehouses from SLE, with optional inventory dimension filters.
- `get_child_list(doctype, parent, fields, pluck)` — whitelisted helper to fetch child table rows.
- `group_insert(docs, commit_each=False)` — batch-insert a list of documents.
- `get_item_variant(item_name=None, item=None)` — loads an Item variant with its template's attributes parsed into `item._attributes`.

#### UOM utilities (`div_frappe_base.uom`)

Generic UOM operations shared by any app that needs unit canonicalisation.

**`create_uom_family(base_uom, primary_symbol, alias_symbols=None, category=None)`** — one call creates a base UOM plus all SI-prefix siblings (Pico…Tera, 9 UOMs total), the chained `1000×` UOM Conversion Factors between consecutive prefixes, and aliases for every `(prefix_letter, symbol, spacer)` combination. Idempotent; rerun to add new prefixes or symbols.

- Aliases generated per prefix: `{letter}{symbol}` + `{letter} {symbol}` for the primary letter (`k`, `m`, `M`…) plus extras (`K` for kilo, `u` for micro). E.g. base `Ohm`/`Ω` with alias `ohm` produces `kΩ`, `k Ω`, `KΩ`, `K Ω`, `kohm`, `k ohm`, `Kohm`, `K ohm` for Kiloohm.
- Conversion factors are chained between consecutive prefixes (1 Larger = 1000 Smaller) rather than each prefix linking to the base, so `Decimal(21,9)` doesn't overflow at Tera/Pico extremes. `select_display_uom` walks the chain transitively.
- Auto-creates the `UOM Category` link if missing.

Exposed as a button on **two surfaces** (both wired in `hooks.py`):

- **UOM list view** — *Create UOM Family* inner button (`public/js/uom_list.js`).
- **Canonical Attribute form** — *Create UOM Family* button, pre-fills the dialog with `frm.doc.uom` as the base (`public/js/canonical_attribute.js`).

Both invoke the shared dialog helper `div_frappe_base.show_uom_family_dialog(opts)` defined in `public/js/uom_family.js` (loaded via `app_include_js`).

**`resolve_uom_from_alias(alias_text)`** — case-sensitive lookup against the UOM Alias child table (with BINARY collation comparison so `MΩ`≠`mΩ`). Falls back to a direct UOM-name match. Per-request cached.

**`convert_to_canonical(value, source_uom, canonical_uom)`** — converts via the `UOM Conversion Factor` graph **transitively**. Stock ERPNext defines factors hub-and-spoke (e.g. `Kg ↔ Gram` and `Kg ↔ Milligram` but no direct `Gram ↔ Milligram`); a direct lookup would silently return the raw value, this walks the chain.

**`select_display_uom(value, base_uom, *, integer=False)`** — picks the UOM in `base_uom`'s decimal-prefix-only conversion graph that gives the shortest decimal representation (length of `%g` with leading `0` dropped, so `.65` counts as 3). Lets canonicalisation store `0.8 mm` instead of `800 µm`, and `631 nm` instead of `0.631 µm`. Tiebreaker prefers the larger unit, so `0.65 mm` wins over `650 µm` on a tie. `integer=True` adds a precision-loss tiebreaker so `1500 mA` stays at `1500 mA` rather than rounding to `2 A`.

#### UOM case-sensitivity (alias + symbol)

`patches/uom_symbol_case_sensitive.py` (pre-model-sync) and `install.after_install` together flip both `tabUOM Alias.alias` and `tabUOM.symbol` from `utf8mb4_unicode_ci` to `utf8mb4_bin`, so SI-prefix variants like `MΩ` (Megaohm) and `mΩ` (Milliohm) — or `MA` Megaampere vs `mA` Milliampere on the parent UOM — stay distinct under their unique indexes. Both ALTERs are idempotent.

#### `setup_inventory_dimensions` utility

`div_frappe_base.utils.setup_inventory_dimensions(inv_dim_dict_list)` creates one or more **Inventory Dimensions** and performs the supporting setup that Frappe's stock module leaves manual:

- Creates the `Inventory Dimension` record (skips if it already exists).
- Uncollapses the resulting inventory-dimension section.
- Relabels `Source {Name}` custom fields to `{Name}` and hides the `Target {Name}` fields (keeping the `Purchase Invoice Item` variant visible and relabeled).
- Sets `no_copy` on all generated custom fields and marks them read-only on doctypes that aren't scannable form targets.

Each entry in the input list accepts:

| Key | Required | Default |
|---|---|---|
| `dimension_name` | yes | — |
| `reference_document` | yes | — |
| `target_fieldname` | no | `None` |
| `apply_to_all_doctypes` | no | `1` |

Used by `procurement` (`Packaging Type`) during install. `div_ems` uses the [BEAM](https://github.com/agritheory/beam) variant (`beam.beam.inventory_dimension.setup_inventory_dimensions`) for its `Feeder` / `Customer` dimensions because BEAM's version also wires carry-forward propagation.

#### Import Profile

Reusable spreadsheet ingestion configuration. An **`Import Profile`** doctype names a target doctype + child-table field, a header row index, optional sheet name, and a column-mapping child table (`Import Profile Column`) that maps source column names → child-doctype fieldnames. An `Import Profile Scope` child table holds optional pre-import filters.

`validate()` enforces that `target_child_table_field` resolves to a `Table` / `Table MultiSelect` field on the target doctype, and that every `column_mappings.target_field` is a real fieldname on the resolved child doctype — config errors are caught at save time, not at import time.

The matching front end ships as `public/js/spreadsheet_importer.bundle.js` — a Vue-style importer dialog that consumes the profile, parses `.xlsx` / `.xls` (auto-picks the data sheet when the active sheet is a metadata cover) or `.csv` rows, applies the mappings, and inserts child rows into the target document.

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
