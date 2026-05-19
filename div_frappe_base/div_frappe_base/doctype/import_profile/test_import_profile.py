# Copyright (c) 2026, Duncan Integrated Ventures LLC and Contributors
# See license.txt

import unittest

from frappe.tests import IntegrationTestCase

from div_frappe_base.div_frappe_base.doctype.import_profile.import_profile import (
	extract_columns_and_data,
	merge_multiword_header_tokens,
)


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestImportProfile(IntegrationTestCase):
	"""
	Integration tests for ImportProfile.
	Use this class for testing interactions between multiple components.
	"""

	pass


class FixedWidthParserTests(unittest.TestCase):
	"""Unit tests for `extract_columns_and_data` against fixed-width (.txt) input.

	The placement-file shape is the motivating case: headers like `Mid X` would
	otherwise split into two columns, and rows with overflowing values or
	multi-word Comments would mis-slice.
	"""

	def test_pick_and_place_multiword_headers(self):
		"""`Mid X`, `Mid Y`, `Pad Y TB` (with TB as its own column) all parse correctly."""
		rows = [
			"Designator Footprint               Mid X         Mid Y         Ref X         Ref Y         Pad X         Pad Y TB      Rotation Comment        ",
			"",
			"C6         CAP_EIA_7343-31   5102.499mil     1892.5mil   5102.499mil     1892.5mil   5102.499mil   2014.547mil  T        360.00 CAP_POLYMER_150UF_6.3V_20%_7343-30",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		self.assertEqual(
			columns,
			[
				"Designator",
				"Footprint",
				"Mid X",
				"Mid Y",
				"Ref X",
				"Ref Y",
				"Pad X",
				"Pad Y",
				"TB",
				"Rotation",
				"Comment",
			],
		)
		# data[0] is None (the blank line); data[1] is the first real row.
		row = dict(zip(columns, data[1], strict=True))
		self.assertEqual(row["Designator"], "C6")
		self.assertEqual(row["Footprint"], "CAP_EIA_7343-31")
		self.assertEqual(row["Mid X"], "5102.499mil")
		self.assertEqual(row["Pad Y"], "2014.547mil")
		self.assertEqual(row["TB"], "T")
		self.assertEqual(row["Comment"], "CAP_POLYMER_150UF_6.3V_20%_7343-30")

	def test_long_value_overflowing_header_column_width(self):
		"""A Footprint wider than the header's column allocation must not bleed
		into the next column. Token-based slicing handles this where pure
		position-based slicing at header offsets would truncate the Footprint."""
		rows = [
			"Designator Footprint               Mid X         Mid Y         Ref X         Ref Y         Pad X         Pad Y TB      Rotation Comment        ",
			"C50        CAP_RADIAL_10MM_DIA_12.5MM_LEN_5MM_LEADSPACE   5129.999mil       1505mil   5129.999mil       1505mil   5031.574mil       1505mil  T         90.00 CAP_ALUM_1000UF_6.3V_20%_RADIAL_10x12.5MM",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		row = dict(zip(columns, data[0], strict=True))
		self.assertEqual(row["Footprint"], "CAP_RADIAL_10MM_DIA_12.5MM_LEN_5MM_LEADSPACE")
		self.assertEqual(row["Mid X"], "5129.999mil")
		self.assertEqual(row["Comment"], "CAP_ALUM_1000UF_6.3V_20%_RADIAL_10x12.5MM")

	def test_last_column_absorbs_internal_spaces(self):
		"""Comments like `R TAIL` (two whitespace-separated tokens) must stay
		whole in the last column rather than landing as one token."""
		rows = [
			"Designator Footprint               Mid X         Mid Y         Ref X         Ref Y         Pad X         Pad Y TB      Rotation Comment        ",
			"CN18       JST_PA_B03B-PASK-1   4740.787mil    573.465mil   4789.999mil        540mil   4789.999mil        540mil  T        180.00 R TAIL         ",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		row = dict(zip(columns, data[0], strict=True))
		self.assertEqual(row["Comment"], "R TAIL")
		self.assertEqual(row["TB"], "T")
		self.assertEqual(row["Rotation"], "180.00")

	def test_single_word_headers_unchanged(self):
		"""When header tokens match data token count exactly, the parser must
		preserve the existing position-based slicing — no spurious merging of
		valid columns like `Designator`/`Footprint`/`Side`."""
		rows = [
			"Designator Footprint Side",
			"R1         R0603     T   ",
			"C1         C0805     B   ",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		self.assertEqual(columns, ["Designator", "Footprint", "Side"])
		self.assertEqual(data[0], ["R1", "R0603", "T"])
		self.assertEqual(data[1], ["C1", "C0805", "B"])

	def test_sparse_section_header_unchanged(self):
		"""`[PLACEMENTS]` headers (one token) followed by dense data rows must
		still use the data-row offsets — this is the pre-existing branch and
		shouldn't regress."""
		rows = [
			"[PLACEMENTS]",
			"R1 1.0 2.0 90  T",
			"R2 3.0 4.0 180 B",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		# First column inherits the [PLACEMENTS] name; the rest are positional.
		self.assertEqual(columns[0], "[PLACEMENTS]")
		self.assertEqual(len(columns), 5)
		self.assertEqual(data[0], ["R1", "1.0", "2.0", "90", "T"])

	def test_single_letter_standalone_columns_not_merged(self):
		"""A legitimate `A B C` schema where every column happens to be a single
		letter must not be collapsed. The safeguard: only attempt multi-word
		merging when header tokens outnumber data tokens, which doesn't hold
		when every header token has a corresponding data value."""
		rows = [
			"A B C",
			"1 2 3",
			"4 5 6",
		]
		columns, data = extract_columns_and_data(rows, 0, "fixed")
		self.assertEqual(columns, ["A", "B", "C"])
		self.assertEqual(data[0], ["1", "2", "3"])

	def test_merge_helper_isolated(self):
		"""Direct check of the merge heuristic boundaries."""
		# `Mid X` (gap 1, single-char) → merged.
		self.assertEqual(
			merge_multiword_header_tokens([("Mid", 0), ("X", 4)]),
			[("Mid X", 0)],
		)
		# `Designator Footprint` (gap 1, but `Footprint` is 9 chars) → kept apart.
		self.assertEqual(
			merge_multiword_header_tokens([("Designator", 0), ("Footprint", 11)]),
			[("Designator", 0), ("Footprint", 11)],
		)
		# `Pad Y TB` — `Y` merges into `Pad`, `TB` stays separate.
		self.assertEqual(
			merge_multiword_header_tokens([("Pad", 0), ("Y", 4), ("TB", 6)]),
			[("Pad Y", 0), ("TB", 6)],
		)
		# Two-space gap doesn't merge (would change the visual layout's intent).
		self.assertEqual(
			merge_multiword_header_tokens([("Mid", 0), ("X", 5)]),
			[("Mid", 0), ("X", 5)],
		)
