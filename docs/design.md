<!-- Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
For license information, please see license.txt-->

# div_frappe_base

Design documents for every feature in `apps/div_frappe_base/`. Each feature is one `##` subsection
below; the structure inside each subsection is fixed (Summary, Problem, Target app, Functional
workflow, Schema, Overrides + hooks, Permissions, Out of scope, Open questions).

This app sits below ERPNext in the bench's stack — it extends the base Frappe / ERPNext layer with utilities every higher-tier app reuses (UOM family management, pricing-rule extensions, child-table import tooling). Anything that would be reasonable to upstream into Frappe / ERPNext (and would benefit every Frappe customer, not just NPXL) lands here.

## SI-Prefix UOM Families

### 1. Summary
Whitelisted helpers that scaffold a base `UOM` plus all its SI-prefixed siblings (Femto through Peta) in one call, wiring up the chained `UOM Conversion Factor` rows and per-UOM aliases. Operators run the dialog from the UOM list view; idempotent — re-running fills gaps without disturbing existing records. A companion helper links two existing UOMs across families (e.g. Bit ↔ Byte = 1/8) and a transitive BFS resolver composes those links with prefix chains so prefix-pair lookups (Mbps↔MBps, …) work without storing every pair explicitly.

### 2. Problem / why now
ERPNext ships with a flat `UOM` doctype and per-pair `UOM Conversion Factor` rows. Modeling SI-prefixed families one UOM at a time is tedious and error-prone — engineers building a new family of physical units (length, frequency, capacitance) end up either skipping prefixes they need later or hand-creating dozens of conversion rows. The dialog and helpers turn it into a one-call operation, and the alias system + case-sensitive symbol column lets case-distinguished prefixes (MΩ vs mΩ) coexist without collation collisions.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **User** opens the UOM list view and clicks **Create UOM Family** (button added by `uom_list.js`). Trigger: list-view button.
2. **User** fills the dialog: Base UOM Name, Primary Symbol, optional Additional Alias Symbols, Category (auto-created if missing), and Min/Max prefix range. Trigger: dialog submit.
3. **System** — `div_frappe_base.uom.create_uom_family(...)` (whitelisted) creates the base UOM, every SI-prefixed sibling within the requested range, the chained Conversion Factor rows (1000× factors between consecutive prefixes), and `UOM Alias` rows for each provided alias symbol. Idempotent: existing rows are skipped without error. Returns `{uoms_created, factors_created, aliases_created}`. Trigger: whitelisted method call.
4. **User** clicks **Link UOM Families** to bridge two families. Fills From UOM, To UOM, Value, Category. Trigger: dialog submit.
5. **System** — `div_frappe_base.uom.link_uom_families(...)` (whitelisted) inserts a single Conversion Factor row joining the two families. The transitive BFS in `select_display_uom` then makes every prefix pair across the two families reachable. Trigger: whitelisted method call.
6. **System (any caller)** — `div_frappe_base.uom.resolve_uom_from_alias(text)` maps a user-entered string to a UOM name by checking, in order: direct UOM name → `UOM.symbol` (BINARY-collated unique) → `UOM Alias.alias`. Used by bulk-import paths so case-distinguished prefixes resolve correctly. Cache lives on `frappe.local` so it resets per HTTP request / RQ job. Trigger: in-method.
7. **System (any caller)** — `convert_to_canonical(value, source_uom, canonical_uom)` walks the Conversion Factor graph via BFS to convert a numeric value across UOMs (within or across families); `select_display_uom(value, base_uom)` picks the family member whose decimal representation is shortest (e.g. `0.8 mm` instead of `800 µm`). Trigger: in-method.

### 5. Schema

#### 5.1 `UOM` — Custom-Field

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `custom_aliases` | Table | UOM Alias | | Per-UOM aliases. Used by `resolve_uom_from_alias` to map free-form user input (e.g. `kΩ`) onto the canonical UOM. |

A property setter on `UOM.symbol` enforces UNIQUE; the column's collation is changed to `utf8mb4_bin` (see § 5.5) so case-distinguished symbols don't collide.

#### 5.2 `UOM Alias` — New (child table)

Parent: `UOM` via the `custom_aliases` Table field.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `alias` | Data | — | ✓ | Unique. Stored on a `utf8mb4_bin` column (see § 5.5) so `MΩ` and `mΩ` are distinct values. |

#### 5.3 `UOM Category` — Touched (no change)

No schema impact. Auto-created at family-creation time when the requested category doesn't already exist.

#### 5.4 `UOM Conversion Factor` — Touched (no change)

No schema impact. Created in bulk at family-creation time and one-at-a-time at link time. The BFS resolvers read this doctype.

#### 5.5 Database collation property setters

`UOM.symbol` and `UOM Alias.alias` are switched to the `utf8mb4_bin` collation at install (`install.after_install`) and via a one-shot `pre_model_sync` patch (`patches/uom_symbol_case_sensitive.py`) for existing benches. Idempotent — both paths check the current collation before issuing the ALTER. The patch must run before the UNIQUE property setter on `UOM.symbol` so the index is built on the case-sensitive column.

### 6. Overrides, hooks, and direct file edits

**`app_include_js`:**
- `/assets/div_frappe_base/js/uom_family.js` — exposes `div_frappe_base.show_uom_family_dialog` and `show_uom_link_dialog`. Used by both the UOM list-view buttons and any other form that wants to launch the dialog (e.g. the Canonical Attribute form).

**`doctype_list_js`:**
- `UOM` → `div_frappe_base/public/js/uom_list.js` — adds the **Create UOM Family** and **Link UOM Families** list-view buttons; both call into `uom_family.js` and refresh the list on completion.

**`after_install`:**
- `div_frappe_base.install.after_install` — calls `make_uom_symbol_case_sensitive` and `make_uom_alias_case_sensitive` (idempotent collation ALTERs).

**`patches`:**
- `patches/uom_symbol_case_sensitive.py` (`pre_model_sync`) — applies the same collation change to existing benches before the UNIQUE property setter is sync'd.

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/uom.py` — exports `create_uom_family`, `link_uom_families`, `resolve_uom_from_alias`, `convert_to_canonical`, `select_display_uom`, `reachable_uoms`. Whitelisted ones are decorated `@frappe.whitelist()`.
- `apps/div_frappe_base/div_frappe_base/install.py` — `after_install` and the two collation helpers.
- `apps/div_frappe_base/div_frappe_base/public/js/uom_family.js` — the two dialog functions.
- `apps/div_frappe_base/div_frappe_base/public/js/uom_list.js` — the list-view button registration.

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| System Manager | ✓ | ✓ | ✓ | ✓ | | |
| Stock User | ✓ | | | | | |

(Applies to the new `UOM Alias` child table and to the whitelisted helpers' write paths into UOM / Conversion Factor / Category.)

### 8. Out of scope

- **Non-decimal prefix families.** The family creator only generates SI prefixes (10ⁿ steps). Binary prefixes (KiB, MiB) and customary-unit families (in/ft/yd) need a different scaffolder.
- **Backfill of conversion factors for cross-family pairs the resolver discovers transitively.** `select_display_uom` discovers reachable UOMs at read time via BFS; it never persists computed factors back to the database.
- **Migration of existing UOMs from the legacy non-binary collation.** The patch handles existing column collation but does not de-dupe symbols that were already collapsed by the old case-insensitive collation; operators handle those manually.

---

## Pricing Rule Per-Line Charge

### 1. Summary
A new `Pricing Rule.custom_per_line_charge` (Currency, non-negative) adds a flat per-row fee that applies once per line regardless of qty — modeling the reeling / cut-tape charges that distributors like DigiKey and Mouser surcharge per line item. The fee folds into ERPNext's tax-and-totals calculation server-side via a monkey patch on `calculate_item_values`, and the same math is mirrored client-side via `pricing_rule_patches.js` so form-level totals stay in sync as the user types. The patch also folds Supplier Quotation Item margins (which ERPNext's whitelist excludes) so SQ pricing behaves like every other transaction doctype.

### 2. Problem / why now
ERPNext's Pricing Rule schema models per-unit margins (`margin_rate_or_amount`) but has no per-line flat charge. Distributor reeling fees are per-line, not per-unit: cutting a 100-unit reel costs $X regardless of how many units you cut. Modeling them as per-unit margins forces the operator to either skip them or guess a unit-equivalent that drifts as quantities change. The custom field plus the calculation patch makes it a first-class concept; the row-pass-through patch (next feature) lets a single Pricing Rule's `condition` reference per-row fields like `supplier_part_no` so reeling charges only apply to the right rows.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **Admin** creates a Pricing Rule with `custom_per_line_charge = 7.00` and `condition = "current_item.get('supplier_part_no')=='DKR-100'"` (the row-pass-through patch — see next feature — exposes `current_item` to the eval scope). Trigger: standard form save.
2. **User** adds a row matching the rule on a Supplier Quotation / Purchase Order / Sales Order / etc. Trigger: form interaction.
3. **System (server)** — at validate, the monkey-patched `erpnext.controllers.taxes_and_totals.calculate_item_values` collects the row's effective Pricing Rules (via standard ERPNext resolution), sums their `custom_per_line_charge`, and adds the total to `net_amount` once after the per-unit margin math has finalized `rate`. Triggered for every transaction doctype that goes through `calculate_taxes_and_totals`. Trigger: server-side validate.
4. **System (server, SQ-only)** — the same patch runs `calculate_margin` for every Supplier Quotation Item row (which ERPNext otherwise skips because SQ Item is excluded from the standard margin whitelist), then folds the result into `item.rate` and `item.amount` so SQ totals match other docs. Trigger: server-side validate.
5. **System (client)** — `pricing_rule_patches.js`, loaded globally via `app_include_js`, attaches a `setup` hook to every transaction doctype's form: it patches `_get_item_list` to copy the row's scalar fields onto the rule eval input (matching the server's row pass-through), warms a per-line-charge cache via `_set_values_for_item_list`, and adds the per-line-charge total in `calculate_item_values` to keep form-level totals matching the server. The cache resets on every form refresh so all rows are re-fetched. Trigger: client-side form events.

### 5. Schema

#### 5.1 `Pricing Rule` — Custom-Field

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `custom_per_line_charge` | Currency | — | | Inserted after `margin_rate_or_amount`. `non_negative = 1`, `print_hide_if_no_value = 1`. The total of every effective Pricing Rule's per-line charge is added once per row to `net_amount` after margin folding. |

#### 5.2 `Supplier Quotation Item` — Touched (no change)

No schema impact. The patch makes `rate_with_margin` populate and folds it into `rate` / `amount` for SQ Item rows specifically (ERPNext's whitelist otherwise excludes SQ Item from margin folding).

### 6. Overrides, hooks, and direct file edits

**Monkey patches** (applied at module-import time from `monkey_patches.py:install()`, called from a top-level import in `div_frappe_base/__init__.py`):

- `erpnext.controllers.taxes_and_totals.calculate_taxes_and_totals.calculate_item_values` — replaced with a wrapper that calls the original, then (a) for SQ rows, runs `calculate_margin` and folds `rate_with_margin` into `rate` and `amount`, and (b) for every row, sums `per_line_charge_for_item(item)` and adds the result to `net_amount`.
- `erpnext.accounts.doctype.pricing_rule.utils.filter_pricing_rule_based_on_condition` — replaced with a wrapper that captures the row dict (the function's local `args`) from the call frame and binds it as `current_item` in the `eval` scope, so Pricing Rule conditions can reference per-row fields. Falls back to ERPNext's no-row behavior when no row context exists (e.g. `apply_pricing_rule_on_transaction`).

**`app_include_js`:**
- `/assets/div_frappe_base/js/pricing_rule_patches.js` — patches `erpnext.TransactionController._get_item_list`, `_set_values_for_item_list`, and `calculate_item_values` on the first transaction-form `setup` event. Mirrors the server's row pass-through and per-line-charge fold so form totals match the server.

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/monkey_patches.py` — exports `install()`, `per_line_charge_for_item(item)`, `effective_per_unit_rate(rule, base_rate, qty)`, plus the two patch functions. Imported at app load.
- `apps/div_frappe_base/div_frappe_base/pricing.py` — Pricing Rule helper functions and the Quantity Lister UI's per-unit display rate computation.

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| (inherits from `Pricing Rule`) | ✓ | ✓ | ✓ | ✓ | | |

The Custom Field inherits Pricing Rule permissions. The patches run unconditionally for every authenticated request; no separate permission surface.

### 8. Out of scope

- **Per-tier per-line charges** (different flat fee per quantity tier on the same Pricing Rule). Not modeled — operators write multiple Pricing Rules with conditions if they need tiered behavior.
- **Alternative reeling-fee structures** (per-cut-segment, per-roll, etc.). Only flat per-line is supported.
- **Migration of existing pricing patterns** (rules that simulated per-line charges by inflating per-unit margins). The custom field is additive; operators rewrite rules at their own pace.
- **Removing the monkey patches via a hook** (e.g. when div_frappe_base is uninstalled). Patches are persistent in-process and assume div_frappe_base is always loaded. Uninstalling without a process restart leaves the patched behavior in place.

---

## Pricing Rule Row Pass-Through Eval

### 1. Summary
A monkey patch on `erpnext.accounts.doctype.pricing_rule.utils.filter_pricing_rule_based_on_condition` makes the row dict that's currently being priced available to the Pricing Rule's `condition` field as `current_item`, so conditions can reference per-row fields like `current_item.get('supplier_part_no')`. Without it, conditions can only reference parent-doc fields and the rule applies all-or-nothing across rows. The companion JS (`pricing_rule_patches.js`) mirrors the same pass-through client-side so form-level rule resolution matches the server.

### 2. Problem / why now
ERPNext's Pricing Rule conditions are evaluated against the parent doc's namespace; per-row fields are unreachable. That means rules like "apply this reeling fee only to Mouser part numbers starting with `MR-`" can't be expressed as a single Pricing Rule — operators end up with one rule per part number, or with a per-row check that lives outside the Pricing Rule framework. Exposing the row dict as `current_item` is the smallest change that lets `condition` do meaningful per-row gating.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **Admin** writes a Pricing Rule with `condition = "current_item.get('supplier_part_no','').startswith('MR-')"`. Trigger: standard form save.
2. **System (server)** — when ERPNext runs Pricing Rule resolution per row, the patched `filter_pricing_rule_based_on_condition` walks one frame up the call stack to capture the original `args` dict (the row dict), binds it as `current_item` in the eval scope, and runs the user's condition. Falls back to ERPNext's no-row behavior when the call site doesn't have a row dict (e.g. `apply_pricing_rule_on_transaction`). Trigger: in-method during validate.
3. **System (client)** — `pricing_rule_patches.js`'s `_get_item_list` patch copies every scalar field from the row doc onto the corresponding entry in the args list passed to ERPNext's rule evaluator. The same key set is then visible to client-side `condition` evaluation, matching the server. Trigger: form events.

### 5. Schema

No schema impact. Both patches operate at the function level; no fields are added or removed.

### 6. Overrides, hooks, and direct file edits

(See § "Pricing Rule Per-Line Charge" → § 6 — the same `monkey_patches.install()` call wires both patches and the `pricing_rule_patches.js` include carries both client-side mirrors. Listed separately here because the two patches address distinct user-visible features and downstream consumers may want to reference one without the other.)

### 7. Permissions

(Inherits from `Pricing Rule`. No separate surface.)

### 8. Out of scope

- **Patching `pricing_rule.apply` to expose `current_item` everywhere.** The current patch covers the condition-eval path that filtering uses; other paths (margin computation, etc.) don't currently see `current_item`.
- **Sandboxing the eval.** ERPNext's existing `frappe.safe_eval` constraints are inherited; the patch doesn't tighten them.

---

## Import Profile

### 1. Summary
A reusable column-mapping template (`Import Profile`) for importing rows into existing parent documents' child tables — e.g. importing a 200-line BOM Item list into a draft BOM, or a PCBA Item list into a draft PCB Assembly. Operators upload an .xlsx / .xls / .csv / .txt file, map source columns to target child-table fields, save the mapping under a profile name, andreuse it for future imports. The dialog supports value maps (per-cell substitutions), required-column gating, header-row auto-detection, and (for fixed-width text) column-boundary detection.

### 2. Problem / why now
Frappe's stock Data Import flow is great for top-level doctypes but doesn't support importing rows into a specific parent doc's child table — and recreating a column mapping every time is tedious. NPXL benches routinely hand-import multi-hundred-row child tables (BOMs from EDA exports, PCBA items from MPN spreadsheets, supplier price lists into Item Supplier rows). Saving the mapping per parent-doctype + child-table-field pair as an `Import Profile` lets operators re-run the same import shape across many parents without re-mapping each time.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **User** opens an existing parent doc (e.g. a draft BOM) and launches the Import dialog from a form button or menu (caller-specific — provided by the parent doctype's form script). Trigger: form button.
2. **User** uploads a file. Trigger: file picker.
3. **System** — `div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.parse_file(filename)` (whitelisted) reads `.xlsx` (openpyxl), `.xls` (xlrd), `.csv` (stdlib), or `.txt` (fixed-width column detection by whitespace heuristics), auto-detects the header row, and returns the parsed grid. Trigger: whitelisted method call.
4. **User** picks `target_doctype` (the parent's doctype) and `target_child_table_field` (which Table field on the parent to populate). The dialog populates `target_child_table_field` options dynamically by calling `list_table_fields(target_doctype)`. Trigger: dialog interaction.
5. **System** — `find_profile(target_doctype, target_child_table_field)` looks up an existing matching Import Profile. If found, its column mappings and value maps are pre-loaded into the dialog. Trigger: in-method.
6. **User** maps source columns to target child-table fields, marks required columns, and (optionally) writes value maps for per-cell substitution. Saves the profile via `save_profile(profile_data)`. Trigger: dialog save.
7. **User** clicks **Import**. Trigger: dialog submit.
8. **System** — `import_data(parent_doctype, parent_name, target_child_table_field, profile_name, file_name)` applies the profile to every row of the parsed file: resolves source columns through the profile's value maps, coerces types via `get_target_field_meta(target_doctype, child_field)`, appends one child row per source row to the parent doc, and saves. Trigger: whitelisted method call.

### 5. Schema

#### 5.1 `Import Profile` — New

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `profile_name` | Data | — | ✓ | Unique. Autoname source. |
| `target_doctype` | Link | DocType | ✓ | `set_only_once = 1`. The parent doctype the profile applies to. |
| `target_child_table_field` | Select | (dynamic — populated from `list_table_fields(target_doctype)`) | ✓ | The fieldname of the Table field on the parent that the import targets. |
| `header_row_index` | Int | — | | Read-only. Set during parse_file. |
| `trailing_rows_to_skip` | Int | — | | Read-only. Set during parse_file. |
| `sheet_name` | Data | — | | Read-only. Set during parse_file (multi-sheet xlsx files). |
| `scope_filters` | Table | Import Profile Scope | | Read-only. Per-import scope filters applied to the imported rows. |
| `column_mappings` | Table | Import Profile Column | | Read-only. The column-to-field mapping. |

#### 5.2 `Import Profile Scope` — New (child table)

Parent: `Import Profile` via `scope_filters`.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `field_name` | Data | — | ✓ | A child-table fieldname on the target. |
| `field_value` | Read Only | — | ✓ | The value all rows must carry for this field (set per import). |

#### 5.3 `Import Profile Column` — New (child table)

Parent: `Import Profile` via `column_mappings`.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `target_field` | Data | — | ✓ | The fieldname on the target child table. |
| `source_column` | Data | — | ✓ | The source column header (or column index for fixed-width text). |
| `is_required` | Check | — | | When set, rows missing this column raise a validation error during import. |
| `value_map` | Data | — | | JSON-encoded `{source: target}` mapping for per-cell substitution. |

### 6. Overrides, hooks, and direct file edits

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/div_frappe_base/doctype/import_profile/import_profile.py` — whitelisted endpoints `parse_file`, `find_profile`, `get_profile`, `get_target_field_meta`, `list_table_fields`, `save_profile`, `import_data`. File parsers: openpyxl for `.xlsx`, xlrd for `.xls`, stdlib `csv` for `.csv`, fixed-width column heuristic for `.txt`.
- `apps/div_frappe_base/div_frappe_base/div_frappe_base/doctype/import_profile/import_profile.js` — onload + `target_doctype` change handlers populate `target_child_table_field` options via `list_table_fields`. Loaded only on the Import Profile form (no global hook).

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| System Manager | ✓ | ✓ | ✓ | ✓ | | |

(Whitelisted methods enforce the calling user's standard write permission on the *target parent doc*, not on Import Profile itself — System Manager is required only to create / edit profile records.)

### 8. Out of scope

- **Auto-discovery of value maps from the source data.** Operators write value maps explicitly; the dialog doesn't suggest mappings.
- **Importing into top-level doctypes.** That's Frappe's stock Data Import — Import Profile is specifically for parent-doc child-table imports.
- **Multi-sheet xlsx import.** One sheet per profile (the parser captures the chosen sheet's name; switching requires re-parsing).
- **Multi-parent batch imports** (one file populating child tables across many parent docs in one click). Profiles target a single parent at a time.

---

## Item Variant Attribute Map

### 1. Summary
A small utility — `div_frappe_base.utils.get_item_variant(item_name=None, item=None)` — that fetches an Item Variant and unpacks its `attributes` table into a typed `_attributes` dict on the returned doc, coercing numeric values to `int` / `float` when the template attribute's `numeric_values` flag is set. Saves callers from writing the unpack/coerce logic at every callsite.

### 2. Problem / why now
ERPNext stores variant attributes as `(attribute, attribute_value)` rows in a child table. Almost every caller wants them as a typed dict, and almost every caller writes the same unpack-and-coerce loop. Centralizing it in one helper keeps the conversion semantics (especially numeric-vs-string) consistent across the bench.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **System (any caller)** — calls `div_frappe_base.utils.get_item_variant(item_name="ITEM-VAR-0001")` or passes a pre-loaded `Item` doc via `item=...`. Trigger: in-method.
2. **System** — fetches the Item if a name was given; walks `item.attributes`, looks up each attribute's template flag, coerces the value to int / float when `numeric_values = 1`, and stores the result on `item._attributes`. Returns the doc. Trigger: in-method.

### 5. Schema

No schema impact. The function reads existing `Item` and `Item Attribute` rows.

### 6. Overrides, hooks, and direct file edits

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/utils.py` — exports `get_item_variant`. Co-located with the other small utilities (`get_total_stock_quantity`, `get_child_list`, `group_insert`).

### 7. Permissions

(Inherits the caller's `Item` read permission. No separate surface.)

### 8. Out of scope

- **Persisting `_attributes` back to the doc.** The dict is read-only convenience; writes go through the standard child-table API.
- **Validation of attribute coercion.** When a template flags an attribute as numeric but a row's value can't be parsed, a `ValueError` propagates — callers handle it.

---

## Stock Quantity & Child-Table Helpers

### 1. Summary
A handful of small utilities under `div_frappe_base.utils`:
- `get_total_stock_quantity(item_code, inventory_dimensions=None)` — sums `actual_qty` from `Stock Ledger Entry` across all warehouses, optionally scoped to specific Inventory Dimension values.
- `get_child_list(doctype, parent, fields=None, pluck=None)` (whitelisted) — wrapper around `frappe.get_all` for child-table queries.
- `group_insert(docs, commit_each=False)` — batch-inserts a list of docs, with optional per-doc commits.

### 2. Problem / why now
Each of these one-liners gets re-written across the bench's apps. Putting them in a single utilities module makes call sites consistent and lets future bench-wide quality-of-life changes (e.g. switching `get_child_list` to honor a permission filter) land in one place.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **System (any caller)** — imports a helper from `div_frappe_base.utils` and calls it in line. Trigger: in-method.

### 5. Schema

No schema impact.

### 6. Overrides, hooks, and direct file edits

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/utils.py` — the three helpers.

### 7. Permissions

`get_child_list` runs `frappe.get_all` and inherits the caller's read permission on the child doctype. The other two are non-whitelisted helpers; they obey the caller's transaction context.

### 8. Out of scope

- **Permission filtering on `get_total_stock_quantity`.** It reads SLE directly without a permission gate; restricted callers compose their own filter.

---

## Item Variants Section depends_on Override

### 1. Summary
A property setter on `Item.variants_section` clears the `depends_on` field — surfacing the variants section unconditionally regardless of the parent Item's `has_variants` state. Single-purpose, no associated runtime code.

### 2. Problem / why now
ERPNext's stock UI hides the variants section behind a `has_variants` check, which makes the section invisible for items that *will* be variant templates but haven't been flipped yet. Surfacing it always is more useful for our workflow — operators set up variants section content before flipping `has_variants`.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

Applied at install / migrate time via the Custom DocType machinery. No runtime workflow.

### 5. Schema

#### 5.1 `Item` — Touched (no change)

No new fields. A property setter clears `Item.variants_section.depends_on` (`sync_on_migrate = 1`).

### 6. Overrides, hooks, and direct file edits

**Property setter:** `apps/div_frappe_base/div_frappe_base/div_frappe_base/custom/item.json` — sets `Item.variants_section.depends_on = ""`.

### 7. Permissions

(Inherits from `Item`.)

### 8. Out of scope

- **Reverting the override.** The property setter persists until removed; uninstalling div_frappe_base does not auto-revert.

---

## Asset → Linked Location Re-parenting

### 1. Summary
Adds `Asset.linked_location` (Link → Location, unique). When the field is set, the Asset *is* that Location — its tree position is owned by the Asset's `location`, not by direct edits on the Location form. An `Asset Movement → on_submit / on_cancel` hook re-parents each row's linked Location to track the moved Asset's new `location`. A `Location → validate` hook refuses direct `parent_location` edits while a linked Asset exists, telling the operator to use Asset Movement instead. Typical use: a machine Asset whose linked Location holds child Locations (slots, bays, racks) — moving the machine via Asset Movement carries every descendant with it.

### 2. Problem / why now
Domain apps that model machine internals as `Location` records under a per-machine container Location run into a shape problem: "moving a machine" requires either editing the container Location's parent directly (which silently desynchronises the machine Asset's recorded `location`) or generating per-child Asset Movement rows for every asset inside (which forces scan events for assets that haven't physically moved). Tying the Location's tree position to the Asset's `location` lets a single Asset Movement on the machine express the move and locks down the wrong-way path. The pattern is generic — any Asset that owns a Location can use it, so it lives here rather than in any single domain app.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **User (admin)** — sets `Asset.linked_location` on an Asset to the Location whose identity belongs to it. Trigger: Asset form save.
2. **System (any caller)** — submits an `Asset Movement` whose `assets` rows include the linked Asset, with `target_location` set to the new parent Location. Trigger: form submit.
3. **System** — inherited `AssetMovement.on_submit` updates `Asset.location` to the row's `target_location`. Trigger: on_submit.
4. **System (hook)** — `div_frappe_base.asset_location_link.reparent_linked_locations` walks `doc.assets`; for each row whose Asset has `linked_location` set, opens that Location, sets its `parent_location` to the Asset's just-updated `location`, and saves with `flags.from_asset_movement = True`. Trigger: on_submit.
5. **System (cancel)** — `reparent_linked_locations` re-runs on `on_cancel`; the inherited cancel logic has already recomputed `Asset.location` from the previous-latest movement, so the linked Location follows back. Trigger: on_cancel.
6. **User (admin, blocked path)** — opens the linked Location form directly and tries to change `parent_location`. Trigger: form save.
7. **System** — `div_frappe_base.asset_location_link.validate_linked_location_parent_change` finds at least one Asset whose `linked_location` is this Location and `frappe.throw`s with the message "Run an Asset Movement on the linked Asset to move this Location — a direct parent change is not allowed." Trigger: validate.

### 5. Schema

#### 5.1 `Asset` — Custom-Field

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `linked_location` | Link | Location |  | Inserted after `location`. Unique — one Location can be linked to at most one Asset. When set, the Location's tree position follows this Asset's `location`. |

#### 5.2 `Location` — Touched (no change)
Existing doctype; the validate hook reads incoming `parent_location` changes and refuses them when a linked Asset exists. No JSON modification.

#### 5.3 `Asset Movement` — Touched (no change)
Existing doctype; the new `on_submit` / `on_cancel` handler is purpose-agnostic and composes with any other app's Asset Movement hooks.

### 6. Overrides, hooks, and direct file edits

**`doc_events`:**
- `Asset Movement` → `on_submit` → `div_frappe_base.asset_location_link.reparent_linked_locations`.
- `Asset Movement` → `on_cancel` → `div_frappe_base.asset_location_link.reparent_linked_locations`.
- `Location` → `validate` → `div_frappe_base.asset_location_link.validate_linked_location_parent_change`.

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/asset_location_link.py` — Add: `reparent_linked_locations(doc, method)` and `validate_linked_location_parent_change(doc, method)`.

### 7. Permissions
No new permissions. Both hooks run under the saving user's session and inherit the existing `Asset Movement` and `Location` write permissions. The validate-time refusal applies to all roles equally — even System Manager must use Asset Movement to re-parent a linked Location.

### 8. Out of scope

- **Bulk Asset re-link.** No tooling to re-point multiple Assets' `linked_location` fields in one action; that's a per-Asset edit.
- **Cascading move of children's children.** Re-parenting the container Location naturally carries every descendant via Frappe's nested-set tree — no hook is needed for slots-of-slots cases. The hook only re-parents the directly-linked Location.
- **`linked_location` validation against `Asset.location`.** The system does not enforce that `Asset.location` is *not* the same as `Asset.linked_location`'s ancestor chain; pathological cycles are not auto-detected. Frappe's nested-set save will throw on a true cycle, so this is left to the user-facing form.
- **Backfill of pre-existing container Locations.** Setting `Asset.linked_location` on Assets that already own container Locations is a one-time operator action; no migration script is included.

---

## AI Settings & Provider-Agnostic Client

### 1. Summary
A bench-wide `AI Settings` Single doctype plus a `div_frappe_base.ai.client` helper module that wraps [LiteLLM](https://docs.litellm.ai/) to give every higher-tier app one provider-agnostic surface for LLM calls. AI Settings holds a child table of named **AI Profile** rows (one per use-case — vision, categorizer, datasheet) where each row carries its own provider, model, API key, optional base URL, temperature, and timeout. The helper exposes `complete(profile_name, prompt, attachments=None, response_format=None)` that resolves the profile, caches the decrypted key on `frappe.cache()` (mirroring the legacy `gemini_client()` pattern), and dispatches through `litellm.completion(...)` so callers can swap a profile's provider from Gemini to Claude / xAI / OpenAI / Bedrock / Ollama / etc. without touching call sites.

### 2. Problem / why now
Higher-tier apps in the bench had several Gemini call sites that each instantiated `google.genai.Client` directly, hardcoded `gemini-2.5-flash`, and read a single shared API-key field on a domain Singles doctype. Three problems compound: (a) the model is uneditable without a code change, (b) switching providers means rewriting every call site, and (c) the API-key field lived one app up the stack from where it belongs, since AI parsing of generic things (datasheets, images) isn't a domain concern. Centralizing the configuration here (every higher-tier app depends on `div_frappe_base`) and routing through LiteLLM (one library, ~100 providers) lets operators reconfigure model + provider in the UI and lets future apps reuse the same plumbing without re-deriving it.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **Admin** opens **AI Settings** (Single, accessible to System Manager) and reviews the seeded `vision` / `categorizer` / `datasheet` profile rows. For each one, picks a provider, fills the API key (Password), enters the model string the provider expects (e.g. `gemini-2.5-flash`, `claude-opus-4-7`, `gpt-4o`), and optionally tunes temperature, timeout, or base URL. Trigger: form save.
2. **System (any caller)** — imports `from div_frappe_base.ai.client import complete` and calls `complete("categorizer", prompt, response_format="json")`. Trigger: in-method.
3. **System** — `complete()` reads the named profile row, resolves the API key via `get_decrypted_password("AI Settings", "AI Settings", f"ai_profile:{name}:api_key")` cached on `frappe.cache()` for one hour (per-profile cache key, so rotating one profile doesn't blast the others). Builds the `messages` list for LiteLLM in OpenAI's format. Trigger: in-method.
4. **System (multimodal caller)** — `complete("vision", prompt, attachments=[{"mime_type": "image/jpeg", "data": image_bytes}], response_format="json")` base64-encodes each attachment, attaches it as an OpenAI-shaped content part (`{"type": "image_url", "image_url": {"url": data_uri}}` for images; `{"type": "file", "file": {"file_data": data_uri}}` for PDFs), and ships the whole list through `litellm.completion(model=f"{provider_prefix}/{model}", messages=..., temperature=..., timeout=..., api_key=..., api_base=base_url or None, response_format=...)`. Trigger: in-method.
5. **System** — extracts `response.choices[0].message.content`, strips any leading/trailing markdown fences (mirroring the existing `categorizer.py` / `ai_vision.py` defensive parsing), and returns the string. Returns `None` (and logs once via `frappe.log_error`, gated by a 1-hour cache key like the existing `gemini_api_key_missing_logged` pattern) when the profile's API key is empty and the caller passed `raise_exception=False`. Trigger: in-method.
6. **System (after_install)** — `div_frappe_base.install.after_install` ensures the three seeded profile rows exist on AI Settings, with `provider="Gemini"`, `model="gemini-2.5-flash"`, blank API keys, default temperature `0.1`, and default timeout (60s for vision/categorizer, 120s for datasheet, mirroring the legacy `http_options.timeout` values). Idempotent — re-running `after_install` doesn't duplicate or overwrite existing operator-configured rows. Trigger: install / migrate.
7. **System (consumers)** — Existing AI call sites in higher-tier apps drop their `genai.Client` construction and call `complete()` with the corresponding profile name. Legacy provider-specific helpers and `from google import genai` imports are removed. Trigger: refactor (see § 6).

### 5. Schema

#### 5.1 `AI Settings` — New (Single)

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `profiles_section` | Section Break | — | | Label: "AI Profiles". |
| `profiles` | Table | AI Profile | | The configured profile rows. Seeded with `vision`, `categorizer`, `datasheet` at install. |

`issingle = 1`. No naming series — it's a Single. The doctype carries no other fields; everything that varies per use-case lives on the child table so adding a new profile is a Frappe row insert, not a JSON edit.

#### 5.2 `AI Profile` — New (child table)

Parent: `AI Settings` via the `profiles` Table field.

| Field name | Fieldtype | Options / Link target | Required | Notes |
|---|---|---|---|---|
| `profile_name` | Data | — | ✓ | Unique within the parent. `in_list_view = 1`. The string callers pass to `complete(profile_name, ...)`. Seeded set: `vision`, `categorizer`, `datasheet`. |
| `provider` | Select | `Gemini\nAnthropic\nOpenAI\nxAI\nVertex AI\nAzure OpenAI\nBedrock\nMistral\nOllama\nCustom` | ✓ | `in_list_view = 1`. The helper maps the selected provider to LiteLLM's prefix (`Gemini` → `gemini/`, `Anthropic` → `anthropic/`, `xAI` → `xai/`, `Custom` → empty prefix; `model` is passed through as-is for `Custom` so the operator can specify any LiteLLM-supported string). |
| `model` | Data | — | ✓ | `in_list_view = 1`. Provider-native model name (e.g. `gemini-2.5-flash`, `claude-opus-4-7`, `gpt-4o`, `grok-2-vision`). The helper composes `f"{prefix}/{model}"` when `prefix` is set, otherwise passes `model` through verbatim. |
| `api_key` | Password | — | | Decrypted via `get_decrypted_password` on first use and cached on `frappe.cache()` for one hour, keyed `f"ai_profile:{profile_name}:api_key"`. Empty when seeded; operator must fill before the profile becomes usable. |
| `base_url` | Data | — | | Optional. Routed to LiteLLM as `api_base`. Used for OpenAI-compatible endpoints (Ollama, custom proxies). |
| `temperature` | Float | — | | Default `0.1`. Routed to LiteLLM as `temperature`. |
| `timeout` | Int | — | | Seconds. Default `60` (overridden to `120` for the seeded `datasheet` profile, matching the legacy 120s `http_options.timeout`). Routed to LiteLLM as `timeout`. |

### 6. Overrides, hooks, and direct file edits

**`after_install`:**
- `div_frappe_base.install.after_install` is extended to call a new `seed_ai_profiles()` helper that ensures the three seeded `AI Profile` rows exist on `AI Settings` with the defaults listed in § 4 step 6. Idempotent — checks for an existing row by `profile_name` before appending.

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/ai/__init__.py` — empty package marker.
- `apps/div_frappe_base/div_frappe_base/ai/client.py` — exports `complete(profile_name, prompt, *, attachments=None, response_format=None, raise_exception=True)`. Resolves the profile from `AI Settings.profiles`, caches the decrypted API key on `frappe.cache()` with a per-profile cache key, base64-encodes attachments into OpenAI-shaped content parts, and dispatches `litellm.completion(...)`. Strips markdown fences from the returned text. Logs missing keys once per hour via `frappe.log_error` (cache-gated, mirroring the existing `gemini_api_key_missing_logged` pattern). Raises a `ProfileNotConfigured` exception (also exported from this module) when `raise_exception=True`, else returns `None`.
- `apps/div_frappe_base/div_frappe_base/div_frappe_base/doctype/ai_settings/ai_settings.py` — `class AISettings(Document): pass`. Single, no controller logic.
- `apps/div_frappe_base/div_frappe_base/div_frappe_base/doctype/ai_profile/ai_profile.py` — `class AIProfile(Document): pass`. Child, no controller logic.
- `apps/div_frappe_base/div_frappe_base/install.py` — `after_install` is extended to call `seed_ai_profiles()`. New function `seed_ai_profiles()` reads the AI Settings Single, iterates the three seed names, appends rows that aren't already present, and `save()`s. Idempotent.
- `apps/div_frappe_base/pyproject.toml` — add `"litellm"` to the `[project] dependencies` array. Pulls in the LiteLLM package for the helper.

(DocType JSONs at `apps/div_frappe_base/div_frappe_base/div_frappe_base/doctype/ai_settings/ai_settings.json` and `.../ai_profile/ai_profile.json` are created in the Frappe UI per the bench convention; this design specifies the schema in § 5 for that UI work.)

### 7. Permissions

| Role | Read | Write | Create | Delete | Submit | Cancel |
|---|---|---|---|---|---|---|
| System Manager | ✓ | ✓ | ✓ | ✓ | | |

`AI Profile` inherits from its parent `AI Settings`. The `complete()` helper runs under the calling user's session and reads the cached API key without a permission check — it's a service-layer helper, not a user-facing endpoint, and is not whitelisted.

### 8. Out of scope

- **Streaming responses.** `complete()` returns the full assistant text string. Token-by-token streaming is not exposed; callers that want it can call `litellm.completion(..., stream=True)` directly.
- **Function calling / tool use.** No surface for OpenAI-style tool definitions. Callers that need it pass through to LiteLLM directly or extend the helper.
- **Token usage accounting.** No rollup of input/output tokens across calls; LiteLLM's `response.usage` is discarded by the helper.
- **Per-call prompt caching** (Anthropic's prompt caching API, Gemini's context caching). Not surfaced in v1.
- **Rate-limit retries / exponential backoff.** LiteLLM has built-in `num_retries` and `retry_strategy` knobs; v1 doesn't expose them. Callers that need retries pass `litellm.completion` directly or extend the helper.
- **A "Test Connection" button** on the AI Settings form. Operators verify by running a real workflow (e.g. re-parsing one Source Category in the categorizer dry-run path).
- **Migration of the legacy `EMS Settings.gemini_api_key` value into AI Settings.** Per scope direction, the EMS field is removed outright with no migration patch — operators re-enter the key on AI Settings.
- **Operator-defined extra profiles beyond the seeded three.** The seeded set is fixed in v1; future call sites that need a new profile add a row at install time via `seed_ai_profiles()` (or the operator hand-adds in the UI). No restriction in the schema, just no tooling around it.

---

## Geolocation Field User-Location Default & Address Search

### 1. Summary
Site-wide client-side patches to Frappe's `ControlGeolocation` widget: (a) on a New form with an empty Geolocation field, the map opens centered on the operator's approximate location instead of the bench-wide India default, and (b) every map gets a Nominatim-backed address search box at top-right. Browser geolocation is preferred but only used when the user has already granted permission — the patch never raises a permission prompt. An IP-geolocation API is the fallback. The lookup is pre-warmed at script load so the first map render is already user-centered, eliminating the India-flash flicker. Loaded via `app_include_js` and applies to every Geolocation field on every doctype.

### 2. Problem / why now
The stock `Geolocation Settings.center` is a single bench-wide pair of coordinates that defaults to India. Every operator opening a new Asset, Location, or any custom doctype with a map field sees India centered first, then has to pan to their actual location — repeated dozens of times per day across the bench. Bumping `Geolocation Settings.center` to the bench's home city helps a single-site bench but doesn't help operators whose actual work areas differ, and the flicker remains. A per-instance override that uses the visitor's actual location (browser fix when permission is already granted, IP geolocation otherwise) costs nothing per-operator and applies everywhere. The companion address-search control replaces the pan-and-zoom dance for any operator who knows the address but not the latitude/longitude.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **System (script load)** — `geolocation_patches.js` (loaded via `app_include_js`) immediately calls `resolve_user_location()` to pre-warm the cache. The lookup runs in the background while the user navigates the desk so the first map render has the result already available. Trigger: page load.
2. **System** — `resolve_user_location()` first calls `navigator.permissions.query({name: 'geolocation'})`. If state is `'granted'`, calls `navigator.geolocation.getCurrentPosition(..., {timeout: 5000, maximumAge: 3600000})` and returns `[lat, lng]`. If state is `'prompt'` or `'denied'`, skips the browser path silently (never raises a permission prompt). Trigger: in-method.
3. **System (fallback)** — when the browser path is unavailable, fetches `https://ipapi.co/json/` (free, no API key, uses the visitor's IP automatically). Returns `[d.latitude, d.longitude]`. Result cached on a module-level `cached_latlng` for the rest of the page session. Trigger: in-method.
4. **User** — opens a New form whose schema includes a Geolocation field. Trigger: form route.
5. **System (warm path)** — the patched `ControlGeolocation.bind_leaflet_map` checks `frm.is_new() && !this.value && cached_latlng`. On match, temporarily swaps `frappe.utils.map_defaults.center` and `.zoom` with the cached user location and a city-level zoom (13), calls the original `bind_leaflet_map`, then restores the global defaults. The map's first and only `setView` already targets the user's location — no India tiles are ever requested. Trigger: control init.
6. **System (cold path)** — when `cached_latlng` isn't ready yet (rare, only on the very first map within ~100ms of page load), the original `bind_leaflet_map` runs with India defaults and an async fallback re-runs `setView(latlng, 13)` once the lookup resolves. The `!this.value` re-check before applying prevents clobbering data that loaded between scheduling and resolution. Trigger: in-method.
7. **System** — `load_geocoder()` lazy-loads `leaflet-control-geocoder@2.4.0` from unpkg the first time any map is created (one CSS link + one script tag, cached after that). On load, attaches `L.Control.geocoder({defaultMarkGeocode: false, position: 'topright'})` to the map; on `markgeocode`, fits the result's bbox (or `setView(center, 13)` if the bbox is missing). Does not auto-place a marker or write to the field — the operator still draws their own shape. Trigger: control init.
8. **System (existing docs)** — when `frm.is_new()` is false or the field already has a value, no recentering happens; `bind_leaflet_data(value)` runs as in stock Frappe and `fit_and_recenter_map` zooms to the saved geometry. The geocoder is still added so operators can navigate to a new address even on a saved doc. Trigger: control init.

### 5. Schema

No schema impact. Both behaviors live entirely in client-side JS; no new fields, doctypes, or property setters. The existing `Geolocation Settings.center` value is read by stock Frappe but overridden per-instance via the temporary `frappe.utils.map_defaults` swap during `bind_leaflet_map`.

### 6. Overrides, hooks, and direct file edits

**`app_include_js`:**
- `/assets/div_frappe_base/js/geolocation_patches.js` — patches `frappe.ui.form.ControlGeolocation.prototype.bind_leaflet_map` site-wide, pre-warms the user-location lookup at script load, and adds a Nominatim-backed `L.Control.Geocoder` (loaded on demand from unpkg) at top-right of every map.

**File-direct edits** (within div_frappe_base):
- `apps/div_frappe_base/div_frappe_base/public/js/geolocation_patches.js` — the IIFE described above. Module-level state: `location_promise`, `cached_latlng`, `geocoder_promise`. Public side-effect only — no exports.

**External dependencies loaded at runtime:**
- `https://unpkg.com/leaflet-control-geocoder@2.4.0/dist/Control.Geocoder.{css,js}` — Nominatim geocoder library. Loaded lazily via injected `<link>` and `<script>` tags the first time any map is created on a page. Pinned to `@2.4.0` so unpkg can't silently serve a major bump.
- `https://ipapi.co/json/` — free IP-geolocation API. Called as a fetch fallback when browser geolocation is unavailable or not granted.

### 7. Permissions

No new permissions. The patches run on the client under the operator's session. The browser Geolocation API requires the user's prior consent (which we never re-prompt for); the IP fallback exposes the operator's public IP to ipapi.co the same way any third-party CDN call would. No server-side surface.

### 8. Out of scope

- **Per-doctype opt-out.** The patches apply to every Geolocation field site-wide. A doctype that wants to keep the bench-wide default needs to override `bind_leaflet_map` itself or pre-set the field value before the patch runs.
- **Server-side IP geolocation.** The fallback hits `ipapi.co` directly from the browser. Routing through the bench (e.g. a whitelisted method backed by a local MaxMind database) would keep the operator's IP off third-party services but isn't implemented.
- **Vendoring the geocoder library.** `Control.Geocoder.{css,js}` is loaded from unpkg at runtime. A future change can mirror it into `public/vendor/` and pin a known-good copy for offline benches; the current code requires internet egress on first map open.
- **Alternative geocoder providers.** Only Nominatim (the library's default) is wired up. Esri / Mapbox / Photon support is a configurable knob that hasn't been exposed.
- **Marker placement on geocode.** The geocoder pans the map view but never writes to the field or drops a marker. Operators still draw their own shape after navigating.
- **Replacing the bench-wide `Geolocation Settings.center`.** The override is per-control, applied at init time. The Geolocation Settings record is left at its default and stock callers that read it directly are unaffected.

---

## Vector Search Infrastructure

### 1. Summary
Shared helper module `div_frappe_base.search.vector` plus a dedicated single-process RQ queue (`embed`) that gives every higher-tier app one API surface for "embed text → store as MariaDB-native vector → cosine-rank". Wraps a CPU-local `sentence-transformers` model (`BAAI/bge-base-en-v1.5`, 768 dims, L2-normalised) so the bench is self-contained and there's no per-query API cost.

### 2. Problem / why now
Description-matching / package-similarity / semantic-search features all want the same primitives: a way to embed strings, a column type to store the vectors, and a single SQL function to rank. Without a shared helper each consumer either re-implements the model loading (and pays the ~600 MB resident cost in every process that touches it) or pushes the cost out to a paid API. Both are wrong defaults. Pulling the helper into `div_frappe_base` and pinning a single dedicated worker keeps the RAM cost paid once per bench and lets every consumer share the same DDL / write / read path.

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **System (consumer app)** — declares a vector column in a `post_model_sync` patch by calling `upgrade_column_to_vector(doctype, field, dim)`. The helper detects the current column type and ALTERs it to `VECTOR(<dim>) NULL`. Trigger: `bench migrate` of the consumer.
2. **System (consumer app)** — at ingestion / sync time, calls `embed_texts(texts) → np.ndarray` to vectorise a batch, then `write_embedding(doctype, name, field, vec)` for each row. `write_embedding` formats the vector as `[v1,v2,…]` text and runs `UPDATE … SET field = VEC_FromText(%s)`. Trigger: in-method.
3. **System (consumer app)** — at query time, calls `search_cosine(doctype, field, query, filters=…, limit=N) → list[dict]`. The helper emits `SELECT name, VEC_DISTANCE_COSINE(field, VEC_FromText(%s)) AS dist FROM tab… WHERE field IS NOT NULL AND … ORDER BY dist ASC LIMIT %s` and returns `[{name, dist, cosine}]` where `cosine = 1 - dist`. Trigger: in-method.
4. **System (boot)** — `load_model()` lazily imports `sentence_transformers`, sets `HF_HOME` to `<bench>/sites/.huggingface_cache/` so weights live with site data instead of the OS user's home, and caches the loaded model in a module-global. First call pays ~30–60 s for the download / load; subsequent calls in the same process return immediately. Trigger: first `embed_texts` call in a process.
5. **System (worker)** — RQ workers running on the `embed` queue (one bench-wide) hold the model resident across jobs. Other workers / gunicorn never load it.

### 5. Schema

No new doctypes. Per-consumer columns are added by each consumer's patch.

#### 5.1 Column shape on consumers

Every vector-search column on a consumer is `VECTOR(<dim>) NULL` (e.g. `VECTOR(768) NULL` for the bge-base model). Declared on the consumer doctype JSON as `fieldtype: "Long Text"` + `is_virtual: 1` so:
- Frappe's schema sync (which doesn't know about the MariaDB `VECTOR` type) skips the column entirely on every `bench migrate`. Without `is_virtual: 1`, schema sync attempts to ALTER the column back to `longtext`, which fails noisily on existing vector data.
- The Frappe ORM (`doc.insert()` / `doc.save()`) doesn't write the column on insert. Writes happen exclusively via `write_embedding`'s raw SQL UPDATE.

**HNSW vector index** is intentionally **not** created. MariaDB ≥ 11.7 requires `NOT NULL` on every column inside a `VECTOR INDEX`, but the ORM-insert path writes `NULL` for the field on row creation (the embedding is computed asynchronously after). Adding the index would require either backfilling every consumer's insert path with a zero-vector default or moving inserts to raw SQL — both larger refactors than this module wants to carry. Without the index, `search_cosine` falls back to a full-scan `VEC_DISTANCE_COSINE`, which is fast enough at current corpus sizes (≤ ~10 k rows) but should be revisited if a consumer's corpus grows materially or the matcher becomes hot.

### 6. Overrides, hooks, and direct file edits

**`scheduler_events`:** none.

**File-direct edits:**

- `apps/div_frappe_base/div_frappe_base/search/__init__.py` — new package.
- `apps/div_frappe_base/div_frappe_base/search/vector.py` — new module. Exports:
  - `embed_texts(texts: list[str]) -> np.ndarray` — runs the bge model on a batch and returns `(N, dim)` float32. Lazy-loads the model once per process; weights download into `HF_HOME` set to `<bench>/sites/.huggingface_cache/`. Output is L2-normalised so `VEC_DISTANCE_COSINE` is equivalent to `1 - dot(a, b)`.
  - `embed_text(text: str) -> np.ndarray` — single-string wrapper.
  - `format_vector_text(vec) -> str` — renders `[v1,v2,…]` for `VEC_FromText(...)`. MariaDB 11.8 rejects raw float32 bytes bound through MySQLdb parameter substitution to a `VECTOR(N)` column with "Incorrect vector value"; the text form is the dialect-safe path.
  - `pack_vector(vec) -> bytes` — packs to little-endian float32 bytes. Retained for callers that want to bypass `write_embedding` (e.g. hex-literal inserts); not used by the helper's own write path.
  - `write_embedding(doctype, name, field, vec) -> None` — runs `UPDATE …tab{doctype}… SET field = VEC_FromText(%s) WHERE name = %s`. Skips the ORM entirely.
  - `search_cosine(doctype, field, query, filters=None, limit=10) -> list[dict]` — full-scan cosine ranker; emits `SELECT name, VEC_DISTANCE_COSINE(field, VEC_FromText(%s)) AS dist FROM tab… WHERE field IS NOT NULL AND … ORDER BY dist ASC LIMIT %s`. Returns `[{name, dist, cosine}]`.
  - `upgrade_column_to_vector(doctype, field, dim, distance="cosine") -> bool` — idempotent ALTER to `VECTOR(<dim>) NULL`. No-op when the column is already native vector. Returns `False` on MariaDB < 11.7 so the consumer can fall through to its non-vector match path. The `vector_index_exists` helper SQL escapes `%VECTOR%` as `%%VECTOR%%` because `frappe.db.sql` runs the query through MySQLdb's printf-style parameter substitution when `args` is present; an unescaped `%V` raises `not enough arguments for format string` before the query reaches the server.
  - `mariadb_supports_vectors() -> bool` — cached `SELECT VERSION()` parse for `≥ 11.7.0`.
  - `column_is_vector_ready(doctype, field) -> bool` / `column_is_native_vector(doctype, field) -> bool` — DATA_TYPE-introspection helpers used by `upgrade_column_to_vector` to keep itself idempotent. `column_is_blob` is preserved as a back-compat alias of `column_is_vector_ready` for older patch-version callers.

**New Python dependencies in `div_frappe_base/pyproject.toml`:**
- `sentence-transformers` (Apache 2.0) — pulls in `transformers` + `torch` (CPU build) + `numpy` transitively. ~1.5 GB on disk in the bench virtualenv; one-time install. Model weights (`BAAI/bge-base-en-v1.5`, ~440 MB) download into `sites/.huggingface_cache/` on first use.

**Embed queue and worker** (new bench-wide infrastructure):

A single new RQ queue, `embed`, served by **one** dedicated worker process. The model is loaded once inside that worker on first use and stays resident; every gunicorn worker, scheduler, and other RQ worker stays lean.

- `apps/div_frappe_base/div_frappe_base/hooks.py` — registers the queue via `worker_queues = ["short", "default", "long", "embed"]`.
- `apps/div_frappe_base/div_frappe_base/install.py` — `after_install` merges `"workers": {"embed": {"queues": ["embed"], "num_workers": 1, "background_workers": 1}}` into `<bench>/sites/common_site_config.json`. Pinning to one worker is essential: a second process would re-load the ~600 MB model.
- `Procfile` — regenerated by `bench setup procfile` once `worker_queues` is set; the new `worker_embed: bench worker --queue embed` line shows up automatically.

**Bench prerequisite:** MariaDB ≥ 11.7 (the `VECTOR` type). On older releases, `upgrade_column_to_vector` returns `False` and consumers must fall through to a non-vector match path.

### 7. Permissions
No new permissions. The module is server-side only and inherits whatever permission the calling code carries on the consumer doctype.

### 8. Out of scope

- **HNSW vector index** on the column. Deferred — see § 5.1 for the `NOT NULL` constraint that blocks it. At current corpus sizes the full-scan cosine is fast enough; revisit when a consumer's corpus grows large enough to matter.
- **Provider-hosted embedding APIs.** The module is CPU-local-only by design — the existing `div_frappe_base.ai.client` already handles provider-API routing for chat / vision / structured-output, and embeddings would land there if / when a hosted option becomes preferable. Today there's no consumer that wants the cost of a per-row API call.
- **Per-row backfill on dim change.** Swapping the embedding model means a new patch per consumer that re-sizes the `VECTOR(dim)` column and re-embeds. The helpers all key off `dim`, so the mechanical work is contained, but the re-embed cost is the consumer's to budget.
- **Cross-consumer rerankers.** Each consumer ranks its own corpus; there's no shared rerank step. Layering a small ranker (e.g. bge-reranker-base) on top of the cosine results is feature-specific and lives in the consumer.

---

## Spreadsheet Importer Dialog

### 1. Summary
A reusable dialog (`frappe.spreadsheet_importer.show_import_dialog(...)`) that loads an Import Profile, lets the operator pick a sheet / header row / trailing-skip, map columns, and configure per-column value translations, then imports the file's rows into a parent doc's child table via `div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.import_data`. Sits on top of the Import Profile doctype documented in § Import Profile above; the dialog is the operator-facing surface and lives in `public/js/spreadsheet_importer.bundle.js`.

### 2. Problem / why now
Operators routinely re-import the same BOM / pick-and-place / supplier-price-list shape from many parent docs (PCB Assemblies, Purchase Orders, etc.). Hand-mapping columns and value translations on every import is repetitive and error-prone. The dialog plus Import Profile persistence collapses that to "pick the file, confirm the saved mapping, click Import".

### 3. Target app
App: `div_frappe_base`
Module: `DIV Frappe Base`

### 4. Functional workflow

1. **User** clicks an Import button on a parent doctype's form (PCB Assembly's "Import BOM", etc.). The button handler calls `frappe.spreadsheet_importer.show_import_dialog({frm, target_child_table_field, file_url, scope_filters, …})`. Trigger: button click.
2. **System** runs three async calls in sequence: `get_target_field_meta` (drives the column-mapping dropdowns), `find_profile` (looks up an existing Import Profile by `target_doctype` + `target_child_table_field` + scope), and `get_profile` (loads its column / value mappings). Trigger: dialog open.
3. **System** sets `header_row_index` and `trailing_rows_to_skip` from the profile on the dialog's Int fields and calls `parse_and_render({ header_row_index })` to fetch the head + tail preview from the server and render the column-mapping grid. The preset values are passed *directly* into `parse_and_render` rather than read back through `dialog.get_value` because Frappe's `dialog.set_value` for an Int routes through `frappe.run_serially`, which defers the actual `$input.val(...)` write to a later microtask — reading via `get_value` on the very next line returns the stale field default and silently makes the preview (and any same-tick Import click) use the wrong header row. Trigger: profile-loaded.
4. **User** confirms / edits the mapping and clicks Import. Trigger: button click.
5. **System** calls `import_data` with the current mapping + header/trailing/sheet config; the server walks rows from `header_row_index + 1` to `len(rows) - trailing_rows_to_skip`, applies the per-column `value_map` translations, coerces numeric fields, and appends to the parent doc's child table. Trigger: server call.

### 5. Schema
No new doctypes. Builds on `Import Profile` / `Import Profile Scope` / `Import Profile Column` documented above.

### 6. Overrides, hooks, and direct file edits

- `apps/div_frappe_base/div_frappe_base/public/js/spreadsheet_importer.bundle.js` — the dialog. Bundled because the column-mapping grid uses a few hundred lines of vanilla JS that's not worth bringing in as a separate plain `.js` include. The `parse_and_render` function accepts an optional `overrides` dict (currently `{header_row_index}`) so callers in the bootstrap chain can pass freshly-set values without round-tripping through `dialog.get_value`.

### 7. Permissions
Inherits the parent doctype's write permission — the dialog only ever writes to the parent's child table via the parent's own ORM save.

### 8. Out of scope
- **Multi-sheet imports in one click.** The dialog handles one sheet at a time; if a workbook has two sheets that both need importing, the operator runs the dialog twice.
- **Streaming row-by-row import for very large files.** The server reads the whole sheet into memory and walks it; not optimised for files larger than a few hundred thousand rows.
