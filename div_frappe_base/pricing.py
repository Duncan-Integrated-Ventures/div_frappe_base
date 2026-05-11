# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Pricing helpers for the per-line charge custom field.

Two separable concerns, two helpers:

  - per_line_charge_for_item: sums custom_per_line_charge across the row's
    linked Pricing Rules. Used by the calculate_item_values patch to add the
    flat fee to net_amount. The patch leverages ERPNext's own calculate_margin
    for the per-unit margin side — margin is ERPNext's responsibility, not
    ours, and item.rate already reflects it by the time the patch runs.

  - effective_per_unit_rate: computes the LP's effective per-unit cost from
    a raw rule and base_rate. The LP has no item-doc context, so can't
    delegate margin to calculate_margin — the math is replicated here.
    Result matches what the patched calculate_item_values + ERPNext's flow
    produces for the same (rule, base_rate, qty), so LP cost comparison and
    saved row totals stay consistent.
"""

from __future__ import annotations

from typing import Union

import frappe
from frappe.model.document import Document

PricingRuleLike = Union[str, Document, dict, None]


def per_line_charge_for_item(item) -> float:
	"""Return the sum of custom_per_line_charge across the row's linked
	Pricing Rules. Returns 0 when no rules are linked or none carry a
	per-line charge."""
	from erpnext.accounts.doctype.pricing_rule.utils import get_applied_pricing_rules

	if not item.get("pricing_rules"):
		return 0.0
	total = 0.0
	for rule_name in get_applied_pricing_rules(item.pricing_rules) or []:
		rule = frappe.get_cached_doc("Pricing Rule", rule_name)
		total += float(rule.get("custom_per_line_charge") or 0)
	return total


def effective_per_unit_rate(
	rule: PricingRuleLike, base_rate: float, qty: float
) -> float:
	"""Effective per-unit rate at `qty` under `rule`.

	    rate_with_margin + per_line_charge / qty
	  = (base_rate + per_unit_margin) + per_line_charge / qty

	Replicates ERPNext's calculate_margin per-unit math because the LP can't
	call it directly (no item-doc context). Equivalent to what the patched
	calculate_item_values produces, divided by qty.

	`rule` may be a name (cached fetch), Document, dict, or None (returns
	`base_rate` unchanged)."""
	if isinstance(rule, str):
		rule = frappe.get_cached_doc("Pricing Rule", rule)
	rate = float(base_rate or 0)
	if not rule:
		return rate
	margin = float(rule.get("margin_rate_or_amount") or 0)
	if margin:
		if rule.get("margin_type") == "Amount":
			rate += margin
		elif rule.get("margin_type") == "Percentage":
			rate += rate * margin / 100.0
	per_line = float(rule.get("custom_per_line_charge") or 0)
	if per_line and qty:
		rate += per_line / float(qty)
	return rate
