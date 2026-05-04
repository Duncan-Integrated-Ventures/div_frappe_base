// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

;(function () {
	const settings = (frappe.listview_settings['UOM'] = frappe.listview_settings['UOM'] || {})
	const original_onload = settings.onload

	settings.onload = function (listview) {
		if (original_onload) original_onload(listview)

		const group = __('UOM Manager')
		listview.page.add_inner_button(
			__('Create UOM Family'),
			function () {
				div_frappe_base.show_uom_family_dialog({
					on_complete: () => listview.refresh(),
				})
			},
			group
		)
		listview.page.add_inner_button(
			__('Link UOM Families'),
			function () {
				div_frappe_base.show_uom_link_dialog({
					on_complete: () => listview.refresh(),
				})
			},
			group
		)
	}
})()
