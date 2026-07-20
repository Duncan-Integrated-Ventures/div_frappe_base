# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Engine-agnostic OCR client.

The image analogue of `div_frappe_base.ai.client`. Callers reference an
**OCR Engine** row (on the `OCR Settings` Single) by name and get back a
normalized `OcrResult` — a list of recognized text lines with per-line
confidence and bounding geometry — without knowing which backend produced
them. Swapping PaddleOCR ⇄ Tesseract ⇄ a vision LLM is a UI edit on
`OCR Settings`, not a code change at the call site.

Backends (`OCR Engine.engine_type`):

  * ``PaddleOCR HTTP`` / ``Custom HTTP`` — POST the image to ``base_url`` and
    parse the JSON response. Tolerant of the common PaddleOCR serving shapes
    (hubserving, PaddleOCR-API, PP-Structure) plus our own documented
    ``{"lines": [{text, confidence, box}]}`` contract — see the div_frappe_base
    README § "OCR service".
  * ``Tesseract`` — runs ``pytesseract`` in-process (lazy import; needs the
    ``tesseract-ocr`` system binary).
  * ``Vision LLM`` — delegates to ``ai.client.complete_json`` against the
    named ``AI Profile`` (e.g. a local Qwen2.5-VL served via Ollama), asking
    for the text lines as JSON.

This module returns *raw OCR* only. Domain mapping (which line is the MPN, the
lot, the date code…) lives with the caller — for supplier labels that's
`div_ems.scan.ocr_label`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe.utils.password import get_decrypted_password

CACHE_KEY_API_KEY = "ocr_engine:{name}:api_key"
CACHE_TTL_KEY_SECONDS = 3600

# Retry count for transient HTTP failures against the OCR service. A warm
# PaddleOCR container occasionally drops the first request after an idle GPU
# context reset; one retry absorbs that without masking a service that's down.
DEFAULT_NUM_RETRIES = 1


@dataclass
class OcrLine:
	"""One recognized text line.

	`box` is the raw geometry the backend reported — a polygon
	``[[x, y], ...]`` in source-image pixels for PaddleOCR / a
	``[x, y, w, h]`` rect for Tesseract. `None` when the backend doesn't
	report geometry (e.g. a vision LLM). `confidence` is normalized to 0–1."""

	text: str
	confidence: float = 1.0
	box: list | None = None


@dataclass
class OcrResult:
	engine: str
	lines: list[OcrLine] = field(default_factory=list)
	raw: Any = None

	@property
	def full_text(self) -> str:
		return "\n".join(line.text for line in self.lines)

	def to_dict(self) -> dict[str, Any]:
		return {
			"engine": self.engine,
			"lines": [{"text": l.text, "confidence": l.confidence, "box": l.box} for l in self.lines],
			"full_text": self.full_text,
		}


class EngineNotConfigured(Exception):
	"""Raised when an OCR Engine row is missing, or an HTTP engine has no
	base_url, and the caller didn't opt out via raise_exception=False."""


def recognize(
	engine_name: str,
	image_bytes: bytes,
	*,
	mime_type: str = "image/jpeg",
	raise_exception: bool = True,
) -> OcrResult | None:
	"""Recognize text in `image_bytes` using the named OCR Engine.

	Returns a normalized `OcrResult`, or `None` when the engine can't run and
	`raise_exception=False` (misconfiguration / backend error). The image is
	passed straight through to the backend — pre-processing (deskew, crop) is
	the caller's responsibility."""
	engine = load_engine(engine_name)
	dispatch = {
		"PaddleOCR HTTP": _recognize_http,
		"Custom HTTP": _recognize_http,
		"Tesseract": _recognize_tesseract,
		"Vision LLM": _recognize_vision,
	}
	handler = dispatch.get(engine.engine_type)
	if not handler:
		if raise_exception:
			frappe.throw(f"OCR Engine {engine_name!r} has unknown engine_type {engine.engine_type!r}.")
		return None

	try:
		result = handler(engine, image_bytes, mime_type)
	except EngineNotConfigured:
		if raise_exception:
			raise
		return None
	except Exception as exc:  # noqa: BLE001 — backend failures shouldn't 500 the whole scan
		frappe.log_error(
			title=f"OCR Engine {engine_name!r} ({engine.engine_type}) failed",
			message=frappe.get_traceback(),
		)
		if raise_exception:
			frappe.throw(f"OCR engine {engine_name!r} failed: {exc}")
		return None

	min_conf = float(engine.min_confidence or 0)
	if min_conf > 0:
		result.lines = [l for l in result.lines if l.confidence >= min_conf]
	return result


def load_engine(engine_name: str):
	"""Return the OCR Engine child row for `engine_name`, throwing if it isn't
	configured on OCR Settings."""
	settings = frappe.get_cached_doc("OCR Settings", "OCR Settings")
	for row in settings.engines:
		if row.engine_name == engine_name:
			return row
	frappe.throw(
		f"OCR Engine {engine_name!r} is not configured on OCR Settings. "
		f"Add a row under OCR Settings → Engines."
	)


def resolve_api_key(engine_name: str) -> str | None:
	"""Cache-fronted decrypted API key lookup. Unlike the AI client, an OCR
	engine key is optional (most local services need none), so a missing key
	returns None quietly rather than logging / raising."""
	cache = frappe.cache()
	cached = cache.get_value(CACHE_KEY_API_KEY.format(name=engine_name))
	if cached:
		return cached

	api_key = get_decrypted_password(
		"OCR Settings",
		"OCR Settings",
		f"ocr_engine:{engine_name}:api_key",
		raise_exception=False,
	)
	if not api_key:
		# Fall back to the child-row's own password slot.
		settings = frappe.get_doc("OCR Settings", "OCR Settings")
		for row in settings.engines:
			if row.engine_name == engine_name:
				try:
					api_key = row.get_password("api_key", raise_exception=False)
				except Exception:
					api_key = None
				break
	if not api_key:
		return None

	cache.set_value(
		CACHE_KEY_API_KEY.format(name=engine_name),
		api_key,
		expires_in_sec=CACHE_TTL_KEY_SECONDS,
	)
	return api_key


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


def _recognize_http(engine, image_bytes: bytes, mime_type: str) -> OcrResult:
	"""POST the image to an OCR HTTP service and normalize the response.

	Request: multipart/form-data with the image under the `file` field, plus a
	`lang` form field. Auth (if `api_key` is set) goes on both the
	`Authorization: Bearer` and `X-API-Key` headers so it works with whichever
	the chosen image expects.

	Response: parsed by `_parse_http_ocr`, which accepts our documented
	`{"lines": [...]}` contract and the common PaddleOCR serving shapes."""
	import requests

	base_url = (engine.base_url or "").rstrip("/")
	if not base_url:
		raise EngineNotConfigured(f"OCR Engine {engine.engine_name!r} has no Base URL set.")

	headers: dict[str, str] = {}
	api_key = resolve_api_key(engine.engine_name)
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"
		headers["X-API-Key"] = api_key

	files = {"file": (f"label{_ext_for(mime_type)}", io.BytesIO(image_bytes), mime_type)}
	data = {"lang": engine.lang or "en"}
	timeout = int(engine.timeout or 60)

	last_exc: Exception | None = None
	for _attempt in range(DEFAULT_NUM_RETRIES + 1):
		try:
			resp = requests.post(base_url, files=files, data=data, headers=headers, timeout=timeout)
			resp.raise_for_status()
			payload = resp.json()
			return _parse_http_ocr(payload, engine.engine_name)
		except (requests.ConnectionError, requests.Timeout) as exc:
			last_exc = exc
			# Rewind the file buffer before retrying.
			files["file"][1].seek(0)
			continue
	raise last_exc  # type: ignore[misc]


def _parse_http_ocr(payload: Any, engine_name: str) -> OcrResult:
	"""Normalize the assorted JSON shapes OCR services return into OcrLines.

	Handled shapes (checked in order):
	  1. ``{"lines": [{"text", "confidence"|"score", "box"|"text_region"}]}`` — our contract.
	  2. PaddleOCR hubserving: ``{"results": [[{"text", "confidence", "text_region"}]]}``.
	  3. PaddleOCR-API / PP-Structure: ``{"data"|"result": [{"text", "score"|"confidence", "box"}]}``.
	  4. A bare list of the same row dicts.
	"""
	rows: list = []
	if isinstance(payload, dict):
		if isinstance(payload.get("lines"), list):
			rows = payload["lines"]
		elif isinstance(payload.get("results"), list):
			results = payload["results"]
			# hubserving nests one list per image; flatten the first image.
			rows = results[0] if results and isinstance(results[0], list) else results
		elif isinstance(payload.get("data"), list):
			rows = payload["data"]
		elif isinstance(payload.get("result"), list):
			rows = payload["result"]
	elif isinstance(payload, list):
		rows = payload

	lines: list[OcrLine] = []
	for row in rows:
		if not isinstance(row, dict):
			continue
		text = row.get("text") or row.get("transcription") or ""
		if not text:
			continue
		conf = row.get("confidence")
		if conf is None:
			conf = row.get("score")
		box = row.get("box") or row.get("text_region") or row.get("points")
		lines.append(OcrLine(text=str(text), confidence=_norm_conf(conf), box=box))

	return OcrResult(engine=engine_name, lines=lines, raw=payload)


def _recognize_tesseract(engine, image_bytes: bytes, mime_type: str) -> OcrResult:
	"""In-process OCR via pytesseract. Groups words into lines using the
	block/paragraph/line indices from `image_to_data`, averaging word
	confidences per line."""
	import pytesseract
	from PIL import Image

	image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
	lang = (engine.lang or "en")
	# Tesseract uses 3-letter codes; map the common "en" alias.
	tess_lang = {"en": "eng"}.get(lang, lang)
	data = pytesseract.image_to_data(
		image, lang=tess_lang, output_type=pytesseract.Output.DICT
	)

	# Aggregate words → lines keyed by (block, par, line).
	buckets: dict[tuple, dict] = {}
	n = len(data["text"])
	for i in range(n):
		word = (data["text"][i] or "").strip()
		if not word:
			continue
		key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
		b = buckets.setdefault(
			key,
			{"words": [], "confs": [], "left": data["left"][i], "top": data["top"][i], "right": 0, "bottom": 0},
		)
		b["words"].append(word)
		try:
			b["confs"].append(float(data["conf"][i]))
		except (TypeError, ValueError):
			pass
		b["left"] = min(b["left"], data["left"][i])
		b["top"] = min(b["top"], data["top"][i])
		b["right"] = max(b["right"], data["left"][i] + data["width"][i])
		b["bottom"] = max(b["bottom"], data["top"][i] + data["height"][i])

	lines: list[OcrLine] = []
	for b in buckets.values():
		confs = [c for c in b["confs"] if c >= 0]
		avg = (sum(confs) / len(confs) / 100.0) if confs else 1.0
		box = [b["left"], b["top"], b["right"] - b["left"], b["bottom"] - b["top"]]
		lines.append(OcrLine(text=" ".join(b["words"]), confidence=avg, box=box))

	return OcrResult(engine=engine.engine_name, lines=lines, raw=None)


def _recognize_vision(engine, image_bytes: bytes, mime_type: str) -> OcrResult:
	"""Delegate to a vision LLM via the AI client. Asks the model to return the
	raw text lines it reads, top-to-bottom, as a JSON array of strings."""
	from div_frappe_base.ai.client import complete_json

	profile = engine.ai_profile or "vision"
	prompt = (
		"You are an OCR engine. Transcribe every line of text you can read in this "
		"image, top to bottom, exactly as printed (preserve case, punctuation, and "
		'part-number characters). Respond ONLY with a JSON array of strings, one '
		'per visual line, e.g. ["line one", "line two"]. No commentary.'
	)
	data = complete_json(
		profile,
		prompt,
		attachments=[{"mime_type": mime_type, "data": image_bytes}],
		raise_exception=False,
	)
	lines: list[OcrLine] = []
	if isinstance(data, list):
		for item in data:
			text = item if isinstance(item, str) else (item.get("text") if isinstance(item, dict) else None)
			if text:
				lines.append(OcrLine(text=str(text), confidence=1.0, box=None))
	return OcrResult(engine=engine.engine_name, lines=lines, raw=data)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _norm_conf(value: Any) -> float:
	"""Coerce a backend confidence to 0–1. PaddleOCR reports 0–1 already;
	anything > 1 is assumed to be a 0–100 percentage."""
	try:
		c = float(value)
	except (TypeError, ValueError):
		return 1.0
	if c > 1.0:
		c = c / 100.0
	return max(0.0, min(1.0, c))


def _ext_for(mime_type: str) -> str:
	return {
		"image/jpeg": ".jpg",
		"image/png": ".png",
		"image/webp": ".webp",
	}.get(mime_type, ".jpg")
