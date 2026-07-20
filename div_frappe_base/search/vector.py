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


def format_vector_text(vec) -> str:
	"""Render a sequence of floats as a `[v1, v2, ...]` text literal suitable
	for `VEC_FromText(...)`. MariaDB 11.8 rejects raw float32 byte payloads
	bound through MySQLdb's parameter substitution into a `VECTOR(N)` column;
	the text form is the dialect-safe path. Accepts numpy arrays or plain
	Python lists."""
	return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def pack_vector(vec) -> bytes:
	"""Pack a sequence of floats as little-endian float32 bytes — the binary
	form MariaDB's `VEC_*` functions accept. Accepts numpy arrays or plain
	Python lists. Raises ValueError on length mismatch."""
	values = [float(x) for x in vec]
	return struct.pack(f"<{len(values)}f", *values)


def write_embedding(doctype: str, name: str, field: str, vec) -> None:
	"""Persist an embedding via raw UPDATE. Skips the ORM entirely so the
	unmodelled VECTOR column doesn't trip Frappe's diff machinery. Uses
	`VEC_FromText(...)` rather than a raw bytes parameter because MariaDB
	11.8 rejects float32 byte payloads passed through MySQLdb's `%s`
	substitution to a `VECTOR(N)` column with "Incorrect vector value"."""
	text = format_vector_text(vec)
	frappe.db.sql(
		f"UPDATE `tab{doctype}` SET `{field}` = VEC_FromText(%s) WHERE name = %s",
		(text, name),
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


def column_is_vector_ready(doctype: str, field: str) -> bool:
	"""True when the column is already in a shape that `VEC_FromText` writes
	and `VEC_DISTANCE_COSINE` reads — either MariaDB-native `VECTOR(N)` or
	one of the BLOB family the older patch produced. Used to keep
	`upgrade_column_to_vector` idempotent across re-runs."""
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


def column_is_native_vector(doctype: str, field: str) -> bool:
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
	return (row[0][0] or "").lower() == "vector"


def column_exists(doctype: str, field: str) -> bool:
	row = frappe.db.sql(
		"""
		SELECT 1
		FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND COLUMN_NAME = %s
		""",
		(f"tab{doctype}", field),
	)
	return bool(row)


# Back-compat alias: existing callers (and tests) imported `column_is_blob`
# from this module; keep the old name pointing at the new function.
column_is_blob = column_is_vector_ready


def vector_index_exists(doctype: str, field: str) -> bool:
	# `%VECTOR%` is escaped to `%%VECTOR%%` because frappe.db.sql forwards the
	# query through MySQLdb's printf-style `%` substitution when args are
	# present; an unescaped `%V` raises "not enough arguments for format
	# string" before the query ever reaches the server.
	row = frappe.db.sql(
		"""
		SELECT INDEX_NAME
		FROM INFORMATION_SCHEMA.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
			AND COLUMN_NAME = %s
			AND INDEX_TYPE LIKE '%%VECTOR%%'
		""",
		(f"tab{doctype}", field),
	)
	return bool(row)


def upgrade_column_to_vector(
	doctype: str, field: str, dim: int, distance: str = "cosine"
) -> bool:
	"""Migrate a Long Text column to a MariaDB-native `VECTOR(dim)` column.

	Returns True when the column ends up in the vector-ready state, False
	when the server is too old (caller is expected to fall through to a
	non-vector match path). Idempotent — re-running on an already-converted
	column is a no-op.

	**HNSW index is intentionally not created here.** MariaDB 11.7+ requires
	`NOT NULL` on every column in a `VECTOR INDEX`, but Frappe's ORM writes
	`NULL` into the field on `doc.insert()` because the doctype JSON
	declares it as Long Text (with no default). Adding the index would
	require either backfilling and gating every insert path with a
	zero-vector default or moving rows to raw-SQL inserts — both larger
	refactors than this patch can carry. Without the index, `search_cosine`
	falls back to a full-scan `VEC_DISTANCE_COSINE`, which is fine at
	current corpus sizes (≈ 6 k rows) but should be revisited if the
	corpus grows materially or the matcher becomes hot."""
	if not mariadb_supports_vectors():
		frappe.log_error(
			title="Vector column upgrade skipped",
			message=(
				f"MariaDB version does not support VECTOR columns; "
				f"`tab{doctype}`.{field} left as text."
			),
		)
		return False

	if column_is_native_vector(doctype, field):
		return True

	# `sql_ddl` commits any pending transaction writes before issuing the
	# ALTER. Frappe's `check_implicit_commit` refuses raw `db.sql` DDL when
	# earlier writes are buffered in the same transaction (e.g. seed helpers
	# in `after_migrate` that ran before this call).
	if not column_exists(doctype, field):
		# Embedding fields are declared `is_virtual: 1` in the doctype JSON
		# so Frappe's schema sync (`frappe/database/schema.py`) skips them on
		# table creation. On fresh sites the column doesn't exist yet — ADD
		# rather than MODIFY.
		frappe.db.sql_ddl(
			f"ALTER TABLE `tab{doctype}` ADD COLUMN `{field}` VECTOR({int(dim)}) NULL"
		)
	else:
		# An earlier patch revision may have already converted the column to
		# BLOB. Either way, MODIFY straight to VECTOR(dim) is safe: VECTOR
		# accepts the existing payload bytes back via VEC_FromText writes, and
		# there's no in-flight float32 data on `dim`-mismatched rows on this
		# bench (sync hasn't successfully populated the column yet).
		frappe.db.sql_ddl(
			f"ALTER TABLE `tab{doctype}` MODIFY COLUMN `{field}` VECTOR({int(dim)}) NULL"
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

	Emits a raw SELECT that uses MariaDB's `VEC_DISTANCE_COSINE`. The HNSW
	index on `field` is the intended ANN driver when present; the SELECT
	still works without it (full scan) so columns that haven't been indexed
	yet — see `upgrade_column_to_vector` for why — degrade gracefully.
	Returns rows of `{name, dist, cosine}` where `cosine = 1 - dist`. The
	caller chooses the acceptance threshold.

	`VEC_FromText(...)` is used rather than a raw bytes parameter because
	MariaDB 11.8 rejects float32 byte payloads bound via MySQLdb's `%s`
	substitution to a `VECTOR(N)` column."""
	text = format_vector_text(query)
	where_sql, where_params = compose_where(filters)
	where_clause = f"WHERE {where_sql}" if where_sql else ""
	sql = (
		f"SELECT name, VEC_DISTANCE_COSINE(`{field}`, VEC_FromText(%s)) AS dist "
		f"FROM `tab{doctype}` "
		f"WHERE `{field}` IS NOT NULL "
		f"{('AND ' + where_sql) if where_sql else ''} "
		f"ORDER BY dist ASC LIMIT %s"
	)
	params = [text, *where_params, int(limit)]
	rows = frappe.db.sql(sql, params, as_dict=True)
	for row in rows:
		row["cosine"] = 1.0 - float(row["dist"])
	return rows
