"""Canonical SQLite connection and schema bootstrap for Memory."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from memai.repository.backup import MemoryBackupManager
from memai.indexes.lexical_index import setup_memory_fts
from memai.application.promotion import setup_memory_promotion_schema
from memai.domain.resource_contract import (
    LEGACY_HEURISTIC_PROFILE_PREDICATES,
    TurnCompressionStatus,
    expected_timeline_parent_type,
    is_derived_relation,
    profile_slot_key,
)
from memai.migrations.runtime_migration import migrate_memory_database
from memai.repository.sqlite import open_memory_sqlite
from memai.domain.scope import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryDatabaseBootstrap:
    """Own one ordered migration and schema initialization sequence."""

    db_path: Path
    backup_manager: MemoryBackupManager

    def initialize(self) -> None:
        self._migrate_legacy_default_database()
        self._backup_before_destructive_schema_migration()
        self._setup_schema()

    def reconcile_schema(self) -> None:
        """Reconcile schema after a validated database restore."""
        self._setup_schema()

    def _migrate_legacy_default_database(self) -> None:
        from memai.repository.paths import (
            get_legacy_memory_db,
            get_mem_runtime_layout,
        )

        canonical = get_mem_runtime_layout().memory_db
        if self.db_path.resolve() != canonical.resolve():
            return
        result = migrate_memory_database(
            source=get_legacy_memory_db(Path.cwd()),
            target=canonical,
        )
        if result.status == "migrated":
            logger.info(
                "Migrated Memory database from %s to %s (integrity=%s)",
                result.source,
                result.target,
                result.integrity_check,
            )

    def _backup_before_destructive_schema_migration(self) -> None:
        if not self.db_path.is_file():
            return
        connection = open_memory_sqlite(self.db_path)
        reasons: list[str] = []
        try:
            compressed_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'compressed_memories'"
            ).fetchone()
            if compressed_exists:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(compressed_memories)"
                    ).fetchall()
                }
                if "embedding" in columns:
                    reasons.append("compressed_memories.embedding")
                if "parent_id" in columns:
                    reasons.append("compressed_memories.parent_id")
            turns_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'turns'"
            ).fetchone()
            if turns_exists:
                turn_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(turns)").fetchall()
                }
                if "compressed_to_tier2" in turn_columns:
                    reasons.append("turns.compressed_to_tier2")
            obsolete_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'memories'"
            ).fetchone()
            if obsolete_exists:
                obsolete_count = int(
                    connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                )
                if obsolete_count == 0:
                    reasons.append("empty obsolete memories table")
        finally:
            connection.close()
        if not reasons:
            return
        backup = self.backup_manager.create_backup()
        logger.info(
            "Created pre-migration Memory backup %s before removing %s",
            backup["backup_id"],
            ", ".join(reasons),
        )

    def _setup_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = open_memory_sqlite(self.db_path)
        try:
            cursor = connection.cursor()
            self._drop_empty_obsolete_memories_table(cursor)
            self._create_tables(cursor)
            setup_memory_promotion_schema(connection)
            self._migrate_scope_schema(cursor)
            self._migrate_domain_schema(cursor)
            quarantined = self._quarantine_evaluation_memories(cursor)
            self._setup_subsystem_schema(connection)
            self._create_indexes(cursor)
            connection.commit()
            if quarantined:
                logger.warning(
                    "Quarantined %d compressed memories sourced from evaluation turns",
                    quarantined,
                )
        finally:
            connection.close()

    @staticmethod
    def _drop_empty_obsolete_memories_table(cursor: sqlite3.Cursor) -> None:
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
        ).fetchone()
        if not exists:
            return
        count = int(cursor.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        if count:
            logger.warning(
                "Preserving obsolete memories table because it contains %d rows; "
                "the table is not part of active Memory recall",
                count,
            )
            return
        cursor.execute("DROP TABLE memories")
        logger.info("Removed empty obsolete memories table")

    def _create_tables(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                metadata TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                relevance_score REAL DEFAULT 1.0,
                decay_factor REAL DEFAULT 0.01,
                tags TEXT,
                metadata TEXT,
                dedup_key TEXT,
                compression_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(compression_status IN (
                        'pending', 'retry_wait', 'compressed', 'quality_quarantined'
                    )),
                compression_retry_count INTEGER NOT NULL DEFAULT 0,
                compression_retry_after TEXT,
                last_decay_at TEXT,
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        self._migrate_turns_schema(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS turns_archive (
                turn_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text_summary TEXT,
                original_text TEXT,
                timestamp TEXT NOT NULL,
                compressed_at TEXT NOT NULL,
                event_ids TEXT,
                scene_ids TEXT,
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default'
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS compression_quality_audit (
                audit_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                evaluated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                event_count INTEGER NOT NULL,
                covered_turn_count INTEGER NOT NULL,
                event_coverage REAL NOT NULL,
                backlinked_event_count INTEGER NOT NULL,
                backlink_completeness REAL NOT NULL,
                source_chars INTEGER NOT NULL,
                event_summary_chars INTEGER NOT NULL,
                compression_ratio REAL NOT NULL,
                degraded_event_count INTEGER NOT NULL,
                degraded_fraction REAL NOT NULL,
                source_supported_event_count INTEGER NOT NULL DEFAULT 0,
                source_support REAL NOT NULL DEFAULT 0,
                identifier_fidelity REAL NOT NULL DEFAULT 0,
                polarity_consistency REAL NOT NULL DEFAULT 0,
                unsupported_identifiers TEXT NOT NULL DEFAULT '[]',
                thresholds TEXT NOT NULL,
                failed_checks TEXT NOT NULL,
                sample_turn_ids TEXT NOT NULL
            )
            """
        )
        quality_columns = self._column_names(cursor, "compression_quality_audit")
        for column, definition in (
            ("source_supported_event_count", "INTEGER NOT NULL DEFAULT 0"),
            ("source_support", "REAL NOT NULL DEFAULT 0"),
            ("identifier_fidelity", "REAL NOT NULL DEFAULT 0"),
            ("polarity_consistency", "REAL NOT NULL DEFAULT 0"),
            ("unsupported_identifiers", "TEXT NOT NULL DEFAULT '[]'"),
            ("owner_id", "TEXT NOT NULL DEFAULT 'local-user'"),
            ("workspace_id", "TEXT NOT NULL DEFAULT 'default'"),
        ):
            if column not in quality_columns:
                cursor.execute(
                    f"ALTER TABLE compression_quality_audit ADD COLUMN "
                    f"{column} {definition}"
                )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_traces (
                trace_id TEXT PRIMARY KEY,
                memory_actor TEXT NOT NULL DEFAULT 'api_a',
                source_domains TEXT NOT NULL DEFAULT '["agent_interaction"]',
                created_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                request_source TEXT NOT NULL,
                session_id TEXT,
                query TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT,
                query_plan TEXT,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                selected_results TEXT NOT NULL DEFAULT '[]',
                context_chars INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                error_type TEXT,
                error_detail TEXT,
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default'
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_feedback (
                feedback_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                trace_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                reason TEXT,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(trace_id, memory_id, owner_id, workspace_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_deletion_audit (
                audit_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                target_kind TEXT NOT NULL,
                target_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                deleted_counts TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_memory_tombstones (
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                revoked_at TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                evidence_turns TEXT NOT NULL DEFAULT '[]',
                reason_hash TEXT NOT NULL,
                PRIMARY KEY(
                    owner_id, workspace_id, memory_domain, subject, predicate
                )
            )
            """
        )
        if "evidence_turns" not in self._column_names(
            cursor, "profile_memory_tombstones"
        ):
            cursor.execute(
                "ALTER TABLE profile_memory_tombstones "
                "ADD COLUMN evidence_turns TEXT NOT NULL DEFAULT '[]'"
            )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS compressed_memories (
                memory_id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                timespan_start TEXT NOT NULL,
                timespan_end TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                confidence REAL DEFAULT 0.5,
                topics TEXT,
                entities TEXT,
                source_turns TEXT,
                timeline_parent_id TEXT,
                derived_from_id TEXT,
                compressed_at TEXT NOT NULL,
                compression_level INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                superseded_by TEXT,
                weight REAL DEFAULT 1.0,
                event_kind TEXT,
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                citation_count INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0,
                identity_layer TEXT,
                evidence_refs TEXT,
                origin_type TEXT,
                origin_id TEXT,
                verified_at TEXT,
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                created_at TEXT,
                lifecycle_retry_count INTEGER NOT NULL DEFAULT 0,
                lifecycle_retry_after TEXT,
                lifecycle_last_error TEXT,
                identity_metadata TEXT
            )
            """
        )
        self._migrate_compressed_memories_schema(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_memories (
                memory_id TEXT PRIMARY KEY,
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                memory_kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                slot_key TEXT NOT NULL,
                value TEXT NOT NULL,
                summary TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                certainty_state TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                evidence_refs TEXT NOT NULL DEFAULT '[]',
                source_turns TEXT NOT NULL DEFAULT '[]',
                supersedes TEXT NOT NULL DEFAULT '[]',
                conflict_refs TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                capture_source TEXT NOT NULL DEFAULT 'explicit_user'
            )
            """
        )
        self._migrate_profile_memories_schema(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS time_summaries (
                summary_id TEXT PRIMARY KEY,
                summary_type TEXT NOT NULL
                    CHECK(summary_type IN ('session', 'day', 'week', 'month')),
                owner_id TEXT NOT NULL DEFAULT 'local-user',
                workspace_id TEXT NOT NULL DEFAULT 'default',
                memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
                bucket_key TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                timezone TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                outcomes TEXT NOT NULL DEFAULT '[]'
                    CHECK(json_valid(outcomes) AND json_type(outcomes) = 'array'),
                open_questions TEXT NOT NULL DEFAULT '[]'
                    CHECK(
                        json_valid(open_questions)
                        AND json_type(open_questions) = 'array'
                    ),
                source_count INTEGER NOT NULL DEFAULT 0 CHECK(source_count >= 0),
                source_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'superseded')),
                supersedes_summary_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(
                    owner_id, workspace_id, memory_domain,
                    summary_type, bucket_key, version
                ),
                FOREIGN KEY (supersedes_summary_id)
                    REFERENCES time_summaries(summary_id)
            )
            """
        )
        self._migrate_time_summaries_schema(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS time_summary_links (
                parent_summary_id TEXT NOT NULL,
                child_summary_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (parent_summary_id, child_summary_id),
                CHECK(parent_summary_id <> child_summary_id),
                FOREIGN KEY (parent_summary_id)
                    REFERENCES time_summaries(summary_id),
                FOREIGN KEY (child_summary_id)
                    REFERENCES time_summaries(summary_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summary_sources (
                summary_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                turn_timestamp TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                PRIMARY KEY (summary_id, turn_id),
                UNIQUE (summary_id, ordinal),
                FOREIGN KEY (summary_id) REFERENCES time_summaries(summary_id)
            )
            """
        )
        self._create_time_summary_triggers(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_revision_proposals (
                proposal_id TEXT PRIMARY KEY,
                target_memory_id TEXT NOT NULL,
                baseline_version TEXT NOT NULL,
                reason TEXT NOT NULL,
                proposed_changes TEXT NOT NULL,
                evidence TEXT NOT NULL,
                source_actor TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_reason TEXT,
                decided_by TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                release_version TEXT,
                released_at TEXT
            )
            """
        )
        revision_columns = self._column_names(cursor, "identity_revision_proposals")
        if "release_version" not in revision_columns:
            cursor.execute(
                "ALTER TABLE identity_revision_proposals "
                "ADD COLUMN release_version TEXT"
            )
        if "released_at" not in revision_columns:
            cursor.execute(
                "ALTER TABLE identity_revision_proposals ADD COLUMN released_at TEXT"
            )

    def _setup_subsystem_schema(self, connection: sqlite3.Connection) -> None:
        from memai.indexes.entity_graph import setup_entity_graph
        from memai.application.identity_seed import (
            ensure_founding_memories,
            reconcile_released_identity_revisions,
        )
        from memai.repository.llm_cache import setup_llm_cache
        from memai.application.maintenance_schedule import setup_memory_rule_state

        setup_memory_fts(connection)
        setup_entity_graph(connection)
        setup_llm_cache(connection)
        setup_memory_rule_state(connection)
        seeded = ensure_founding_memories(connection)
        released = reconcile_released_identity_revisions(connection)
        logger.info(
            "Memory database initialized at %s "
            "(founding identity rows added: %d, identity revisions released: %d)",
            self.db_path,
            seeded,
            released,
        )

    @staticmethod
    def _quarantine_evaluation_memories(cursor: sqlite3.Cursor) -> int:
        rows = cursor.execute(
            "SELECT DISTINCT cm.memory_id, cm.owner_id, cm.workspace_id, cm.memory_domain "
            "FROM compressed_memories AS cm "
            "JOIN json_each(CASE WHEN json_valid(COALESCE(cm.source_turns, '[]')) "
            "THEN cm.source_turns ELSE '[]' END) AS source "
            "JOIN turns AS turn_row ON turn_row.turn_id = CAST(source.value AS TEXT) "
            "WHERE cm.hidden = 0 AND json_valid(COALESCE(turn_row.tags, '[]')) "
            "AND EXISTS (SELECT 1 FROM json_each(turn_row.tags) AS tag "
            "WHERE lower(CAST(tag.value AS TEXT)) = 'evaluation')"
        ).fetchall()
        if not rows:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        for memory_id, owner_id, workspace_id, memory_domain in rows:
            cursor.execute(
                "UPDATE compressed_memories SET hidden = 1 WHERE memory_id = ?",
                (memory_id,),
            )
            audit_identity = "\0".join(
                (
                    str(owner_id),
                    str(workspace_id),
                    str(memory_domain),
                    str(memory_id),
                )
            )
            digest = hashlib.sha256(audit_identity.encode("utf-8")).hexdigest()
            cursor.execute(
                "INSERT OR IGNORE INTO memory_deletion_audit "
                "(audit_id, memory_domain, target_kind, target_hash, reason, "
                "deleted_counts, owner_id, workspace_id, created_at) "
                "VALUES (?, ?, 'compressed_memory_quarantine', ?, ?, ?, ?, ?, ?)",
                (
                    f"evaluation-quarantine-{digest[:24]}",
                    str(memory_domain),
                    hashlib.sha256(str(memory_id).encode("utf-8")).hexdigest(),
                    "Excluded compressed memory sourced from evaluation turns",
                    json.dumps({"compressed_memories_hidden": 1}, sort_keys=True),
                    str(owner_id),
                    str(workspace_id),
                    now,
                ),
            )
        return len(rows)

    @staticmethod
    def _column_names(cursor: sqlite3.Cursor, table: str) -> set[str]:
        return {
            str(row[1])
            for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _migrate_turns_schema(self, cursor: sqlite3.Cursor) -> None:
        existing = self._column_names(cursor, "turns")
        if "dedup_key" not in existing:
            cursor.execute("ALTER TABLE turns ADD COLUMN dedup_key TEXT")
        if "last_decay_at" not in existing:
            cursor.execute("ALTER TABLE turns ADD COLUMN last_decay_at TEXT")
        if "compression_retry_count" not in existing:
            cursor.execute(
                "ALTER TABLE turns ADD COLUMN compression_retry_count "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "compression_retry_after" not in existing:
            cursor.execute("ALTER TABLE turns ADD COLUMN compression_retry_after TEXT")
        if "compression_status" not in existing:
            cursor.execute(
                "ALTER TABLE turns ADD COLUMN compression_status TEXT NOT NULL "
                "DEFAULT 'pending'"
            )
        if "compressed_to_tier2" in existing:
            cursor.execute(
                "UPDATE turns SET compression_status = CASE "
                "WHEN compressed_to_tier2 = 1 THEN ? "
                "WHEN compressed_to_tier2 = -1 THEN ? "
                "WHEN compression_retry_count > 0 "
                "AND compression_retry_after IS NOT NULL THEN ? ELSE ? END",
                (
                    TurnCompressionStatus.COMPRESSED.value,
                    TurnCompressionStatus.QUALITY_QUARANTINED.value,
                    TurnCompressionStatus.RETRY_WAIT.value,
                    TurnCompressionStatus.PENDING.value,
                ),
            )
            cursor.execute("DROP INDEX IF EXISTS idx_turns_compressed")
            cursor.execute("DROP INDEX IF EXISTS idx_turns_scope_time")
            cursor.execute("ALTER TABLE turns DROP COLUMN compressed_to_tier2")

    def _migrate_scope_schema(self, cursor: sqlite3.Cursor) -> None:
        for table in (
            "sessions",
            "turns",
            "turns_archive",
            "compressed_memories",
            "recall_traces",
        ):
            columns = self._column_names(cursor, table)
            if "owner_id" not in columns:
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN owner_id TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_OWNER_ID}'"
                )
            if "workspace_id" not in columns:
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN workspace_id TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_WORKSPACE_ID}'"
                )

    def _migrate_domain_schema(self, cursor: sqlite3.Cursor) -> None:
        column_definitions = {
            "sessions": ("memory_domain", "TEXT NOT NULL DEFAULT 'agent_interaction'"),
            "turns": ("memory_domain", "TEXT NOT NULL DEFAULT 'agent_interaction'"),
            "turns_archive": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
            "compressed_memories": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
            "profile_memories": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
            "compression_quality_audit": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
            "recall_traces": (
                "source_domains",
                "TEXT NOT NULL DEFAULT '[\"agent_interaction\"]'",
            ),
            "recall_feedback": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
            "memory_deletion_audit": (
                "memory_domain",
                "TEXT NOT NULL DEFAULT 'agent_interaction'",
            ),
        }
        for table, (column, definition) in column_definitions.items():
            if column not in self._column_names(cursor, table):
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if "memory_actor" not in self._column_names(cursor, "recall_traces"):
            cursor.execute(
                "ALTER TABLE recall_traces ADD COLUMN memory_actor TEXT NOT NULL "
                "DEFAULT 'api_a'"
            )

        tombstone_info = cursor.execute(
            "PRAGMA table_info(profile_memory_tombstones)"
        ).fetchall()
        tombstone_pk = [
            str(row[1])
            for row in sorted(tombstone_info, key=lambda row: int(row[5] or 0))
            if int(row[5] or 0) > 0
        ]
        expected_pk = [
            "owner_id",
            "workspace_id",
            "memory_domain",
            "subject",
            "predicate",
        ]
        if tombstone_pk == expected_pk:
            return
        has_domain = any(str(row[1]) == "memory_domain" for row in tombstone_info)
        cursor.execute("SAVEPOINT profile_tombstone_domain_migration")
        try:
            cursor.execute(
                "CREATE TABLE profile_memory_tombstones_domain_migration ("
                "owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, "
                "memory_domain TEXT NOT NULL DEFAULT 'agent_interaction', "
                "subject TEXT NOT NULL, predicate TEXT NOT NULL, revoked_at TEXT NOT NULL, "
                "source_turn_id TEXT NOT NULL, evidence_turns TEXT NOT NULL DEFAULT '[]', "
                "reason_hash TEXT NOT NULL, PRIMARY KEY(owner_id, workspace_id, "
                "memory_domain, subject, predicate))"
            )
            domain_expression = "memory_domain" if has_domain else "'agent_interaction'"
            cursor.execute(
                "INSERT INTO profile_memory_tombstones_domain_migration "
                "(owner_id, workspace_id, memory_domain, subject, predicate, revoked_at, "
                "source_turn_id, evidence_turns, reason_hash) SELECT owner_id, workspace_id, "
                f"{domain_expression}, subject, predicate, revoked_at, source_turn_id, "
                "evidence_turns, reason_hash FROM profile_memory_tombstones"
            )
            cursor.execute("DROP TABLE profile_memory_tombstones")
            cursor.execute(
                "ALTER TABLE profile_memory_tombstones_domain_migration "
                "RENAME TO profile_memory_tombstones"
            )
        except Exception:
            cursor.execute("ROLLBACK TO profile_tombstone_domain_migration")
            cursor.execute("RELEASE profile_tombstone_domain_migration")
            raise
        cursor.execute("RELEASE profile_tombstone_domain_migration")

    def _migrate_compressed_memories_schema(self, cursor: sqlite3.Cursor) -> None:
        existing = self._column_names(cursor, "compressed_memories")
        migrations = (
            ("compression_level", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'active'"),
            ("superseded_by", "TEXT"),
            ("weight", "REAL DEFAULT 1.0"),
            ("created_at", "TEXT"),
            ("event_kind", "TEXT"),
            ("access_count", "INTEGER DEFAULT 0"),
            ("last_accessed_at", "TEXT"),
            ("citation_count", "INTEGER DEFAULT 0"),
            ("pinned", "INTEGER DEFAULT 0"),
            ("hidden", "INTEGER DEFAULT 0"),
            ("identity_layer", "TEXT"),
            ("evidence_refs", "TEXT"),
            ("origin_type", "TEXT"),
            ("origin_id", "TEXT"),
            ("verified_at", "TEXT"),
            ("lifecycle_retry_count", "INTEGER NOT NULL DEFAULT 0"),
            ("lifecycle_retry_after", "TEXT"),
            ("lifecycle_last_error", "TEXT"),
            ("identity_metadata", "TEXT"),
            ("timeline_parent_id", "TEXT"),
            ("derived_from_id", "TEXT"),
        )
        for column, definition in migrations:
            if column not in existing:
                cursor.execute(
                    f"ALTER TABLE compressed_memories ADD COLUMN "
                    f"{column} {definition}"
                )
        cursor.execute(
            "UPDATE compressed_memories SET created_at = compressed_at "
            "WHERE created_at IS NULL"
        )
        if "embedding" in existing:
            cursor.execute("ALTER TABLE compressed_memories DROP COLUMN embedding")
        if "parent_id" in existing:
            scoped_columns = self._column_names(cursor, "compressed_memories")
            owner_expression = (
                "owner_id" if "owner_id" in scoped_columns else f"'{DEFAULT_OWNER_ID}'"
            )
            workspace_expression = (
                "workspace_id"
                if "workspace_id" in scoped_columns
                else f"'{DEFAULT_WORKSPACE_ID}'"
            )
            domain_expression = (
                "memory_domain"
                if "memory_domain" in scoped_columns
                else "'agent_interaction'"
            )
            rows = cursor.execute(
                "SELECT memory_id, memory_type, parent_id, "
                f"{owner_expression}, {workspace_expression}, {domain_expression} "
                "FROM compressed_memories WHERE parent_id IS NOT NULL"
            ).fetchall()
            types_by_scope = {
                (str(row[0]), str(row[2]), str(row[3]), str(row[4])): str(row[1])
                for row in cursor.execute(
                    "SELECT memory_id, memory_type, "
                    f"{owner_expression}, {workspace_expression}, {domain_expression} "
                    "FROM compressed_memories"
                ).fetchall()
            }
            repaired_dangling = 0
            for memory_id, memory_type, parent_id, owner_id, workspace_id, domain in rows:
                referenced_type = types_by_scope.get(
                    (str(parent_id), str(owner_id), str(workspace_id), str(domain))
                )
                timeline_parent_id = None
                derived_from_id = None
                if referenced_type == expected_timeline_parent_type(memory_type):
                    timeline_parent_id = parent_id
                elif referenced_type and is_derived_relation(memory_type, referenced_type):
                    derived_from_id = parent_id
                elif referenced_type:
                    derived_from_id = parent_id
                else:
                    repaired_dangling += 1
                cursor.execute(
                    "UPDATE compressed_memories SET timeline_parent_id = ?, "
                    "derived_from_id = ? WHERE memory_id = ?",
                    (timeline_parent_id, derived_from_id, memory_id),
                )
            cursor.execute("ALTER TABLE compressed_memories DROP COLUMN parent_id")
            if repaired_dangling:
                logger.warning(
                    "Removed %d dangling compressed-memory parent references",
                    repaired_dangling,
                )

    def _migrate_profile_memories_schema(self, cursor: sqlite3.Cursor) -> None:
        existing = self._column_names(cursor, "profile_memories")
        if "slot_key" not in existing:
            cursor.execute(
                "ALTER TABLE profile_memories ADD COLUMN slot_key TEXT NOT NULL DEFAULT ''"
            )
        if "capture_source" not in existing:
            cursor.execute(
                "ALTER TABLE profile_memories ADD COLUMN capture_source TEXT NOT NULL "
                "DEFAULT 'legacy_tier2'"
            )
        rows = cursor.execute(
            "SELECT memory_id, predicate, value FROM profile_memories "
            "WHERE slot_key = ''"
        ).fetchall()
        cursor.executemany(
            "UPDATE profile_memories SET slot_key = ? WHERE memory_id = ?",
            [
                (profile_slot_key(predicate, value), memory_id)
                for memory_id, predicate, value in rows
            ],
        )
        if LEGACY_HEURISTIC_PROFILE_PREDICATES:
            placeholders = ",".join("?" for _ in LEGACY_HEURISTIC_PROFILE_PREDICATES)
            cursor.execute(
                "UPDATE profile_memories SET status = 'quarantined', "
                "valid_to = COALESCE(valid_to, updated_at) "
                f"WHERE status = 'active' AND predicate IN ({placeholders}) "
                "AND capture_source = 'legacy_tier2'",
                tuple(sorted(LEGACY_HEURISTIC_PROFILE_PREDICATES)),
            )

    def _migrate_time_summaries_schema(self, cursor: sqlite3.Cursor) -> None:
        existing = self._column_names(cursor, "time_summaries")
        if "source_hash" not in existing:
            cursor.execute(
                "ALTER TABLE time_summaries ADD COLUMN source_hash "
                "TEXT NOT NULL DEFAULT ''"
            )

    @staticmethod
    def _create_time_summary_triggers(cursor: sqlite3.Cursor) -> None:
        immutable_columns = (
            "summary_type",
            "owner_id",
            "workspace_id",
            "memory_domain",
            "bucket_key",
            "period_start",
            "period_end",
            "timezone",
            "title",
            "summary",
            "outcomes",
            "open_questions",
            "source_count",
            "source_hash",
            "content_hash",
            "version",
            "supersedes_summary_id",
            "created_at",
        )
        mutation_condition = " OR ".join(
            f"OLD.{column} IS NOT NEW.{column}" for column in immutable_columns
        )
        cursor.execute(
            "CREATE TRIGGER IF NOT EXISTS prevent_time_summary_version_mutation "
            f"BEFORE UPDATE OF {', '.join(immutable_columns)} ON time_summaries "
            f"WHEN {mutation_condition} BEGIN "
            "SELECT RAISE(ABORT, 'time summary versions are immutable'); END"
        )

        valid_direct_link = (
            "EXISTS (SELECT 1 FROM time_summaries AS parent "
            "JOIN time_summaries AS child "
            "ON child.summary_id = NEW.child_summary_id "
            "WHERE parent.summary_id = NEW.parent_summary_id "
            "AND parent.owner_id = child.owner_id "
            "AND parent.workspace_id = child.workspace_id "
            "AND parent.memory_domain = child.memory_domain "
            "AND ((parent.summary_type = 'month' AND child.summary_type = 'week') "
            "OR (parent.summary_type = 'week' AND child.summary_type = 'day') "
            "OR (parent.summary_type = 'day' AND child.summary_type = 'session')))"
        )
        for operation in ("INSERT", "UPDATE"):
            cursor.execute(
                f"CREATE TRIGGER IF NOT EXISTS validate_time_summary_link_"
                f"{operation.lower()} BEFORE {operation} ON time_summary_links "
                f"WHEN NOT {valid_direct_link} BEGIN "
                "SELECT RAISE(ABORT, 'time summary links require same-scope "
                "direct levels'); END"
            )

        valid_supersession = (
            "NEW.supersedes_summary_id IS NULL OR EXISTS ("
            "SELECT 1 FROM time_summaries AS previous "
            "WHERE previous.summary_id = NEW.supersedes_summary_id "
            "AND previous.owner_id = NEW.owner_id "
            "AND previous.workspace_id = NEW.workspace_id "
            "AND previous.memory_domain = NEW.memory_domain "
            "AND previous.summary_type = NEW.summary_type "
            "AND previous.bucket_key = NEW.bucket_key "
            "AND previous.version < NEW.version)"
        )
        for operation in ("INSERT", "UPDATE"):
            cursor.execute(
                f"CREATE TRIGGER IF NOT EXISTS validate_time_summary_supersession_"
                f"{operation.lower()} BEFORE {operation} ON time_summaries "
                f"WHEN NOT ({valid_supersession}) BEGIN "
                "SELECT RAISE(ABORT, 'time summary supersession requires an older "
                "version of the same bucket'); END"
            )

        cursor.execute(
            "CREATE TRIGGER IF NOT EXISTS validate_session_summary_source_insert "
            "BEFORE INSERT ON session_summary_sources WHEN NOT EXISTS ("
            "SELECT 1 FROM time_summaries WHERE summary_id = NEW.summary_id "
            "AND summary_type = 'session') BEGIN "
            "SELECT RAISE(ABORT, 'session summary sources require a session summary'); "
            "END"
        )

    @staticmethod
    def _create_indexes(cursor: sqlite3.Cursor) -> None:
        statements = (
            "CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_turns_relevance ON turns(relevance_score)",
            "CREATE INDEX IF NOT EXISTS idx_turns_compression_status ON turns(compression_status)",
            "CREATE INDEX IF NOT EXISTS idx_turns_scope_time ON turns("
            "owner_id, workspace_id, memory_domain, compression_status, timestamp)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_session_dedup ON "
            "turns(session_id, dedup_key) WHERE dedup_key IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON turns_archive(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_archive_session ON turns_archive(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_compression_quality_evaluated ON "
            "compression_quality_audit(evaluated_at)",
            "CREATE INDEX IF NOT EXISTS idx_recall_traces_created ON "
            "recall_traces(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_recall_traces_session ON "
            "recall_traces(session_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_recall_feedback_memory ON "
            "recall_feedback(owner_id, workspace_id, memory_domain, memory_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_memory_deletion_audit_scope ON "
            "memory_deletion_audit(owner_id, workspace_id, memory_domain, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_profile_tombstones_scope ON "
            "profile_memory_tombstones(owner_id, workspace_id, memory_domain, revoked_at)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_type ON compressed_memories(memory_type)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_timespan ON "
            "compressed_memories(timespan_start, timespan_end)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_status ON compressed_memories(status)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_level ON "
            "compressed_memories(compression_level)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_identity_layer ON "
            "compressed_memories(identity_layer, status, timespan_end)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_scope_status ON "
            "compressed_memories(owner_id, workspace_id, memory_domain, status, timespan_end)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_timeline_parent ON "
            "compressed_memories(timeline_parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_derived_from ON "
            "compressed_memories(derived_from_id)",
            "CREATE INDEX IF NOT EXISTS idx_profile_scope_status ON "
            "profile_memories(owner_id, workspace_id, memory_domain, status, valid_from)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_active_slot ON "
            "profile_memories(owner_id, workspace_id, memory_domain, subject, predicate, "
            "slot_key) WHERE status = 'active'",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_time_summary_active_bucket ON "
            "time_summaries(owner_id, workspace_id, memory_domain, summary_type, "
            "bucket_key) WHERE status = 'active'",
            "CREATE INDEX IF NOT EXISTS idx_time_summary_scope_period ON "
            "time_summaries(owner_id, workspace_id, memory_domain, summary_type, "
            "status, period_start, period_end)",
            "CREATE INDEX IF NOT EXISTS idx_time_summary_supersedes ON "
            "time_summaries(supersedes_summary_id)",
            "CREATE INDEX IF NOT EXISTS idx_time_summary_links_child ON "
            "time_summary_links(child_summary_id)",
            "CREATE INDEX IF NOT EXISTS idx_session_summary_sources_turn ON "
            "session_summary_sources(turn_id)",
            "CREATE INDEX IF NOT EXISTS idx_identity_revision_status ON "
            "identity_revision_proposals(status, created_at)",
        )
        for statement in statements:
            cursor.execute(statement)
