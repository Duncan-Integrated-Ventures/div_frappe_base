# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Provider-agnostic LLM client.

Wraps `litellm.completion` so callers reference an AI Profile by name
(e.g. "vision", "categorizer", "datasheet") and the configured provider /
model / key / temperature / timeout follow from AI Settings — without the
caller knowing which provider is on the other end.
"""

import base64
import json

import frappe
from frappe.utils.password import get_decrypted_password

CACHE_KEY_API_KEY = "ai_profile:{name}:api_key"
CACHE_KEY_MISSING = "ai_profile:{name}:api_key_missing"
CACHE_KEY_MISSING_LOGGED = "ai_profile:{name}:api_key_missing_logged"
CACHE_TTL_KEY_SECONDS = 3600
CACHE_TTL_MISSING_SECONDS = 300
CACHE_TTL_MISSING_LOGGED_SECONDS = 3600


PROVIDER_PREFIX = {
	"Gemini": "gemini",
	"Anthropic": "anthropic",
	"OpenAI": "openai",
	"xAI": "xai",
	"Vertex AI": "vertex_ai",
	"Azure OpenAI": "azure",
	"Bedrock": "bedrock",
	"Mistral": "mistral",
	"Ollama": "ollama",
	"Custom": "",
}


class ProfileNotConfigured(Exception):
	"""Raised when an AI Profile has no API key set and the caller didn't
	opt out via raise_exception=False."""


def complete(
	profile_name: str,
	prompt: str,
	*,
	attachments: list[dict] | None = None,
	response_format=None,
	raise_exception: bool = True,
) -> str | None:
	"""Run a single LLM completion against the named profile.

	`attachments` is a list of `{"mime_type": str, "data": bytes}` dicts.
	Each is base64-encoded into a data URI and attached as an `image_url`
	content part. Gemini handles `application/pdf` natively under this
	shape; behavior for other providers depends on LiteLLM's provider
	adapter for the attachment type.

	`response_format` accepts `None` (free-form text), the string `"json"`
	(maps to `{"type": "json_object"}`), or a dict passed through verbatim
	(e.g. `{"type": "json_schema", "json_schema": {...}}`).

	Returns the assistant's text content with leading / trailing markdown
	fences stripped (mirrors the existing categorizer.py and ai_vision.py
	defensive parsing). Returns `None` when the profile's API key is unset
	and `raise_exception=False`.
	"""
	profile = load_profile(profile_name)
	api_key = resolve_api_key(profile_name, raise_exception=raise_exception)
	if api_key is None:
		return None

	model_string = build_model_string(profile.provider, profile.model)
	messages = build_messages(prompt, attachments)

	import litellm  # imported lazily so test runs that don't touch AI don't pay the import cost

	kwargs = {
		"model": model_string,
		"messages": messages,
		"api_key": api_key,
		"temperature": profile.temperature,
		"timeout": profile.timeout,
	}
	if profile.base_url:
		kwargs["api_base"] = profile.base_url
	if response_format is not None:
		kwargs["response_format"] = (
			{"type": "json_object"} if response_format == "json" else response_format
		)

	response = litellm.completion(**kwargs)
	text = (response.choices[0].message.content or "").strip()
	return strip_fences(text)


def load_profile(profile_name: str):
	"""Return the AI Profile child row for `profile_name`, throwing if
	the profile isn't seeded on AI Settings."""
	settings = frappe.get_cached_doc("AI Settings", "AI Settings")
	for row in settings.profiles:
		if row.profile_name == profile_name:
			return row
	frappe.throw(
		f"AI Profile {profile_name!r} is not configured on AI Settings. "
		f"Add a row under AI Settings → Profiles."
	)


def resolve_api_key(profile_name: str, *, raise_exception: bool) -> str | None:
	"""Cache-fronted decrypted API key lookup.

	Mirrors the legacy `gemini_client()` cache pattern — one cache slot per
	profile, refreshed hourly. When the key is empty, log once per hour
	(cache-gated) so a batch worker doesn't spam the error log, and either
	raise ProfileNotConfigured or return None depending on the caller.
	"""
	cache = frappe.cache()
	cached = cache.get_value(CACHE_KEY_API_KEY.format(name=profile_name))
	if cached:
		return cached

	api_key = get_decrypted_password(
		"AI Settings",
		"AI Settings",
		f"ai_profile:{profile_name}:api_key",
		raise_exception=False,
	)
	if not api_key:
		# Try the standard child-row password naming as a fallback — Frappe
		# stores child-row passwords under the parent's combined fieldname,
		# but the exact slot is implementation-detail; resolve via the doc.
		api_key = get_password_from_child_row(profile_name)

	if not api_key:
		cache.set_value(
			CACHE_KEY_MISSING.format(name=profile_name),
			True,
			expires_in_sec=CACHE_TTL_MISSING_SECONDS,
		)
		if not cache.get_value(CACHE_KEY_MISSING_LOGGED.format(name=profile_name)):
			frappe.log_error(
				title="AI Profile API key not configured",
				message=(
					f"AI Settings → profile {profile_name!r} has no API key, so any "
					f"LLM call against this profile is disabled. Set the key under "
					f"AI Settings → Profiles."
				),
			)
			cache.set_value(
				CACHE_KEY_MISSING_LOGGED.format(name=profile_name),
				True,
				expires_in_sec=CACHE_TTL_MISSING_LOGGED_SECONDS,
			)
		if raise_exception:
			raise ProfileNotConfigured(f"AI Profile {profile_name!r} has no API key configured.")
		return None

	cache.set_value(
		CACHE_KEY_API_KEY.format(name=profile_name),
		api_key,
		expires_in_sec=CACHE_TTL_KEY_SECONDS,
	)
	return api_key


def get_password_from_child_row(profile_name: str) -> str | None:
	"""Resolve the api_key for a given profile by walking the AI Settings
	child rows and using each row's own get_password helper. Frappe stores
	child-table Password fields under the child doctype's auth slot, keyed
	by the row's `name`."""
	settings = frappe.get_doc("AI Settings", "AI Settings")
	for row in settings.profiles:
		if row.profile_name != profile_name:
			continue
		try:
			return row.get_password("api_key", raise_exception=False)
		except Exception:
			return None
	return None


def build_model_string(provider: str, model: str) -> str:
	prefix = PROVIDER_PREFIX.get(provider, "")
	if not prefix:
		# Custom provider — operator supplies the full LiteLLM model
		# string (e.g. "openrouter/anthropic/claude-3-opus") in `model`.
		return model
	return f"{prefix}/{model}"


def build_messages(prompt: str, attachments: list[dict] | None) -> list[dict]:
	"""Assemble OpenAI-shaped messages with optional multimodal attachments."""
	if not attachments:
		return [{"role": "user", "content": prompt}]

	content: list[dict] = [{"type": "text", "text": prompt}]
	for att in attachments:
		mime_type = att["mime_type"]
		data = att["data"]
		encoded = base64.b64encode(data).decode("ascii")
		data_uri = f"data:{mime_type};base64,{encoded}"
		content.append({"type": "image_url", "image_url": {"url": data_uri}})
	return [{"role": "user", "content": content}]


def strip_fences(text: str) -> str:
	"""Remove leading / trailing markdown fences and an optional ```json
	hint. Mirrors the defensive parsing already in categorizer.py /
	ai_vision.py so callers don't need to repeat it."""
	if text.startswith("```"):
		text = text.strip("`")
		if text.lower().startswith("json"):
			text = text[4:].lstrip()
	return text.strip()


def complete_json(
	profile_name: str,
	prompt: str,
	*,
	attachments: list[dict] | None = None,
	raise_exception: bool = True,
) -> dict | list | None:
	"""Convenience wrapper: runs `complete(...)` with `response_format="json"`
	and parses the response as JSON. Returns `None` when the profile's key
	isn't set (and `raise_exception=False`) or when the response can't be
	parsed."""
	text = complete(
		profile_name,
		prompt,
		attachments=attachments,
		response_format="json",
		raise_exception=raise_exception,
	)
	if text is None:
		return None
	try:
		return json.loads(text)
	except json.JSONDecodeError:
		frappe.log_error(
			title=f"AI Profile {profile_name!r}: non-JSON response",
			message=f"raw={text[:500]!r}",
		)
		return None
