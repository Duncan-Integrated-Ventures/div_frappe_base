// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

/**
 * Generic spreadsheet importer dialog. Loads an Import Profile (or creates one),
 * lets the user pick a sheet / header row / trailing skip, map columns, and
 * configure value translations for boolean / two-option-select target fields.
 *
 * Public API (attached to window for cross-app use):
 *   frappe.spreadsheet_importer.show_import_dialog({
 *     frm,                          // parent frm (its doc receives imported child rows)
 *     target_child_table_field,     // child-table fieldname on parent doctype
 *     file_url,                     // file_url to import
 *     scope_filters,                // {field_name: field_value, ...}
 *     target_fields,                // optional [{fieldname, label?}, ...] — curated subset
 *                                   //   in display order. If omitted, all editable fields
 *                                   //   on the child doctype are exposed. Labels default
 *                                   //   to the doctype's field label.
 *     title,                        // optional override title
 *     freeze_message,               // optional override import freeze message
 *     on_complete,                  // optional (response) => void
 *   })
 */

frappe.provide('frappe.spreadsheet_importer')

frappe.spreadsheet_importer.show_import_dialog = ({
	frm,
	target_child_table_field,
	file_url,
	scope_filters = {},
	target_fields = null,
	title,
	freeze_message,
	on_complete,
}) => {
	let target_doctype = frm.doc.doctype
	let state = {
		profile_name: null,
		profile: null,
		target_field_meta: [], // [{fieldname, label, fieldtype, options, reqd}, ...]
		child_doctype: null,
		columns: [],
		sample_data: {},
		column_values: {},
		sheets: [],
		head_preview: [],
		tail_preview: [],
		kind: 'tabular',
		header_row_index: 0,
		active_preview_tab: 'head',
	}

	let dialog = new frappe.ui.Dialog({
		title: title || `Import ${target_child_table_field}`,
		size: 'extra-large',
		fields: [],
		primary_action_label: 'Import',
		primary_action: () => run_import(),
	})
	dialog.$wrapper.find('.modal-dialog').css({ 'max-width': '95vw', width: '95vw' })

	if (dialog.fields_dict['__section_1']) {
		dialog.fields_dict['__section_1'].wrapper.remove()
		delete dialog.fields_dict['__section_1']
	}

	dialog.add_fields([
		{
			label: 'Sheet',
			fieldname: 'sheet_name',
			fieldtype: 'Select',
			change: () => parse_and_render(),
		},
		{
			fieldname: '__sheet_col',
			fieldtype: 'Column Break',
		},
		{
			label: 'Header Row Index',
			fieldname: 'header_row_index',
			fieldtype: 'Int',
			default: 1,
			description: 'Row containing column headers (1 = first row). Use ↑ / ↓ to step.',
		},
		{
			fieldname: '__trailing_col',
			fieldtype: 'Column Break',
		},
		{
			label: 'Trailing Rows to Skip',
			fieldname: 'trailing_rows_to_skip',
			fieldtype: 'Int',
			default: 0,
			description:
				'Rows at the end of the file to ignore (e.g. fiducials). Strikethrough below shows what will be skipped.',
		},
		{
			fieldname: 'preview_section',
			fieldtype: 'Section Break',
			label: 'Rows in File',
		},
		{
			fieldname: 'preview_html',
			fieldtype: 'HTML',
		},
	])

	dialog.show()
	wire_int_input('header_row_index', () => parse_and_render())
	wire_int_input('trailing_rows_to_skip', () => refresh_tail_preview_strike())

	bootstrap()

	function bootstrap() {
		// 1) load target field meta (drives column-mapping UI)
		// 2) find existing profile for this scope (or null)
		// 3) parse file with profile defaults
		frappe
			.call({
				method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.get_target_field_meta',
				args: { target_doctype, target_child_table_field },
			})
			.then(r => {
				let all_fields = (r.message && r.message.fields) || []
				state.child_doctype = r.message && r.message.child_doctype
				if (target_fields && target_fields.length) {
					// Caller-curated subset: filter and reorder, applying optional label overrides.
					let by_fieldname = Object.fromEntries(all_fields.map(f => [f.fieldname, f]))
					state.target_field_meta = target_fields
						.map(t => {
							let base = by_fieldname[t.fieldname]
							if (!base) {
								console.warn(`spreadsheet_importer: '${t.fieldname}' is not a field on ${state.child_doctype}`)
								return null
							}
							return { ...base, label: t.label || base.label }
						})
						.filter(Boolean)
				} else {
					state.target_field_meta = all_fields
				}
				return frappe.call({
					method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.find_profile',
					args: {
						target_doctype,
						target_child_table_field,
						scope_filters: scope_filters,
					},
				})
			})
			.then(r => {
				let profile_name = r.message
				if (!profile_name) return null
				state.profile_name = profile_name
				return frappe.call({
					method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.get_profile',
					args: { profile_name },
				})
			})
			.then(r => {
				let initial_header = 1
				let initial_trailing = 0
				if (r && r.message) {
					state.profile = r.message
					initial_header = state.profile.header_row_index || 1
					initial_trailing = state.profile.trailing_rows_to_skip || 0
					// dialog.set_value routes through frappe.run_serially, which
					// defers the underlying $input.val(...) write to a later
					// microtask. Pass the preset header_row_index directly to
					// parse_and_render so the very first server call and
					// preview render use the preset, not the field default.
					dialog.set_value('header_row_index', initial_header)
					dialog.set_value('trailing_rows_to_skip', initial_trailing)
				}
				parse_and_render({ header_row_index: initial_header })
			})
	}

	function parse_and_render(overrides = {}) {
		let header_row_index = overrides.header_row_index ?? Number(dialog.get_value('header_row_index')) ?? 1
		header_row_index = Number(header_row_index) || 1
		let sheet_name = dialog.get_value('sheet_name') || (state.profile ? state.profile.sheet_name : null) || null

		frappe
			.call({
				method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.parse_file',
				args: {
					file_path: file_url,
					header_row_index,
					sheet_name,
				},
			})
			.then(r => {
				let m = r.message || {}
				state.columns = m.columns || []
				state.sample_data = m.sample_data || {}
				state.column_values = m.column_values || {}
				state.sheets = m.sheets || []
				state.head_preview = m.head_preview || []
				state.tail_preview = m.tail_preview || []
				state.kind = m.kind || 'tabular'
				state.header_row_index = header_row_index

				populate_sheet_select(m.sheet_name)
				clamp_header_row_input(m.total_file_rows || 0)
				render_preview()
				render_column_mapping()
				render_value_mapping()
			})
	}

	function populate_sheet_select(picked_sheet) {
		let field = dialog.get_field('sheet_name')
		if (!field) return
		let has_sheets = state.sheets && state.sheets.length > 0
		dialog.set_df_property('sheet_name', 'hidden', has_sheets ? 0 : 1)
		if (!has_sheets) return

		let saved = (state.profile && state.profile.sheet_name) || picked_sheet || state.sheets[0]
		field.df.options = state.sheets.join('\n')
		field.refresh()
		// set_value would re-fire the `change` handler and recurse into parse_and_render
		// — set the underlying input value directly instead.
		if (dialog.get_value('sheet_name') !== saved) {
			field.$input.val(saved)
		}
	}

	function clamp_header_row_input(total_file_rows) {
		let $inp = dialog.get_field('header_row_index')?.$input
		if (!$inp || !$inp.length) return
		let max_index = Math.max(1, total_file_rows)
		$inp.attr({ type: 'number', step: '1', min: '1', max: String(max_index) })
		let cur = Number($inp.val())
		if (!Number.isFinite(cur) || cur < 1) $inp.val(1)
		else if (cur > max_index) $inp.val(max_index)
	}

	function wire_int_input(fieldname, on_committed) {
		let $inp = dialog.get_field(fieldname)?.$input
		if (!$inp || !$inp.length) return
		// header_row_index is 1-indexed; trailing_rows_to_skip is a 0+ count
		let floor = fieldname === 'header_row_index' ? 1 : 0
		$inp.attr({ type: 'number', step: '1', min: String(floor) })
		let timer = null
		$inp.on('input', () => {
			let max_s = $inp.attr('max')
			let max = max_s == null || max_s === '' ? null : Number(max_s)
			let val = Number($inp.val())
			if (Number.isFinite(val)) {
				if (val < floor) $inp.val(floor)
				else if (max != null && val > max) $inp.val(max)
			}
			clearTimeout(timer)
			timer = setTimeout(() => on_committed(), 250)
		})
	}

	// ---------- preview (head + tail tabs) -----------------------------------

	function render_preview() {
		let $wrap = dialog.fields_dict.preview_html?.$wrapper
		if (!$wrap) return
		if (!state.head_preview.length && !state.tail_preview.length) {
			$wrap.html(`<div class="text-muted small">No rows to preview.</div>`)
			dialog.set_df_property('preview_section', 'hidden', 1)
			return
		}
		dialog.set_df_property('preview_section', 'hidden', 0)

		let total_data_rows =
			state.tail_preview.length > 0 ? state.tail_preview[state.tail_preview.length - 1].data_index + 1 : 0

		let head_html = build_head_table_html()
		let tail_html = build_tail_table_html()

		let head_active = state.active_preview_tab === 'head'
		$wrap.html(`
			<style>
				.ssi-preview-tabs { display: flex; gap: 4px; margin-bottom: 8px; border-bottom: 1px solid var(--border-color); }
				.ssi-preview-tab-btn { background: transparent; border: 1px solid transparent; border-bottom: none; padding: 6px 12px; font-size: 12px; font-weight: 600; color: var(--text-muted); cursor: pointer; border-radius: var(--border-radius) var(--border-radius) 0 0; margin-bottom: -1px; }
				.ssi-preview-tab-btn.active { color: var(--text-color); background: var(--fg-color); border-color: var(--border-color); }
				.ssi-preview-tab-content { display: none; }
				.ssi-preview-tab-content.active { display: block; }
				.ssi-tail-wrapper { max-height: 220px; overflow: auto; border: 1px solid var(--border-color); border-radius: var(--border-radius); }
				.ssi-tail-table { width: 100%; font-size: 12px; border-collapse: collapse; }
				.ssi-tail-table th, .ssi-tail-table td { padding: 4px 8px; border-bottom: 1px solid var(--border-color); white-space: nowrap; text-align: left; }
				.ssi-tail-table thead th { position: sticky; top: 0; background: var(--fg-color); z-index: 1; font-weight: 600; }
				.ssi-tail-rownum { color: var(--text-muted); font-weight: 600; width: 1px; }
				.ssi-tail-skip td, .ssi-tail-skip th { text-decoration: line-through; color: var(--text-muted); background: var(--bg-light, #fafafa); }
				.ssi-head-fixed td { font-family: var(--font-stack-mono, monospace); white-space: pre; }
				.ssi-head-row-header td, .ssi-head-row-header th { background: var(--blue-50, #e7f1ff); font-weight: 600; }
				.ssi-head-row-header .ssi-tail-rownum::after { content: ' ←'; color: var(--blue-500, #2490ef); }
			</style>
			<div class="ssi-preview-tabs">
				<button type="button" class="ssi-preview-tab-btn ${head_active ? 'active' : ''}" data-tab="head">First ${
					state.head_preview.length
				} rows</button>
				<button type="button" class="ssi-preview-tab-btn ${head_active ? '' : 'active'}" data-tab="tail">Last ${
					state.tail_preview.length
				} rows</button>
			</div>
			<div class="ssi-preview-tab-content ${head_active ? 'active' : ''}" data-tab="head">
				${head_html}
				<div class="text-muted small" style="margin-top:6px;">
					The highlighted row is the current <b>Header Row Index</b>. Adjust if the header sits below preamble like a section marker.
				</div>
			</div>
			<div class="ssi-preview-tab-content ${head_active ? '' : 'active'}" data-tab="tail">
				${tail_html}
				<div class="text-muted small" style="margin-top:6px;">
					Showing last ${state.tail_preview.length} of ${total_data_rows} data rows.
					Increase <b>Trailing Rows to Skip</b> until the rows you want to ignore are struck through.
				</div>
			</div>
		`)

		$wrap.find('.ssi-preview-tab-btn').on('click', e => {
			let tab = e.currentTarget.dataset.tab
			state.active_preview_tab = tab
			$wrap.find('.ssi-preview-tab-btn').each((_, btn) => {
				btn.classList.toggle('active', btn.dataset.tab === tab)
			})
			$wrap.find('.ssi-preview-tab-content').each((_, el) => {
				el.classList.toggle('active', el.dataset.tab === tab)
			})
		})

		// Clamp the trailing skip to the number of data rows
		let $inp = dialog.get_field('trailing_rows_to_skip')?.$input
		if ($inp && $inp.length) $inp.attr({ max: String(total_data_rows) })

		refresh_tail_preview_strike()
	}

	function build_head_table_html() {
		if (!state.head_preview.length) {
			return `<div class="text-muted small">No rows to preview.</div>`
		}
		let max_cells = state.head_preview.reduce((m, p) => Math.max(m, p.values.length), 0)
		// Numeric column index headers — the actual columns header may not be in
		// any of these rows (e.g. `[PLACEMENTS]` section marker on row 1).
		let col_headers = ''
		for (let i = 0; i < max_cells; i++) {
			col_headers += `<th>${i + 1}</th>`
		}
		let fixed_class = state.kind === 'fixed' ? ' ssi-head-fixed' : ''
		let row_html = state.head_preview
			.map(p => {
				let is_header = p.file_row === state.header_row_index
				let cells = ''
				for (let i = 0; i < max_cells; i++) {
					let v = i < p.values.length ? p.values[i] : ''
					cells += `<td>${_esc(v)}</td>`
				}
				let cls = is_header ? ' class="ssi-head-row-header"' : ''
				return `<tr${cls}><th class="ssi-tail-rownum">${p.file_row}</th>${cells}</tr>`
			})
			.join('')
		return `
			<div class="ssi-tail-wrapper">
				<table class="ssi-tail-table${fixed_class}">
					<thead><tr><th class="ssi-tail-rownum">Row</th>${col_headers}</tr></thead>
					<tbody>${row_html}</tbody>
				</table>
			</div>
		`
	}

	function build_tail_table_html() {
		if (!state.tail_preview.length) {
			return `<div class="text-muted small">No data rows to preview.</div>`
		}
		let header_html = state.columns.map(c => `<th title="${_esc(c)}">${_esc(c)}</th>`).join('')
		let row_html = state.tail_preview
			.map(p => {
				// header_row_index is already 1-based; data rows start at header_row + 1
				let display_row = state.header_row_index + p.data_index + 1
				let cells = p.values.map(v => `<td>${_esc(v)}</td>`).join('')
				return `<tr data-data-index="${p.data_index}"><th class="ssi-tail-rownum">${display_row}</th>${cells}</tr>`
			})
			.join('')
		return `
			<div class="ssi-tail-wrapper">
				<table class="ssi-tail-table">
					<thead><tr><th class="ssi-tail-rownum">Row</th>${header_html}</tr></thead>
					<tbody>${row_html}</tbody>
				</table>
			</div>
		`
	}

	function refresh_tail_preview_strike() {
		let $wrap = dialog.fields_dict.preview_html?.$wrapper
		if (!$wrap) return
		let trailing = Number(dialog.get_value('trailing_rows_to_skip')) || 0
		let total_data_rows =
			state.tail_preview.length > 0 ? state.tail_preview[state.tail_preview.length - 1].data_index + 1 : 0
		let last_kept_index = total_data_rows - trailing - 1
		$wrap.find('.ssi-preview-tab-content[data-tab="tail"] tbody tr').each((_, el) => {
			let di = Number(el.dataset.dataIndex)
			if (di > last_kept_index) el.classList.add('ssi-tail-skip')
			else el.classList.remove('ssi-tail-skip')
		})
	}

	// ---------- column mapping grid ------------------------------------------

	function render_column_mapping() {
		ensure_section('mapping_section', 'Column Mapping')
		ensure_html('mapping_html')

		// If a select inside the column-mapping grid currently has focus, the
		// browser's native dropdown is open. Replacing the HTML now would close
		// it. Defer the re-render until that select blurs.
		let active = document.activeElement
		let $wrap_existing = dialog.fields_dict.mapping_html.$wrapper
		if (active && $wrap_existing && $.contains($wrap_existing[0], active) && active.matches('.ssi-map-select')) {
			$(active)
				.off('blur.ssi-defer')
				.on('blur.ssi-defer', function () {
					$(this).off('blur.ssi-defer')
					render_column_mapping()
				})
			return
		}

		// Build label list of target fields plus a blank "ignore" option.
		let target_options = ['', ...state.target_field_meta.map(f => `${f.label} [${f.fieldname}]`)]
		let label_to_fieldname = {}
		state.target_field_meta.forEach(f => {
			label_to_fieldname[`${f.label} [${f.fieldname}]`] = f.fieldname
		})

		// Reverse lookup: for each target_field, what source column did the saved profile map?
		let fieldname_to_source = {}
		if (state.profile) {
			for (let cm of state.profile.column_mappings || []) {
				fieldname_to_source[cm.target_field] = cm.source_column
			}
		}

		let cells = state.columns
			.map((col, idx) => {
				let current_label = ''
				for (let f of state.target_field_meta) {
					if (fieldname_to_source[f.fieldname] === col) {
						current_label = `${f.label} [${f.fieldname}]`
						break
					}
				}
				let options_html = target_options
					.map(opt => {
						let v = _esc(opt)
						let sel = opt === current_label ? ' selected' : ''
						return `<option value="${v}"${sel}>${v}</option>`
					})
					.join('')
				let sample = state.sample_data[col] == null ? '' : String(state.sample_data[col])
				return `
					<div class="ssi-mapping-cell" data-col="${_esc(col)}" data-idx="${idx}">
						<div class="ssi-col-name" title="${_esc(col)}">${_esc(col)}</div>
						<select class="form-control ssi-map-select" data-col="${_esc(col)}">${options_html}</select>
						<div class="ssi-sample text-muted" title="${_esc(sample)}">${_esc(sample)}</div>
					</div>
				`
			})
			.join('')

		let html = `
			<style>
				.ssi-mapping-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; margin-top: 8px; }
				@media (max-width: 900px) { .ssi-mapping-grid { grid-template-columns: repeat(2, minmax(0,1fr)); } }
				@media (max-width: 600px) { .ssi-mapping-grid { grid-template-columns: 1fr; } }
				.ssi-mapping-cell { display: flex; flex-direction: column; gap: 4px; padding: 10px; border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--fg-color); }
				.ssi-col-name { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.ssi-sample { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 1em; }
			</style>
			<div class="ssi-mapping-grid">${cells}</div>
		`
		let $wrap = dialog.fields_dict.mapping_html.$wrapper
		$wrap.html(html)
		$wrap.find('.ssi-map-select').on('change', () => {
			validate_unique(label_to_fieldname)
			render_value_mapping()
		})
	}

	function validate_unique(label_to_fieldname) {
		let $sel = dialog.fields_dict.mapping_html?.$wrapper.find('.ssi-map-select')
		if (!$sel || !$sel.length) return
		let used = new Set()
		let dup = null
		$sel.each((_, el) => {
			if (!el.value) return
			let fn = label_to_fieldname[el.value]
			if (used.has(fn)) dup = el.value
			else used.add(fn)
		})
		if (dup) {
			frappe.show_alert({ message: __('Field "{0}" is mapped to multiple columns', [dup]), indicator: 'orange' }, 4)
		}
	}

	function read_column_mappings_from_grid() {
		let label_to_fieldname = {}
		state.target_field_meta.forEach(f => {
			label_to_fieldname[`${f.label} [${f.fieldname}]`] = f.fieldname
		})
		let mappings = []
		let used = new Set()
		let dup = false
		let $sel = dialog.fields_dict.mapping_html?.$wrapper.find('.ssi-map-select')
		$sel?.each((_, el) => {
			if (!el.value) return
			let fn = label_to_fieldname[el.value]
			if (used.has(fn)) {
				dup = true
				return
			}
			used.add(fn)
			let target_meta = state.target_field_meta.find(f => f.fieldname === fn)
			mappings.push({
				target_field: fn,
				source_column: el.dataset.col,
				is_required: target_meta?.reqd ? 1 : 0,
			})
		})
		if (dup) {
			frappe.msgprint(__('A target field is mapped to multiple columns. Each can only be used once.'))
			return null
		}
		return mappings
	}

	// ---------- value mapping section ----------------------------------------

	function render_value_mapping() {
		ensure_section('value_mapping_section', 'Value Mapping')
		ensure_html('value_mapping_html')

		let $wrap = dialog.fields_dict.value_mapping_html.$wrapper
		let mappings = peek_column_mappings()
		let translatable = mappings.filter(m => {
			let meta = state.target_field_meta.find(f => f.fieldname === m.target_field)
			return meta && is_translatable_fieldtype(meta)
		})

		if (!translatable.length) {
			$wrap.html(
				`<div class="text-muted small">No translations needed. Map a column to a Check or Select field to configure one.</div>`
			)
			dialog.set_df_property('value_mapping_section', 'hidden', 1)
			return
		}
		dialog.set_df_property('value_mapping_section', 'hidden', 0)

		let saved_value_maps = {}
		if (state.profile) {
			for (let cm of state.profile.column_mappings || []) {
				if (cm.value_map) saved_value_maps[cm.target_field] = cm.value_map
			}
		}

		let blocks = translatable
			.map(m => {
				let meta = state.target_field_meta.find(f => f.fieldname === m.target_field)
				let saved = parse_value_map(saved_value_maps[m.target_field])
				if (meta.fieldtype === 'Check') {
					return render_check_block(m, meta, saved)
				}
				return render_select_block(m, meta, saved)
			})
			.join('')

		$wrap.html(`
			<style>
				.ssi-vm-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 8px; }
				@media (max-width: 700px) { .ssi-vm-grid { grid-template-columns: 1fr; } }
				.ssi-vm-row { padding: 10px; border: 1px solid var(--border-color); border-radius: var(--border-radius); display: flex; flex-direction: column; gap: 6px; }
				.ssi-vm-label { font-size: 13px; }
				.ssi-vm-table { width: 100%; font-size: 12px; border-collapse: collapse; }
				.ssi-vm-table th, .ssi-vm-table td { padding: 4px 6px; border-bottom: 1px solid var(--border-color); text-align: left; }
				.ssi-vm-table th { font-weight: 600; color: var(--text-muted); }
				.ssi-vm-table td.ssi-vm-source { font-family: var(--font-stack-mono, monospace); }
				.ssi-vm-table .form-control { padding: 2px 6px; height: auto; font-size: 12px; }
			</style>
			<div class="ssi-vm-grid">${blocks}</div>
		`)
	}

	function render_check_block(m, meta, saved) {
		// Check: single picker — "which source value means 1?"
		let source_values = state.column_values[m.source_column] || []
		let saved_match_source = pick_saved_source(saved, 1)
		let options_html = ['', ...source_values]
			.map(v => {
				let escv = _esc(v)
				let sel = v === saved_match_source ? ' selected' : ''
				return `<option value="${escv}"${sel}>${escv}</option>`
			})
			.join('')
		return `
			<div class="ssi-vm-row" data-row-type="check" data-target-field="${_esc(m.target_field)}" data-source-column="${_esc(
				m.source_column
			)}">
				<div class="ssi-vm-label"><b>${_esc(
					meta.label
				)}</b> <span class="text-muted">— value meaning <code>1</code></span></div>
				<select class="form-control ssi-vm-check-select">${options_html}</select>
				<div class="text-muted small">Other values become <code>0</code>.</div>
			</div>
		`
	}

	function render_select_block(m, meta, saved) {
		// Select with N options: render a row per distinct source value, each with
		// a target-option dropdown (blank = leave to fall through).
		let source_values = state.column_values[m.source_column] || []
		let target_opts = meta.options
			.split('\n')
			.map(o => o.trim())
			.filter(Boolean)

		let rows_html = source_values
			.map(src => {
				let saved_target = saved && saved[src] != null ? String(saved[src]) : ''
				let opts_html = ['', ...target_opts]
					.map(opt => {
						let v = _esc(opt)
						let sel = opt === saved_target ? ' selected' : ''
						return `<option value="${v}"${sel}>${v}</option>`
					})
					.join('')
				return `
					<tr>
						<td class="ssi-vm-source" title="${_esc(src)}">${_esc(src)}</td>
						<td><select class="form-control ssi-vm-select-row" data-source-value="${_esc(src)}">${opts_html}</select></td>
					</tr>
				`
			})
			.join('')

		let body =
			source_values.length === 0
				? `<div class="text-muted small">No values found in source column "${_esc(m.source_column)}".</div>`
				: `<table class="ssi-vm-table">
					<thead><tr><th>Source value</th><th>Maps to</th></tr></thead>
					<tbody>${rows_html}</tbody>
				</table>`

		return `
			<div class="ssi-vm-row" data-row-type="select" data-target-field="${_esc(m.target_field)}" data-source-column="${_esc(
				m.source_column
			)}">
				<div class="ssi-vm-label"><b>${_esc(
					meta.label
				)}</b> <span class="text-muted">— map each source value to one of: ${target_opts
					.map(o => `<code>${_esc(o)}</code>`)
					.join(', ')}</span></div>
				${body}
			</div>
		`
	}

	function peek_column_mappings() {
		// Same as read_column_mappings_from_grid but without alerts (used for live re-render).
		let label_to_fieldname = {}
		state.target_field_meta.forEach(f => {
			label_to_fieldname[`${f.label} [${f.fieldname}]`] = f.fieldname
		})
		let mappings = []
		let used = new Set()
		let $sel = dialog.fields_dict.mapping_html?.$wrapper.find('.ssi-map-select')
		$sel?.each((_, el) => {
			if (!el.value) return
			let fn = label_to_fieldname[el.value]
			if (used.has(fn)) return
			used.add(fn)
			mappings.push({ target_field: fn, source_column: el.dataset.col })
		})
		return mappings
	}

	function is_translatable_fieldtype(meta) {
		if (meta.fieldtype === 'Check') return true
		if (meta.fieldtype === 'Select' && meta.options) {
			let opts = meta.options
				.split('\n')
				.map(o => o.trim())
				.filter(Boolean)
			return opts.length >= 2
		}
		return false
	}

	function pick_saved_source(saved_map, match_target) {
		// Used for Check rows to pre-select the source value that maps to 1.
		if (!saved_map) return ''
		for (let [src, tgt] of Object.entries(saved_map)) {
			if (tgt === match_target || String(tgt) === String(match_target)) return src
		}
		return ''
	}

	function parse_value_map(json_str) {
		if (!json_str) return null
		try {
			return JSON.parse(json_str)
		} catch {
			return null
		}
	}

	function read_value_maps_from_grid() {
		let out = {}
		let $rows = dialog.fields_dict.value_mapping_html?.$wrapper.find('.ssi-vm-row')
		$rows?.each((_, el) => {
			let target_field = el.dataset.targetField
			let row_type = el.dataset.rowType
			if (row_type === 'check') {
				let source_value = $(el).find('.ssi-vm-check-select').val()
				out[target_field] = source_value ? JSON.stringify({ [source_value]: 1 }) : ''
				return
			}
			// row_type === 'select' — collect every source row that has a non-blank target
			let map = {}
			$(el)
				.find('.ssi-vm-select-row')
				.each((_, sel) => {
					let src = sel.dataset.sourceValue
					let tgt = sel.value
					if (src && tgt) map[src] = tgt
				})
			out[target_field] = Object.keys(map).length ? JSON.stringify(map) : ''
		})
		return out
	}

	// ---------- run import ----------------------------------------------------

	function run_import() {
		let column_mappings = read_column_mappings_from_grid()
		if (!column_mappings) return
		if (column_mappings.length === 0) {
			frappe.msgprint(__('Please map at least one column.'))
			return
		}
		let value_maps = read_value_maps_from_grid()
		let header_row_index = Number(dialog.get_value('header_row_index')) || 0
		let trailing_rows_to_skip = Number(dialog.get_value('trailing_rows_to_skip')) || 0
		let sheet_name = dialog.get_value('sheet_name') || null

		let scope_array = Object.entries(scope_filters).map(([field_name, field_value]) => ({
			field_name,
			field_value,
		}))

		let do_save_then_import = profile_name => {
			frappe.call({
				method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.import_data',
				args: {
					profile_name,
					file_path: file_url,
					parent_doctype: frm.doc.doctype,
					parent_doc: frm.doc.name,
					header_row_index,
					trailing_rows_to_skip,
					sheet_name,
					column_mappings,
					value_maps,
					scope_filters: scope_array,
				},
				freeze: true,
				freeze_message: freeze_message || __('Importing…'),
				callback: r => {
					if (r.message && r.message.success) {
						frappe.show_alert(__('Imported {0} rows.', [r.message.imported_count]))
						dialog.hide()
						frm.reload_doc().then(() => {
							if (on_complete) on_complete(r.message)
						})
					}
				},
			})
		}

		if (state.profile_name) {
			do_save_then_import(state.profile_name)
			return
		}
		// No profile yet — create one first
		frappe
			.call({
				method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.save_profile',
				args: {
					target_doctype,
					target_child_table_field,
					scope_filters: scope_array,
					column_mappings,
					header_row_index,
					trailing_rows_to_skip,
					sheet_name,
				},
			})
			.then(r => {
				if (r.message && r.message.name) {
					state.profile_name = r.message.name
					do_save_then_import(r.message.name)
				}
			})
	}

	// ---------- helpers -------------------------------------------------------

	function ensure_section(fieldname, label) {
		if (dialog.fields_dict[fieldname]) return
		dialog.add_fields([{ fieldname, fieldtype: 'Section Break', label }])
	}
	function ensure_html(fieldname) {
		if (dialog.fields_dict[fieldname]) return
		dialog.add_fields([{ fieldname, fieldtype: 'HTML' }])
	}
	function _esc(s) {
		return String(s == null ? '' : s)
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#39;')
	}
}
