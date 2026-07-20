# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document


class LabelLayoutFormat(Document):
	def validate(self):
		self._validate_regions()

	def _validate_regions(self):
		"""Reject region boxes outside the unit square and malformed per-region
		regexes early, so a bad layout can't wedge the extraction pipeline."""
		for row in self.field_regions:
			for coord in ("x", "y", "w", "h"):
				val = row.get(coord) or 0
				if val < 0 or val > 1:
					frappe.throw(
						f"Region for {row.field_key!r}: {coord} = {val} must be a fraction "
						f"between 0 and 1 (of the reference image)."
					)
			if (row.x or 0) + (row.w or 0) > 1.0001 or (row.y or 0) + (row.h or 0) > 1.0001:
				frappe.throw(f"Region for {row.field_key!r} extends past the image edge.")
			if row.regex:
				try:
					re.compile(row.regex)
				except re.error as exc:
					frappe.throw(f"Region for {row.field_key!r}: invalid regex — {exc}.")
