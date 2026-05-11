# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Asset → Location identity link.

When an `Asset.linked_location` is set, the Asset *is* that Location — the
Location's tree position is owned by the Asset's location, not by direct
edits on the Location form. Two hooks enforce that contract:

  - `Asset Movement → on_submit / on_cancel → reparent_linked_locations`
    re-parents each row's linked Location to track the moved Asset's new
    `Asset.location`.
  - `Location → validate → validate_linked_location_parent_change` refuses
    a direct `parent_location` edit while a linked Asset exists, telling
    the operator to use Asset Movement instead.

Typical use: a Placement Machine Asset's `linked_location` is the machine's
container Location, whose children are the machine's slot Locations. Moving
the machine via Asset Movement carries every slot (and the feeders sitting
in those slots) with it — no per-slot Asset Movement rows needed.
"""

import frappe
from frappe import _


def reparent_linked_locations(doc, method):
	"""For every row in `doc.assets`, if the moved Asset has a `linked_location`
	set, re-parent that Location to the Asset's current `location` (which the
	inherited `set_latest_location_and_custodian_in_asset` has already updated
	to the row's target on `on_submit`, or to the previous-latest movement's
	target on `on_cancel`).

	Uses the `from_asset_movement` flag to bypass
	`validate_linked_location_parent_change` for this save.
	"""
	for row in doc.assets:
		linked_location = frappe.db.get_value("Asset", row.asset, "linked_location")
		if not linked_location:
			continue
		new_parent = frappe.db.get_value("Asset", row.asset, "location")
		current_parent = frappe.db.get_value("Location", linked_location, "parent_location")
		if current_parent == new_parent:
			continue
		loc = frappe.get_doc("Location", linked_location)
		loc.parent_location = new_parent
		loc.flags.from_asset_movement = True
		loc.save(ignore_permissions=True)


def validate_linked_location_parent_change(doc, method):
	"""Refuse a direct edit of `Location.parent_location` while any Asset has
	this Location set as its `linked_location`. The user must run an Asset
	Movement on the linked Asset(s) instead.

	`reparent_linked_locations` sets `doc.flags.from_asset_movement` to bypass
	this check during the automated re-parent.
	"""
	if doc.flags.from_asset_movement:
		return
	if doc.is_new():
		return
	if not doc.has_value_changed("parent_location"):
		return
	linked_assets = frappe.get_all(
		"Asset",
		filters={"linked_location": doc.name},
		pluck="name",
	)
	if not linked_assets:
		return
	frappe.throw(
		_(
			"Location {0} is the linked Location of Asset(s) {1}. "
			"Run an Asset Movement on the linked Asset to move this Location — "
			"a direct parent change is not allowed."
		).format(doc.name, ", ".join(linked_assets))
	)
