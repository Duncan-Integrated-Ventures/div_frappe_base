// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

frappe.ui.form.on('Import Profile', {
	onload(frm) {
		populate_table_field_options(frm)
	},
	target_doctype(frm) {
		populate_table_field_options(frm)
	},
})

let populate_table_field_options = frm => {
	if (!frm.doc.target_doctype) {
		frm.set_df_property('target_child_table_field', 'options', '')
		frm.refresh_field('target_child_table_field')
		return
	}
	frappe
		.call({
			method: 'div_frappe_base.div_frappe_base.doctype.import_profile.import_profile.list_table_fields',
			args: { target_doctype: frm.doc.target_doctype },
		})
		.then(r => {
			let fields = r.message || []
			let options = ['', ...fields.map(f => f.fieldname)]
			frm.set_df_property('target_child_table_field', 'options', options.join('\n'))
			frm.refresh_field('target_child_table_field')
		})
}
