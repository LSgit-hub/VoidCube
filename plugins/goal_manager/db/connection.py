"""SQLite-backed Goal Service store.

The store is deliberately synchronous.  Goal mutations are small, strongly
transactional operations and the service owns its SQLite file exclusively.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from voidcube.infrastructure.config.runtime_paths import get_VoidCube_home
from voidcube.infrastructure.persistence.sqlite_owner import SQLiteOwnerLease

from ..domain.events import dump_json, load_json, new_id
from ..domain.graph import GoalConflict, bounded_subgraph, find_cycle_path
from ..domain.guard import ConfirmationGuard, ConfirmationRequired
from ..domain.progress import evidence_progress, weighted_children_progress


NODE_TYPES = {"project", "objective", "milestone", "feature", "task", "bug", "test", "release"}
STATUSES = {"planned", "in_progress", "blocked", "waiting_review", "completed", "cancelled"}
PROGRESS_MODES = {"manual", "weighted_children", "evidence_based"}
EDGE_TYPES = {"decomposes_to", "depends_on", "blocks"}
ACTORS = {"user", "agent", "supervisor", "system"}
EVIDENCE_TYPES = {"test_result", "ci_build", "git_commit", "pr", "issue", "note", "file", "manual"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any, field: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{field} is required")
    return result


def _number(value: Any, field: str, default: float = 0.0) -> float:
    try:
        result = float(default if value is None else value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if not 0 <= result <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return result


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _node_payload(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["acceptance_criteria"] = load_json(result.pop("acceptance_criteria_json", None), [])
    return result


def _edge_payload(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["required"] = bool(result.get("required", 1))
    return result


def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["before"] = load_json(result.pop("before_json", None))
    result["after"] = load_json(result.pop("after_json", None))
    return result


class GoalStore:
    """Own and mutate one Goal Service SQLite database."""

    def __init__(self, db_path: str | Path | None = None, owner: str = "goal_service") -> None:
        configured = db_path or (get_VoidCube_home() / "runtime" / "goals" / "goals.db")
        self.db_path = Path(configured).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.owner_lease = SQLiteOwnerLease(self.db_path, owner)
        self.guard = ConfirmationGuard()
        self._initialize()

    def close(self) -> None:
        self.owner_lease.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            schema_path = Path(__file__).with_name("schema.sql")
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    @staticmethod
    def _actor(
        actor_type: str = "agent",
        actor_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        actor = _text(actor_type, "actor_type") or "agent"
        if actor not in ACTORS:
            raise ValueError(f"actor_type must be one of {sorted(ACTORS)}")
        return actor, _text(actor_id, "actor_id") or None, _text(session_id, "session_id") or None

    def _event(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        before: Any = None,
        after: Any = None,
        reason: str,
        batch_id: str | None,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
    ) -> None:
        reason = _text(reason, "reason", required=True)
        conn.execute(
            """
            INSERT INTO goal_events
              (id, project_id, batch_id, actor_type, actor_id, session_id,
               event_type, entity_type, entity_id, before_json, after_json,
               reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt_"), project_id, batch_id, actor_type, actor_id, session_id,
                event_type, entity_type, entity_id, dump_json(before), dump_json(after),
                reason, utc_now(),
            ),
        )

    def _get_node(self, conn: sqlite3.Connection, node_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        query = "SELECT * FROM goal_nodes WHERE id = ?"
        params: list[Any] = [node_id]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = _row(conn.execute(query, params).fetchone())
        if row is None:
            raise KeyError(f"node not found: {node_id}")
        return _node_payload(row)

    def _get_edge(self, conn: sqlite3.Connection, edge_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        query = "SELECT * FROM goal_edges WHERE id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = _row(conn.execute(query, (edge_id,)).fetchone())
        if row is None:
            raise KeyError(f"edge not found: {edge_id}")
        return _edge_payload(row)

    def _ensure_project(self, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = _row(conn.execute(
            "SELECT * FROM goal_projects WHERE id = ? AND deleted_at IS NULL", (project_id,)
        ).fetchone())
        if row is None:
            raise KeyError(f"project not found: {project_id}")
        return row

    def _recompute_project_progress(self, conn: sqlite3.Connection, project_id: str) -> None:
        nodes = {
            row["id"]: _node_payload(dict(row))
            for row in conn.execute(
                "SELECT * FROM goal_nodes WHERE project_id = ? AND deleted_at IS NULL", (project_id,)
            ).fetchall()
        }
        edges = [
            _edge_payload(dict(row))
            for row in conn.execute(
                "SELECT * FROM goal_edges WHERE project_id = ? AND deleted_at IS NULL "
                "AND edge_type = 'decomposes_to'", (project_id,)
            ).fetchall()
        ]
        # Repeating to a fixed point handles the usual parent -> child DAG
        # without requiring a second graph representation in the persistence layer.
        for _ in range(max(1, len(nodes))):
            changed = False
            for node_id, node in nodes.items():
                if node["progress_mode"] == "weighted_children":
                    children = [
                        (nodes[edge["target_id"]], edge)
                        for edge in edges
                        if edge["source_id"] == node_id and edge["target_id"] in nodes
                    ]
                    calculated = weighted_children_progress(children)
                    if calculated is not None and abs(float(node["progress"]) - calculated) > 1e-9:
                        node["progress"] = calculated
                        changed = True
                elif node["progress_mode"] == "evidence_based":
                    calculated = evidence_progress(node["acceptance_criteria"])
                    if abs(float(node["progress"]) - calculated) > 1e-9:
                        node["progress"] = calculated
                        changed = True
            if not changed:
                break
        now = utc_now()
        for node in nodes.values():
            conn.execute(
                "UPDATE goal_nodes SET progress = ?, updated_at = ? WHERE id = ?",
                (node["progress"], now, node["id"]),
            )
        conn.execute(
            "UPDATE goal_projects SET updated_at = ? WHERE id = ?",
            (now, project_id),
        )

    def _validate_node_fields(self, data: dict[str, Any], *, creating: bool = True) -> dict[str, Any]:
        node_type = _text(data.get("node_type") or data.get("type"), "node_type", required=True)
        if node_type not in NODE_TYPES:
            raise ValueError(f"node_type must be one of {sorted(NODE_TYPES)}")
        if creating and node_type == "project":
            raise ValueError("project nodes are created with a project")
        status = _text(data.get("status"), "status") or "planned"
        if status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        progress_mode = _text(data.get("progress_mode"), "progress_mode") or "manual"
        if progress_mode not in PROGRESS_MODES:
            raise ValueError(f"progress_mode must be one of {sorted(PROGRESS_MODES)}")
        criteria = data.get("acceptance_criteria", data.get("acceptance_criteria_json", []))
        if isinstance(criteria, str):
            criteria = load_json(criteria, [])
        if not isinstance(criteria, list):
            raise ValueError("acceptance_criteria must be a list")
        return {
            "node_type": node_type,
            "title": _text(data.get("title"), "title", required=True),
            "description": _text(data.get("description"), "description"),
            "status": status,
            "progress": _number(data.get("progress"), "progress", 0),
            "progress_mode": progress_mode,
            "confidence": _number(data.get("confidence"), "confidence", 1),
            "priority": int(data.get("priority", 0)),
            "start_at": _text(data.get("start_at"), "start_at") or None,
            "due_at": _text(data.get("due_at"), "due_at") or None,
            "completed_at": _text(data.get("completed_at"), "completed_at") or None,
            "acceptance_criteria_json": json.dumps(criteria, ensure_ascii=False),
            "owner": _text(data.get("owner"), "owner") or None,
            "assigned_to": _text(data.get("assigned_to"), "assigned_to") or None,
        }

    def _insert_node(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        data: dict[str, Any],
        created_by: str,
        reason: str,
        batch_id: str | None,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
        node_id: str | None = None,
        allow_project: bool = False,
    ) -> dict[str, Any]:
        self._ensure_project(conn, project_id)
        fields = self._validate_node_fields(data, creating=not allow_project)
        node_id = node_id or new_id("goal_")
        now = utc_now()
        conn.execute(
            """
            INSERT INTO goal_nodes
              (id, project_id, node_type, title, description, status, progress,
               progress_mode, confidence, priority, start_at, due_at, completed_at,
               acceptance_criteria_json, owner, assigned_to, version, created_by,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                node_id, project_id, fields["node_type"], fields["title"], fields["description"],
                fields["status"], fields["progress"], fields["progress_mode"], fields["confidence"],
                fields["priority"], fields["start_at"], fields["due_at"], fields["completed_at"],
                fields["acceptance_criteria_json"], fields["owner"], fields["assigned_to"],
                created_by, now, now,
            ),
        )
        after = self._get_node(conn, node_id)
        self._event(
            conn, project_id=project_id, event_type="create_node", entity_type="node",
            entity_id=node_id, after=after, reason=reason, batch_id=batch_id,
            actor_type=actor_type, actor_id=actor_id, session_id=session_id,
        )
        return after

    def create_project(
        self, name: str, description: str = "", *,
        created_by: str = "agent", reason: str = "create project",
        actor_type: str = "agent", actor_id: str | None = None, session_id: str | None = None,
    ) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        name = _text(name, "name", required=True)
        reason = _text(reason, "reason", required=True)
        project_id = new_id("proj_")
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            now = utc_now()
            conn.execute(
                "INSERT INTO goal_projects (id, name, description, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, _text(description, "description"), created_by, now, now),
            )
            root = self._insert_node(
                conn, project_id=project_id,
                data={"node_type": "project", "title": name, "description": description},
                created_by=created_by, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                node_id=new_id("root_"), allow_project=True,
            )
            conn.execute(
                "UPDATE goal_projects SET root_node_id = ?, updated_at = ? WHERE id = ?",
                (root["id"], now, project_id),
            )
            project = _row(conn.execute(
                "SELECT * FROM goal_projects WHERE id = ?", (project_id,)
            ).fetchone())
            self._event(
                conn, project_id=project_id, event_type="create_project", entity_type="project",
                entity_id=project_id, after=project, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            return {"project": project, "root": root, "batch_id": batch_id}

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = [_row(row) for row in conn.execute(
                "SELECT * FROM goal_projects WHERE deleted_at IS NULL ORDER BY created_at"
            ).fetchall()]
            result = []
            for project in rows:
                project["progress"] = self._project_progress(conn, project["id"])
                result.append(project)
            return result

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            project = self._ensure_project(conn, project_id)
            result = dict(project)
            result["progress"] = self._project_progress(conn, project_id)
            result["root"] = _node_payload(dict(conn.execute(
                "SELECT * FROM goal_nodes WHERE id = ?", (project["root_node_id"],)
            ).fetchone()))
            return result

    def _project_progress(self, conn: sqlite3.Connection, project_id: str) -> float:
        rows = conn.execute(
            "SELECT id, progress FROM goal_nodes WHERE project_id = ? AND deleted_at IS NULL "
            "AND node_type IN ('task','bug','test')", (project_id,)
        ).fetchall()
        if not rows:
            return 0.0
        return sum(float(row["progress"]) for row in rows) / len(rows)

    def create_node(self, project_id: str, data: dict[str, Any], *,
                    created_by: str = "agent", reason: str,
                    actor_type: str = "agent", actor_id: str | None = None,
                    session_id: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            node = self._insert_node(
                conn, project_id=project_id, data=data, created_by=created_by, reason=reason,
                batch_id=batch_id, actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            self._recompute_project_progress(conn, project_id)
            return {"node": node, "batch_id": batch_id}

    def update_node(self, node_id: str, expected_version: int, patch: dict[str, Any], *,
                    reason: str, actor_type: str = "agent", actor_id: str | None = None,
                    session_id: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            before = self._get_node(conn, node_id)
            if int(before["version"]) != int(expected_version):
                raise GoalConflict(
                    "node version conflict",
                    latest=before, expected_version=expected_version,
                )
            merged = dict(before)
            merged.update(patch or {})
            fields = self._validate_node_fields(merged, creating=False)
            if before["node_type"] == "project" and fields["node_type"] != "project":
                raise ValueError("project root node type cannot change")
            if fields["status"] == "completed" and fields["progress"] < 1:
                criteria = load_json(fields["acceptance_criteria_json"], [])
                if not criteria or not all(isinstance(item, dict) and item.get("met") for item in criteria):
                    fields["confidence"] = min(float(fields["confidence"]), 0.5)
            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE goal_nodes SET node_type=?, title=?, description=?, status=?,
                  progress=?, progress_mode=?, confidence=?, priority=?, start_at=?,
                  due_at=?, completed_at=?, acceptance_criteria_json=?, owner=?,
                  assigned_to=?, version=version+1, updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL
                """,
                (
                    fields["node_type"], fields["title"], fields["description"], fields["status"],
                    fields["progress"], fields["progress_mode"], fields["confidence"], fields["priority"],
                    fields["start_at"], fields["due_at"],
                    fields["completed_at"] or (now if fields["status"] == "completed" else None),
                    fields["acceptance_criteria_json"], fields["owner"], fields["assigned_to"],
                    now, node_id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                latest = self._get_node(conn, node_id)
                raise GoalConflict("node version conflict", latest=latest, expected_version=expected_version)
            after = self._get_node(conn, node_id)
            self._event(
                conn, project_id=after["project_id"], event_type="update_node", entity_type="node",
                entity_id=node_id, before=before, after=after, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            self._recompute_project_progress(conn, after["project_id"])
            return {"node": self._get_node(conn, node_id), "batch_id": batch_id}

    def _guard_delete(self, conn: sqlite3.Connection, node_id: str, cascade: bool, confirm_token: str | None) -> dict[str, Any]:
        node = self._get_node(conn, node_id)
        project = self._ensure_project(conn, node["project_id"])
        children = conn.execute(
            "SELECT COUNT(*) AS count FROM goal_edges WHERE source_id=? AND edge_type='decomposes_to' "
            "AND deleted_at IS NULL", (node_id,)
        ).fetchone()["count"]
        if node_id == project["root_node_id"]:
            self.guard.require_or_consume("delete_root", {"node_id": node_id, "cascade": cascade}, confirm_token)
        if children and not cascade:
            raise GoalConflict("node has children; use cascade=true or delete child edges first", child_count=children)
        return node

    def delete_node(self, node_id: str, *, cascade: bool = False, reason: str,
                    confirm_token: str | None = None, actor_type: str = "agent",
                    actor_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            node = self._guard_delete(conn, node_id, cascade, confirm_token)
            project_id = node["project_id"]
            now = utc_now()
            conn.execute("UPDATE goal_nodes SET deleted_at=?, version=version+1, updated_at=? WHERE id=?",
                         (now, now, node_id))
            after = self._get_node(conn, node_id, include_deleted=True)
            self._event(
                conn, project_id=project_id, event_type="delete_node", entity_type="node",
                entity_id=node_id, before=node, after=after, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            if cascade:
                edge_rows = conn.execute(
                    "SELECT * FROM goal_edges WHERE project_id=? AND deleted_at IS NULL "
                    "AND (source_id=? OR target_id=?)", (project_id, node_id, node_id)
                ).fetchall()
                for raw in edge_rows:
                    edge = _edge_payload(dict(raw))
                    conn.execute("UPDATE goal_edges SET deleted_at=? WHERE id=?", (now, edge["id"]))
                    edge_after = self._get_edge(conn, edge["id"], include_deleted=True)
                    self._event(
                        conn, project_id=project_id, event_type="delete_edge", entity_type="edge",
                        entity_id=edge["id"], before=edge, after=edge_after, reason=reason,
                        batch_id=batch_id, actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                    )
            self._recompute_project_progress(conn, project_id)
            return {"node": after, "batch_id": batch_id}

    def create_edge(self, data: dict[str, Any], *, created_by: str = "agent", reason: str,
                    confirm_token: str | None = None, actor_type: str = "agent",
                    actor_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        del confirm_token
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        source_id = _text(data.get("source_id", data.get("sourceId")), "source_id", required=True)
        target_id = _text(data.get("target_id", data.get("targetId")), "target_id", required=True)
        edge_type = _text(data.get("edge_type", data.get("edgeType")), "edge_type", required=True)
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"edge_type must be one of {sorted(EDGE_TYPES)}")
        if source_id == target_id:
            raise GoalConflict("self-referential edges are not allowed", cycle_path=[source_id, target_id])
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            source = self._get_node(conn, source_id)
            target = self._get_node(conn, target_id)
            if source["project_id"] != target["project_id"]:
                raise ValueError("edge endpoints must belong to the same project")
            if edge_type == "decomposes_to" and target["node_type"] == "project":
                raise ValueError("project nodes cannot be decomposition targets")
            if edge_type == "decomposes_to" and source["node_type"] == "release":
                raise ValueError("release nodes cannot have child nodes")
            outgoing = lambda node: [
                row["target_id"] for row in conn.execute(
                    "SELECT target_id FROM goal_edges WHERE project_id=? AND source_id=? "
                    "AND deleted_at IS NULL", (source["project_id"], node)
                ).fetchall()
            ]
            cycle = find_cycle_path(source_id, target_id, outgoing)
            if cycle:
                raise GoalConflict("edge would create a cycle", cycle_path=cycle)
            existing = conn.execute(
                "SELECT * FROM goal_edges WHERE project_id=? AND source_id=? AND target_id=? "
                "AND edge_type=?",
                (source["project_id"], source_id, target_id, edge_type),
            ).fetchone()
            if existing is not None:
                existing_edge = _edge_payload(dict(existing))
                if existing_edge["deleted_at"] is None:
                    return {"edge": existing_edge, "batch_id": batch_id, "unchanged": True}
                conn.execute(
                    "UPDATE goal_edges SET deleted_at=NULL, created_by=?, created_at=? WHERE id=?",
                    (created_by, utc_now(), existing_edge["id"]),
                )
                restored = self._get_edge(conn, existing_edge["id"])
                self._event(
                    conn, project_id=source["project_id"], event_type="create_edge",
                    entity_type="edge", entity_id=restored["id"], before=existing_edge,
                    after=restored, reason=reason, batch_id=batch_id,
                    actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                )
                self._recompute_project_progress(conn, source["project_id"])
                return {"edge": restored, "batch_id": batch_id, "restored": True}
            edge_id = new_id("edge_")
            now = utc_now()
            conn.execute(
                "INSERT INTO goal_edges (id, project_id, source_id, target_id, edge_type, "
                "progress_weight, required, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    edge_id, source["project_id"], source_id, target_id, edge_type,
                    max(0.0, float(data.get("progress_weight", 1))),
                    1 if data.get("required", True) else 0, created_by, now,
                ),
            )
            after = self._get_edge(conn, edge_id)
            self._event(
                conn, project_id=source["project_id"], event_type="create_edge", entity_type="edge",
                entity_id=edge_id, after=after, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            self._recompute_project_progress(conn, source["project_id"])
            return {"edge": after, "batch_id": batch_id}

    def delete_edge(self, edge_id: str, *, reason: str, actor_type: str = "agent",
                    actor_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            before = self._get_edge(conn, edge_id)
            now = utc_now()
            conn.execute("UPDATE goal_edges SET deleted_at=? WHERE id=?", (now, edge_id))
            after = self._get_edge(conn, edge_id, include_deleted=True)
            self._event(
                conn, project_id=before["project_id"], event_type="delete_edge", entity_type="edge",
                entity_id=edge_id, before=before, after=after, reason=reason, batch_id=batch_id,
                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            self._recompute_project_progress(conn, before["project_id"])
            return {"edge": after, "batch_id": batch_id}

    def apply_batch(self, project_id: str, operations: list[dict[str, Any]], *,
                    reason: str, created_by: str = "agent", actor_type: str = "agent",
                    actor_id: str | None = None, session_id: str | None = None,
                    confirm_token: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty list")
        reason = _text(reason, "reason", required=True)
        delete_nodes = sum(1 for op in operations if op.get("op") == "delete_node")
        delete_edges = sum(1 for op in operations if op.get("op") == "delete_edge")
        if delete_nodes > 10 or delete_edges > 30:
            self.guard.require_or_consume(
                "large_batch_delete",
                {"project_id": project_id, "operations": operations},
                confirm_token,
            )
        batch_id = new_id("batch_")
        temp_ids: dict[str, str] = {}
        results: list[dict[str, Any]] = []
        with self._transaction() as conn:
            self._ensure_project(conn, project_id)
            for operation in operations:
                op = str(operation.get("op") or "").strip()
                if op == "create_node":
                    temp_id = _text(operation.get("temp_id"), "temp_id") or None
                    node_id = new_id("goal_")
                    node = self._insert_node(
                        conn, project_id=project_id, data=operation, created_by=created_by,
                        reason=reason, batch_id=batch_id, actor_type=actor_type, actor_id=actor_id,
                        session_id=session_id, node_id=node_id,
                    )
                    if temp_id:
                        temp_ids[temp_id] = node_id
                    results.append({"op": op, "node": node, "temp_id": temp_id})
                elif op == "create_edge":
                    source_id = temp_ids.get(operation.get("source_id", operation.get("sourceId")), operation.get("source_id", operation.get("sourceId")))
                    target_id = temp_ids.get(operation.get("target_id", operation.get("targetId")), operation.get("target_id", operation.get("targetId")))
                    edge_data = dict(operation)
                    edge_data.update({"source_id": source_id, "target_id": target_id})
                    edge = self._create_edge_in_transaction(
                        conn, project_id, edge_data, created_by, reason, batch_id,
                        actor_type, actor_id, session_id,
                    )
                    results.append({"op": op, "edge": edge})
                elif op == "update_node":
                    node_id = temp_ids.get(operation.get("node_id"), operation.get("node_id"))
                    before = self._get_node(conn, node_id)
                    expected = int(operation.get("expected_version", before["version"]))
                    if before["version"] != expected:
                        raise GoalConflict("node version conflict", latest=before, expected_version=expected)
                    fields = self._validate_node_fields({**before, **(operation.get("patch") or {})}, creating=False)
                    now = utc_now()
                    cursor = conn.execute(
                        "UPDATE goal_nodes SET node_type=?, title=?, description=?, status=?, progress=?, "
                        "progress_mode=?, confidence=?, priority=?, start_at=?, due_at=?, completed_at=?, "
                        "acceptance_criteria_json=?, owner=?, assigned_to=?, version=version+1, updated_at=? "
                        "WHERE id=? AND version=?",
                        (
                            fields["node_type"], fields["title"], fields["description"], fields["status"],
                            fields["progress"], fields["progress_mode"], fields["confidence"], fields["priority"],
                            fields["start_at"], fields["due_at"], fields["completed_at"], fields["acceptance_criteria_json"],
                            fields["owner"], fields["assigned_to"], now, node_id, expected,
                        ),
                    )
                    if cursor.rowcount != 1:
                        latest = self._get_node(conn, node_id)
                        raise GoalConflict(
                            "node version conflict",
                            latest=latest,
                            expected_version=expected,
                        )
                    after = self._get_node(conn, node_id)
                    self._event(
                        conn, project_id=project_id, event_type="update_node", entity_type="node",
                        entity_id=node_id, before=before, after=after, reason=reason, batch_id=batch_id,
                        actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                    )
                    results.append({"op": op, "node": after})
                elif op == "delete_node":
                    node_id = temp_ids.get(operation.get("node_id"), operation.get("node_id"))
                    node = self._guard_delete(conn, node_id, bool(operation.get("cascade")), confirm_token)
                    now = utc_now()
                    conn.execute("UPDATE goal_nodes SET deleted_at=?, version=version+1, updated_at=? WHERE id=?",
                                 (now, now, node_id))
                    after = self._get_node(conn, node_id, include_deleted=True)
                    self._event(
                        conn, project_id=project_id, event_type="delete_node", entity_type="node",
                        entity_id=node_id, before=node, after=after, reason=reason, batch_id=batch_id,
                        actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                    )
                    if operation.get("cascade"):
                        for raw_edge in conn.execute(
                            "SELECT * FROM goal_edges WHERE project_id=? AND deleted_at IS NULL "
                            "AND (source_id=? OR target_id=?)",
                            (project_id, node_id, node_id),
                        ).fetchall():
                            edge = _edge_payload(dict(raw_edge))
                            conn.execute(
                                "UPDATE goal_edges SET deleted_at=? WHERE id=?",
                                (now, edge["id"]),
                            )
                            edge_after = self._get_edge(conn, edge["id"], include_deleted=True)
                            self._event(
                                conn, project_id=project_id, event_type="delete_edge",
                                entity_type="edge", entity_id=edge["id"], before=edge,
                                after=edge_after, reason=reason, batch_id=batch_id,
                                actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                            )
                    results.append({"op": op, "node": after})
                elif op == "delete_edge":
                    edge = self._get_edge(conn, operation.get("edge_id"))
                    now = utc_now()
                    conn.execute("UPDATE goal_edges SET deleted_at=? WHERE id=?", (now, edge["id"]))
                    after = self._get_edge(conn, edge["id"], include_deleted=True)
                    self._event(
                        conn, project_id=project_id, event_type="delete_edge", entity_type="edge",
                        entity_id=edge["id"], before=edge, after=after, reason=reason, batch_id=batch_id,
                        actor_type=actor_type, actor_id=actor_id, session_id=session_id,
                    )
                    results.append({"op": op, "edge": after})
                else:
                    raise ValueError(f"unsupported batch operation: {op}")
            self._recompute_project_progress(conn, project_id)
        return {"batch_id": batch_id, "temp_ids": temp_ids, "results": results}

    def _create_edge_in_transaction(
        self, conn: sqlite3.Connection, project_id: str, data: dict[str, Any],
        created_by: str, reason: str, batch_id: str,
        actor_type: str, actor_id: str | None, session_id: str | None,
    ) -> dict[str, Any]:
        source_id = _text(data.get("source_id"), "source_id", required=True)
        target_id = _text(data.get("target_id"), "target_id", required=True)
        edge_type = _text(data.get("edge_type", data.get("edgeType")), "edge_type", required=True)
        if source_id == target_id:
            raise GoalConflict("self-referential edges are not allowed", cycle_path=[source_id, target_id])
        source = self._get_node(conn, source_id)
        target = self._get_node(conn, target_id)
        if source["project_id"] != project_id or target["project_id"] != project_id:
            raise ValueError("edge endpoints must belong to the batch project")
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"edge_type must be one of {sorted(EDGE_TYPES)}")
        if edge_type == "decomposes_to" and target["node_type"] == "project":
            raise ValueError("project nodes cannot be decomposition targets")
        if edge_type == "decomposes_to" and source["node_type"] == "release":
            raise ValueError("release nodes cannot have child nodes")
        outgoing = lambda node: [
            row["target_id"] for row in conn.execute(
                "SELECT target_id FROM goal_edges WHERE project_id=? AND source_id=? AND deleted_at IS NULL",
                (project_id, node),
            ).fetchall()
        ]
        cycle = find_cycle_path(source_id, target_id, outgoing)
        if cycle:
            raise GoalConflict("edge would create a cycle", cycle_path=cycle)
        existing = conn.execute(
            "SELECT * FROM goal_edges WHERE project_id=? AND source_id=? AND target_id=? "
            "AND edge_type=?",
            (project_id, source_id, target_id, edge_type),
        ).fetchone()
        if existing is not None:
            existing_edge = _edge_payload(dict(existing))
            if existing_edge["deleted_at"] is None:
                return existing_edge
            conn.execute(
                "UPDATE goal_edges SET deleted_at=NULL, created_by=?, created_at=? WHERE id=?",
                (created_by, utc_now(), existing_edge["id"]),
            )
            restored = self._get_edge(conn, existing_edge["id"])
            self._event(
                conn, project_id=project_id, event_type="create_edge", entity_type="edge",
                entity_id=restored["id"], before=existing_edge, after=restored, reason=reason,
                batch_id=batch_id, actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            return restored
        edge_id = new_id("edge_")
        now = utc_now()
        conn.execute(
            "INSERT INTO goal_edges (id, project_id, source_id, target_id, edge_type, progress_weight, "
            "required, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                edge_id, project_id, source_id, target_id, edge_type,
                max(0.0, float(data.get("progress_weight", 1))),
                1 if data.get("required", True) else 0, created_by, now,
            ),
        )
        after = self._get_edge(conn, edge_id)
        self._event(
            conn, project_id=project_id, event_type="create_edge", entity_type="edge",
            entity_id=edge_id, after=after, reason=reason, batch_id=batch_id,
            actor_type=actor_type, actor_id=actor_id, session_id=session_id,
        )
        return after

    def get_node(self, node_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            node = self._get_node(conn, node_id)
            node["evidence"] = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM goal_evidence WHERE node_id=? AND deleted_at IS NULL ORDER BY created_at",
                    (node_id,),
                ).fetchall()
            ]
            node["events"] = [
                _event_payload(dict(row)) for row in conn.execute(
                    "SELECT * FROM goal_events WHERE entity_id=? ORDER BY created_at DESC LIMIT 20",
                    (node_id,),
                ).fetchall()
            ]
            return node

    def get_focus(self, project_id: str, node_id: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            project = self._ensure_project(conn, project_id)
            focus_id = node_id or project["root_node_id"]
            focus = self._get_node(conn, focus_id)
            children = [
                _node_payload(dict(row)) for row in conn.execute(
                    "SELECT n.* FROM goal_nodes n JOIN goal_edges e ON e.target_id=n.id "
                    "WHERE e.source_id=? AND e.edge_type='decomposes_to' AND e.deleted_at IS NULL "
                    "AND n.deleted_at IS NULL ORDER BY n.priority DESC, n.created_at",
                    (focus_id,),
                ).fetchall()
            ]
            parents = conn.execute(
                "SELECT COUNT(*) AS count FROM goal_edges WHERE target_id=? "
                "AND edge_type='decomposes_to' AND deleted_at IS NULL", (focus_id,)
            ).fetchone()["count"]
            return {
                "focus": focus, "children": children, "parent_hint_count": parents,
                "can_back": False, "can_forward": False,
            }

    def overview(self, project_id: str, mode: str = "parents_only") -> dict[str, Any]:
        if mode not in {"parents_only", "dependencies"}:
            raise ValueError("mode must be parents_only or dependencies")
        with self._connect() as conn:
            self._ensure_project(conn, project_id)
            nodes = [_node_payload(dict(row)) for row in conn.execute(
                "SELECT * FROM goal_nodes WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at",
                (project_id,),
            ).fetchall()]
            allowed = {"decomposes_to"} if mode == "parents_only" else EDGE_TYPES
            edges = [_edge_payload(dict(row)) for row in conn.execute(
                "SELECT * FROM goal_edges WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at",
                (project_id,),
            ).fetchall() if row["edge_type"] in allowed]
            return {"nodes": nodes, "edges": edges, "mode": mode}

    def graph_query(self, project_id: str, start_node: str, depth: int = 3,
                    edge_types: list[str] | None = None) -> dict[str, Any]:
        if depth < 0 or depth > 3:
            raise ValueError("depth must be between 0 and 3")
        with self._connect() as conn:
            self._ensure_project(conn, project_id)
            all_edges = [_edge_payload(dict(row)) for row in conn.execute(
                "SELECT * FROM goal_edges WHERE project_id=? AND deleted_at IS NULL", (project_id,)
            ).fetchall()]
            node_ids, edges = bounded_subgraph(start_node, depth, all_edges, set(edge_types or []))
            placeholders = ",".join("?" for _ in node_ids) or "?"
            rows = conn.execute(
                f"SELECT * FROM goal_nodes WHERE project_id=? AND deleted_at IS NULL AND id IN ({placeholders})",
                [project_id, *node_ids] if node_ids else [project_id, start_node],
            ).fetchall()
            return {"nodes": [_node_payload(dict(row)) for row in rows], "edges": edges}

    def get_context(self, node_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            node = self._get_node(conn, node_id)
            children = [_node_payload(dict(row)) for row in conn.execute(
                "SELECT n.* FROM goal_nodes n JOIN goal_edges e ON e.target_id=n.id "
                "WHERE e.source_id=? AND e.edge_type='decomposes_to' AND e.deleted_at IS NULL "
                "AND n.deleted_at IS NULL", (node_id,)
            ).fetchall()]
            deps, blocks = [], []
            for row in conn.execute(
                "SELECT e.*, n.* FROM goal_edges e JOIN goal_nodes n ON n.id=e.target_id "
                "WHERE e.source_id=? AND e.deleted_at IS NULL AND n.deleted_at IS NULL", (node_id,)
            ).fetchall():
                item = dict(row)
                if item.get("edge_type") == "depends_on":
                    deps.append({"id": item["target_id"], "title": item["title"], "status": item["status"]})
                elif item.get("edge_type") == "blocks":
                    blocks.append({"id": item["target_id"], "title": item["title"], "status": item["status"]})
            return {
                "node": node, "children": children, "dependencies": deps, "blocks": blocks,
                "evidence": [dict(row) for row in conn.execute(
                    "SELECT * FROM goal_evidence WHERE node_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 20",
                    (node_id,),
                ).fetchall()],
                "recent_events": [_event_payload(dict(row)) for row in conn.execute(
                    "SELECT * FROM goal_events WHERE entity_id=? ORDER BY created_at DESC LIMIT 20",
                    (node_id,),
                ).fetchall()],
            }

    def attach_evidence(self, node_id: str, data: dict[str, Any], *,
                        created_by: str = "agent", reason: str,
                        actor_type: str = "agent", actor_id: str | None = None,
                        session_id: str | None = None) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        evidence_type = _text(data.get("evidence_type", data.get("evidenceType")), "evidence_type", required=True)
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"evidence_type must be one of {sorted(EVIDENCE_TYPES)}")
        batch_id = new_id("batch_")
        with self._transaction() as conn:
            node = self._get_node(conn, node_id)
            evidence_id = new_id("ev_")
            now = utc_now()
            conn.execute(
                "INSERT INTO goal_evidence (id, node_id, evidence_type, title, content, uri, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence_id, node_id, evidence_type, _text(data.get("title"), "title") or None,
                    _text(data.get("content"), "content") or None, _text(data.get("uri"), "uri") or None,
                    created_by, now,
                ),
            )
            evidence = dict(conn.execute(
                "SELECT * FROM goal_evidence WHERE id=?", (evidence_id,)
            ).fetchone())
            self._event(
                conn, project_id=node["project_id"], event_type="attach_evidence",
                entity_type="evidence", entity_id=evidence_id, after=evidence, reason=reason,
                batch_id=batch_id, actor_type=actor_type, actor_id=actor_id, session_id=session_id,
            )
            return {"evidence": evidence, "batch_id": batch_id}

    def list_events(self, project_id: str, after: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            self._ensure_project(conn, project_id)
            query = "SELECT * FROM goal_events WHERE project_id=?"
            params: list[Any] = [project_id]
            if after:
                cursor = conn.execute(
                    "SELECT rowid FROM goal_events WHERE id=? AND project_id=?",
                    (after, project_id),
                ).fetchone()
                if cursor is not None:
                    query += " AND rowid > ?"
                    params.append(cursor["rowid"])
                else:
                    # Preserve the original timestamp cursor for callers
                    # using the non-streaming events endpoint.
                    query += " AND created_at > ?"
                    params.append(after)
            query += " ORDER BY rowid LIMIT ?"
            params.append(limit)
            return [_event_payload(dict(row)) for row in conn.execute(query, params).fetchall()]

    def latest_event_id(self, project_id: str) -> str | None:
        with self._connect() as conn:
            self._ensure_project(conn, project_id)
            row = conn.execute(
                "SELECT id FROM goal_events WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return row["id"] if row is not None else None

    def next_actions(self, project_id: str, limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        with self._connect() as conn:
            self._ensure_project(conn, project_id)
            candidates = [_node_payload(dict(row)) for row in conn.execute(
                "SELECT * FROM goal_nodes WHERE project_id=? AND deleted_at IS NULL "
                "AND status IN ('planned','in_progress') AND node_type IN ('task','bug','test','feature')",
                (project_id,),
            ).fetchall()]
            if filters.get("statuses"):
                candidates = [n for n in candidates if n["status"] in filters["statuses"]]
            if filters.get("types"):
                candidates = [n for n in candidates if n["node_type"] in filters["types"]]
            edges = [_edge_payload(dict(row)) for row in conn.execute(
                "SELECT * FROM goal_edges WHERE project_id=? AND deleted_at IS NULL", (project_id,)
            ).fetchall()]
            by_id = {
                row["id"]: row for row in conn.execute(
                    "SELECT * FROM goal_nodes WHERE project_id=? AND deleted_at IS NULL", (project_id,)
                ).fetchall()
            }
            available = []
            for node in candidates:
                blocked = False
                for edge in edges:
                    if edge["edge_type"] == "depends_on" and edge["source_id"] == node["id"]:
                        prerequisite = by_id.get(edge["target_id"])
                    elif edge["edge_type"] == "blocks" and edge["target_id"] == node["id"]:
                        prerequisite = by_id.get(edge["source_id"])
                    else:
                        continue
                    if prerequisite and prerequisite["status"] not in {"completed", "cancelled"}:
                        blocked = True
                        break
                if node["status"] == "blocked":
                    blocked = True
                if not blocked:
                    available.append(node)
            available.sort(key=lambda n: (-int(n["priority"]), n["due_at"] is None, n["due_at"] or "", float(n["progress"])))
            return available[:max(1, min(100, int(limit)))]

    def rollback(self, batch_id: str, *, reason: str = "rollback batch",
                 actor_type: str = "agent", actor_id: str | None = None,
                 session_id: str | None = None, confirm: bool = False) -> dict[str, Any]:
        actor_type, actor_id, session_id = self._actor(actor_type, actor_id, session_id)
        batch_id = _text(batch_id, "batch_id", required=True)
        with self._transaction() as conn:
            events = conn.execute(
                "SELECT * FROM goal_events WHERE batch_id=? ORDER BY created_at DESC, rowid DESC",
                (batch_id,),
            ).fetchall()
            if not events:
                raise KeyError(f"batch not found: {batch_id}")
            project_id = events[0]["project_id"]
            latest = conn.execute(
                "SELECT e.batch_id FROM goal_events e WHERE e.project_id=? "
                "AND e.batch_id IS NOT NULL AND e.event_type != 'rollback' "
                "AND NOT EXISTS (SELECT 1 FROM goal_events r "
                "WHERE r.event_type='rollback' AND r.entity_id=e.batch_id) "
                "GROUP BY e.batch_id ORDER BY MAX(e.created_at) DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if latest and latest["batch_id"] != batch_id:
                raise GoalConflict("only the latest unrolled batch can be rolled back", latest_batch_id=latest["batch_id"])
            if conn.execute(
                "SELECT 1 FROM goal_events WHERE event_type='rollback' AND entity_id=?",
                (batch_id,),
            ).fetchone():
                raise GoalConflict("batch has already been rolled back")
            if not confirm and len(events) > 10:
                # Keep large replays explicit while preserving the same token contract.
                self.guard.require_or_consume("rollback", {"batch_id": batch_id}, None)
            for event in events:
                before = load_json(event["before_json"])
                after = load_json(event["after_json"])
                entity_id = event["entity_id"]
                if event["entity_type"] == "node":
                    if event["event_type"] == "create_node":
                        conn.execute("UPDATE goal_nodes SET deleted_at=? WHERE id=?", (utc_now(), entity_id))
                    elif before:
                        current = self._get_node(conn, entity_id, include_deleted=True)
                        fields = dict(before)
                        conn.execute(
                            "UPDATE goal_nodes SET node_type=?, title=?, description=?, status=?, progress=?, "
                            "progress_mode=?, confidence=?, priority=?, start_at=?, due_at=?, completed_at=?, "
                            "acceptance_criteria_json=?, owner=?, assigned_to=?, deleted_at=?, version=? WHERE id=?",
                            (
                                fields["node_type"], fields["title"], fields["description"], fields["status"],
                                fields["progress"], fields["progress_mode"], fields["confidence"], fields["priority"],
                                fields["start_at"], fields["due_at"], fields["completed_at"],
                                json.dumps(fields["acceptance_criteria"], ensure_ascii=False),
                                fields["owner"], fields["assigned_to"], fields.get("deleted_at"),
                                int(current["version"]) + 1, entity_id,
                            ),
                        )
                elif event["entity_type"] == "edge":
                    if event["event_type"] == "create_edge":
                        conn.execute("UPDATE goal_edges SET deleted_at=? WHERE id=?", (utc_now(), entity_id))
                    elif before:
                        conn.execute("UPDATE goal_edges SET deleted_at=NULL WHERE id=?", (entity_id,))
                elif event["entity_type"] == "evidence" and event["event_type"] == "attach_evidence":
                    conn.execute("UPDATE goal_evidence SET deleted_at=? WHERE id=?", (utc_now(), entity_id))
                elif event["entity_type"] == "project" and after:
                    conn.execute("UPDATE goal_projects SET deleted_at=? WHERE id=?", (utc_now(), entity_id))
            self._event(
                conn, project_id=project_id, event_type="rollback", entity_type="batch",
                entity_id=batch_id, before={"batch_id": batch_id}, after=None,
                reason=reason, batch_id=None, actor_type=actor_type, actor_id=actor_id,
                session_id=session_id,
            )
            self._recompute_project_progress(conn, project_id)
            return {"batch_id": batch_id, "rolled_back": True}
