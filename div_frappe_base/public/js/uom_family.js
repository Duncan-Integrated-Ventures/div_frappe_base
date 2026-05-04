// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt
//
// UOM Manager dialogs — shared by the UOM list view's "UOM Manager" button
// group and the Canonical Attribute form. Exposes:
//   div_frappe_base.show_uom_family_dialog → creates/updates an SI-prefix
//     family via div_frappe_base.uom.create_uom_family.
//   div_frappe_base.show_uom_link_dialog → links two existing UOMs (typically
//     across families) via div_frappe_base.uom.link_uom_families. One edge is
//     enough — the BFS in select_display_uom composes the prefix chains
//     transitively.

frappe.provide('div_frappe_base')

div_frappe_base.show_uom_family_dialog = function (opts) {
	opts = opts || {}
	const dialog = new frappe.ui.Dialog({
		title: __('Create UOM Family'),
		fields: [
			{
				fieldname: 'base_uom',
				fieldtype: 'Data',
				label: __('Base UOM Name'),
				reqd: 1,
				default: opts.base_uom,
				description: __('e.g. "Ohm" — siblings Picoohm, Nanoohm, … Teraohm will be created.'),
			},
			{
				fieldname: 'primary_symbol',
				fieldtype: 'Data',
				label: __('Primary Symbol'),
				reqd: 1,
				description: __('e.g. "Ω" — the canonical written form. Each SI prefix gets prepended to this (kΩ, MΩ, …).'),
			},
			{
				fieldname: 'alias_symbols',
				fieldtype: 'Small Text',
				label: __('Additional Alias Symbols'),
				description: __(
					'One per line or comma-separated. e.g. "ohm, ohms, OHMS". Each gets prefix-decorated too (kohm, k ohm, MOHM …).'
				),
			},
			{
				fieldname: 'category',
				fieldtype: 'Link',
				options: 'UOM Category',
				label: __('Conversion Factor Category'),
				description: __("Tagged on each created UOM Conversion Factor. Auto-created if it doesn't exist."),
			},
			{
				fieldname: 'min_prefix',
				fieldtype: 'Select',
				label: __('Smallest SI Prefix'),
				options: 'Femto\nPico\nNano\nMicro\nMilli\nBase\nKilo\nMega\nGiga\nTera\nPeta',
				default: 'Femto',
				description: __(
					'Floor of the prefix range to generate. Choose "Base" for discrete units (e.g. Bit, Sample/Second) where sub-base prefixes are nonsensical.'
				),
			},
			{
				fieldname: 'max_prefix',
				fieldtype: 'Select',
				label: __('Largest SI Prefix'),
				options: 'Femto\nPico\nNano\nMicro\nMilli\nBase\nKilo\nMega\nGiga\nTera\nPeta',
				default: 'Peta',
				description: __(
					'Ceiling of the prefix range. Tighten for units that never appear large (e.g. Henry, Farad → "Kilo").'
				),
			},
		],
		primary_action_label: __('Create / Update'),
		primary_action(values) {
			const aliases = (values.alias_symbols || '')
				.split(/[\n,]/)
				.map(s => s.trim())
				.filter(Boolean)

			const decode = v => (v === 'Base' ? '' : v)
			const min_prefix = decode(values.min_prefix || 'Femto')
			const max_prefix = decode(values.max_prefix || 'Peta')

			frappe.call({
				method: 'div_frappe_base.uom.create_uom_family',
				freeze: true,
				freeze_message: __('Creating UOM family...'),
				args: {
					base_uom: values.base_uom,
					primary_symbol: values.primary_symbol,
					alias_symbols: aliases,
					category: values.category,
					min_prefix: min_prefix,
					max_prefix: max_prefix,
				},
				callback(r) {
					if (!r.message) return
					const s = r.message
					frappe.show_alert({
						message: __('Created {0} UOM(s), {1} factor(s), {2} alias(es).', [
							s.uoms_created,
							s.factors_created,
							s.aliases_created,
						]),
						indicator: 'green',
					})
					dialog.hide()
					if (opts.on_complete) opts.on_complete(s)
				},
			})
		},
	})

	dialog.show()
}

div_frappe_base.show_uom_link_dialog = function (opts) {
	opts = opts || {}
	const dialog = new frappe.ui.Dialog({
		title: __('Link UOM Families'),
		fields: [
			{
				fieldname: 'from_uom',
				fieldtype: 'Link',
				options: 'UOM',
				label: __('From UOM'),
				reqd: 1,
				default: opts.from_uom,
			},
			{
				fieldname: 'to_uom',
				fieldtype: 'Link',
				options: 'UOM',
				label: __('To UOM'),
				reqd: 1,
				default: opts.to_uom,
			},
			{
				fieldname: 'value',
				fieldtype: 'Float',
				label: __('Value'),
				reqd: 1,
				precision: 9,
				description: __('1 × From UOM = Value × To UOM. e.g. Bit → Byte = 0.125, Hour → Second = 3600.'),
			},
			{
				fieldname: 'category',
				fieldtype: 'Link',
				options: 'UOM Category',
				label: __('Conversion Factor Category'),
				description: __("Tagged on the created UOM Conversion Factor. Auto-created if it doesn't exist."),
			},
			{
				fieldname: 'transitive_note',
				fieldtype: 'HTML',
				options: `<div class="text-muted small">${__(
					'Only one edge is needed. The display-UOM resolver walks both directions and composes SI prefixes transitively, so a single link makes every prefix on either side cross-convertible (e.g. Bit ↔ Byte → Kbps ↔ KBps, Mbps ↔ MBps, …).'
				)}</div>`,
			},
		],
		primary_action_label: __('Link'),
		primary_action(values) {
			frappe.call({
				method: 'div_frappe_base.uom.link_uom_families',
				freeze: true,
				freeze_message: __('Linking UOMs...'),
				args: {
					from_uom: values.from_uom,
					to_uom: values.to_uom,
					value: values.value,
					category: values.category,
				},
				callback(r) {
					if (!r.message) return
					const s = r.message
					frappe.show_alert({
						message: s.factors_created
							? __('Linked {0} → {1} ({2} factor created).', [values.from_uom, values.to_uom, s.factors_created])
							: __('Link {0} → {1} already exists.', [values.from_uom, values.to_uom]),
						indicator: s.factors_created ? 'green' : 'blue',
					})
					dialog.hide()
					if (opts.on_complete) opts.on_complete(s)
				},
			})
		},
	})

	dialog.show()
}
