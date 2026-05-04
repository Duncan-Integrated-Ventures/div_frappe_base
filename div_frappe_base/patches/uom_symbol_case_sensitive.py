# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""One-shot migration: switch tabUOM.symbol to utf8mb4_bin before the
property_setter that adds UNIQUE(symbol) is applied.

Stock Frappe ships UOM.symbol with utf8mb4_unicode_ci collation, which is
case-insensitive — `MΩ` (Megaohm) and `mΩ` (Milliohm) compare equal under
that collation, so a UNIQUE index would reject them as duplicates even
though they're distinct SI-prefix UOMs. Switching to utf8mb4_bin keeps the
column case-significant; the unique index then sees them as different rows.

Mirrors the same fix already applied to UOM Alias.alias by
make_uom_alias_case_sensitive in install.py — the after_install path
applies it for fresh installs; this patch handles existing benches.

Listed under [pre_model_sync] in patches.txt so the column collation is
fixed BEFORE doctype migration tries to add the UNIQUE index from the
property_setter."""

from div_frappe_base.install import make_uom_symbol_case_sensitive


def execute():
	make_uom_symbol_case_sensitive()
