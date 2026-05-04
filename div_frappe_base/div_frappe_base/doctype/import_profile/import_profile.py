# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

import csv
import json
import os

from openpyxl import load_workbook

import frappe
from frappe import _
from frappe.model.document import Document


class ImportProfile(Document):
	def validate(self):
		"""Confirm target_child_table_field is a Table on target_doctype, populate
		target_child_doctype, and confirm every column_mappings.target_field is a
		real fieldname on the target child doctype."""
		if not self.target_doctype or not self.target_child_table_field:
			return

		target_meta = frappe.get_meta(self.target_doctype)
		table_field = target_meta.get_field(self.target_child_table_field)
		if not table_field or table_field.fieldtype not in ("Table", "Table MultiSelect"):
			frappe.throw(
				_("'{0}' is not a Table field on {1}").format(
					self.target_child_table_field, self.target_doctype
				)
			)

		child_doctype = table_field.options
		child_meta = frappe.get_meta(child_doctype)
		valid_fieldnames = {df.fieldname for df in child_meta.fields}

		for row in self.column_mappings or []:
			if row.target_field not in valid_fieldnames:
				frappe.throw(
					_("Column mapping row {0}: '{1}' is not a field on {2}").format(
						row.idx, row.target_field, child_doctype
					)
				)


# ---------- file parsing ----------------------------------------------------


def read_excel_rows(file_path, sheet_name=None):
	"""Read all rows from an Excel file as tuples of cell values.

	Returns (rows, sheet_name_used, all_sheet_names). If sheet_name is None,
	auto-picks the sheet with the most non-empty rows (so multi-sheet workbooks
	exported from Numbers / Excel templates that put a metadata cover on the
	active sheet still land on the data sheet).

	Handles .xlsx via openpyxl and legacy .xls via xlrd.
	"""
	ext = os.path.splitext(file_path)[1].lower()
	if ext == ".xls":
		import xlrd

		book = xlrd.open_workbook(file_path)
		all_sheets = book.sheet_names()
		picked = sheet_name if sheet_name in all_sheets else pick_xls_sheet(book, all_sheets)
		sheet = book.sheet_by_name(picked)
		rows = []
		for row_idx in range(sheet.nrows):
			row = []
			for col_idx in range(sheet.ncols):
				cell = sheet.cell(row_idx, col_idx)
				value = cell.value
				if cell.ctype == xlrd.XL_CELL_EMPTY:
					value = None
				elif cell.ctype == xlrd.XL_CELL_NUMBER and value == int(value):
					value = int(value)
				row.append(value)
			rows.append(tuple(row))
		return rows, picked, all_sheets

	wb = load_workbook(filename=file_path, read_only=True, data_only=True)
	all_sheets = wb.sheetnames
	picked = sheet_name if sheet_name in all_sheets else pick_xlsx_sheet(wb, all_sheets)
	ws = wb[picked]
	rows = list(ws.iter_rows(values_only=True))
	wb.close()
	return rows, picked, all_sheets


def pick_xlsx_sheet(wb, all_sheets):
	"""Return the sheet name with the most non-empty rows."""
	best = all_sheets[0]
	best_count = -1
	for name in all_sheets:
		ws = wb[name]
		count = sum(
			1 for row in ws.iter_rows(values_only=True) if any(c is not None for c in row)
		)
		if count > best_count:
			best_count = count
			best = name
	return best


def pick_xls_sheet(book, all_sheets):
	import xlrd

	best = all_sheets[0]
	best_count = -1
	for name in all_sheets:
		sheet = book.sheet_by_name(name)
		count = 0
		for row_idx in range(sheet.nrows):
			has_value = False
			for col_idx in range(sheet.ncols):
				cell = sheet.cell(row_idx, col_idx)
				if cell.ctype != xlrd.XL_CELL_EMPTY:
					has_value = True
					break
			if has_value:
				count += 1
		if count > best_count:
			best_count = count
			best = name
	return best


def read_csv_rows(file_path):
	with open(file_path, encoding="utf-8-sig", newline="") as f:
		return list(csv.reader(f))


def get_txt_col_offsets(header_line):
	"""Detect fixed-width column start positions from a header line."""
	offsets = []
	i = 0
	while i < len(header_line):
		if header_line[i] != " ":
			j = i
			while j < len(header_line) and header_line[j] != " ":
				j += 1
			offsets.append((header_line[i:j], i))
			i = j
		else:
			i += 1
	return offsets


def slice_txt_row(line, col_offsets):
	values = []
	for idx, (_col_name, start) in enumerate(col_offsets):
		end = col_offsets[idx + 1][1] if idx + 1 < len(col_offsets) else len(line)
		values.append(line[start:end].strip())
	return values


def read_txt_file(file_path):
	with open(file_path, encoding="utf-8-sig") as f:
		return [line.rstrip("\r\n") for line in f.readlines()]


def read_rows(file_path_full, sheet_name=None):
	"""Dispatch to the right reader. Returns (rows, sheet_name_used, all_sheets, kind).

	`kind` is 'tabular' (xlsx/xls/csv — rows are tuples/lists) or 'fixed' (txt — rows
	are raw lines). Caller uses kind to decide how to extract column headers.
	"""
	ext = os.path.splitext(file_path_full)[1].lower()
	if ext in (".xlsx", ".xls"):
		rows, picked, all_sheets = read_excel_rows(file_path_full, sheet_name)
		return rows, picked, all_sheets, "tabular"
	if ext == ".csv":
		return read_csv_rows(file_path_full), None, [], "tabular"
	if ext == ".txt":
		return read_txt_file(file_path_full), None, [], "fixed"
	frappe.throw(_("Unsupported file format. Use .xlsx, .xls, .csv, or .txt"))


def file_path_full(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	return file_doc.get_full_path()


def extract_columns_and_data(rows, header_row_index, kind):
	"""Given raw rows from read_rows, return (columns, data_rows).

	For tabular files, data_rows are tuples in column order. For fixed-width txt
	files, data_rows are strings sliced into lists by column offset.
	"""
	if header_row_index >= len(rows):
		return [], []

	if kind == "fixed":
		header_line = rows[header_row_index]
		if not header_line.strip():
			return [], []
		header_tokens = get_txt_col_offsets(header_line)
		# If the header line is sparse (e.g. a `[PLACEMENTS]` section marker)
		# but the data rows have more tokens, use the data row's offsets as the
		# column structure so each field gets its own column. Header tokens
		# still supply names where their start offset falls within a column.
		col_offsets = header_tokens
		first_data = next(
			(line for line in rows[header_row_index + 1 :] if line and line.strip()),
			None,
		)
		if first_data is not None:
			data_tokens = get_txt_col_offsets(first_data)
			if len(data_tokens) > len(header_tokens):
				col_offsets = [(None, off) for _, off in data_tokens]
				header_starts = [off for _, off in header_tokens]
				header_names = [name for name, _ in header_tokens]
				bounds = [off for _, off in col_offsets] + [10**9]
				resolved = []
				for i, (_n, start) in enumerate(col_offsets):
					end = bounds[i + 1]
					name = None
					for hn, hs in zip(header_names, header_starts, strict=False):
						if start <= hs < end:
							name = hn
							break
					resolved.append((name or f"Column_{i}", start))
				col_offsets = resolved
		columns = [name for name, _ in col_offsets]
		data_rows = []
		for line in rows[header_row_index + 1 :]:
			if not line.strip():
				data_rows.append(None)
				continue
			data_rows.append(slice_txt_row(line, col_offsets))
		return columns, data_rows

	header_row = rows[header_row_index]
	# Empty cells next to a populated header (e.g. a `[PLACEMENTS]` section
	# marker followed by blanks) come through as None or "" depending on
	# format — treat both as missing and fall back to a positional name.
	columns = []
	for i, cell in enumerate(header_row):
		name = str(cell).strip() if cell is not None else ""
		columns.append(name or f"Column_{i}")
	data_rows = list(rows[header_row_index + 1 :])
	return columns, data_rows


# ---------- whitelisted endpoints --------------------------------------------


TAIL_PREVIEW_ROWS = 30
HEAD_PREVIEW_ROWS = 30


@frappe.whitelist()
def parse_file(file_path, header_row_index=1, sheet_name=None):
	"""Parse a file and return columns, sample data, and metadata for the dialog.

	`header_row_index` is 1-indexed (matching spreadsheet row numbering); 1 means
	the first row in the sheet. Internally we convert to a 0-based list offset
	for slicing.

	Returns:
	  columns: list[str]
	  sample_data: dict[col_name -> first non-empty value]
	  column_values: dict[col_name -> sorted distinct values] (used to populate
	    value-map UI dropdowns from real file data)
	  total_file_rows: total rows in the chosen sheet (used to clamp the header
	    row spinner — independent of header_row_index)
	  sheets: list of sheet names (xlsx/xls only; empty for csv/txt)
	  sheet_name: which sheet was actually read
	  kind: 'tabular' or 'fixed' — lets the dialog render fixed-width files in
	    a monospace single-column view so whitespace alignment stays visible
	  head_preview: first ~30 raw rows of the file (pre-header content included),
	    used so the user can see the header and any preamble at a glance. Each
	    entry is {"file_row": int (1-indexed), "values": [stringified cells]}.
	  tail_preview: last ~30 data rows (after the header) so the user can pick a
	    Trailing Rows to Skip count by sight. Each entry is
	    {"data_index": int (0-based offset from first data row),
	     "values": [stringified cell values]}
	"""
	header_row_index = max(1, int(header_row_index))
	full_path = file_path_full(file_path)
	rows, picked_sheet, all_sheets, kind = read_rows(full_path, sheet_name)

	head_preview = build_head_preview(rows, HEAD_PREVIEW_ROWS, kind)

	columns, data_rows = extract_columns_and_data(rows, header_row_index - 1, kind)
	if not columns:
		return {
			"columns": [],
			"sample_data": {},
			"column_values": {},
			"total_file_rows": len(rows),
			"sheets": all_sheets,
			"sheet_name": picked_sheet,
			"kind": kind,
			"head_preview": head_preview,
			"tail_preview": [],
		}

	sample_data = {}
	column_values = {col: set() for col in columns}
	for row in data_rows:
		if row is None:
			continue
		for i, col in enumerate(columns):
			if i >= len(row):
				continue
			value = row[i]
			if value is None:
				continue
			s = str(value).strip()
			if not s:
				continue
			column_values[col].add(s)
			if col not in sample_data:
				sample_data[col] = s

	tail_preview = build_tail_preview(data_rows, len(columns), TAIL_PREVIEW_ROWS)

	return {
		"columns": columns,
		"sample_data": sample_data,
		"column_values": {col: sorted(vals) for col, vals in column_values.items()},
		"total_file_rows": len(rows),
		"sheets": all_sheets,
		"sheet_name": picked_sheet,
		"kind": kind,
		"head_preview": head_preview,
		"tail_preview": tail_preview,
	}


def build_head_preview(rows, n, kind):
	"""Return the first `n` raw rows of the file as
	[{"file_row": int (1-indexed), "values": [str, ...]}].

	For tabular files, `values` is one entry per cell. For fixed-width txt,
	`values` is a single-element list with the raw line so whitespace alignment
	is preserved for the dialog's monospace rendering.
	"""
	preview = []
	for i, raw in enumerate(rows[:n]):
		if kind == "fixed":
			line = "" if raw is None else str(raw)
			values = [line]
		else:
			if raw is None:
				values = []
			else:
				values = [str(v).strip() if v is not None else "" for v in raw]
		preview.append({"file_row": i + 1, "values": values})
	return preview


def build_tail_preview(data_rows, n_columns, n):
	"""Return the last `n` data rows (after the header) as
	[{"data_index": int, "values": [str, ...]}], with each row's values padded
	or truncated to `n_columns` so the preview lines up with the columns the
	dialog renders.
	"""
	start = max(0, len(data_rows) - n)
	preview = []
	for offset, raw in enumerate(data_rows[start:], start=start):
		if raw is None:
			values = ["" for _ in range(n_columns)]
		else:
			values = [str(v).strip() if v is not None else "" for v in raw]
			if len(values) < n_columns:
				values += [""] * (n_columns - len(values))
			else:
				values = values[:n_columns]
		preview.append({"data_index": offset, "values": values})
	return preview


@frappe.whitelist()
def find_profile(target_doctype, target_child_table_field, scope_filters):
	"""Return the name of the most-specific Import Profile whose scope_filters
	are all satisfied by the given criteria, or None.

	scope_filters argument (from the client) is a JSON dict like
	{"customer": "ACME", "engineer": "..."} — the values from the parent doc
	that we're looking up a profile for.

	A profile matches when every (field_name, field_value) row in its
	scope_filters table is present in the criteria with the same value. Among
	matching profiles, the one with the most scope rows wins (most specific).
	A profile with no scope rows is the universal fallback.
	"""
	if isinstance(scope_filters, str):
		scope_filters = json.loads(scope_filters)

	candidates = frappe.get_all(
		"Import Profile",
		filters={
			"target_doctype": target_doctype,
			"target_child_table_field": target_child_table_field,
		},
		pluck="name",
	)

	best = None
	best_specificity = -1
	for name in candidates:
		profile = frappe.get_cached_doc("Import Profile", name)
		profile_scope = {
			row.field_name: row.field_value for row in profile.scope_filters or []
		}
		if not all(scope_filters.get(k) == v for k, v in profile_scope.items()):
			continue
		if len(profile_scope) > best_specificity:
			best_specificity = len(profile_scope)
			best = name
	return best


@frappe.whitelist()
def get_profile(profile_name):
	"""Return a Profile shaped for the dialog: header/sheet/trailing config plus
	column_mappings keyed by target_field for easy client-side lookup.
	"""
	doc = frappe.get_doc("Import Profile", profile_name)
	return {
		"name": doc.name,
		"profile_name": doc.profile_name,
		"target_doctype": doc.target_doctype,
		"target_child_table_field": doc.target_child_table_field,
		"header_row_index": doc.header_row_index or 0,
		"trailing_rows_to_skip": doc.trailing_rows_to_skip or 0,
		"sheet_name": doc.sheet_name or "",
		"scope_filters": [
			{"field_name": r.field_name, "field_value": r.field_value}
			for r in doc.scope_filters or []
		],
		"column_mappings": [
			{
				"target_field": r.target_field,
				"source_column": r.source_column,
				"is_required": r.is_required,
				"value_map": r.value_map or "",
			}
			for r in doc.column_mappings or []
		],
	}


@frappe.whitelist()
def get_target_field_meta(target_doctype, target_child_table_field):
	"""Return a list of {fieldname, label, fieldtype, options} for fields on the
	target child doctype. Used by the dialog to drive the value-map UI and to
	populate target-field dropdowns.
	"""
	target_meta = frappe.get_meta(target_doctype)
	table_field = target_meta.get_field(target_child_table_field)
	if not table_field or table_field.fieldtype not in ("Table", "Table MultiSelect"):
		frappe.throw(
			_("'{0}' is not a Table field on {1}").format(
				target_child_table_field, target_doctype
			)
		)
	child_meta = frappe.get_meta(table_field.options)
	skip_types = {"Section Break", "Column Break", "Tab Break", "Table Break", "HTML"}
	excluded_fieldnames = {
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"parent",
		"parentfield",
		"parenttype",
		"idx",
	}
	out = []
	for df in child_meta.fields:
		if df.fieldtype in skip_types:
			continue
		if df.fieldname in excluded_fieldnames:
			continue
		# `read_only` is a display flag; the controller still writes these on import
		# (e.g. PCBA Item.qty / .designators are read_only=1 but populated by the
		# importer). Don't exclude them.
		out.append(
			{
				"fieldname": df.fieldname,
				"label": df.label or df.fieldname,
				"fieldtype": df.fieldtype,
				"options": df.options or "",
				"reqd": bool(df.reqd),
			}
		)
	return {"child_doctype": table_field.options, "fields": out}


@frappe.whitelist()
def list_table_fields(target_doctype):
	"""Return Table-typed fields on the target doctype. Used to populate the
	`target_child_table_field` Select dynamically client-side."""
	if not target_doctype:
		return []
	meta = frappe.get_meta(target_doctype)
	return [
		{"fieldname": df.fieldname, "label": df.label or df.fieldname}
		for df in meta.fields
		if df.fieldtype in ("Table", "Table MultiSelect")
	]


@frappe.whitelist()
def save_profile(
	target_doctype,
	target_child_table_field,
	scope_filters,
	column_mappings,
	header_row_index=0,
	trailing_rows_to_skip=0,
	sheet_name=None,
	profile_name=None,
):
	"""Create or update an Import Profile.

	scope_filters: list of {field_name, field_value}
	column_mappings: list of {target_field, source_column, is_required, value_map}
	"""
	if isinstance(scope_filters, str):
		scope_filters = json.loads(scope_filters)
	if isinstance(column_mappings, str):
		column_mappings = json.loads(column_mappings)

	if profile_name:
		doc = frappe.get_doc("Import Profile", profile_name)
	else:
		# Look up by (target_doctype, target_child_table_field, scope) — keeps the
		# upsert behavior the old PCBA flow relied on.
		scope_dict = {f["field_name"]: f["field_value"] for f in scope_filters}
		existing = find_profile(target_doctype, target_child_table_field, scope_dict)
		if existing:
			doc = frappe.get_doc("Import Profile", existing)
		else:
			doc = frappe.new_doc("Import Profile")
			doc.target_doctype = target_doctype
			doc.target_child_table_field = target_child_table_field
			doc.profile_name = autogen_profile_name(
				target_doctype, target_child_table_field, scope_filters
			)

	doc.header_row_index = int(header_row_index or 0)
	doc.trailing_rows_to_skip = int(trailing_rows_to_skip or 0)
	doc.sheet_name = sheet_name or ""

	doc.scope_filters = []
	for sf in scope_filters:
		doc.append(
			"scope_filters", {"field_name": sf["field_name"], "field_value": sf["field_value"]}
		)

	doc.column_mappings = []
	for cm in column_mappings:
		doc.append(
			"column_mappings",
			{
				"target_field": cm["target_field"],
				"source_column": cm["source_column"],
				"is_required": int(cm.get("is_required") or 0),
				"value_map": cm.get("value_map") or "",
			},
		)

	doc.save(ignore_permissions=True)
	return {"name": doc.name}


def autogen_profile_name(target_doctype, target_child_table_field, scope_filters):
	scope_str = " / ".join(f"{f['field_name']}={f['field_value']}" for f in scope_filters)
	suffix = f" ({scope_str})" if scope_str else ""
	return f"{target_doctype} → {target_child_table_field}{suffix}"


# ---------- value-map application --------------------------------------------


def apply_value_map(raw_value, value_map_json, fieldtype, options):
	"""Translate a raw cell value via the column's value_map JSON. Falls back to
	a sensible default for Check fields (0) and 2-option Select fields (the
	option not present in the map). Returns raw_value unchanged when no map is
	configured or no default can be inferred."""
	if not value_map_json:
		return raw_value
	try:
		m = json.loads(value_map_json) if isinstance(value_map_json, str) else value_map_json
	except (TypeError, ValueError):
		return raw_value
	if not m:
		return raw_value
	if raw_value is None:
		return None
	key = str(raw_value).strip()
	if key in m:
		return m[key]
	mapped_targets = set(m.values())
	if fieldtype == "Check":
		if 1 in mapped_targets and 0 not in mapped_targets:
			return 0
		if 0 in mapped_targets and 1 not in mapped_targets:
			return 1
		return raw_value
	if fieldtype == "Select" and options:
		opts = [o.strip() for o in options.split("\n") if o.strip()]
		if len(opts) == 2:
			others = [o for o in opts if o not in mapped_targets]
			if len(others) == 1:
				return others[0]
	return raw_value


def coerce_to_fieldtype(value, fieldtype):
	"""Cast a string cell value to the target field's native Python type so the
	parent doctype's `validate` (which often does numeric comparisons like
	`if row.qty <= 1`) doesn't choke on raw strings. value_map outputs are
	passed through unchanged when already non-string (e.g. Check fields
	receive int 0/1 from the map)."""
	if value is None or not isinstance(value, str):
		return value
	if fieldtype == "Int":
		try:
			return int(float(value))
		except (TypeError, ValueError):
			return value
	if fieldtype in ("Float", "Currency", "Percent"):
		try:
			return float(value)
		except (TypeError, ValueError):
			return value
	if fieldtype == "Check":
		s = value.strip().lower()
		if s in ("1", "true", "yes", "y", "t"):
			return 1
		if s in ("", "0", "false", "no", "n", "f"):
			return 0
		try:
			return 1 if int(float(value)) else 0
		except (TypeError, ValueError):
			return 0
	return value


# ---------- whitelisted import -----------------------------------------------


@frappe.whitelist()
def import_data(
	profile_name,
	file_path,
	parent_doctype,
	parent_doc,
	header_row_index=None,
	trailing_rows_to_skip=None,
	sheet_name=None,
	column_mappings=None,
	value_maps=None,
	scope_filters=None,
):
	"""Run the import using the profile, applying any per-call overrides and
	persisting them back to the profile (so next import picks them up).

	header_row_index, trailing_rows_to_skip, sheet_name: per-call overrides
	column_mappings: list of {target_field, source_column, is_required} — per-call override
	value_maps: dict of {target_field: value_map_json_string} — per-call override
	"""
	if isinstance(column_mappings, str):
		column_mappings = json.loads(column_mappings)
	if isinstance(value_maps, str):
		value_maps = json.loads(value_maps)
	if isinstance(scope_filters, str):
		scope_filters = json.loads(scope_filters)

	profile = frappe.get_doc("Import Profile", profile_name)

	if header_row_index is not None:
		profile.header_row_index = int(header_row_index)
	if trailing_rows_to_skip is not None:
		profile.trailing_rows_to_skip = int(trailing_rows_to_skip)
	if sheet_name is not None:
		profile.sheet_name = sheet_name

	if column_mappings is not None:
		merged_value_maps = {
			row.target_field: row.value_map for row in profile.column_mappings or []
		}
		profile.column_mappings = []
		for cm in column_mappings:
			tgt = cm["target_field"]
			vm = (value_maps or {}).get(tgt) if value_maps else merged_value_maps.get(tgt, "")
			profile.append(
				"column_mappings",
				{
					"target_field": tgt,
					"source_column": cm["source_column"],
					"is_required": int(cm.get("is_required") or 0),
					"value_map": vm or "",
				},
			)
	elif value_maps:
		for row in profile.column_mappings or []:
			if row.target_field in value_maps:
				row.value_map = value_maps[row.target_field] or ""

	if scope_filters is not None:
		profile.scope_filters = []
		for sf in scope_filters:
			profile.append(
				"scope_filters",
				{"field_name": sf["field_name"], "field_value": sf["field_value"]},
			)

	profile.save(ignore_permissions=True)

	# Now read the file and apply the (now-current) profile settings.
	# header_row_index is stored 1-indexed; convert to 0-based for slicing.
	full_path = file_path_full(file_path)
	rows, _picked_sheet, _all_sheets, kind = read_rows(
		full_path, profile.sheet_name or None
	)
	columns, data_rows = extract_columns_and_data(
		rows, max(0, (profile.header_row_index or 1) - 1), kind
	)

	trailing = profile.trailing_rows_to_skip or 0
	if trailing:
		data_rows = data_rows[: max(0, len(data_rows) - trailing)]

	target_meta = frappe.get_meta(profile.target_doctype)
	table_field = target_meta.get_field(profile.target_child_table_field)
	child_meta = frappe.get_meta(table_field.options)
	field_meta_by_name = {df.fieldname: df for df in child_meta.fields}

	col_indices = {}
	for cm in profile.column_mappings or []:
		if cm.source_column in columns:
			col_indices[cm.target_field] = (
				columns.index(cm.source_column),
				cm.value_map or "",
			)

	imported_rows = []
	for raw in data_rows:
		if raw is None:
			continue
		row_data = {}
		for target_field, (col_index, value_map_json) in col_indices.items():
			if col_index >= len(raw):
				continue
			value = raw[col_index]
			if value is None:
				continue
			s = str(value).strip()
			if not s:
				continue
			df = field_meta_by_name.get(target_field)
			fieldtype = df.fieldtype if df else "Data"
			options = df.options if df else ""
			mapped = apply_value_map(s, value_map_json, fieldtype, options)
			row_data[target_field] = coerce_to_fieldtype(mapped, fieldtype)
		if row_data:
			imported_rows.append(row_data)

	doc = frappe.get_doc(parent_doctype, parent_doc)
	setattr(doc, profile.target_child_table_field, [])
	for row_data in imported_rows:
		doc.append(profile.target_child_table_field, row_data)
	doc.save()

	return {
		"success": True,
		"imported_count": len(imported_rows),
		"profile_name": profile.name,
	}
