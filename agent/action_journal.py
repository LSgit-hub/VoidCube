"""Durable intent, dispatch, outcome, and evidence records for side effects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from VoidCube_core.constants import get_VoidCube_home


EffectClass = Literal["read_only", "idempotent_write", "non_idempotent_write"]
ActionState = Literal[
    "prepared", "dispatched", "succeeded", "failed", "cancelled",
    "timed_out", "unknown", "reconciling",
]

_TRANSITIONS = {
    "prepared": {"dispatched", "cancelled"},
    "dispatched": {"succeeded", "failed", "cancelled", "timed_out", "unknown"},
    "unknown": {"reconciling"},
    "reconciling": {"succeeded", "failed", "unknown"},
}


@dataclass(frozen=True, slots=True)
class PreparedAction:
    action_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    evidence_id: str
    kind: str
    content_hash: str
    collected_at: float


@dataclass(frozen=True, slots=True)
class ActionRef:
    action_id: str
    state: str
    target_summary: str
    evidence_refs: tuple[EvidenceProjection, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "state": self.state,
            "target_summary": self.target_summary,
            "evidence_refs": [asdict(item) for item in self.evidence_refs],
        }


class ActionJournal:
    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """CREATE TABLE IF NOT EXISTS action_meta(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions(
                    action_id TEXT PRIMARY KEY, task_id TEXT,
                    lease_generation INTEGER, attempt_id TEXT,
                    tool_name TEXT NOT NULL, normalized_arguments TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    risk_level TEXT NOT NULL, target_resource TEXT,
                    state TEXT NOT NULL, retryability TEXT NOT NULL,
                    prepared_at REAL NOT NULL, dispatched_at REAL, finished_at REAL,
                    error_code TEXT, error_summary TEXT, schema_version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS action_transitions(
                    transition_id TEXT PRIMARY KEY, action_id TEXT NOT NULL,
                    from_state TEXT, to_state TEXT NOT NULL, actor TEXT NOT NULL,
                    occurred_at REAL NOT NULL, reason TEXT, details_hash TEXT,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id));
                CREATE TABLE IF NOT EXISTS action_evidence(
                    evidence_id TEXT PRIMARY KEY, action_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL, collected_at REAL NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id));
                CREATE INDEX IF NOT EXISTS idx_actions_state ON actions(state);
                CREATE INDEX IF NOT EXISTS idx_actions_task ON actions(task_id);
                """
            )
            columns = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(actions)").fetchall()
            }
            if "call_id" not in columns:
                self._conn.execute("ALTER TABLE actions ADD COLUMN call_id TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_actions_call_id ON actions(call_id)"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO action_meta(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                normalized_key = str(key).casefold().replace("-", "_")
                if any(
                    marker in normalized_key
                    for marker in (
                        "api_key",
                        "authorization",
                        "credential",
                        "password",
                        "secret",
                        "token",
                    )
                ):
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = cls._redact(item)
            return redacted
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item) for item in value]
        return value

    def prepare(
        self, *, tool_name: str, arguments: dict[str, Any], effect: EffectClass,
        task_id: str | None = None, lease_generation: int | None = None,
        attempt_id: str | None = None, call_id: str | None = None,
    ) -> PreparedAction:
        canonical_arguments = self._canonical(arguments)
        normalized = self._canonical(self._redact(arguments))
        arguments_hash = hashlib.sha256(canonical_arguments.encode()).hexdigest()
        stable_source = call_id or str(uuid.uuid4())
        idempotency_key = hashlib.sha256(
            f"{task_id or ''}:{lease_generation or 0}:{stable_source}:{tool_name}:{arguments_hash}".encode()
        ).hexdigest()
        retryability = "safe" if effect == "idempotent_write" else "reconcile_first"
        action_id = f"act_{uuid.uuid4().hex}"
        now = time.time()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO actions(
                action_id, task_id, lease_generation, attempt_id, call_id,
                tool_name, normalized_arguments, arguments_hash, idempotency_key,
                risk_level, target_resource, state, retryability, prepared_at, schema_version)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?)""",
                (action_id, task_id, lease_generation, attempt_id, call_id, tool_name, normalized,
                 arguments_hash, idempotency_key, effect, self._target(arguments),
                 retryability, now, self.SCHEMA_VERSION),
            )
            if cursor.rowcount == 1:
                self._transition_row(
                    action_id, None, "prepared", "coordinator", "write_before_dispatch"
                )
                return PreparedAction(action_id, idempotency_key)
            existing = self._conn.execute(
                "SELECT action_id, idempotency_key FROM actions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("action_prepare_conflict_without_existing_record")
            return PreparedAction(existing["action_id"], existing["idempotency_key"])

    @staticmethod
    def _target(arguments: dict[str, Any]) -> str:
        for key in ("path", "file_path", "url", "resource", "command"):
            if arguments.get(key):
                return str(arguments[key])[:500]
        return ""

    def transition(
        self, action_id: str, state: ActionState, *, reason: str = "",
        error_code: str | None = None, error_summary: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
            if not row:
                raise KeyError(action_id)
            current = str(row["state"])
            if state == current:
                return
            if state not in _TRANSITIONS.get(current, set()):
                raise ValueError(f"Illegal action transition: {current} -> {state}")
            now = time.time()
            dispatched_at = now if state == "dispatched" else None
            finished_at = now if state in {"succeeded", "failed", "cancelled", "timed_out", "unknown"} else None
            self._conn.execute(
                """UPDATE actions SET state = ?, dispatched_at = COALESCE(dispatched_at, ?),
                finished_at = COALESCE(?, finished_at), error_code = ?, error_summary = ?
                WHERE action_id = ?""",
                (state, dispatched_at, finished_at, error_code, (error_summary or "")[:1000], action_id),
            )
            self._transition_row(action_id, current, state, "coordinator", reason)
            if evidence is not None:
                payload = self._canonical(evidence)
                self._conn.execute(
                    "INSERT INTO action_evidence VALUES(?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), action_id, "execution_result", payload,
                     hashlib.sha256(payload.encode()).hexdigest(), now),
                )

    def claim_dispatch(self, action_id: str, *, reason: str = "") -> bool:
        """原子抢占 prepared 动作的实际执行权。"""
        with self._lock, self._conn:
            now = time.time()
            cursor = self._conn.execute(
                "UPDATE actions SET state = 'dispatched', dispatched_at = ? "
                "WHERE action_id = ? AND state = 'prepared'",
                (now, action_id),
            )
            if cursor.rowcount != 1:
                return False
            self._transition_row(
                action_id,
                "prepared",
                "dispatched",
                "coordinator",
                reason or "tool_dispatch_started",
            )
            return True

    def _transition_row(self, action_id, from_state, to_state, actor, reason) -> None:
        details_hash = hashlib.sha256(str(reason).encode()).hexdigest()
        self._conn.execute(
            "INSERT INTO action_transitions VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), action_id, from_state, to_state, actor, time.time(), reason, details_hash),
        )

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
            return dict(row) if row else None

    def find_by_call_id(
        self,
        call_id: str,
        *,
        task_id: str | None = None,
    ) -> ActionRef | None:
        if not call_id:
            return None
        with self._lock:
            if task_id is None:
                row = self._conn.execute(
                    "SELECT action_id FROM actions WHERE call_id = ? "
                    "ORDER BY prepared_at DESC LIMIT 1",
                    (call_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT action_id FROM actions WHERE call_id = ? AND task_id = ? "
                    "ORDER BY prepared_at DESC LIMIT 1",
                    (call_id, task_id),
                ).fetchone()
        return self.action_ref(str(row["action_id"])) if row else None

    def action_ref(self, action_id: str) -> ActionRef | None:
        with self._lock:
            action = self._conn.execute(
                "SELECT action_id, state, target_resource FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            if not action:
                return None
            evidence = self._conn.execute(
                "SELECT evidence_id, kind, content_hash, collected_at "
                "FROM action_evidence WHERE action_id = ? ORDER BY collected_at",
                (action_id,),
            ).fetchall()
        return ActionRef(
            action_id=str(action["action_id"]),
            state=str(action["state"]),
            target_summary=str(action["target_resource"] or "")[:500],
            evidence_refs=tuple(
                EvidenceProjection(
                    evidence_id=str(item["evidence_id"]),
                    kind=str(item["kind"]),
                    content_hash=str(item["content_hash"]),
                    collected_at=float(item["collected_at"]),
                )
                for item in evidence
            ),
        )

    def evidence_projection(
        self,
        action_id: str,
        *,
        include_payload: bool = False,
    ) -> list[dict[str, Any]]:
        """Return evidence metadata, with a small filtered payload only on request."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT evidence_id, kind, payload_json, content_hash, collected_at "
                "FROM action_evidence WHERE action_id = ? ORDER BY collected_at",
                (action_id,),
            ).fetchall()
        projections = []
        for row in rows:
            item = {
                "evidence_id": str(row["evidence_id"]),
                "kind": str(row["kind"]),
                "content_hash": str(row["content_hash"]),
                "collected_at": float(row["collected_at"]),
            }
            if include_payload:
                payload = json.loads(row["payload_json"])
                if isinstance(payload, dict):
                    item["payload"] = {
                        key: value
                        for key, value in payload.items()
                        if key in {
                            "exit_code",
                            "resource_id",
                            "result_hash",
                            "state",
                            "status",
                        }
                    }
            projections.append(item)
        return projections

    def list_unknown(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(
                "SELECT * FROM actions WHERE state = 'unknown' ORDER BY prepared_at"
            ).fetchall()]

    def begin_reconcile(self, action_id: str, *, reason: str) -> None:
        self.transition(action_id, "reconciling", reason=reason)

    def reconcile(
        self,
        action_id: str,
        *,
        state: Literal["succeeded", "failed", "unknown"],
        evidence: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        self.transition(action_id, state, reason=reason, evidence=evidence)


_default_journal: ActionJournal | None = None
_default_lock = threading.Lock()


def get_action_journal() -> ActionJournal:
    global _default_journal
    with _default_lock:
        if _default_journal is None:
            _default_journal = ActionJournal(get_VoidCube_home() / "actions.db")
        return _default_journal


__all__ = [
    "ActionJournal",
    "ActionRef",
    "EffectClass",
    "EvidenceProjection",
    "PreparedAction",
    "get_action_journal",
]
