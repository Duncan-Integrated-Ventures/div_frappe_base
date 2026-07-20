# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Generic photo-of-label extraction: OCR + barcode decode → labeled fields.

App-agnostic. Given a photo and a `Label Layout Format`, returns the value of
each configured field region — read as OCR text or a decoded barcode — with
per-field confidence and source, cross-verifying a field that a layout captures
both ways. Knows nothing about part numbers, suppliers, or any domain: the
field keys are whatever the layout defines, and the caller maps them.

Pipeline (`extract_label_fields`):
  1. Decode every barcode in the image with zxing-cpp (Code 128 + Data Matrix +
     QR in one pass).
  2. OCR the image via `div_frappe_base.ocr.client.recognize` (engine chosen on
     OCR Settings).
  3. Resolve a `Label Layout Format` — explicit name, else best text-anchor
     match against the OCR text.
  4. Extract each region: Text → OCR lines whose center is inside the box;
     Barcode → decoded barcodes inside the box. Apply the region's regex.
  5. When a layout captures one field key via both a Text and a Barcode region,
     cross-verify: the barcode wins (errorless), a mismatch is flagged.

When no layout matches, `fields` is empty but the raw `ocr_text`, `ocr_lines`,
and `barcodes` come back so the caller can apply its own heuristics. The caller
is expected to pass a *rectified* label crop; region fractions are interpreted
against the image as received.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import frappe


@dataclass
class FieldValue:
	value: str | None = None
	confidence: float = 0.0
	source: str | None = None  # "text" | "barcode" | "barcode+text"

	def as_dict(self) -> dict[str, Any]:
		return {"value": self.value, "confidence": self.confidence, "source": self.source}


@dataclass
class LabelExtraction:
	layout: str | None = None
	fields: dict[str, FieldValue] = field(default_factory=dict)
	barcodes: list[dict] = field(default_factory=list)
	ocr_text: str = ""
	ocr_lines: list[dict] = field(default_factory=list)
	warnings: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			"layout": self.layout,
			"fields": {k: v.as_dict() for k, v in self.fields.items()},
			"barcodes": self.barcodes,
			"ocr_text": self.ocr_text,
			"ocr_lines": self.ocr_lines,
			"warnings": self.warnings,
		}


def extract_label_fields(
	image_bytes: bytes,
	*,
	mime_type: str = "image/jpeg",
	engine_name: str = "default",
	layout_name: str | None = None,
) -> LabelExtraction:
	"""Extract labeled fields from a captured photo. See module docstring."""
	width, height = _image_size(image_bytes)
	barcodes = _decode_barcodes(image_bytes)
	ocr = _run_ocr(engine_name, image_bytes, mime_type)

	result = LabelExtraction(
		barcodes=[_barcode_public(b) for b in barcodes],
		ocr_text=ocr.full_text if ocr else "",
		ocr_lines=[{"text": l.text, "confidence": l.confidence, "box": l.box} for l in ocr.lines]
		if ocr
		else [],
	)

	layout = _resolve_layout(layout_name, result.ocr_text)
	if not layout:
		return result

	result.layout = layout.layout_name
	result.fields = _extract_by_regions(layout, ocr, barcodes, width, height, result.warnings)
	return result


# --------------------------------------------------------------------------
# Barcode decode (zxing-cpp)
# --------------------------------------------------------------------------


def _decode_barcodes(image_bytes: bytes) -> list:
	"""Return raw zxing-cpp Barcode objects, or [] if the library / image is
	unavailable. Never raises — barcode decode is best-effort."""
	try:
		import zxingcpp
		from PIL import Image
	except ImportError:
		return []
	try:
		image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
		return list(zxingcpp.read_barcodes(image))
	except Exception:  # noqa: BLE001 — a bad frame shouldn't fail the whole extraction
		frappe.log_error(title="Label barcode decode failed", message=frappe.get_traceback())
		return []


def _barcode_public(bc) -> dict:
	return {
		"text": getattr(bc, "text", None),
		"format": str(getattr(bc, "format", "")),
		"center": _barcode_center(bc),
	}


def _barcode_center(bc) -> list | None:
	pos = getattr(bc, "position", None)
	if not pos:
		return None
	pts = []
	for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
		p = getattr(pos, name, None)
		if p is not None:
			pts.append((p.x, p.y))
	if not pts:
		return None
	return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------


def _run_ocr(engine_name: str, image_bytes: bytes, mime_type: str):
	from div_frappe_base.ocr.client import recognize

	return recognize(engine_name, image_bytes, mime_type=mime_type, raise_exception=False)


# --------------------------------------------------------------------------
# Layout resolution
# --------------------------------------------------------------------------


def _resolve_layout(layout_name: str | None, ocr_text: str):
	"""Explicit name → best text-anchor match. Returns the loaded doc or None.
	Supplier/domain-preference resolution is the caller's job — pass a
	`layout_name` if you've already picked one."""
	if layout_name:
		return frappe.get_cached_doc("Label Layout Format", layout_name)
	return _match_layout_by_anchors(ocr_text)


def _match_layout_by_anchors(ocr_text: str):
	"""Score each enabled layout by how many of its match_anchors appear in the
	OCR text; best wins (ties break on region count). None if nothing scores."""
	if not ocr_text:
		return None
	haystack = ocr_text.lower()
	best = None
	best_score = 0
	best_regions = -1
	for row in frappe.get_all(
		"Label Layout Format",
		filters={"enabled": 1, "match_anchors": ["is", "set"]},
		fields=["name"],
	):
		doc = frappe.get_cached_doc("Label Layout Format", row.name)
		anchors = [a.strip().lower() for a in (doc.match_anchors or "").splitlines() if a.strip()]
		score = sum(1 for a in anchors if a in haystack)
		if score == 0:
			continue
		regions = len(doc.field_regions)
		if score > best_score or (score == best_score and regions > best_regions):
			best, best_score, best_regions = doc, score, regions
	return best


# --------------------------------------------------------------------------
# Region-based extraction + cross-verification
# --------------------------------------------------------------------------


def _extract_by_regions(layout, ocr, barcodes, width, height, warnings) -> dict[str, FieldValue]:
	"""Read each region, group candidates by field_key, then merge. When a key
	has both a text and a barcode candidate, the barcode wins and a mismatch is
	warned."""
	if not width or not height:
		return {}

	candidates: dict[str, list[FieldValue]] = {}
	for region in layout.field_regions:
		rect = (region.x * width, region.y * height, region.w * width, region.h * height)
		if region.source == "Barcode":
			value, conf, src = *_barcode_in_rect(barcodes, rect, region.barcode_format), "barcode"
		else:
			value, conf, src = *_text_in_rect(ocr, rect), "text"
		value = _apply_regex(value, region.regex)
		if value:
			candidates.setdefault(region.field_key, []).append(FieldValue(value, conf, src))

	fields: dict[str, FieldValue] = {}
	for key, cands in candidates.items():
		fields[key] = _merge_candidates(key, cands, warnings)
	return fields


def _merge_candidates(key: str, cands: list[FieldValue], warnings: list[str]) -> FieldValue:
	"""Barcode-wins merge with mismatch warning."""
	barcode = next((c for c in cands if c.source == "barcode"), None)
	text = next((c for c in cands if c.source == "text"), None)
	if barcode and text:
		if _norm(barcode.value) != _norm(text.value):
			warnings.append(
				f"{key}: barcode read {barcode.value!r} but text read {text.value!r} — "
				f"using the barcode; verify."
			)
			return FieldValue(barcode.value, 1.0, "barcode")
		return FieldValue(barcode.value, 1.0, "barcode+text")
	return barcode or text or cands[0]


def _text_in_rect(ocr, rect) -> tuple[str | None, float]:
	"""Concatenate OCR lines whose box center falls inside `rect`, top-to-bottom
	then left-to-right. Confidence is the min across the contributing lines."""
	if not ocr:
		return None, 0.0
	rx, ry, rw, rh = rect
	inside = []
	for line in ocr.lines:
		c = _box_center(line.box)
		if c is None:
			continue
		if rx <= c[0] <= rx + rw and ry <= c[1] <= ry + rh:
			inside.append((c[1], c[0], line))
	if not inside:
		return None, 0.0
	inside.sort(key=lambda t: (round(t[0] / 10), t[1]))
	text = " ".join(t[2].text for t in inside).strip()
	conf = min(t[2].confidence for t in inside)
	return (text or None), conf


def _barcode_in_rect(barcodes, rect, fmt_filter) -> tuple[str | None, float]:
	rx, ry, rw, rh = rect
	want = (fmt_filter or "").replace(" ", "").lower() or None
	for bc in barcodes:
		c = _barcode_center(bc)
		if c is None:
			continue
		if want and want not in str(getattr(bc, "format", "")).replace(" ", "").lower():
			continue
		if rx <= c[0] <= rx + rw and ry <= c[1] <= ry + rh:
			return (getattr(bc, "text", None) or None), 1.0
	return None, 0.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _image_size(image_bytes: bytes) -> tuple[int, int]:
	try:
		from PIL import Image

		with Image.open(io.BytesIO(image_bytes)) as im:
			return im.width, im.height
	except Exception:  # noqa: BLE001
		return 0, 0


def _box_center(box) -> list | None:
	"""Center of an OCR box, accepting a polygon [[x,y],...] or a rect [x,y,w,h]."""
	if not box:
		return None
	if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
		x, y, w, h = box
		return [x + w / 2, y + h / 2]
	try:
		xs = [p[0] for p in box]
		ys = [p[1] for p in box]
		return [sum(xs) / len(xs), sum(ys) / len(ys)]
	except (TypeError, IndexError):
		return None


def _apply_regex(value: str | None, pattern: str | None) -> str | None:
	if not value or not pattern:
		return value
	m = re.search(pattern, value)
	if not m:
		return value
	return (m.group(1) if m.groups() else m.group(0)).strip()


def _norm(value: str | None) -> str:
	return re.sub(r"\s+", "", (value or "")).upper()
