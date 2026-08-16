"""Versioned embedding index for optional semantic memory recall.

Search is accelerated by ``sqlite-vec`` (vec0 virtual table) when the
extension is available, falling back to Python cosine distance otherwise.
The JSON ``vector`` column on ``memory_embeddings`` is kept for backward
compatibility; vec0 stores the same vectors as compact binary blobs aligned
by rowid.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.request import Request, urlopen

from memai.domain.scope import GLOBAL_SCOPE_ID
from memai.host_integration import get_mem_host_integration
from memai.repository.sqlite import open_memory_sqlite

logger = logging.getLogger(__name__)

EmbeddingTransport = Callable[[Sequence[str]], list[list[float]]]

def _calibrate_local_similarity(
    raw_similarity: float,
    *,
    independent_evidence: int = 2,
) -> float:
    """Map collision-free local overlap to the shared recall score scale."""
    if independent_evidence < 1:
        return 0.0
    multiplier = 12.5 if independent_evidence == 1 else 5.0
    return min(1.0, multiplier * max(0.0, float(raw_similarity)))

_VEC0_TABLE = "memory_embeddings_vec"
_vec0_load_attempted = False
_vec0_loadable = False


@dataclass(frozen=True, slots=True)
class SemanticIndexConfig:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    dimensions: int | None = None
    timeout_seconds: float = 5.0
    backfill_batch_size: int = 64

    @classmethod
    def from_host_config(cls) -> "SemanticIndexConfig":
        try:
            host = get_mem_host_integration()
            raw = dict(
                host.load_config().get("memory", {}).get("semantic_recall", {}) or {}
            )
            api_key_env = str(raw.get("api_key_env") or "").strip()
            api_key = str(host.get_env_value(api_key_env) or "") if api_key_env else ""
        except Exception:
            raw = {}
            api_key = ""
        return cls(
            enabled=bool(raw.get("enabled", False)),
            provider=str(raw.get("provider") or "").strip(),
            model=str(raw.get("model") or "").strip(),
            base_url=str(raw.get("base_url") or "").strip().rstrip("/"),
            api_key=api_key,
            dimensions=(int(raw["dimensions"]) if raw.get("dimensions") else None),
            timeout_seconds=max(0.5, float(raw.get("timeout_seconds", 5.0))),
            backfill_batch_size=max(1, min(256, int(raw.get("backfill_batch_size", 64)))),
        )

    @property
    def ready(self) -> bool:
        """Ready when enabled — either an external provider is configured, or
        the zero-dependency local ``CharNgramEmbedder`` fallback applies
        (empty provider or ``provider == "local"``)."""
        if not self.enabled:
            return False
        if not self.provider or self.provider == "local":
            return True  # local zero-dep embedder
        return bool(
            self.provider
            and self.model
            and self.base_url
            and (self.api_key or self.provider == "ollama")
        )


def _ensure_vec0_loaded(conn) -> bool:
    """Try to load sqlite-vec on *conn*; return True on success.

    Each connection calls ``sqlite_vec.load(conn)`` even after prior
    successes, because the extension must be initialized per-connection.
    The global flag records the first failure so we don't keep retrying
    across connections.
    """
    global _vec0_load_attempted, _vec0_loadable  # noqa: PLW0603
    if not _vec0_loadable and _vec0_load_attempted:
        return False  # already tried and failed — don't keep retrying
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        _vec0_loadable = True
        _vec0_load_attempted = True
        return True
    except Exception:
        _vec0_loadable = False
        _vec0_load_attempted = True
        return False


def _vec0_available(conn) -> bool:
    """Lazy-init vec0 on *conn*; ensure loaded on every connection.

    The native extension load is global, but each SQLite connection must have
    it initialized separately (``sqlite_vec.load(conn)`` must be called per
    connection).
    """
    return _ensure_vec0_loaded(conn)


def _vector_blob(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class SemanticMemoryIndex:
    def __init__(
        self,
        db_path: str | Path,
        config: SemanticIndexConfig | None = None,
        *,
        transport: EmbeddingTransport | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or SemanticIndexConfig.from_host_config()
        self._transport = transport
        self._local_fallback = False
        if (
            self._transport is None
            and self.config.enabled
            and (not self.config.provider or self.config.provider == "local")
        ):
            from memai.indexes.local_embedding import CharNgramEmbedder

            if self.config.dimensions is not None and self.config.dimensions < 64:
                self.config = replace(self.config, dimensions=64)
            self._transport = CharNgramEmbedder(
                dimensions=self.config.dimensions or 256
            )
            self._local_fallback = True
        self._last_error = ""
        self._vec0_ready = False  # set by _setup_table when vec0 loads
        self._index_lock = threading.Lock()
        self._setup_table()

    @property
    def enabled(self) -> bool:
        return self.config.ready or self._transport is not None

    def status(self) -> dict[str, Any]:
        conn = open_memory_sqlite(self.db_path)
        try:
            indexed = conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings WHERE provider = ? AND model = ?",
                (self.config.provider, self.config.model),
            ).fetchone()[0]
        finally:
            conn.close()
        return {
            "enabled": self.enabled,
            "provider": self.config.provider,
            "model": self.config.model,
            "dimensions": self.config.dimensions,
            "indexed_records": int(indexed),
            "vec0_available": self._vec0_ready,
            "last_error": self._last_error,
        }

    def pending_count(self) -> int:
        """Return the number of active records not indexed for this model."""
        if not self.enabled:
            return 0
        return len(self._pending_records(1_000_000))

    def index_pending(self, limit: int | None = None) -> int:
        with self._index_lock:
            return self._index_pending_locked(limit)

    def _index_pending_locked(self, limit: int | None) -> int:
        if not self.enabled:
            return 0
        records = self._pending_records(
            limit or self.config.backfill_batch_size
        )
        if not records:
            return 0
        try:
            vectors = self._embed([record[5] for record in records])
            if len(vectors) != len(records):
                raise ValueError("Embedding response count does not match input count")
            conn = open_memory_sqlite(self.db_path)
            indexed = 0
            try:
                now = datetime.now(timezone.utc).isoformat()
                vec0 = _vec0_available(conn)
                for record, vector in zip(records, vectors):
                    source_type, memory_id, owner_id, workspace_id, memory_domain, content = record
                    current_hash = self._source_content_hash(
                        conn,
                        source_type,
                        memory_id,
                        owner_id,
                        workspace_id,
                        memory_domain,
                    )
                    if current_hash != _content_hash(content):
                        continue
                    self._validate_vector(vector)
                    conn.execute(
                        "INSERT INTO memory_embeddings "
                        "(source_type, memory_id, owner_id, workspace_id, memory_domain, content_hash, "
                        "provider, model, dimensions, vector, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source_type, memory_id, owner_id, workspace_id, "
                        "memory_domain, provider, model) DO UPDATE SET "
                        "content_hash = excluded.content_hash, provider = excluded.provider, "
                        "dimensions = excluded.dimensions, vector = excluded.vector, "
                        "updated_at = excluded.updated_at",
                        (
                            source_type,
                            memory_id,
                            owner_id,
                            workspace_id,
                            memory_domain,
                            _content_hash(content),
                            self.config.provider,
                            self.config.model,
                            len(vector),
                            json.dumps(vector, separators=(",", ":")),
                            now,
                        ),
                    )
                    if vec0:
                        rowid = conn.execute(
                            "SELECT rowid FROM memory_embeddings WHERE "
                            "source_type = ? AND memory_id = ? AND owner_id = ? "
                            "AND workspace_id = ? AND memory_domain = ? "
                            "AND provider = ? AND model = ?",
                            (
                                source_type,
                                memory_id,
                                owner_id,
                                workspace_id,
                                memory_domain,
                                self.config.provider,
                                self.config.model,
                            ),
                        ).fetchone()[0]
                        # sqlite-vec exposes rowid as a strict primary key and
                        # does not implement SQLite's REPLACE semantics. Remove
                        # the previous vector explicitly before refreshing it.
                        conn.execute(
                            f"DELETE FROM {_VEC0_TABLE} WHERE rowid = ?", (rowid,)
                        )
                        conn.execute(
                            f"INSERT INTO {_VEC0_TABLE} (rowid, embedding) "
                            f"VALUES (?, ?)",
                            (rowid, _vector_blob(vector)),
                        )
                    indexed += 1
                conn.commit()
            finally:
                conn.close()
            self._last_error = ""
            return indexed
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return 0

    @staticmethod
    def _source_content_hash(
        conn,
        source_type: str,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
        memory_domain: str,
    ) -> str | None:
        source_queries = {
            "turn": (
                "SELECT COALESCE(text, '') FROM turns "
                "WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ? AND compression_status != 'compressed'"
            ),
            "archive": (
                "SELECT COALESCE(original_text, text_summary, '') FROM turns_archive "
                "WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ?"
            ),
            "compressed": (
                "SELECT COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || "
                "COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
                "FROM compressed_memories WHERE memory_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ? "
                "AND status = 'active' AND hidden = 0"
            ),
            "profile": (
                "SELECT COALESCE(subject, '') || ' ' || COALESCE(predicate, '') || ' ' || "
                "COALESCE(value, '') || ' ' || COALESCE(summary, '') "
                "FROM profile_memories WHERE memory_id = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ? "
                "AND status = 'active'"
            ),
        }
        query = source_queries.get(source_type)
        if query is None:
            return None
        row = conn.execute(
            query,
            (memory_id, owner_id, workspace_id, memory_domain),
        ).fetchone()
        return _content_hash(str(row[0] or "")) if row is not None else None

    def search(
        self,
        query: str,
        *,
        owner_id: str,
        workspace_id: str,
        source_domains: Sequence[str] = ("agent_interaction",),
        limit: int = 50,
    ) -> dict[tuple[str, str], float]:
        if not self.enabled or not str(query or "").strip():
            return {}
        try:
            if self._local_fallback:
                return self._search_local_exact(
                    str(query),
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    source_domains=source_domains,
                    limit=limit,
                )
            query_vector = self._embed([str(query)])[0]
            self._validate_vector(query_vector)
            bounded_limit = max(1, min(int(limit), 500))
            conn = open_memory_sqlite(self.db_path)
            try:
                domains = tuple(dict.fromkeys(str(item) for item in source_domains))
                if not domains:
                    return {}
                if self._vec0_ready:
                    return self._search_vec0(
                        conn,
                        query_vector,
                        domains,
                        owner_id,
                        workspace_id,
                        bounded_limit,
                    )
                domain_placeholders = ",".join("?" for _ in domains)
                rows = conn.execute(
                    "SELECT source_type, memory_id, vector FROM memory_embeddings "
                    "WHERE provider = ? AND model = ? AND dimensions = ? "
                    "AND ((owner_id = ? AND workspace_id = ?) OR "
                    "(owner_id = ? AND workspace_id = ?)) "
                    f"AND memory_domain IN ({domain_placeholders})",
                    (
                        self.config.provider,
                        self.config.model,
                        len(query_vector),
                        owner_id,
                        workspace_id,
                        GLOBAL_SCOPE_ID,
                        GLOBAL_SCOPE_ID,
                        *domains,
                    ),
                ).fetchall()
            finally:
                conn.close()
            ranked = sorted(
                (
                    (
                        (str(row[0]), str(row[1])),
                        (
                            _calibrate_local_similarity(
                                _cosine(query_vector, json.loads(row[2]))
                            )
                            if self._local_fallback
                            else _cosine(query_vector, json.loads(row[2]))
                        ),
                    )
                    for row in rows
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            self._last_error = ""
            return {
                key: round(max(0.0, min(1.0, score)), 6)
                for key, score in ranked[:bounded_limit]
                if score > 0.0
            }
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return {}

    def _search_local_exact(
        self,
        query: str,
        *,
        owner_id: str,
        workspace_id: str,
        source_domains: Sequence[str],
        limit: int,
    ) -> dict[tuple[str, str], float]:
        from memai.indexes.local_embedding import CharNgramEmbedder

        domains = tuple(dict.fromkeys(str(item) for item in source_domains))
        if not domains:
            return {}
        placeholders = ",".join("?" for _ in domains)
        scope_params = (
            owner_id,
            workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
            *domains,
        )
        conn = open_memory_sqlite(self.db_path)
        try:
            rows = conn.execute(
                "WITH source_records(source_type, memory_id, owner_id, workspace_id, "
                "memory_domain, content) AS ("
                "SELECT 'turn', turn_id, owner_id, workspace_id, memory_domain, text "
                "FROM turns WHERE compression_status != 'compressed' "
                "UNION ALL SELECT 'archive', turn_id, owner_id, workspace_id, "
                "memory_domain, COALESCE(original_text, text_summary, '') "
                "FROM turns_archive "
                "UNION ALL SELECT 'compressed', memory_id, owner_id, workspace_id, "
                "memory_domain, COALESCE(title, '') || ' ' || COALESCE(summary, '') || "
                "' ' || COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
                "FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND COALESCE(identity_layer, '') != 'founding' "
                "UNION ALL SELECT 'profile', memory_id, owner_id, workspace_id, "
                "memory_domain, COALESCE(subject, '') || ' ' || COALESCE(predicate, '') || "
                "' ' || COALESCE(value, '') || ' ' || COALESCE(summary, '') "
                "FROM profile_memories WHERE status = 'active') "
                "SELECT source_type, memory_id, content FROM source_records "
                "WHERE ((owner_id = ? AND workspace_id = ?) OR "
                "(owner_id = ? AND workspace_id = ?)) "
                f"AND memory_domain IN ({placeholders})",
                scope_params,
            ).fetchall()
        finally:
            conn.close()
        def calibrated_score(content: object) -> float:
            similarity, _ = CharNgramEmbedder.exact_similarity_evidence(
                query,
                str(content or ""),
            )
            evidence = CharNgramEmbedder.meaningful_similarity_evidence(
                query,
                str(content or ""),
            )
            return _calibrate_local_similarity(
                similarity,
                independent_evidence=evidence,
            )

        ranked = sorted(
            (
                (
                    (str(source_type), str(memory_id)),
                    calibrated_score(content),
                )
                for source_type, memory_id, content in rows
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        self._last_error = ""
        return {
            key: round(score, 6)
            for key, score in ranked[: max(1, min(int(limit), 500))]
            if score > 0.0
        }

    def _search_vec0(
        self,
        conn,
        query_vector: Sequence[float],
        domains: tuple[str, ...],
        owner_id: str,
        workspace_id: str,
        limit: int,
    ) -> dict[tuple[str, str], float]:
        """KNN search via vec0 virtual table with scope/domain filtering."""
        domain_placeholders = ",".join("?" for _ in domains)
        params: list[Any] = [
            _vector_blob(query_vector),
            self.config.provider,
            self.config.model,
            owner_id,
            workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
            *domains,
            limit + 10,  # slight overfetch to account for stale/removed refs
        ]
        rows = conn.execute(
            f"SELECT e.source_type, e.memory_id, e.vector, "
            f"v.distance "
            f"FROM {_VEC0_TABLE} v "
            f"JOIN memory_embeddings e ON e.rowid = v.rowid "
            f"WHERE v.embedding MATCH ? "
            f"AND e.provider = ? AND e.model = ? "
            f"AND ((e.owner_id = ? AND e.workspace_id = ?) OR "
            f"(e.owner_id = ? AND e.workspace_id = ?)) "
            f"AND e.memory_domain IN ({domain_placeholders}) "
            f"AND k = ?",
            params,
        ).fetchall()
        # sqlite-vec returns L2 distance for vec0 by default; convert to
        # cosine-distance-like similarity for compatibility with callers.
        # Use the stored JSON vectors for the final cosine (more precise than
        # recomputing from distance, and keeps the display score consistent).
        results: dict[tuple[str, str], float] = {}
        for source_type, memory_id, vector_json, _vec0_l2_distance in rows:
            try:
                raw_similarity = _cosine(query_vector, json.loads(vector_json))
                similarity = (
                    _calibrate_local_similarity(raw_similarity)
                    if self._local_fallback
                    else raw_similarity
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if similarity > 0.0 and len(results) < limit:
                results[(str(source_type), str(memory_id))] = round(
                    max(0.0, min(1.0, similarity)), 6
                )
        return results

    def _setup_table(self) -> None:
        conn = open_memory_sqlite(self.db_path)
        try:
            existing = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'memory_embeddings'"
            ).fetchone()
            if existing:
                table_info = conn.execute(
                    "PRAGMA table_info(memory_embeddings)"
                ).fetchall()
                primary_key = [
                    str(row[1])
                    for row in sorted(table_info, key=lambda item: int(item[5] or 0))
                    if int(row[5] or 0) > 0
                ]
                columns = {str(row[1]) for row in table_info}
                if (
                    {"owner_id", "workspace_id", "memory_domain"} - columns
                    or primary_key
                    != [
                        "source_type",
                        "memory_id",
                        "owner_id",
                        "workspace_id",
                        "memory_domain",
                        "provider",
                        "model",
                    ]
                ):
                    # Embeddings are derived data. Rebuild instead of preserving an
                    # ambiguous provider-less uniqueness contract.
                    conn.execute("DROP TABLE memory_embeddings")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_embeddings ("
                "source_type TEXT NOT NULL, memory_id TEXT NOT NULL, owner_id TEXT NOT NULL, "
                "workspace_id TEXT NOT NULL, memory_domain TEXT NOT NULL DEFAULT 'agent_interaction', "
                "content_hash TEXT NOT NULL, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL, "
                "vector TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY(source_type, memory_id, owner_id, workspace_id, "
                "memory_domain, provider, model))"
            )
            conn.execute(
                "DROP INDEX IF EXISTS idx_memory_embeddings_scope_v2"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_scope_v3 "
                "ON memory_embeddings(provider, model, owner_id, workspace_id, memory_domain, source_type)"
            )
            conn.execute(
                "DELETE FROM memory_embeddings WHERE source_type = 'compressed' "
                "AND memory_id IN (SELECT memory_id FROM compressed_memories "
                "WHERE identity_layer = 'founding')"
            )
            if _vec0_available(conn):
                dims = self.config.dimensions or 256
                try:
                    # Rebuild the vec0 table on every dimensions change so it
                    # never drifts from the current config (vectors in
                    # memory_eddings are the source of truth; they are
                    # backfilled below).  The CREATE VIRTUAL TABLE is not
                    # IF NOT EXISTS — the preceding DROP ensures a clean
                    # schema.
                    conn.execute(f"DROP TABLE IF EXISTS {_VEC0_TABLE}")
                    conn.execute(
                        f"CREATE VIRTUAL TABLE {_VEC0_TABLE} "
                        f"USING vec0(embedding float[{dims}])"
                    )
                    # Cascade DELETE triggers: when a source row is removed,
                    # clean up both the vec0 entry and the metadata row.
                    for trigger_name, table, id_column, source_type in (
                        ("memory_embeddings_turn_delete", "turns", "turn_id", "turn"),
                        ("memory_embeddings_archive_delete", "turns_archive", "turn_id", "archive"),
                        ("memory_embeddings_compressed_delete", "compressed_memories", "memory_id", "compressed"),
                        ("memory_embeddings_profile_delete", "profile_memories", "memory_id", "profile"),
                    ):
                        conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
                        conn.execute(
                            f"CREATE TRIGGER IF NOT EXISTS {trigger_name} AFTER DELETE ON {table} "
                            "BEGIN "
                            f"DELETE FROM {_VEC0_TABLE} WHERE rowid IN ("
                            "SELECT rowid FROM memory_embeddings WHERE source_type = "
                            f"'{source_type}' AND memory_id = OLD.{id_column} "
                            "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                            "AND memory_domain = OLD.memory_domain); "
                            "DELETE FROM memory_embeddings WHERE source_type = "
                            f"'{source_type}' AND memory_id = OLD.{id_column} "
                            "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                            "AND memory_domain = OLD.memory_domain; "
                            "END"
                        )
                        scope_trigger = f"{trigger_name}_scope_update"
                        conn.execute(f"DROP TRIGGER IF EXISTS {scope_trigger}")
                        conn.execute(
                            f"CREATE TRIGGER IF NOT EXISTS {scope_trigger} "
                            f"AFTER UPDATE OF owner_id, workspace_id, memory_domain ON {table} "
                            "WHEN OLD.owner_id IS NOT NEW.owner_id "
                            "OR OLD.workspace_id IS NOT NEW.workspace_id "
                            "OR OLD.memory_domain IS NOT NEW.memory_domain "
                            "BEGIN "
                            f"DELETE FROM {_VEC0_TABLE} WHERE rowid IN ("
                            "SELECT rowid FROM memory_embeddings WHERE source_type = "
                            f"'{source_type}' AND memory_id = OLD.{id_column} "
                            "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                            "AND memory_domain = OLD.memory_domain); "
                            "DELETE FROM memory_embeddings WHERE source_type = "
                            f"'{source_type}' AND memory_id = OLD.{id_column} "
                            "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                            "AND memory_domain = OLD.memory_domain; END"
                        )
                    # Backfill existing JSON vectors into vec0 (binary blobs).
                    existing = conn.execute(
                        "SELECT rowid, vector FROM memory_embeddings "
                        "WHERE provider = ? AND model = ? AND typeof(vector) = 'text'",
                        (self.config.provider, self.config.model),
                    ).fetchall()
                    for erowid, evector_json in existing:
                        try:
                            evector = json.loads(evector_json)
                            conn.execute(
                                f"INSERT OR IGNORE INTO {_VEC0_TABLE} "
                                f"(rowid, embedding) VALUES (?, ?)",
                                (int(erowid), _vector_blob(evector)),
                            )
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass
                    self._vec0_ready = True
                except Exception:
                    # vec0 setup failed (e.g. source tables missing in a bare
                    # test environment) — silently fall back to the Python
                    # cosine path. The plain DELETE triggers are created below.
                    self._vec0_ready = False
            if not self._vec0_ready:
                for trigger_name, table, id_column, source_type in (
                    ("memory_embeddings_turn_delete", "turns", "turn_id", "turn"),
                    ("memory_embeddings_archive_delete", "turns_archive", "turn_id", "archive"),
                    ("memory_embeddings_compressed_delete", "compressed_memories", "memory_id", "compressed"),
                    ("memory_embeddings_profile_delete", "profile_memories", "memory_id", "profile"),
                ):
                    conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger_name} AFTER DELETE ON {table} "
                        "BEGIN DELETE FROM memory_embeddings WHERE source_type = "
                        f"'{source_type}' AND memory_id = OLD.{id_column} "
                        "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                        "AND memory_domain = OLD.memory_domain; END"
                    )
                    scope_trigger = f"{trigger_name}_scope_update"
                    conn.execute(f"DROP TRIGGER IF EXISTS {scope_trigger}")
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {scope_trigger} "
                        f"AFTER UPDATE OF owner_id, workspace_id, memory_domain ON {table} "
                        "WHEN OLD.owner_id IS NOT NEW.owner_id "
                        "OR OLD.workspace_id IS NOT NEW.workspace_id "
                        "OR OLD.memory_domain IS NOT NEW.memory_domain "
                        "BEGIN DELETE FROM memory_embeddings WHERE source_type = "
                        f"'{source_type}' AND memory_id = OLD.{id_column} "
                        "AND owner_id = OLD.owner_id AND workspace_id = OLD.workspace_id "
                        "AND memory_domain = OLD.memory_domain; END"
                    )
            conn.commit()
        finally:
            conn.close()

    def _pending_records(self, limit: int) -> list[tuple[str, str, str, str, str, str]]:
        conn = open_memory_sqlite(self.db_path)
        try:
            conn.create_function(
                "memory_content_hash",
                1,
                _content_hash,
                deterministic=True,
            )
            dimension_clause = ""
            params: list[Any] = [self.config.provider, self.config.model]
            if self.config.dimensions is not None:
                dimension_clause = " OR embedding.dimensions != ?"
                params.append(self.config.dimensions)
            params.append(max(1, int(limit)))
            rows = conn.execute(
                "WITH source_records(source_type, memory_id, owner_id, workspace_id, memory_domain, content) AS ("
                "SELECT 'turn', turn_id, owner_id, workspace_id, memory_domain, COALESCE(text, '') "
                "FROM turns WHERE compression_status != 'compressed' "
                "UNION ALL SELECT 'archive', turn_id, owner_id, workspace_id, memory_domain, "
                "COALESCE(original_text, text_summary, '') FROM turns_archive "
                "UNION ALL SELECT 'compressed', memory_id, owner_id, workspace_id, memory_domain, "
                "COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || "
                "COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
                "FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "AND COALESCE(identity_layer, '') != 'founding' "
                "UNION ALL SELECT 'profile', memory_id, owner_id, workspace_id, memory_domain, "
                "COALESCE(subject, '') || ' ' || COALESCE(predicate, '') || ' ' || "
                "COALESCE(value, '') || ' ' || COALESCE(summary, '') "
                "FROM profile_memories WHERE status = 'active') "
                "SELECT source.source_type, source.memory_id, source.owner_id, "
                "source.workspace_id, source.memory_domain, source.content FROM source_records AS source "
                "LEFT JOIN memory_embeddings AS embedding ON "
                "embedding.source_type = source.source_type AND "
                "embedding.memory_id = source.memory_id AND "
                "embedding.owner_id = source.owner_id AND "
                "embedding.workspace_id = source.workspace_id AND "
                "embedding.memory_domain = source.memory_domain AND "
                "embedding.provider = ? AND embedding.model = ? "
                "WHERE embedding.memory_id IS NULL OR "
                "embedding.content_hash != memory_content_hash(source.content)"
                + dimension_clause
                + " ORDER BY source.source_type, source.memory_id LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [
            (
                str(row[0] or ""),
                str(row[1] or ""),
                str(row[2] or ""),
                str(row[3] or ""),
                str(row[4] or ""),
                str(row[5] or ""),
            )
            for row in rows
        ]

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._transport is not None:
            return self._transport(texts)
        payload: dict[str, Any] = {"model": self.config.model, "input": list(texts)}
        if self.config.dimensions:
            payload["dimensions"] = self.config.dimensions
        request = Request(
            f"{self.config.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key or 'no-key-required'}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = sorted(body.get("data") or [], key=lambda item: int(item.get("index", 0)))
        return [list(map(float, item["embedding"])) for item in data]

    def _validate_vector(self, vector: Sequence[float]) -> None:
        if not vector or any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("Embedding vector is empty or non-finite")
        if self.config.dimensions and len(vector) != self.config.dimensions:
            raise ValueError("Embedding vector dimensions do not match configured dimensions")


def _content_hash(content: str) -> str:
    return hashlib.sha256(str(content).encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
