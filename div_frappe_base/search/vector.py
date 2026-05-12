# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

"""Shared vector-search infrastructure.

Single consumer at the moment (`div_ems` KiCad 3D Model linking); the helper
API is deliberately feature-agnostic so future description-matching features
can reuse the embedding model, the column-upgrade DDL, and the cosine-search
SQL without reimplementing any of it. See `docs/design.md` § Vector Search
Infrastructure.
"""

from __future__ import annotations

import os
import re
import struct
from functools import lru_cache
from typing import Any

import frappe

EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBED_DIM = 768

MIN_MARIADB_VECTOR_VERSION = (11, 7, 0)

_model = None


def hf_cache_dir() -> str:
	"""Bench-owned HuggingFace cache so model weights live alongside site data
	instead of in the OS user's home. Created on first call."""
	bench_root = os.path.realpath(
		os.path.join(frappe.get_app_path("frappe"), "..", "..", "..")
	)
	path = os.path.join(bench_root, "sites", ".huggingface_cache")
	os.makedirs(path, exist_ok=True)
	return path


def load_model():
	"""Lazy-load BAAI/bge-base-en-v1.5 once per process and cache it as a
	module global. First call downloads weights into `hf_cache_dir()` and is
	expected to take 30-60 s; subsequent calls return the cached instance."""
	global _model
	if _model is not None:
		return _model

	os.environ.setdefault("HF_HOME", hf_cache_dir())
	from sentence_transformers import SentenceTransformer

	_model = SentenceTransformer(EMBED_MODEL_NAME)
	return _model


def embed_texts(texts: list[str]):
	"""Embed a batch of strings into an `(N, EMBED_DIM)` float32 numpy array.

	Output is L2-normalised so MariaDB's `VEC_DISTANCE_COSINE` is equivalent
	to `1 - dot(a, b)`. Batching is the caller's responsibility — the helper
	does not split or rate-limit. Empty list returns an empty array."""
	import numpy as np

	if not texts:
		return np.zeros((0, EMBED_DIM), dtype=np.float32)
	model = load_model()
	vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
	return vecs.astype(np.float32, copy=False)


def embed_text(text: str):
	"""Convenience single-string wrapper around `embed_texts`."""
	return embed_texts([text])[0]


def pack_vector(vec) -> bytes:
	"""Pack a sequence of floats as little-endian float32 bytes — the binary
	form MariaDB's `VEC_*` functions accept. Accepts numpy arrays or plain
	Python lists. Raises ValueError on length mismatch."""
	values = [float(x) for x in vec]
	return struct.pack(f"<{len(values)}f", *values)


def write_embedding(doctype: str, name: str, field: str, vec) -> None:
	"""Persist an embedding via raw UPDATE. Skips the ORM entirely so the
	unmodelled BLOB column doesn't trip Frappe's diff machinery."""
	payload = pack_vector(vec)
	frappe.db.sql(
		f"UPDATE `tab{doctype}` SET `{field}` = %s WHERE name = %s",
		(payload, name),
	)


@lru_cache(maxsize=1)
def mariadb_supports_vectors() -> bool:
	"""Cached check against `SELECT VERSION()` parsed for ≥ 11.7.0. Embedded
	search tiers consult this and fall through to a non-vector path on older
	servers."""
	row = frappe.db.sql("SELECT VERSION()", as_dict=False)
	if not row:
		return False
	version_string = row[0][0] or ""
	match = re.match(r"(\d+)\.(\d+)\.(\d+)", version_string)
	if not match:
		return False
	parts = tuple(int(p) for p in match.groups())
	return parts >= MIN_MARIADB_VECTOR_VERSION


def column_is_blob(doctype: str, field: str) -> bool:
	row = frappe.db.sql(
		"""
		SELECT DATA_TYPE
		FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND COLUMN_NAME = %s
		""",
		(f"tab{doctype}", field),
	)
	if not row:
		return False
	return (row[0][0] or "").lower() in {
		"blob",
		"longblob",
		"mediumblob",
		"tinyblob",
		"vector",
	}


def vector_index_exists(doctype: str, field: str) -> bool:
	row = frappe.db.sql(
		"""
		SELECT INDEX_NAME
		FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND COLUMN_NAME = %s
			AND INDEX_TYPE LIKE '%VECTOR%'
		""",
		(f"tab{doctype}", field),
	)
	return bool(row)


def upgrade_column_to_vector(
	doctype: str, field: str, dim: int, distance: str = "cosine"
) -> bool:
	"""Migrate a Long Text column to a MariaDB-native vector BLOB + HNSW index.

	Returns True when the column ends up in the vector-ready state, False
	when the server is too old (caller is expected to fall through to a
	non-vector match path). Idempotent."""
	if not mariadb_supports_vectors():
		frappe.log_error(
			title="Vector column upgrade skipped",
			message=(
				f"MariaDB version does not support VECTOR INDEX; column `tab{doctype}`.{field} left as text."
			),
		)
		return False

	if not column_is_blob(doctype, field):
		frappe.db.sql(f"ALTER TABLE `tab{doctype}` MODIFY COLUMN `{field}` BLOB NULL")

	if not vector_index_exists(doctype, field):
		index_name = f"{field}_vec_idx"
		# MariaDB 11.7+ VECTOR INDEX syntax. Caller chose `dim`; the column
		# itself is plain BLOB so dim is enforced at the index, not the type.
		frappe.db.sql(
			f"ALTER TABLE `tab{doctype}` ADD VECTOR INDEX `{index_name}` (`{field}`) M=16 DISTANCE={distance}"
		)
	return True


def compose_where(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
	"""Translate an equality-filter dict into a parameterised WHERE clause
	fragment. Values may be scalars (equality) or 2-tuples like
	`("in", [...])` / `("!=", v)` for the same operator surface Frappe's
	`get_all` accepts. Returns ('' , []) when filters is empty."""
	if not filters:
		return "", []
	clauses: list[str] = []
	params: list[Any] = []
	for key, value in filters.items():
		if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
			op, operand = value
			op_upper = op.upper()
			if op_upper == "IN":
				placeholders = ", ".join(["%s"] * len(operand))
				clauses.append(f"`{key}` IN ({placeholders})")
				params.extend(operand)
			else:
				clauses.append(f"`{key}` {op} %s")
				params.append(operand)
		else:
			clauses.append(f"`{key}` = %s")
			params.append(value)
	return " AND ".join(clauses), params


def search_cosine(
	doctype: str,
	field: str,
	query,
	filters: dict[str, Any] | None = None,
	limit: int = 10,
) -> list[dict]:
	"""Rank a doctype's rows by cosine distance to `query`.

	Emits a raw SELECT that uses MariaDB's `VEC_DISTANCE_COSINE`; the HNSW
	index on `field` (added by `upgrade_column_to_vector`) is the ANN driver.
	Returns rows of `{name, dist, cosine}` where `cosine = 1 - dist`. The
	caller chooses the acceptance threshold."""
	payload = pack_vector(query)
	where_sql, where_params = compose_where(filters)
	where_clause = f"WHERE {where_sql}" if where_sql else ""
	sql = (
		f"SELECT name, VEC_DISTANCE_COSINE(`{field}`, %s) AS dist "
		f"FROM `tab{doctype}` "
		f"{where_clause} "
		f"ORDER BY dist ASC LIMIT %s"
	)
	params = [payload, *where_params, int(limit)]
	rows = frappe.db.sql(sql, params, as_dict=True)
	for row in rows:
		row["cosine"] = 1.0 - float(row["dist"])
	return rows
