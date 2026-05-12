# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Install hooks for div_frappe_base."""

import json
import os

import frappe

AI_PROFILE_SEEDS = [
	{"profile_name": "vision", "timeout": 60},
	{"profile_name": "categorizer", "timeout": 60},
	{"profile_name": "datasheet", "timeout": 120},
]

EMBED_WORKER_CONFIG = {
	"queues": ["embed"],
	"num_workers": 1,
	"background_workers": 1,
}


def after_install():
	make_uom_alias_case_sensitive()
	make_uom_symbol_case_sensitive()
	seed_ai_profiles()
	ensure_embed_worker_config()


def ensure_embed_worker_config():
	"""Pin the `embed` RQ worker to one process in `common_site_config.json`.

	The vector-search infrastructure ships a sentence-transformer model that's
	~600 MB resident; a second worker would re-load it and blow the 4 GB
	sizing budget. Pinning `num_workers = 1` on the `embed` queue keeps the
	model load to one process bench-wide. See `docs/design.md` § Vector
	Search Infrastructure.

	Idempotent — leaves an operator-configured value untouched. Re-running is
	a no-op once the entry is present."""
	common_path = common_site_config_path()
	if not common_path or not os.path.exists(common_path):
		# Pre-install state on some images; the hook re-runs on the next
		# bench install-app, so silently skip rather than throw.
		return

	with open(common_path) as fh:
		config = json.load(fh)

	workers = dict(config.get("workers") or {})
	existing = workers.get("embed")
	if isinstance(existing, dict) and existing.get("num_workers") == 1:
		return

	workers["embed"] = EMBED_WORKER_CONFIG
	frappe.installer.update_site_config(
		"workers",
		workers,
		validate=False,
		site_config_path=common_path,
	)


def common_site_config_path() -> str | None:
	"""Path to `<bench>/sites/common_site_config.json`. The bench root is
	two levels up from the active site directory; falling back to
	`frappe.utils.get_bench_path` keeps this working in unusual layouts."""
	try:
		bench_path = frappe.utils.get_bench_path()
	except Exception:
		bench_path = None
	if not bench_path:
		bench_path = os.path.realpath(
			os.path.join(frappe.get_app_path("frappe"), "..", "..", "..")
		)
	return os.path.join(bench_path, "sites", "common_site_config.json")


def seed_ai_profiles():
	"""Ensure the three default AI Profile rows exist on AI Settings.

	Idempotent — checks for an existing row by profile_name before appending,
	and never overwrites an operator-configured row. Safe to invoke from
	`bench execute` if an operator wants to re-seed missing rows on an
	existing bench."""
	settings = frappe.get_single("AI Settings")
	existing = {row.profile_name for row in settings.profiles}
	added = False
	for seed in AI_PROFILE_SEEDS:
		if seed["profile_name"] in existing:
			continue
		settings.append(
			"profiles",
			{
				"profile_name": seed["profile_name"],
				"provider": "Gemini",
				"model": "gemini-2.5-flash",
				"temperature": 0.1,
				"timeout": seed["timeout"],
			},
		)
		added = True
	if added:
		settings.save(ignore_permissions=True)


def make_uom_symbol_case_sensitive():
	"""Switch tabUOM.symbol to utf8mb4_bin so the upcoming UNIQUE index on it
	doesn't false-positive across case-different SI prefixes (MA Megaampere vs
	mA Milliampere, MΩ Megaohm vs mΩ Milliohm, etc.). Same rationale as
	make_uom_alias_case_sensitive but for the parent UOM table's symbol
	column. Idempotent.

	Symbol is nullable in stock ERPNext (no reqd=1 on the DocField), so the
	ALTER preserves NULL — many UOMs ship without a symbol set. The unique
	index cooperates: MySQL allows multiple NULLs in a UNIQUE index."""
	if frappe.db.db_type != "mariadb":
		return

	current = frappe.db.sql(
		"""
		SELECT collation_name
		FROM information_schema.columns
		WHERE table_schema = DATABASE()
		  AND table_name = 'tabUOM'
		  AND column_name = 'symbol'
		""",
		as_dict=True,
	)
	if not current or current[0].collation_name == "utf8mb4_bin":
		return

	frappe.db.sql(
		"""
		ALTER TABLE `tabUOM`
		MODIFY symbol VARCHAR(140)
		CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL
		"""
	)


def make_uom_alias_case_sensitive():
	"""Switch the UOM Alias `alias` column to a case-sensitive collation.

	Default Frappe column collation (utf8mb4_unicode_ci) treats `MΩ` (Megaohm)
	and `mΩ` (Milliohm) as equal — fatal for SI prefix families where case
	distinguishes Mega/Milli, Kilo/(none), Tera/etc. The unique index on the
	column then rejects the second insert.

	Switching to utf8mb4_bin makes the unique index case-sensitive while keeping
	the index itself in place (so `alias` is still globally unique, just
	case-significant). Idempotent — re-running is a no-op once the collation is
	already utf8mb4_bin.
	"""
	if frappe.db.db_type != "mariadb":
		return

	current = frappe.db.sql(
		"""
		SELECT collation_name
		FROM information_schema.columns
		WHERE table_schema = DATABASE()
		  AND table_name = 'tabUOM Alias'
		  AND column_name = 'alias'
		""",
		as_dict=True,
	)
	if not current or current[0].collation_name == "utf8mb4_bin":
		return

	frappe.db.sql(
		"""
		ALTER TABLE `tabUOM Alias`
		MODIFY alias VARCHAR(140)
		CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
		"""
	)
