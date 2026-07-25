"""Versioned embedding index for optional semantic memory recall."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.request import Request, urlopen

from systems.memory.scope import GLOBAL_SCOPE_ID
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite


EmbeddingTransport = Callable[[Sequence[str]], list[list[float]]]


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
    def from_voidcube_config(cls) -> "SemanticIndexConfig":
        try:
            from VoidCube_cli.config import get_env_value, load_config

            raw = dict(load_config().get("memory", {}).get("semantic_recall", {}) or {})
            api_key_env = str(raw.get("api_key_env") or "").strip()
            api_key = str(get_env_value(api_key_env) or "") if api_key_env else ""
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
        return bool(
            self.enabled
            and self.provider
            and self.model
            and self.base_url
            and (self.api_key or self.provider == "ollama")
        )


class SemanticMemoryIndex:
    def __init__(
        self,
        db_path: str | Path,
        config: SemanticIndexConfig | None = None,
        *,
        transport: EmbeddingTransport | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or SemanticIndexConfig.from_voidcube_config()
        self._transport = transport
        self._last_error = ""
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
            "last_error": self._last_error,
        }

    def index_pending(self, limit: int | None = None) -> int:
        if not self.enabled:
            return 0
        records = self._pending_records(
            limit or self.config.backfill_batch_size
        )
        if not records:
            return 0
        try:
            vectors = self._embed([record[4] for record in records])
            if len(vectors) != len(records):
                raise ValueError("Embedding response count does not match input count")
            conn = open_memory_sqlite(self.db_path)
            try:
                now = datetime.now(timezone.utc).isoformat()
                for record, vector in zip(records, vectors):
                    source_type, memory_id, owner_id, workspace_id, content = record
                    self._validate_vector(vector)
                    conn.execute(
                        "INSERT INTO memory_embeddings "
                        "(source_type, memory_id, owner_id, workspace_id, content_hash, "
                        "provider, model, dimensions, vector, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source_type, memory_id, provider, model) DO UPDATE SET "
                        "owner_id = excluded.owner_id, workspace_id = excluded.workspace_id, "
                        "content_hash = excluded.content_hash, provider = excluded.provider, "
                        "dimensions = excluded.dimensions, vector = excluded.vector, "
                        "updated_at = excluded.updated_at",
                        (
                            source_type,
                            memory_id,
                            owner_id,
                            workspace_id,
                            _content_hash(content),
                            self.config.provider,
                            self.config.model,
                            len(vector),
                            json.dumps(vector, separators=(",", ":")),
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
            self._last_error = ""
            return len(records)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return 0

    def search(
        self,
        query: str,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int = 50,
    ) -> dict[tuple[str, str], float]:
        if not self.enabled or not str(query or "").strip():
            return {}
        try:
            query_vector = self._embed([str(query)])[0]
            self._validate_vector(query_vector)
            conn = open_memory_sqlite(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT source_type, memory_id, vector FROM memory_embeddings "
                    "WHERE provider = ? AND model = ? AND dimensions = ? "
                    "AND ((owner_id = ? AND workspace_id = ?) OR "
                    "(owner_id = ? AND workspace_id = ?))",
                    (
                        self.config.provider,
                        self.config.model,
                        len(query_vector),
                        owner_id,
                        workspace_id,
                        GLOBAL_SCOPE_ID,
                        GLOBAL_SCOPE_ID,
                    ),
                ).fetchall()
            finally:
                conn.close()
            ranked = sorted(
                (
                    ((str(row[0]), str(row[1])), _cosine(query_vector, json.loads(row[2])))
                    for row in rows
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            self._last_error = ""
            return {
                key: round(max(0.0, min(1.0, score)), 6)
                for key, score in ranked[: max(1, min(int(limit), 500))]
                if score > 0.0
            }
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return {}

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
                if primary_key != ["source_type", "memory_id", "provider", "model"]:
                    # Embeddings are derived data. Rebuild instead of preserving an
                    # ambiguous provider-less uniqueness contract.
                    conn.execute("DROP TABLE memory_embeddings")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_embeddings ("
                "source_type TEXT NOT NULL, memory_id TEXT NOT NULL, owner_id TEXT NOT NULL, "
                "workspace_id TEXT NOT NULL, content_hash TEXT NOT NULL, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL, "
                "vector TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY(source_type, memory_id, provider, model))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_scope_v2 "
                "ON memory_embeddings(provider, model, owner_id, workspace_id, source_type)"
            )
            for trigger_name, table, id_column, source_type in (
                ("memory_embeddings_turn_delete", "turns", "turn_id", "turn"),
                ("memory_embeddings_archive_delete", "turns_archive", "turn_id", "archive"),
                (
                    "memory_embeddings_compressed_delete",
                    "compressed_memories",
                    "memory_id",
                    "compressed",
                ),
                (
                    "memory_embeddings_profile_delete",
                    "profile_memories",
                    "memory_id",
                    "profile",
                ),
            ):
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger_name} AFTER DELETE ON {table} "
                    "BEGIN DELETE FROM memory_embeddings WHERE source_type = "
                    f"'{source_type}' AND memory_id = OLD.{id_column}; END"
                )
            conn.commit()
        finally:
            conn.close()

    def _pending_records(self, limit: int) -> list[tuple[str, str, str, str, str]]:
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
                "WITH source_records(source_type, memory_id, owner_id, workspace_id, content) AS ("
                "SELECT 'turn', turn_id, owner_id, workspace_id, COALESCE(text, '') "
                "FROM turns WHERE compressed_to_tier2 = 0 "
                "UNION ALL SELECT 'archive', turn_id, owner_id, workspace_id, "
                "COALESCE(original_text, text_summary, '') FROM turns_archive "
                "UNION ALL SELECT 'compressed', memory_id, owner_id, workspace_id, "
                "COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || "
                "COALESCE(topics, '') || ' ' || COALESCE(entities, '') "
                "FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
                "UNION ALL SELECT 'profile', memory_id, owner_id, workspace_id, "
                "COALESCE(subject, '') || ' ' || COALESCE(predicate, '') || ' ' || "
                "COALESCE(value, '') || ' ' || COALESCE(summary, '') "
                "FROM profile_memories WHERE status = 'active') "
                "SELECT source.source_type, source.memory_id, source.owner_id, "
                "source.workspace_id, source.content FROM source_records AS source "
                "LEFT JOIN memory_embeddings AS embedding ON "
                "embedding.source_type = source.source_type AND "
                "embedding.memory_id = source.memory_id AND embedding.provider = ? AND "
                "embedding.model = ? WHERE embedding.memory_id IS NULL OR "
                "embedding.content_hash != memory_content_hash(source.content) OR "
                "embedding.owner_id != source.owner_id OR "
                "embedding.workspace_id != source.workspace_id"
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
