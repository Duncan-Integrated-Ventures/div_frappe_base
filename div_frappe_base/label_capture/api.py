# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Whitelisted surface for the generic label-capture extractor.

The reusable camera helper (`public/js/label_capture.js`) POSTs a captured
image here and gets back the layout-matched fields. Domain apps can call this
directly and map the generic `{field_key: value}` result, or wrap it in their
own endpoint to add domain steps (see `div_ems.scan.receipt.extract_label`)."""

from __future__ import annotations

import base64

import frappe


@frappe.whitelist()
def extract_label(image=None, mime_type=None, engine=None, layout=None):
	"""Run the generic extractor on a captured label photo.

	`image` is a base64 string (bare or a `data:<mime>;base64,...` URL). Returns
	`{layout, fields: {key: {value, confidence, source}}, barcodes, ocr_text,
	ocr_lines, warnings}` — field keys are whatever the matched Label Layout
	Format defines. Never resolves or writes anything; pure read."""
	from div_frappe_base.label_capture.extractor import extract_label_fields

	image_bytes, resolved_mime = decode_image_arg(image, mime_type)
	if not image_bytes:
		frappe.throw("No image provided to extract_label.")

	extraction = extract_label_fields(
		image_bytes,
		mime_type=resolved_mime,
		engine_name=engine or "default",
		layout_name=layout or None,
	)
	return extraction.to_dict()


def decode_image_arg(image, mime_type) -> tuple[bytes | None, str]:
	"""Decode the `image` arg (data URL or bare base64) to bytes, inferring the
	mime type from a data-URL prefix when not given explicitly. Shared with
	domain wrappers."""
	if not image:
		return None, mime_type or "image/jpeg"
	resolved_mime = mime_type or "image/jpeg"
	data = image
	if isinstance(image, str) and image.startswith("data:"):
		header, _, b64 = image.partition(",")
		data = b64
		if ";" in header and ":" in header:
			resolved_mime = header.split(":", 1)[1].split(";", 1)[0] or resolved_mime
	try:
		return base64.b64decode(data), resolved_mime
	except (ValueError, TypeError):
		frappe.throw("extract_label: image is not valid base64.")
