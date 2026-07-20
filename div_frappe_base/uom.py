# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Generic UOM utilities — alias resolution, transitive conversion via the
UOM Conversion Factor graph, display-UOM selection (cleanest engineering
representation), and one-shot SI-prefix family creation.

Lives in div_frappe_base so any app that needs UOM canonicalisation (not just
component sourcing) can import from here. Domain-specific glue (e.g. picking
which Component Specifications to re-resolve when a UOM changes) stays in the
consuming app."""

from __future__ import annotations

import math

import frappe
from pypika import CustomFunction

_Binary = CustomFunction("BINARY", ["expr"])


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


def resolve_uom_from_alias(alias_text: str) -> str | None:
	"""Map `alias_text` (case-sensitive, whitespace-trimmed) to a UOM name.

	Resolution order: direct UOM name → UOM `symbol` field → UOM Alias child
	rows. Names and symbols are the canonical identifiers — checking them
	first means a value like "Ohm" or "Ω" never accidentally hits an alias
	row that some other UOM borrowed. Aliases are the fallback for synonyms
	the source uses (e.g. "ohms", "OHM") that aren't a real UOM identity.

	Case-sensitive throughout: `MΩ` (Megaohm) and `mΩ` (Milliohm) must NOT
	collide. The alias column is utf8mb4_bin (see patch
	uom_alias_case_sensitive); we force BINARY comparison on the SQL side for
	name and symbol lookups too in case the connection-side collation differs.

	Cached per request to keep bulk imports from re-querying per parameter."""
	if not alias_text:
		return None
	stripped = alias_text.strip()
	if not stripped:
		return None

	cache = alias_cache()
	if stripped in cache:
		return cache[stripped]

	uom_table = frappe.qb.DocType("UOM")
	# 1. Direct UOM.name match.
	match = (
		frappe.qb.from_(uom_table)
		.select(uom_table.name)
		.where(_Binary(uom_table.name) == stripped)
		.limit(1)
		.run(pluck=True)
	)
	uom = match[0] if match else None

	# 2. UOM.symbol match (unique-enforced via property_setter).
	if not uom:
		match = (
			frappe.qb.from_(uom_table)
			.select(uom_table.name)
			.where(_Binary(uom_table.symbol) == stripped)
			.limit(1)
			.run(pluck=True)
		)
		uom = match[0] if match else None

	# 3. UOM Alias child rows. Bracket access for the `alias` column —
	# pypika.Table.alias is the table-aliasing attribute (returns None), so
	# dotted access here renders as BINARY(NULL) and silently never matches.
	if not uom:
		alias_table = frappe.qb.DocType("UOM Alias")
		match = (
			frappe.qb.from_(alias_table)
			.select(alias_table.parent)
			.where(_Binary(alias_table["alias"]) == stripped)
			.limit(1)
			.run(pluck=True)
		)
		uom = match[0] if match else None

	cache[stripped] = uom
	return uom


def alias_cache() -> dict[str, str | None]:
	"""Per-request alias cache. Lives on frappe.local so it resets per HTTP
	request / RQ job."""
	cache = getattr(frappe.local, "_uom_alias_cache", None)
	if cache is None:
		cache = {}
		frappe.local._uom_alias_cache = cache
	return cache


# ---------------------------------------------------------------------------
# Conversion + display-UOM selection
# ---------------------------------------------------------------------------


def convert_to_canonical(
	value: float, source_uom: str, canonical_uom: str, *, allow_non_decimal: bool = False
) -> float:
	"""Convert `value` from `source_uom` to `canonical_uom` by walking the
	UOM Conversion Factor graph transitively.

	Stock ERPNext often defines factors via a hub UOM (e.g. Kg ↔ Gram and
	Kg ↔ Milligram, but no direct Gram ↔ Milligram). A direct lookup would
	miss that and return the raw value. reachable_uoms BFS-accumulates the
	chain in either direction, so we just look the source up in the
	canonical's reachable set.

	By default the BFS is restricted to power-of-10 factors so it doesn't
	jump across physical dimensions via Frappe's stock cross-dimension
	factors (e.g. Hz ↔ Wavelength In Kilometres at 299792458). Pass
	`allow_non_decimal=True` to traverse non-power-of-10 edges too — needed
	for conversions like Inch ↔ Millimeter (25.4) that are within a single
	dimension but not metric-prefixed.

	Returns the raw value when source and canonical are the same, or when no
	path connects them (logs a 'missing UOM conversion' warning so the row
	still stores something but is flagged for review)."""
	if not source_uom or not canonical_uom or source_uom == canonical_uom:
		return value

	# reachable_uoms(canonical) returns {uom: factor} where
	# `value_in_uom = value_in_canonical * factor`. Inverting that gives
	# `value_in_canonical = value_in_source / factor[source]`.
	factors = reachable_uoms(canonical_uom, allow_non_decimal=allow_non_decimal)
	source_factor = factors.get(source_uom)
	if source_factor:
		return float(value) / float(source_factor)

	frappe.log_error(
		title="Missing UOM Conversion",
		message=(
			f"No UOM Conversion Factor path between {source_uom!r} and "
			f"{canonical_uom!r}. Value {value!r} stored unconverted."
		),
	)
	return value


def select_display_uom(
	value: float, base_uom: str, *, integer: bool = False, exclude: set[str] | None = None
) -> tuple[float, str]:
	"""Pick the UOM in `base_uom`'s conversion graph that gives the shortest
	decimal representation of `value`. Returns (converted_value, chosen_uom).

	Lets canonicalisation store `0.8 mm` instead of `800 µm`, and `631 nm`
	instead of `0.631 µm`, by minimising the length of the value formatted as
	`%g` with a leading `0` dropped (so `0.65` counts as `.65`, length 3).
	Tiebreaker prefers the larger unit (smaller absolute value), so on a tie
	`0.65 mm` beats `650 µm`. Falls back to (value, base_uom) when nothing
	better is reachable.

	`integer=True` adds a tiebreaker preferring UOMs where the value is closer
	to integer (avoids precision loss when the caller will int-round).

	`exclude` is a per-call set of UOM names the caller never wants chosen
	(e.g. a Canonical Attribute's `excluded_display_uoms`). It is unioned with
	the globally-flagged set (UOM.custom_exclude_from_display) so a blanket
	"never show Centimeter" rule and per-canonical overrides compose. `base_uom`
	itself is always kept as the floor fallback even if excluded — there must be
	a valid display unit."""
	if not base_uom or value is None:
		return value, base_uom
	excluded = global_excluded_display_uoms() | (exclude or set())
	candidates = reachable_uoms(base_uom)
	best_uom = base_uom
	best_value = value
	best_score = display_score(value, integer=integer)
	for uom, factor in candidates.items():
		if uom in excluded:
			continue
		candidate = value * factor
		score = display_score(candidate, integer=integer)
		if score < best_score:
			best_score = score
			best_uom = uom
			best_value = candidate
	return best_value, best_uom


def global_excluded_display_uoms() -> set[str]:
	"""UOM names flagged `custom_exclude_from_display` — never auto-picked as a
	display unit anywhere. Cached per request (resets per HTTP request / RQ
	job). Tolerant of the custom field not existing yet (pre-migrate) so
	imports don't break mid-deploy."""
	cache = getattr(frappe.local, "_uom_display_excluded", None)
	if cache is None:
		try:
			cache = set(
				frappe.get_all(
					"UOM", filters={"custom_exclude_from_display": 1}, pluck="name"
				)
			)
		except Exception:
			cache = set()
		frappe.local._uom_display_excluded = cache
	return cache


def display_score(v: float, *, integer: bool) -> tuple[float, int, float]:
	"""Lower is better. Tuple:
	(precision_loss_penalty, value_string_length, larger_unit_tiebreaker).
	value_string_length is `len(f"{abs(v):g}")` after dropping a leading `0`
	in `0.X` form, so `.8` scores 2 and `800` scores 3, but `.631` scores 4
	and `631` scores 3. larger_unit_tiebreaker is `abs(v)`, so when two UOMs
	tie on length (e.g. `.65` vs `650`) the larger unit wins. precision_loss
	only matters for integer canonicals."""
	if v == 0:
		return (0.0, 1, 0.0)
	s = f"{abs(v):g}"
	if s.startswith("0."):
		s = s[1:]
	precision_loss = abs(v - round(v)) if integer else 0.0
	return (precision_loss, len(s), abs(v))


def reachable_uoms(
	base_uom: str, *, allow_non_decimal: bool = False
) -> dict[str, float]:
	"""BFS from `base_uom` over UOM Conversion Factor (both directions).
	Returns {uom: factor_to_multiply_base_value_by_to_get_value_in_uom}.

	Per the convention `1 from_uom = value to_uom`,
	`value_in_to_uom = value_in_from_uom * value`.

	By default only decimal-prefix factors (≈10ⁿ) are followed so the picker
	doesn't jump across physical dimensions via Frappe's stock cross-dimension
	factors (e.g. Hz ↔ Wavelength In Kilometres at 299792458). Pass
	`allow_non_decimal=True` to also traverse non-power-of-10 edges — needed
	for within-dimension conversions like Inch ↔ Millimeter (25.4)."""
	cache = reachable_cache()
	cache_key = (base_uom, allow_non_decimal)
	if cache_key in cache:
		return cache[cache_key]
	seen = {base_uom: 1.0}
	queue = [base_uom]
	while queue:
		current = queue.pop()
		for f in frappe.get_all(
			"UOM Conversion Factor",
			filters={"from_uom": current},
			fields=["to_uom", "value"],
		):
			if (
				f.to_uom not in seen
				and f.value
				and (allow_non_decimal or is_decimal_factor(float(f.value)))
			):
				seen[f.to_uom] = seen[current] * float(f.value)
				queue.append(f.to_uom)
		for f in frappe.get_all(
			"UOM Conversion Factor",
			filters={"to_uom": current},
			fields=["from_uom", "value"],
		):
			if (
				f.from_uom not in seen
				and f.value
				and (allow_non_decimal or is_decimal_factor(float(f.value)))
			):
				seen[f.from_uom] = seen[current] / float(f.value)
				queue.append(f.from_uom)
	cache[cache_key] = seen
	return seen


def is_decimal_factor(value: float) -> bool:
	"""True when `value` is a power of 10 (within 0.1% tolerance). Filters out
	cross-dimension conversion factors like Hz↔Wavelength (299792458) while
	keeping metric prefix factors (1e3, 1e-6, ...)."""
	if value <= 0:
		return False
	log = math.log10(value)
	return abs(log - round(log)) < 0.001


def reachable_cache() -> dict[str, dict[str, float]]:
	cache = getattr(frappe.local, "_uom_reachable_cache", None)
	if cache is None:
		cache = {}
		frappe.local._uom_reachable_cache = cache
	return cache


# ---------------------------------------------------------------------------
# UOM family creator — base UOM + SI-prefixed siblings + factors + aliases
# ---------------------------------------------------------------------------

# SI prefix family in ascending order of magnitude. The base unit sits in the
# middle (empty prefix). Conversion factors are chained between consecutive
# entries (each step is 1000×) rather than each prefix linking directly to the
# base — UOM Conversion Factor.value is Decimal(21,9), too narrow for ±1e12.
# select_display_uom's BFS accumulates the chain transitively, so the user
# still gets nF↔kF↔TF interconversion.
#
# (prefix_name, prefix_letter, extra_letters)
# extras: alternate single-character prefixes (kilo also accepts 'K', micro
# also accepts 'u' — both common in real-world source data).
SI_PREFIXES: list[tuple[str, str, list[str]]] = [
	("Femto", "f", []),
	("Pico", "p", []),
	("Nano", "n", []),
	("Micro", "µ", ["u"]),
	("Milli", "m", []),
	("", "", []),  # base — no prefix decoration
	("Kilo", "k", ["K"]),
	("Mega", "M", []),
	("Giga", "G", []),
	("Tera", "T", []),
	("Peta", "P", []),
]
_PREFIX_STEP = 1000.0


def prefix_range(
	min_prefix: str = "Femto", max_prefix: str = "Peta"
) -> list[tuple[str, str, list[str]]]:
	"""Slice SI_PREFIXES inclusive of `min_prefix` and `max_prefix`."""
	names = [p[0] for p in SI_PREFIXES]
	options = [n or "(base)" for n in names]
	if min_prefix not in names:
		frappe.throw(f"Invalid min_prefix {min_prefix!r}. Must be one of {options}.")
	if max_prefix not in names:
		frappe.throw(f"Invalid max_prefix {max_prefix!r}. Must be one of {options}.")
	start = names.index(min_prefix)
	end = names.index(max_prefix) + 1
	if start >= end:
		frappe.throw(
			f"min_prefix {min_prefix!r} is not smaller than max_prefix {max_prefix!r}."
		)
	return SI_PREFIXES[start:end]


@frappe.whitelist()
def create_uom_family(
	base_uom: str,
	primary_symbol: str,
	alias_symbols: list[str] | str | None = None,
	category: str | None = None,
	min_prefix: str = "Femto",
	max_prefix: str = "Peta",
) -> dict:
	"""Create a base UOM plus all SI-prefixed siblings in [min_prefix, max_prefix],
	UOM Conversion Factors chaining consecutive prefixes (factor 1000 each),
	and aliases for every (prefix_letter, symbol) combination.

	`min_prefix` / `max_prefix` are SI prefix names — "Femto", "Pico", "Nano",
	"Micro", "Milli", "" (= base), "Kilo", "Mega", "Giga", "Tera", "Peta".
	Use min_prefix="" for discrete units like Bit or Sample/Second where
	sub-base prefixes are nonsensical. Tighten max_prefix for units that
	never appear large (e.g. Henry, Farad → "Kilo").

	Idempotent — re-running fills in anything missing without touching what's
	already there. Safe to run on an existing base UOM to add prefixes or
	additional alias_symbols later.

	Returns counts so the caller can show a summary in the dialog."""
	alias_symbols = (
		frappe.parse_json(alias_symbols)
		if isinstance(alias_symbols, str)
		else (alias_symbols or [])
	)
	all_symbols = [s.strip() for s in [primary_symbol, *alias_symbols] if s and s.strip()]
	if not base_uom or not base_uom.strip():
		frappe.throw("Base UOM name is required.")
	if not all_symbols:
		frappe.throw("At least one symbol is required.")
	base_uom = base_uom.strip()
	category = (category or "").strip() or None

	prefixes = prefix_range(min_prefix, max_prefix)

	stats = {"uoms_created": 0, "factors_created": 0, "aliases_created": 0}

	# Auto-create the UOM Category if missing — saves the user a trip to a
	# separate form just to satisfy the Link field on UOM Conversion Factor.
	if category and not frappe.db.exists("UOM Category", category):
		frappe.get_doc({"doctype": "UOM Category", "category_name": category}).insert(
			ignore_permissions=True
		)

	# Pass 1: ensure every UOM exists and pick up its decorated aliases.
	# Names follow Frappe's "one capitalised word" convention ("Ohm" → "Kiloohm").
	uom_chain: list[str] = []
	for prefix_name, prefix_letter, extras in prefixes:
		if prefix_name:
			prefixed_uom = f"{prefix_name}{base_uom.lower()}"
			prefixed_uom = prefixed_uom[0].upper() + prefixed_uom[1:]
			uom_symbol = f"{prefix_letter}{primary_symbol}"
		else:
			prefixed_uom = base_uom
			uom_symbol = primary_symbol
		uom_chain.append(prefixed_uom)
		ensure_uom(prefixed_uom, uom_symbol, stats)

		if prefix_name:
			decorated = [
				f"{letter}{spacer}{symbol}"
				for letter in [prefix_letter, *extras]
				for symbol in [*all_symbols, base_uom]
				for spacer in ("", " ")
			]
		else:
			decorated = list(all_symbols)
		append_aliases(
			prefixed_uom,
			decorated + name_variants(prefix_name, base_uom, prefixed_uom),
			stats,
		)

	# Pass 2: chain factors. `1 larger = 1000 smaller` for every consecutive
	# pair. Reachability fans out transitively from BFS in reachable_uoms,
	# so a Tera→Pico hop gets resolved as 1e24 without ever storing a factor
	# that overflows Decimal(21,9).
	for smaller_uom, larger_uom in zip(uom_chain, uom_chain[1:]):
		ensure_factor(larger_uom, smaller_uom, _PREFIX_STEP, category, stats)

	return stats


@frappe.whitelist()
def link_uom_families(
	from_uom: str,
	to_uom: str,
	value: float | str,
	category: str | None = None,
) -> dict:
	"""Create one UOM Conversion Factor `1 from_uom = value × to_uom` between
	two existing UOMs, typically across families.

	One edge is enough: `select_display_uom`'s BFS walks UCFs in both
	directions and composes the SI-prefix chain transitively, so e.g. linking
	`Bit ↔ Byte = 1/8` makes Kbps↔KBps, Mbps↔MBps, … all reachable for free.

	Idempotent — no-op if a factor already exists in this direction."""
	if not (from_uom and from_uom.strip()) or not (to_uom and to_uom.strip()):
		frappe.throw("Both from and to UOMs are required.")
	from_uom, to_uom = from_uom.strip(), to_uom.strip()
	if from_uom == to_uom:
		frappe.throw("from and to UOMs must differ.")
	try:
		value = float(value)
	except (TypeError, ValueError):
		frappe.throw(f"Invalid conversion value {value!r}.")
	if value == 0:
		frappe.throw("Conversion factor must be non-zero.")
	for uom in (from_uom, to_uom):
		if not frappe.db.exists("UOM", uom):
			frappe.throw(f"UOM {uom!r} does not exist.")

	category = (category or "").strip() or None
	if category and not frappe.db.exists("UOM Category", category):
		frappe.get_doc({"doctype": "UOM Category", "category_name": category}).insert(
			ignore_permissions=True
		)

	stats = {"factors_created": 0}
	ensure_factor(from_uom, to_uom, value, category, stats)
	return stats


def ensure_uom(name: str, symbol: str, stats: dict) -> None:
	if frappe.db.exists("UOM", name):
		return
	frappe.get_doc(
		{"doctype": "UOM", "uom_name": name, "symbol": symbol, "enabled": 1}
	).insert(ignore_permissions=True)
	stats["uoms_created"] += 1


def ensure_factor(
	from_uom: str, to_uom: str, value: float, category: str | None, stats: dict
) -> None:
	"""Create the from→to factor if it doesn't already exist in this direction.
	`select_display_uom`'s BFS walks both directions, so we don't need both."""
	if from_uom == to_uom:
		return
	if frappe.db.exists("UOM Conversion Factor", {"from_uom": from_uom, "to_uom": to_uom}):
		return
	frappe.get_doc(
		{
			"doctype": "UOM Conversion Factor",
			"from_uom": from_uom,
			"to_uom": to_uom,
			"value": value,
			"category": category,
		}
	).insert(ignore_permissions=True)
	stats["factors_created"] += 1


def name_variants(prefix_name: str, base_uom: str, prefixed_uom: str) -> list[str]:
	"""Casing/spacing/plural permutations of a (prefixed) UOM name.

	For "Mega" + "Ohm" → megaohm, megaohms, mega ohm, mega ohms, MEGAOHM,
	MEGAOHMS, MEGA OHM, MEGA OHMS, Mega ohm, Mega ohms, Mega Ohm, Mega Ohms,
	mega Ohm, mega Ohms (plus PascalCase / camelCase no-space variants).

	Plural-`s` casing tracks the base's casing — `MEGAOHMS`, not `MEGAOHMs`.

	Skips `prefixed_uom` itself — Frappe's resolver falls back to direct UOM
	name match, so the canonical doesn't need an explicit alias row."""
	base_lo, base_hi = base_uom.lower(), base_uom.upper()
	if not prefix_name:
		# Unprefixed base — single token; no spacing/prefix combinatorics.
		# Filter the canonical (e.g. "PPM" vs upper "PPM" → don't alias to self).
		candidates = [base_lo, base_lo + "s", base_hi, base_hi + "S", base_uom + "s"]
		return [v for v in candidates if v != prefixed_uom]

	pre_lo, pre_hi = prefix_name.lower(), prefix_name.upper()
	# (prefix-casing, base-casing, plural-suffix) covering every realistic blend.
	casings = [
		(pre_lo, base_lo, "s"),  # mega ohm / megaohm
		(pre_hi, base_hi, "S"),  # MEGA OHM / MEGAOHM
		(prefix_name, base_lo, "s"),  # Mega ohm / Megaohm (canonical)
		(prefix_name, base_uom, "s"),  # Mega Ohm / MegaOhm
		(pre_lo, base_uom, "s"),  # mega Ohm / megaOhm
	]
	variants = []
	for pre, suf, plural_s in casings:
		for spacer in ("", " "):
			for plural in ("", plural_s):
				variants.append(f"{pre}{spacer}{suf}{plural}")
	return [v for v in variants if v != prefixed_uom]


def append_aliases(uom_name: str, aliases: list[str], stats: dict) -> None:
	"""Append any aliases not already present (case-sensitive) to the UOM's
	custom_aliases child table. Saves once per UOM. Dedup is case-sensitive
	because the underlying column is utf8mb4_bin — `MΩ` (Megaohm) and `mΩ`
	(Milliohm) are intentionally distinct."""
	if not aliases:
		return
	uom_doc = frappe.get_doc("UOM", uom_name)
	existing = {(a.alias or "").strip() for a in uom_doc.get("custom_aliases") or []}
	added = False
	for alias in aliases:
		alias = alias.strip()
		if not alias or alias in existing:
			continue
		uom_doc.append("custom_aliases", {"alias": alias})
		existing.add(alias)
		stats["aliases_created"] += 1
		added = True
	if added:
		uom_doc.save(ignore_permissions=True)
