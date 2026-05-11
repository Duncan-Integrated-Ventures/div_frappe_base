# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Runtime patches against framework / ERPNext internals.

Imported from div_frappe_base/__init__.py so the patches install once when
Frappe loads installed apps. Each patch is gated on the upstream symbol
existing so an app upgrade that removes the target won't crash boot."""

from __future__ import annotations

import inspect

import frappe
from frappe.utils import flt


def _patch_filter_pricing_rule_based_on_condition() -> None:
	"""Expose the row currently being priced to a Pricing Rule's `condition`.

	Stock erpnext.accounts.doctype.pricing_rule.utils.filter_pricing_rule_based_on_condition
	evaluates each rule's condition against doc.as_dict() — i.e. the parent
	document only. Conditions that need to differentiate by row-level fields
	(supplier_part_no, custom_supplier_packaging_type, etc.) over-match when
	the parent contains multiple rows whose values together satisfy multiple
	rules' conditions, surfacing as MultiplePricingRuleConflict.

	This patch walks the call stack from the filter back to get_pricing_rules
	(the only frame in the standard apply-pricing-rule path that holds the
	per-row args dict) and binds it as `current_item` in the eval scope.
	Conditions can then say:

	    current_item.get('supplier_part_no') == 'X'

	and apply only when the row currently being priced matches.

	When args isn't reachable (e.g. apply_pricing_rule_on_transaction calls
	the filter directly with no row context), behavior is identical to
	upstream — `current_item` is simply absent from locals and any condition
	that references it raises, which the existing try/except already swallows
	by skipping that rule.
	"""
	try:
		from erpnext.accounts.doctype.pricing_rule import utils as pr_utils
	except ImportError:
		return

	def filter_pricing_rule_based_on_condition(pricing_rules, doc=None):
		if not doc:
			return pricing_rules
		current_item = None
		frame = inspect.currentframe()
		frame = frame.f_back if frame else None
		try:
			while frame is not None:
				if frame.f_code.co_name == "get_pricing_rules":
					candidate = frame.f_locals.get("args")
					if isinstance(candidate, dict):
						current_item = candidate
					break
				frame = frame.f_back
		finally:
			del frame
		eval_locals = doc.as_dict()
		if current_item is not None:
			eval_locals["current_item"] = current_item
		filtered = []
		for rule in pricing_rules:
			if rule.condition:
				try:
					if frappe.safe_eval(rule.condition, None, eval_locals):
						filtered.append(rule)
				except Exception:
					pass
			else:
				filtered.append(rule)
		return filtered

	pr_utils.filter_pricing_rule_based_on_condition = (
		filter_pricing_rule_based_on_condition
	)


_TRANSACTION_DOCTYPES = {
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"POS Invoice",
}


def _patch_calculate_item_values() -> None:
	"""Close two gaps in upstream calculate_item_values:

	1. SQ Item is excluded from the doctype whitelist that gates the margin
	   block — Pricing Rule margin never propagates to the row, never folds
	   into rate. Patch runs ERPNext's calculate_margin for SQ so it behaves
	   like the other transaction doctypes (margin_type / margin_rate_or_amount
	   / rate_with_margin populated, item.rate folds in margin).

	2. No transaction doctype recognizes per-line charges
	   (custom_per_line_charge), our addition for flat per-row fees
	   (DigiReeling, MouseReel) that don't scale with qty. Patch sums them
	   across the row's linked rules and adds once to net_amount, after the
	   per-unit margin / discount math has already settled.

	Margin remains ERPNext's responsibility — by the time the per-line block
	runs, item.rate already reflects margin (either via upstream's whitelisted
	branch, or via the SQ block above). The patch only owns the per-line
	addition; div_frappe_base.pricing.per_line_charge_for_item is the only
	place that touches the new custom field."""
	try:
		from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
	except ImportError:
		return

	original = calculate_taxes_and_totals.calculate_item_values

	def calculate_item_values(self) -> None:
		# Lazy imports: ERPNext modules pull in DocTypes that aren't ready at
		# div_frappe_base load time, and pricing.py is in the same app so a
		# top-level import would form a load cycle through __init__.py.
		from div_frappe_base.pricing import per_line_charge_for_item

		original(self)
		if (
			self.doc.get("is_consolidated")
			or getattr(self, "discount_amount_applied", False)
			or self.doc.doctype not in _TRANSACTION_DOCTYPES
		):
			return

		for item in self.doc.items:
			# SQ-specific: fold margin into rate (upstream's whitelist excludes
			# Supplier Quotation Item — without this, item.rate stays at the
			# bare price_list_rate even when a margin-bearing rule applies).
			if self.doc.doctype == "Supplier Quotation" and item.price_list_rate:
				item.rate_with_margin, item.base_rate_with_margin = self.calculate_margin(item)
				if flt(item.rate_with_margin) > 0:
					item.rate = flt(
						item.rate_with_margin * (1.0 - flt(item.discount_percentage) / 100.0),
						item.precision("rate"),
					)
					item.amount = flt(item.rate * item.qty, item.precision("amount"))
					item.net_rate = item.rate
					item.net_amount = item.amount
			# Per-line charge addition (all transaction doctypes). item.rate
			# is authoritative here — ERPNext (or the SQ block above) already
			# folded margin in.
			charge = per_line_charge_for_item(item)
			if charge:
				item.amount = flt(item.amount + charge, item.precision("amount"))
				item.net_amount = item.amount
				self._set_in_company_currency(item, ["amount", "net_amount"])

	calculate_taxes_and_totals.calculate_item_values = calculate_item_values


def install() -> None:
	_patch_filter_pricing_rule_based_on_condition()
	_patch_calculate_item_values()


install()
