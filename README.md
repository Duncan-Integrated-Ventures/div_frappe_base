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

#### Pricing Rule Patches (`div_frappe_base.monkey_patches`)

Two runtime patches against ERPNext's Pricing Rule machinery, installed on app load via `div_frappe_base/__init__.py`. Each patch is gated on the upstream symbol existing so an ERPNext upgrade that removes the target won't crash boot.

**Row-scoped Pricing Rule conditions** — patches `erpnext.accounts.doctype.pricing_rule.utils.filter_pricing_rule_based_on_condition`. Stock ERPNext evaluates each rule's `condition` against `doc.as_dict()` — i.e. the parent doc only, with no per-row context. Conditions that need to differentiate by row-level fields (`supplier_part_no`, `custom_supplier_packaging_type`, etc.) over-match when the parent contains multiple rows whose values together satisfy several rules' conditions, surfacing as `MultiplePricingRuleConflict`.

The patch walks the call stack from the filter back to `get_pricing_rules` (the only frame in the standard apply-pricing-rule path that holds the per-row args dict) and binds it as `current_item` in the eval scope. Conditions can then say:

```python
current_item.get('supplier_part_no') == 'X'
```

and apply only when the row currently being priced matches. When `args` isn't reachable (e.g. transaction-level rules calling the filter directly with no row context), behavior is identical to upstream — `current_item` is simply absent from locals; conditions referencing it raise, which the existing try/except already swallows by skipping that rule.

Worth filing upstream — passing `args` through `filter_pricing_rule_based_on_condition` cleanly is a small backwards-compatible change. The frame-walking workaround stays local until that lands.

**Margin folding for Supplier Quotation Item + per-line charges** — patches `erpnext.controllers.taxes_and_totals.calculate_taxes_and_totals.calculate_item_values`. Closes two gaps:

1. **SQ Item is excluded from upstream's margin whitelist.** `calculate_item_values` runs `calculate_margin` only for eight transaction-item doctypes (Quotation Item, Sales Order Item, Delivery Note Item, Sales Invoice Item, POS Invoice Item, Purchase Invoice Item, Purchase Order Item, Purchase Receipt Item). Supplier Quotation Item is omitted, so a matching Pricing Rule never propagates margin to the row and never folds margin into rate. The patch runs `self.calculate_margin(item)` for SQ rows so they behave like the other doctypes — `margin_type` / `margin_rate_or_amount` / `rate_with_margin` populate, `item.rate` reflects the fold. Worth filing upstream as a one-line whitelist addition.

2. **Per-line charges (`Pricing Rule.custom_per_line_charge`).** Pricing Rule margin is per-unit (`rate × qty` grows with qty). Some supplier fees are flat per-line setup costs that don't scale (e.g. distributor reeling fees on cut+respool packagings — typically $7 charged once regardless of qty). Stuffing these into `margin_rate_or_amount` is wrong: a $7 fee on qty 6 would inflate to $42. The custom field stores them separately; the patch sums them across the row's linked rules and adds once to `net_amount` after the standard `rate × qty` calculation, so `calculate_net_total` / taxes see the inclusive total.

Margin remains ERPNext's responsibility — by the time the per-line block runs, `item.rate` already reflects margin (either via upstream's whitelisted branch, or via the SQ block above). The patch only owns the per-line addition.

#### Pricing Rule Patches (JS — `public/js/pricing_rule_patches.js`)

Client-side mirrors of the two Python patches above. Three prototype patches (one no-op on non-ERPNext deployments) plus a refresh-time cache warmer:

- **Row pass-through for `_get_item_list`** — extends the per-row args dict with **every** scalar field present on the row (`locals[doctype][name]`) before the front end posts to `apply_pricing_rule`. Stock ERPNext sends a fixed subset (`item_code`, `qty`, `uom`, `rate`, …) and any Pricing Rule `condition` referencing a field outside that subset (`current_item.get('any_custom_field')`) silently fails — server logs no error, the row just gets `pricing_rules = ""` until save. The pass-through is field-agnostic: skips framework internals (`__*`), nulls, and nested objects/arrays; never shadows fields ERPNext already populated. Any consumer app's Pricing Rule conditions can read any of their custom row-level fields without coordinating field lists with this app.

- **`_set_values_for_item_list` wrap** — pre-fetches `Pricing Rule.custom_per_line_charge` for any rule named in the response into `div_frappe_base._per_line_charge_cache` before the chained `calculate_taxes_and_totals` runs. Returns a Promise; all upstream callers are inside server-response callbacks so awaiting it is safe.

- **`calculate_item_values` extension** — adds the cached per-line charge to `item.amount` / `item.net_amount` after upstream's per-unit math settles. Same `_TX_DOCTYPES` set as the Python patch (Supplier Quotation, Purchase Order, Purchase Receipt, Purchase Invoice, Quotation, Sales Order, Delivery Note, Sales Invoice, POS Invoice). The Python patch remains authoritative on save; this exists so users see the right total before clicking Save.

- **`warm_per_line_charge_cache(frm)`** — called on every transaction-form refresh, fetches `custom_per_line_charge` for every row currently in the doc. Required because `calculate_item_values` iterates **all** items on every recalc and resets `amount = rate × qty`; if the cache only had entries for the row whose pricing rule just changed (the response children of `_set_values_for_item_list`), every other row's per-line charge would silently drop to 0. Warming on refresh keeps the totals stable when one row's pricing rule changes.

**Install path**: `frappe.ui.form.on(<transaction doctype>, { setup, refresh })` for each `_TX_DOCTYPES` entry. Setup installs the prototype patches once (idempotent via `_patches_installed` flag); refresh runs the cache warmer. The setup hook is the reliable trigger because `erpnext.TransactionController` is guaranteed to exist by the time any controller is being constructed — script-execution time isn't, since this file loads earlier than the erpnext bundle in the bench's app order.

The per-line-charge cache is per-page-load. Rules with no per-line charge are stored as `0` so they're not refetched within the session; a page reload picks up upstream Pricing Rule edits.

#### Pricing Helpers (`div_frappe_base.pricing`)

Two helpers, separable concerns:

- **`per_line_charge_for_item(item)`** — sums `custom_per_line_charge` across the row's linked Pricing Rules. Used by the `calculate_item_values` patch.
- **`effective_per_unit_rate(rule, base_rate, qty)`** — computes `(base_rate + per_unit_margin) + per_line_charge / qty` for callers that work with raw rule data and have no item-doc context (e.g. cost-comparison probes that need a per-unit rate without instantiating a synthetic doc + item + `calculate_taxes_and_totals` per probe).

Both produce the same total cost — `(base_rate + per_unit_margin) × qty + per_line_charge` — so any caller that uses `effective_per_unit_rate` and any caller that uses ERPNext's `calculate_margin` + `per_line_charge_for_item` end up consistent byte-for-byte for the same `(rule, base_rate, qty)`.

`Pricing Rule.custom_per_line_charge` is defined in `div_frappe_base/custom/pricing_rule.json` (Currency, non-negative, inserted after `margin_rate_or_amount`). Loaded on `bench migrate`.

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
