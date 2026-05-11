// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

/* global div_frappe_base, erpnext */

/**
 * Client-side mirrors of div_frappe_base.monkey_patches so live SPN /
 * packaging / qty edits in transaction forms (1) hand the server enough row
 * context for `current_item.<anything>` conditions to resolve and (2) reflect
 * Pricing Rule.custom_per_line_charge in row totals before the user saves.
 *
 * No app-specific field references — the row-pass-through patch sends every
 * scalar field on the row dict so any consumer's Pricing Rule conditions can
 * read any row-level field via `current_item.get('fieldname')` without
 * coordinating field lists across apps.
 *
 * Install: prototypes are patched on the first transaction-form `setup` event
 * — by then erpnext.TransactionController is guaranteed to exist (it's the
 * controller class being constructed). The cache is warmed on every refresh
 * so calculate_item_values (which iterates ALL rows on every recalc) finds
 * per-line charges for rows other than the one just changed.
 */

frappe.provide('div_frappe_base')

div_frappe_base._per_line_charge_cache = {}

div_frappe_base.parse_pricing_rules = s => {
	if (!s) return []
	if (s.startsWith('[')) {
		try {
			return JSON.parse(s) || []
		} catch (e) {
			return []
		}
	}
	return s
		.split(',')
		.map(x => x.trim())
		.filter(Boolean)
}

div_frappe_base.fetch_per_line_charges = rule_names => {
	const need = (rule_names || []).filter(n => !(n in div_frappe_base._per_line_charge_cache))
	if (!need.length) return Promise.resolve()
	return frappe.db
		.get_list('Pricing Rule', {
			filters: { name: ['in', need] },
			fields: ['name', 'custom_per_line_charge'],
			limit: 0,
		})
		.then(rows => {
			for (const row of rows) {
				div_frappe_base._per_line_charge_cache[row.name] = parseFloat(row.custom_per_line_charge) || 0
			}
			for (const n of need) {
				if (!(n in div_frappe_base._per_line_charge_cache)) {
					div_frappe_base._per_line_charge_cache[n] = 0
				}
			}
		})
}

div_frappe_base.per_line_charge_for_item = item => {
	const rules = div_frappe_base.parse_pricing_rules(item.pricing_rules)
	let total = 0
	for (const r of rules) total += div_frappe_base._per_line_charge_cache[r] || 0
	return total
}

div_frappe_base.warm_per_line_charge_cache = frm => {
	const rule_names = []
	for (const item of frm.doc.items || []) {
		rule_names.push(...div_frappe_base.parse_pricing_rules(item.pricing_rules))
	}
	return div_frappe_base.fetch_per_line_charges(rule_names)
}

const _TX_DOCTYPES = [
	'Supplier Quotation',
	'Purchase Order',
	'Purchase Receipt',
	'Purchase Invoice',
	'Quotation',
	'Sales Order',
	'Delivery Note',
	'Sales Invoice',
	'POS Invoice',
]
const _TX_DOCTYPE_SET = new Set(_TX_DOCTYPES)

div_frappe_base.install_pricing_rule_patches = () => {
	if (div_frappe_base._patches_installed) return
	if (typeof erpnext === 'undefined' || !erpnext.TransactionController || !erpnext.taxes_and_totals) return
	div_frappe_base._patches_installed = true

	const _orig_get_item_list = erpnext.TransactionController.prototype._get_item_list
	erpnext.TransactionController.prototype._get_item_list = function (item) {
		const list = _orig_get_item_list.call(this, item)
		for (const entry of list) {
			const row = locals[entry.doctype] && locals[entry.doctype][entry.name]
			if (!row) continue
			for (const key of Object.keys(row)) {
				if (key in entry) continue
				if (key.startsWith('__')) continue
				const v = row[key]
				if (v === null || v === undefined) continue
				if (typeof v === 'object') continue
				entry[key] = v
			}
		}
		return list
	}

	const _orig_set_values_for_item_list = erpnext.TransactionController.prototype._set_values_for_item_list
	erpnext.TransactionController.prototype._set_values_for_item_list = function (children) {
		const me = this
		const rule_names = []
		for (const child of children || []) {
			rule_names.push(...div_frappe_base.parse_pricing_rules(child.pricing_rules))
		}
		return div_frappe_base
			.fetch_per_line_charges(rule_names)
			.then(() => _orig_set_values_for_item_list.call(me, children))
	}

	const _orig_calculate_item_values = erpnext.taxes_and_totals.prototype.calculate_item_values
	erpnext.taxes_and_totals.prototype.calculate_item_values = function () {
		_orig_calculate_item_values.call(this)
		if (this.discount_amount_applied) return
		if (!_TX_DOCTYPE_SET.has(this.frm.doc.doctype)) return
		for (const item of this.frm.doc.items || []) {
			const charge = div_frappe_base.per_line_charge_for_item(item)
			if (!charge) continue
			item.amount = flt(item.amount + charge, precision('amount', item))
			item.net_amount = item.amount
			this.set_in_company_currency(item, ['amount', 'net_amount'])
		}
	}
}

// Install on every transaction form's setup (erpnext.TransactionController is
// guaranteed by then); warm the per-line-charge cache on every refresh so
// calculate_item_values finds entries for all rows, not just the one whose
// pricing rule just changed.
for (const dt of _TX_DOCTYPES) {
	frappe.ui.form.on(dt, {
		setup() {
			div_frappe_base.install_pricing_rule_patches()
		},
		refresh(frm) {
			div_frappe_base.warm_per_line_charge_cache(frm)
		},
	})
}
