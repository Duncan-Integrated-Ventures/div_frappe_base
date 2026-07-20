# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Unit tests for select_display_uom's exclusion handling. Pure-function tests:
the two DB-touching helpers (reachable_uoms, global_excluded_display_uoms) are
monkeypatched so the picker logic is exercised without a live site."""

import unittest

from div_frappe_base import uom


class TestSelectDisplayUOMExclusion(unittest.TestCase):
	def setUp(self):
		# Meter graph: cm is ×100, mm is ×1000 relative to the base.
		self._orig_reachable = uom.reachable_uoms
		self._orig_excluded = uom.global_excluded_display_uoms
		uom.reachable_uoms = lambda base, **kw: {
			"Meter": 1.0,
			"Centimeter": 100.0,
			"Millimeter": 1000.0,
			"Micrometer": 1_000_000.0,
		}

	def tearDown(self):
		uom.reachable_uoms = self._orig_reachable
		uom.global_excluded_display_uoms = self._orig_excluded

	def _set_global(self, excluded):
		uom.global_excluded_display_uoms = lambda: set(excluded)

	def test_picks_cm_without_exclusion(self):
		# 3.2 mm: "3.2" (mm) and ".32" (cm) tie at 3 chars → larger unit (cm) wins.
		self._set_global(set())
		value, picked = uom.select_display_uom(0.0032, "Meter")
		self.assertEqual(picked, "Centimeter")
		self.assertAlmostEqual(value, 0.32)

	def test_global_exclusion_forces_mm(self):
		self._set_global({"Centimeter"})
		value, picked = uom.select_display_uom(0.0032, "Meter")
		self.assertEqual(picked, "Millimeter")
		self.assertAlmostEqual(value, 3.2)

	def test_per_call_exclusion_unions_with_global(self):
		self._set_global(set())
		value, picked = uom.select_display_uom(0.0032, "Meter", exclude={"Centimeter"})
		self.assertEqual(picked, "Millimeter")
		self.assertAlmostEqual(value, 3.2)

	def test_base_uom_kept_when_everything_excluded(self):
		# Even if every reachable alt is excluded, the base UOM survives as floor.
		self._set_global({"Centimeter", "Millimeter", "Micrometer"})
		value, picked = uom.select_display_uom(0.0032, "Meter")
		self.assertEqual(picked, "Meter")
		self.assertAlmostEqual(value, 0.0032)


if __name__ == "__main__":
	unittest.main()
