from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, Field

from .evolution_evaluation.models import ExecutionEnvironmentManifest

from ..infrastructure.persistence.file_store import atomic_json_write
from ..infrastructure.persistence.file_store import interprocess_file_lock

BodyState = Literal["shell", "candidate", "probe", "awaiting_user_consent", "active", "retired"]

DEFAULT_SLOT_IDS: tuple[str, str] = ("slot-A", "slot-B")
DEFAULT_SLOT_COPY_IGNORE_NAMES: tuple[str, ...] = (
    ".git",
    ".body-active.json",
    ".body-slots",
    ".body-registry.json",
    ".pytest_cache",
    ".pytest_tmp",
    ".mypy_cache",
    ".venv",
    "__pycache__",
    ".soul-runtime",
    "cache",
    "logs",
    "sessions",
    "state",
)

ALLOWED_STATE_TRANSITIONS: dict[str, set[str]] = {
    "shell": {"candidate"},
    "candidate": {"probe", "shell"},
    "probe": {"awaiting_user_consent", "shell"},
    "awaiting_user_consent": {"shell"},
    "active": {"retired"},
    "retired": {"active", "shell"},
}


class WatchWindowState(BaseModel):
    status: str = "inactive"
    slot_id: Optional[str] = None
    started_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    stable_window_days: int = 3
    stable_health_checks: int = 3


class BodySlotMeta(BaseModel):
    """Per-child-agent body slot metadata.

    Each slot owns the minimum structure needed for independent cultivation,
    validation, switching, and rollback: worktree, runtime, logs, and meta.
    """

    slot_id: str
    body_state: BodyState = "shell"
    body_version: str = "bootstrap"
    generation: int = 0
    worktree_path: str
    runtime_path: str
    logs_path: str
    pid: Optional[int] = None
    lease: Optional[str] = None
    build_from_commit: Optional[str] = None
    source_branch: Optional[str] = None
    source_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    active_ref: Optional[str] = None
    active_commit: Optional[str] = None
    rollback_ref: Optional[str] = None
    rollback_commit: Optional[str] = None
    diff_summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    last_probe_result: Optional[Dict[str, Any]] = None
    last_switch_at: Optional[datetime] = None
    last_retired_at: Optional[datetime] = None
    materialized_from: Optional[str] = None
    last_materialized_at: Optional[datetime] = None
    runtime_bootstrapped_at: Optional[datetime] = None
    switch_consent_request: Optional[Dict[str, Any]] = None
    switch_consent_requested_at: Optional[datetime] = None
    switch_consent_approved_at: Optional[datetime] = None

    health_score: float = 0.0
    health_history: list[dict] = Field(default_factory=list)
    improvement_count: int = 0
    last_improvement_at: Optional[str] = None
    current_healthy_commit: Optional[str] = None
    previous_healthy_commit: Optional[str] = None
    decay_applied_at: Optional[str] = None
    rollback_in_progress: Optional[Dict[str, Any]] = None
    last_improvement_rollback: Optional[Dict[str, Any]] = None


class BodyRegistry(BaseModel):
    slot_ids: list[str] = Field(default_factory=lambda: list(DEFAULT_SLOT_IDS))
    active_slot: Optional[str] = None
    shell_slot: Optional[str] = None
    retired_slot: Optional[str] = None
    current_generation: int = 0
    watch_window: WatchWindowState = Field(default_factory=WatchWindowState)
    last_switch_result: Optional[Dict[str, Any]] = None


class BodyLaunchTarget(BaseModel):
    """Resolved metadata pointer for the currently selected body slot."""

    slot_id: str
    body_state: BodyState
    worktree_path: str
    runtime_path: str
    logs_path: str
    body_version: str
    generation: int
    materialized_from: Optional[str] = None
    active_ref: Optional[str] = None
    active_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BodyWorkspaceRecoveryRequired(RuntimeError):
    """Raised when startup cannot safely rebuild a non-empty body workspace."""


HEAD_CHANGE_AUDIT_EVENT_TYPE = "body_head_changed"
HEAD_CHANGE_AUDIT_LIMIT = 50


class BodyImprovementReport(BaseModel):
    """Agent 提交的替身改进报告（API 契约）"""
    slot_id: str
    task_id: str
    baseline_commit: str
    commit_hash: str
    branch_name: str = ""
    diff_summary: str
    changed_files: list[str] = Field(default_factory=list)
    learning_refs: list[dict] = Field(default_factory=list)
    improvement_description: str
    execution_environment: ExecutionEnvironmentManifest
    executed_at: str = ""


class BodyRegistryManager:
    """Manage the on-disk registry for the dual child-agent body layout.

    The registry keeps the global role assignment (`active`, `shell`,
    `retired`) while each child agent slot keeps its own `worktree`,
    `runtime`, `logs`, and `meta.json` with state and paths.
    """

    def __init__(
        self,
        source_root: str | Path,
        *,
        state_root: str | Path,
        slot_ids: Iterable[str] = DEFAULT_SLOT_IDS,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.state_root = Path(state_root).resolve()
        self.slot_ids = tuple(slot_ids)
        if len(self.slot_ids) < 2:
            raise ValueError("At least two body slots are required")
        self.slots_root = self.state_root / "slots"
        self.registry_path = self.state_root / "registry.json"
        self.head_change_audit_path = self.state_root / "body-head-changes.jsonl"
        self._registry_cache: tuple[int, BodyRegistry] | None = None
        self._slot_meta_cache: dict[str, tuple[int, BodySlotMeta]] = {}
        self._head_change_audit_cache: tuple[int | None, list[dict[str, Any]]] | None = None

    @staticmethod
    def _path_mtime_ns(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def initialize_layout(self) -> BodyRegistry:
        """Create independent child-agent slot directories and a default registry."""
        self.slots_root.mkdir(parents=True, exist_ok=True)

        registry = self._load_or_create_registry()

        for idx, slot_id in enumerate(self.slot_ids):
            slot_dir = self.slot_root(slot_id)
            (slot_dir / "worktree").mkdir(parents=True, exist_ok=True)
            (slot_dir / "runtime").mkdir(parents=True, exist_ok=True)
            (slot_dir / "logs").mkdir(parents=True, exist_ok=True)

            meta_path = self.slot_meta_path(slot_id)
            if not meta_path.exists():
                state: BodyState = "active" if idx == 0 else "shell"
                meta = BodySlotMeta(
                    slot_id=slot_id,
                    body_state=state,
                    generation=0,
                    worktree_path=str((slot_dir / "worktree").resolve()),
                    runtime_path=str((slot_dir / "runtime").resolve()),
                    logs_path=str((slot_dir / "logs").resolve()),
                    lease="active" if state == "active" else None,
                )
                self.save_slot_meta(meta)

        if registry.active_slot is None:
            registry.active_slot = self.slot_ids[0]
        if registry.shell_slot is None:
            registry.shell_slot = next(
                (
                    slot_id
                    for slot_id in self.slot_ids
                    if slot_id != registry.active_slot
                    and self.load_slot_meta(slot_id).body_state == "shell"
                ),
                None,
            )
        self.save_registry(registry)
        if registry.active_slot:
            active_meta = self.load_slot_meta(registry.active_slot)
            if not self._slot_workspace_is_materialized(active_meta):
                active_meta = self._prepare_slot_workspace_at_startup(
                    registry.active_slot,
                    source_path=self.source_root,
                )
            active_meta.body_state = "active"
            active_meta.lease = "active"
            active_commit = self._git_head_for_path(Path(active_meta.worktree_path))
            active_meta.active_ref = active_meta.active_ref or f"body/{registry.active_slot}"
            active_meta.active_commit = active_meta.active_commit or active_commit
            active_meta.current_healthy_commit = (
                active_meta.current_healthy_commit or active_commit
            )
            self.save_slot_meta(active_meta)
        if registry.shell_slot:
            shell_meta = self.load_slot_meta(registry.shell_slot)
            if shell_meta.body_state != "shell":
                raise ValueError(
                    f"Registry shell slot {registry.shell_slot} is in "
                    f"{shell_meta.body_state!r} state."
                )
            if not self._slot_workspace_is_materialized(shell_meta):
                self._prepare_slot_workspace_at_startup(
                    registry.shell_slot,
                )
        if registry.active_slot:
            self.write_active_body_pointer(registry.active_slot)
        return registry

    def list_slots(self) -> dict[str, BodySlotMeta]:
        return {slot_id: self.load_slot_meta(slot_id) for slot_id in self.slot_ids}

    def inspect_layout(self) -> dict[str, Any]:
        """Return a read-only integrity report for registry, slots, and pointer."""
        violations: list[dict[str, Any]] = []

        def add_violation(code: str, message: str, *, slot_id: Optional[str] = None) -> None:
            item: dict[str, Any] = {"code": code, "message": message}
            if slot_id:
                item["slot_id"] = slot_id
            violations.append(item)

        try:
            registry = self.load_registry()
        except (OSError, ValueError, FileNotFoundError) as exc:
            add_violation("registry_unreadable", str(exc))
            return {
                "healthy": False,
                "registry": None,
                "slots": {},
                "active_pointer": {"healthy": False, "present": False},
                "head_change_audit": self._head_change_audit_report(),
                "violations": violations,
            }

        head_change_events = self._load_head_change_events()
        configured_slots = list(self.slot_ids)
        if registry.slot_ids != configured_slots:
            add_violation(
                "registry_slot_ids_mismatch",
                f"Registry slots {registry.slot_ids!r} do not match {configured_slots!r}.",
            )
        if not registry.active_slot:
            add_violation("active_slot_missing", "Registry has no active slot.")

        role_slots = {
            "active": registry.active_slot,
            "shell": registry.shell_slot,
            "retired": registry.retired_slot,
        }
        assigned = [slot_id for slot_id in role_slots.values() if slot_id]
        if len(assigned) != len(set(assigned)):
            add_violation(
                "duplicate_role_assignment",
                "A body slot is assigned to more than one registry role.",
            )
        for role, slot_id in role_slots.items():
            if slot_id and slot_id not in self.slot_ids:
                add_violation(
                    "unknown_role_slot",
                    f"Registry {role} slot {slot_id!r} is not configured.",
                    slot_id=slot_id,
                )

        slot_reports: dict[str, dict[str, Any]] = {}
        expected_states = {"active": "active", "shell": "shell", "retired": "retired"}
        for slot_id in self.slot_ids:
            role = next(
                (name for name, assigned_slot in role_slots.items() if assigned_slot == slot_id),
                None,
            )
            try:
                meta = self.load_slot_meta(slot_id)
            except (OSError, ValueError, FileNotFoundError) as exc:
                add_violation("slot_meta_unreadable", str(exc), slot_id=slot_id)
                slot_reports[slot_id] = {
                    "role": role,
                    "healthy": False,
                    "materialized": False,
                }
                continue

            materialized = self._slot_workspace_is_materialized(meta)
            slot_healthy = materialized
            if not materialized:
                add_violation(
                    "slot_not_materialized",
                    f"Slot {slot_id} has no valid worktree materialization.",
                    slot_id=slot_id,
                )
            if role and meta.body_state != expected_states[role]:
                slot_healthy = False
                add_violation(
                    "slot_role_mismatch",
                    f"Slot {slot_id} is {meta.body_state!r}, expected {expected_states[role]!r}.",
                    slot_id=slot_id,
                )
            if role is None and meta.body_state in expected_states.values():
                slot_healthy = False
                add_violation(
                    "unassigned_role_state",
                    f"Slot {slot_id} has role state {meta.body_state!r} but no registry role.",
                    slot_id=slot_id,
                )
            git_report: dict[str, Any] = {"mode": None}
            if materialized:
                manifest = self._read_worktree_manifest(slot_id)
                materialization_mode = str(
                    manifest.get("materialization_mode") or ""
                ).strip()
                git_report["mode"] = materialization_mode or None
                if materialization_mode == "git_worktree":
                    git_report["head"] = self._git_head_for_isolated_worktree(
                        Path(meta.worktree_path).resolve()
                    )
                    expected_head = (
                        meta.candidate_commit
                        or meta.active_commit
                        or meta.current_healthy_commit
                    )
                    git_report["expected_head"] = expected_head
                    if not git_report["head"]:
                        slot_healthy = False
                        add_violation(
                            "slot_git_head_unavailable",
                            f"Git HEAD for slot {slot_id} is unavailable.",
                            slot_id=slot_id,
                        )
                    elif expected_head and str(git_report["head"]).lower() != str(
                        expected_head
                    ).lower():
                        slot_healthy = False
                        add_violation(
                            "slot_head_metadata_mismatch",
                            f"Slot {slot_id} HEAD does not match its recorded commit metadata.",
                            slot_id=slot_id,
                        )
                    try:
                        status = self._run_git(
                            Path(meta.worktree_path),
                            ["status", "--porcelain", "--untracked-files=all"],
                            timeout=15,
                        )
                    except ValueError as exc:
                        status = None
                        git_report["status_error"] = str(exc)
                    if status is None:
                        slot_healthy = False
                        git_report["clean"] = False
                        add_violation(
                            "slot_git_status_unavailable",
                            f"Git status for slot {slot_id} is unavailable.",
                            slot_id=slot_id,
                        )
                    elif status.returncode != 0:
                        slot_healthy = False
                        git_report["clean"] = False
                        git_report["status_error"] = status.stderr.strip()
                        add_violation(
                            "slot_git_status_unavailable",
                            f"Git status for slot {slot_id} is unavailable.",
                            slot_id=slot_id,
                        )
                    else:
                        git_report["clean"] = not bool(status.stdout.strip())
                        if not git_report["clean"]:
                            slot_healthy = False
                            add_violation(
                                "slot_worktree_dirty",
                                f"Slot {slot_id} has uncommitted or untracked Git changes.",
                                slot_id=slot_id,
                            )
            slot_head_change_events = [
                dict(event)
                for event in head_change_events
                if event.get("slot_id") == slot_id
            ][:5]
            slot_reports[slot_id] = {
                "role": role,
                "body_state": meta.body_state,
                "healthy": slot_healthy,
                "materialized": materialized,
                "worktree_path": meta.worktree_path,
                "manifest_path": str(self.slot_worktree_manifest_path(slot_id).resolve()),
                "source_commit": meta.source_commit,
                "active_commit": meta.active_commit,
                "candidate_commit": meta.candidate_commit,
                "head_change_audit": slot_head_change_events,
                "git": git_report,
            }

        pointer_report: dict[str, Any] = {
            "healthy": False,
            "present": self.active_body_pointer_path().is_file(),
        }
        active_slot = registry.active_slot
        if active_slot in self.slot_ids:
            try:
                pointer_data = json.loads(
                    self.active_body_pointer_path().read_text(encoding="utf-8")
                )
                pointer = BodyLaunchTarget.model_validate(pointer_data)
                active_meta = self.load_slot_meta(active_slot)
                pointer_report = {
                    "healthy": True,
                    "present": True,
                    "slot_id": pointer.slot_id,
                    "body_state": pointer.body_state,
                    "worktree_path": pointer.worktree_path,
                    "active_commit": pointer.active_commit,
                }
                pointer_mismatches = []
                if pointer.slot_id != active_slot:
                    pointer_mismatches.append("slot_id")
                if pointer.body_state != "active":
                    pointer_mismatches.append("body_state")
                if Path(pointer.worktree_path).resolve() != Path(active_meta.worktree_path).resolve():
                    pointer_mismatches.append("worktree_path")
                if pointer.active_commit != active_meta.active_commit:
                    pointer_mismatches.append("active_commit")
                if pointer_mismatches:
                    pointer_report["healthy"] = False
                    pointer_report["mismatches"] = pointer_mismatches
                    add_violation(
                        "active_pointer_mismatch",
                        "Active body pointer differs from active slot metadata: "
                        + ", ".join(pointer_mismatches),
                        slot_id=active_slot,
                    )
            except (OSError, ValueError, FileNotFoundError) as exc:
                add_violation("active_pointer_unreadable", str(exc), slot_id=active_slot)

        return {
            "healthy": not violations,
            "registry": registry.model_dump(mode="json"),
            "slots": slot_reports,
            "active_pointer": pointer_report,
            "head_change_audit": {
                "path": str(self.head_change_audit_path.resolve()),
                "events": [dict(event) for event in head_change_events[:10]],
            },
            "violations": violations,
        }

    def list_head_change_events(
        self,
        *,
        slot_id: Optional[str] = None,
        limit: int = HEAD_CHANGE_AUDIT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Read the append-only record of deliberate body HEAD changes."""
        if slot_id is not None:
            self._validate_slot_id(slot_id)
        bounded_limit = max(1, min(int(limit), HEAD_CHANGE_AUDIT_LIMIT))
        events = self._load_head_change_events()
        if slot_id is not None:
            events = [event for event in events if event.get("slot_id") == slot_id]
        return [dict(event) for event in events[:bounded_limit]]

    def _head_change_audit_report(self) -> dict[str, Any]:
        return {
            "path": str(self.head_change_audit_path.resolve()),
            "events": [dict(event) for event in self._load_head_change_events()[:10]],
        }

    def _head_change_event_exists(
        self,
        *,
        slot_id: str,
        before_commit: Optional[str],
        after_commit: Optional[str],
        operation: str,
        request_id: Optional[str] = None,
    ) -> bool:
        """Check whether an append was visible before writing a compensation."""
        before = str(before_commit or "").strip().lower() or None
        after = str(after_commit or "").strip().lower() or None
        expected_operation = str(operation or "").strip()
        expected_request = str(request_id or "").strip() or None
        for event in self.list_head_change_events(slot_id=slot_id):
            if str(event.get("operation") or "").strip() != expected_operation:
                continue
            if str(event.get("before_commit") or "").strip().lower() != (before or ""):
                continue
            if str(event.get("after_commit") or "").strip().lower() != (after or ""):
                continue
            if expected_request is not None and event.get("request_id") != expected_request:
                continue
            return True
        return False

    def _load_head_change_events(self) -> list[dict[str, Any]]:
        mtime_ns = self._path_mtime_ns(self.head_change_audit_path)
        cached = self._head_change_audit_cache
        if cached is not None and cached[0] == mtime_ns:
            return [deepcopy(event) for event in cached[1]]
        if mtime_ns is None and not self.head_change_audit_path.is_file():
            self._head_change_audit_cache = (None, [])
            return []

        events: list[dict[str, Any]] = []
        try:
            lines = self.head_change_audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event_type") != HEAD_CHANGE_AUDIT_EVENT_TYPE:
                continue
            events.append(event)
        self._head_change_audit_cache = (mtime_ns, [deepcopy(event) for event in events])
        return [deepcopy(event) for event in events]

    def _record_head_change(
        self,
        *,
        slot_id: str,
        before_commit: Optional[str],
        after_commit: Optional[str],
        operation: str,
        reason: str,
        request_id: Optional[str] = None,
        changed_files: Optional[Iterable[str]] = None,
        source_label: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist one deliberate HEAD change, ignoring no-op observations."""
        before = str(before_commit or "").strip() or None
        after = str(after_commit or "").strip() or None
        if before and after and before.lower() == after.lower():
            return None
        if not after:
            raise ValueError(f"Cannot audit body {slot_id} HEAD without an after commit.")
        self._validate_slot_id(slot_id)
        event = {
            "event_id": f"body-head-{uuid.uuid4().hex}",
            "event_type": HEAD_CHANGE_AUDIT_EVENT_TYPE,
            "slot_id": slot_id,
            "before_commit": before,
            "after_commit": after,
            "operation": str(operation or "body_head_change").strip(),
            "reason": str(reason or "").strip(),
            "request_id": str(request_id or "").strip() or None,
            "changed_files": self._normalize_changed_files(changed_files or []),
            "source_label": str(source_label or "").strip() or None,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "actor": "supervisor.body_registry",
        }
        self.head_change_audit_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.head_change_audit_path.with_suffix(".lock")
        with interprocess_file_lock(lock_path):
            with self.head_change_audit_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def get_shell_slot(self) -> Optional[BodySlotMeta]:
        """获取 shell 槽位的元数据"""
        registry = self.load_registry()
        if registry.shell_slot:
            return self.load_slot_meta(registry.shell_slot)
        return None

    def get_active_slot(self) -> Optional[BodySlotMeta]:
        """获取 active 槽位的元数据"""
        registry = self.load_registry()
        if registry.active_slot:
            return self.load_slot_meta(registry.active_slot)
        return None

    def load_registry(self) -> BodyRegistry:
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Body registry not found: {self.registry_path}")
        mtime_ns = self._path_mtime_ns(self.registry_path)
        cached = self._registry_cache
        if mtime_ns is not None and cached is not None and cached[0] == mtime_ns:
            return cached[1].model_copy(deep=True)
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        registry = BodyRegistry.model_validate(data)
        if mtime_ns is not None:
            self._registry_cache = (mtime_ns, registry.model_copy(deep=True))
        return registry

    def save_registry(self, registry: BodyRegistry) -> None:
        atomic_json_write(
            self.registry_path,
            registry.model_dump(mode="json"),
        )
        mtime_ns = self._path_mtime_ns(self.registry_path)
        if mtime_ns is not None:
            self._registry_cache = (mtime_ns, registry.model_copy(deep=True))

    def load_slot_meta(self, slot_id: str) -> BodySlotMeta:
        self._validate_slot_id(slot_id)
        meta_path = self.slot_meta_path(slot_id)
        if not meta_path.exists():
            raise FileNotFoundError(f"Slot metadata not found: {meta_path}")
        mtime_ns = self._path_mtime_ns(meta_path)
        cached = self._slot_meta_cache.get(slot_id)
        if mtime_ns is not None and cached is not None and cached[0] == mtime_ns:
            return cached[1].model_copy(deep=True)
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        meta = BodySlotMeta.model_validate(data)
        if mtime_ns is not None:
            self._slot_meta_cache[slot_id] = (mtime_ns, meta.model_copy(deep=True))
        return meta

    def save_slot_meta(self, meta: BodySlotMeta) -> None:
        self._validate_slot_id(meta.slot_id)
        atomic_json_write(
            self.slot_meta_path(meta.slot_id),
            meta.model_dump(mode="json"),
        )
        meta_path = self.slot_meta_path(meta.slot_id)
        mtime_ns = self._path_mtime_ns(meta_path)
        if mtime_ns is not None:
            self._slot_meta_cache[meta.slot_id] = (mtime_ns, meta.model_copy(deep=True))

    def mark_candidate(
        self,
        slot_id: str,
        *,
        body_version: Optional[str] = None,
        build_from_commit: Optional[str] = None,
        source_branch: Optional[str] = None,
        source_commit: Optional[str] = None,
        candidate_branch: Optional[str] = None,
        candidate_commit: Optional[str] = None,
        active_ref: Optional[str] = None,
        rollback_ref: Optional[str] = None,
        rollback_commit: Optional[str] = None,
        diff_summary: Optional[str] = None,
        changed_files: Optional[Iterable[str]] = None,
    ) -> BodySlotMeta:
        meta = self.transition_slot(slot_id, "candidate")
        auto_commit = self._git_head_for_path(Path(meta.worktree_path)) or self._git_head_for_path(self.source_root)
        auto_branch = self._git_branch_for_path(Path(meta.worktree_path)) or self._git_branch_for_path(self.source_root)
        if body_version:
            meta.body_version = body_version
        if build_from_commit:
            meta.build_from_commit = build_from_commit
        meta.source_branch = source_branch or meta.source_branch or auto_branch
        meta.source_commit = source_commit or meta.source_commit or auto_commit
        meta.candidate_branch = candidate_branch or meta.candidate_branch or auto_branch
        meta.candidate_commit = candidate_commit or meta.candidate_commit or build_from_commit or auto_commit
        meta.active_ref = active_ref or meta.active_ref
        meta.rollback_ref = rollback_ref or meta.rollback_ref or meta.source_branch
        meta.rollback_commit = rollback_commit or meta.rollback_commit or meta.source_commit
        meta.build_from_commit = meta.build_from_commit or meta.candidate_commit
        if diff_summary is not None:
            meta.diff_summary = diff_summary
        if changed_files is not None:
            meta.changed_files = [str(path) for path in changed_files]
        elif not meta.changed_files:
            meta.changed_files = self._git_changed_files_for_path(
                Path(meta.worktree_path),
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            ) or self._git_changed_files_for_path(
                self.source_root,
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            )
        self.save_slot_meta(meta)
        return meta

    def start_probe(
        self,
        slot_id: str,
        *,
        lease: str = "probe",
    ) -> BodySlotMeta:
        meta = self.transition_slot(slot_id, "probe")
        meta.lease = lease
        self.save_slot_meta(meta)
        return meta

    def activate_slot(
        self,
        slot_id: str,
        *,
        lease: str = "active",
        watch_window_seconds: int = 300,
        stable_window_days: int = 3,
        stable_health_checks: int = 3,
        reason: str = "switch_approved",
        runtime_task_profile: Optional[Dict[str, Any]] = None,
    ) -> BodyRegistry:
        """Promote a slot to active and retire the previous active slot."""
        self._validate_slot_id(slot_id)
        registry = self.load_registry()
        now = datetime.utcnow()

        target = self.load_slot_meta(slot_id)
        if target.body_state not in {"awaiting_user_consent", "retired"}:
            raise ValueError(
                f"Slot {slot_id} must be awaiting user consent or retired before activation; "
                f"got {target.body_state!r}"
            )
        previous_active = registry.active_slot
        if previous_active and previous_active != slot_id:
            previous = self.load_slot_meta(previous_active)
            if previous.body_state != "active":
                raise ValueError(
                    f"Registry active slot {previous_active} is not active; "
                    f"got {previous.body_state!r}"
                )
            previous.body_state = "retired"
            previous.lease = None
            previous.last_retired_at = now
            self.save_slot_meta(previous)
            registry.retired_slot = previous_active

        target.body_state = "active"
        target.lease = lease
        target.last_switch_at = now
        target.switch_consent_approved_at = target.switch_consent_approved_at or now
        target.generation = registry.current_generation + 1
        target.active_ref = target.active_ref or f"body/{slot_id}"
        target.active_commit = (
            target.candidate_commit
            or target.build_from_commit
            or self._git_head_for_path(Path(target.worktree_path))
            or self._git_head_for_path(self.source_root)
        )
        self.save_slot_meta(target)

        registry.current_generation += 1
        registry.active_slot = slot_id
        if registry.shell_slot == slot_id:
            registry.shell_slot = None
        if registry.retired_slot == slot_id:
            registry.retired_slot = None
        registry.watch_window = WatchWindowState(
            status="active",
            slot_id=slot_id,
            started_at=now,
            expires_at=now + timedelta(seconds=watch_window_seconds),
            stable_window_days=stable_window_days,
            stable_health_checks=stable_health_checks,
        )
        registry.last_switch_result = {
            "decision": "activated",
            "slot_id": slot_id,
            "previous_active_slot": previous_active,
            "reason": reason,
            "timestamp": now.isoformat(),
            "active_ref": target.active_ref,
            "active_commit": target.active_commit,
            "candidate_branch": target.candidate_branch,
            "candidate_commit": target.candidate_commit,
            "rollback_ref": target.rollback_ref,
            "rollback_commit": target.rollback_commit,
            "stable_window_days": stable_window_days,
            "stable_health_checks": stable_health_checks,
        }
        if isinstance(runtime_task_profile, dict):
            registry.last_switch_result["runtime_task_profile"] = dict(runtime_task_profile)
            for key in ("governance_task_type", "task_family", "execution_kind"):
                value = runtime_task_profile.get(key)
                if value is not None:
                    registry.last_switch_result[key] = value
        self.save_registry(registry)
        self.write_active_body_pointer(slot_id)
        return registry

    def await_user_consent(
        self,
        slot_id: str,
        *,
        reason: str = "governor_approved_pending_user_consent",
        request_payload: Optional[Dict[str, Any]] = None,
        runtime_task_profile: Optional[Dict[str, Any]] = None,
    ) -> BodyRegistry:
        """Hold a probe-passed slot at the final user-consent gate."""
        self._validate_slot_id(slot_id)
        registry = self.load_registry()
        now = datetime.utcnow()

        meta = self.load_slot_meta(slot_id)
        if meta.body_state != "probe":
            raise ValueError(
                f"Slot {slot_id} must be in probe before awaiting user consent; "
                f"got {meta.body_state!r}"
            )

        meta.body_state = "awaiting_user_consent"
        meta.lease = "awaiting_user_consent"
        meta.switch_consent_requested_at = now
        meta.switch_consent_approved_at = None
        meta.switch_consent_request = dict(request_payload or {})
        if isinstance(runtime_task_profile, dict):
            meta.switch_consent_request["runtime_task_profile"] = dict(runtime_task_profile)
        self.save_slot_meta(meta)

        registry.last_switch_result = {
            "decision": "awaiting_user_consent",
            "slot_id": slot_id,
            "previous_active_slot": registry.active_slot,
            "reason": reason,
            "timestamp": now.isoformat(),
            "candidate_branch": meta.candidate_branch,
            "candidate_commit": meta.candidate_commit,
            "rollback_ref": meta.rollback_ref,
            "rollback_commit": meta.rollback_commit,
            "requires_user_consent": True,
        }
        if isinstance(runtime_task_profile, dict):
            registry.last_switch_result["runtime_task_profile"] = dict(runtime_task_profile)
            for key in ("governance_task_type", "task_family", "execution_kind"):
                value = runtime_task_profile.get(key)
                if value is not None:
                    registry.last_switch_result[key] = value
        self.save_registry(registry)
        return registry

    def recycle_retired_slot(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
    ) -> BodyRegistry:
        """Return a retired slot to shell state after sync and watch window completion."""
        self._validate_slot_id(slot_id)
        meta = self.load_slot_meta(slot_id)
        if meta.body_state != "retired":
            raise ValueError(
                f"Slot {slot_id} must be retired before recycling; got {meta.body_state!r}"
            )
        self.prepare_slot_workspace(
            slot_id,
            source_slot_id=source_slot_id,
            source_path=source_path,
            clear_existing=True,
            operation="recycle_retired_slot",
            reason="retired_slot_recycled_to_shell",
        )
        meta = self.transition_slot(slot_id, "shell")
        meta.last_retired_at = datetime.utcnow()
        self.save_slot_meta(meta)

        registry = self.load_registry()
        registry.shell_slot = slot_id
        if registry.retired_slot == slot_id:
            registry.retired_slot = None
        if registry.watch_window.slot_id == registry.active_slot:
            registry.watch_window.status = "completed"
        self.save_registry(registry)
        return registry

    def abandon_candidate(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
    ) -> BodySlotMeta:
        """Discard a non-active candidate and restore a clean shell baseline."""
        self._validate_slot_id(slot_id)
        meta = self.load_slot_meta(slot_id)
        if meta.body_state not in {"candidate", "probe", "awaiting_user_consent"}:
            raise ValueError(
                f"Slot {slot_id} must be candidate, probe, or awaiting user consent "
                "before abandonment; "
                f"got {meta.body_state!r}"
            )
        self.prepare_slot_workspace(
            slot_id,
            source_slot_id=source_slot_id,
            source_path=source_path,
            clear_existing=True,
            operation="abandon_candidate",
            reason="candidate_abandoned_to_shell_baseline",
        )
        return self.transition_slot(slot_id, "shell")

    def transition_slot(
        self,
        slot_id: str,
        new_state: BodyState,
    ) -> BodySlotMeta:
        """Apply a single validated state transition to a slot."""
        self._validate_slot_id(slot_id)
        meta = self.load_slot_meta(slot_id)
        current_state = meta.body_state
        allowed = ALLOWED_STATE_TRANSITIONS.get(current_state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Illegal body state transition: {current_state!r} -> {new_state!r}"
            )

        meta.body_state = new_state
        if new_state == "shell":
            meta.pid = None
            meta.lease = None
            meta.switch_consent_request = None
            meta.switch_consent_requested_at = None
            meta.switch_consent_approved_at = None
        self.save_slot_meta(meta)

        registry = self.load_registry()
        if current_state == "shell" and registry.shell_slot == slot_id:
            registry.shell_slot = None
        if new_state == "shell":
            registry.shell_slot = slot_id
            if registry.retired_slot == slot_id:
                registry.retired_slot = None
        self.save_registry(registry)
        return meta

    def write_probe_report(self, slot_id: str, report: Dict[str, Any]) -> BodySlotMeta:
        meta = self.load_slot_meta(slot_id)
        report = dict(report)
        if not report.get("source_branch"):
            report["source_branch"] = meta.source_branch
        if not report.get("source_commit"):
            report["source_commit"] = meta.source_commit
        if not report.get("candidate_branch"):
            report["candidate_branch"] = meta.candidate_branch
        if not report.get("candidate_commit"):
            report["candidate_commit"] = meta.candidate_commit or meta.build_from_commit
        if not report.get("active_ref"):
            report["active_ref"] = meta.active_ref
        if not report.get("rollback_ref"):
            report["rollback_ref"] = meta.rollback_ref
        if not report.get("rollback_commit"):
            report["rollback_commit"] = meta.rollback_commit
        if not report.get("diff_summary"):
            report["diff_summary"] = meta.diff_summary
        if not report.get("changed_files"):
            report["changed_files"] = list(meta.changed_files)
        meta.last_probe_result = report
        self.save_slot_meta(meta)
        return meta

    def restore_previous_healthy_commit(
        self,
        slot_id: str,
        *,
        expected_current_commit: Optional[str] = None,
        request_id: Optional[str] = None,
        reason: str = "destructive_body_improvement",
    ) -> BodySlotMeta:
        """Restore an isolated non-active slot to its recorded healthy ancestor."""
        self._validate_slot_id(slot_id)
        registry = self.load_registry()
        meta = self.load_slot_meta(slot_id)
        if registry.active_slot == slot_id or meta.body_state in {"active", "retired"}:
            raise ValueError(
                "Active or retired bodies must use the switch watch-window rollback protocol."
            )
        target_commit = str(meta.previous_healthy_commit or "").strip()
        if not target_commit:
            raise ValueError("No previous healthy commit is recorded for this slot.")

        worktree = Path(meta.worktree_path).resolve()
        if self._git_top_level_for_path(worktree) != worktree:
            raise ValueError(
                "Body improvement rollback requires an isolated linked Git worktree."
            )

        status = self._run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all"],
            timeout=15,
        )
        if status.returncode != 0:
            raise ValueError("Unable to inspect the body worktree before rollback.")
        if status.stdout.strip():
            raise ValueError("Body worktree must be clean before rollback.")

        current_head = self._git_head_for_isolated_worktree(worktree)
        if not current_head:
            raise ValueError("Unable to resolve the body worktree HEAD before rollback.")
        if expected_current_commit:
            expected = self._run_git(
                worktree,
                ["rev-parse", "--verify", f"{expected_current_commit}^{{commit}}"],
                timeout=15,
            )
            if expected.returncode != 0 or expected.stdout.strip().lower() != current_head.lower():
                raise ValueError("Body worktree HEAD does not match the governed rollback source.")

        resolved_target = self._run_git(
            worktree,
            ["rev-parse", "--verify", f"{target_commit}^{{commit}}"],
            timeout=15,
        )
        if resolved_target.returncode != 0:
            raise ValueError("The recorded previous healthy commit is not available.")
        target_commit = resolved_target.stdout.strip()
        if target_commit.lower() == current_head.lower():
            raise ValueError("The worktree is already at the previous healthy commit.")

        ancestry = self._run_git(
            worktree,
            ["merge-base", "--is-ancestor", target_commit, current_head],
            timeout=15,
        )
        if ancestry.returncode != 0:
            raise ValueError("The previous healthy commit is not an ancestor of the current HEAD.")

        reset = self._run_git(
            worktree,
            ["reset", "--hard", target_commit],
            timeout=30,
        )
        if reset.returncode != 0:
            raise ValueError("Git failed to restore the previous healthy commit: " + reset.stderr.strip())
        restored_head = self._git_head_for_isolated_worktree(worktree)
        if not restored_head or restored_head.lower() != target_commit.lower():
            raise ValueError("Git rollback completed without restoring the expected commit.")
        old_meta = meta.model_copy(deep=True)
        old_registry = registry.model_copy(deep=True)
        now = datetime.now(timezone.utc).isoformat()
        meta.body_state = "probe"
        meta.lease = "rollback_probe"
        meta.pid = None
        meta.candidate_commit = target_commit
        meta.build_from_commit = target_commit
        meta.last_probe_result = None
        meta.switch_consent_request = None
        meta.switch_consent_requested_at = None
        meta.switch_consent_approved_at = None
        meta.rollback_in_progress = {
            "request_id": request_id,
            "failure_reason": reason,
            "source_commit": current_head,
            "target_commit": target_commit,
            "started_at": now,
        }
        registry_changed = registry.shell_slot == slot_id
        if registry_changed:
            registry.shell_slot = None

        def restore_previous_state(error: BaseException, message: str) -> None:
            rollback_errors: list[str] = []
            rollback = self._run_git(
                worktree,
                ["reset", "--hard", current_head],
                timeout=30,
            )
            if rollback.returncode != 0:
                rollback_errors.append(
                    "failed to restore the original HEAD: " + rollback.stderr.strip()
                )
            try:
                self.save_slot_meta(old_meta)
            except Exception as exc:
                rollback_errors.append(f"failed to restore slot metadata: {exc}")
            try:
                self.save_registry(old_registry)
            except Exception as exc:
                rollback_errors.append(f"failed to restore registry metadata: {exc}")
            if rollback_errors:
                raise RuntimeError(
                    message + " Rollback was incomplete: " + "; ".join(rollback_errors)
                ) from error

            # A filesystem error can happen after an append reached the audit
            # file. Record the compensating HEAD move when possible so the
            # append-only history still describes the final state.
            if self._head_change_event_exists(
                slot_id=slot_id,
                before_commit=current_head,
                after_commit=target_commit,
                operation="restore_previous_healthy_commit",
                request_id=request_id,
            ):
                try:
                    self._record_head_change(
                        slot_id=slot_id,
                        before_commit=target_commit,
                        after_commit=current_head,
                        operation="restore_previous_healthy_commit_compensation",
                        reason="failed_body_rollback_compensated",
                        request_id=request_id,
                        source_label="rollback_compensation",
                    )
                except Exception:
                    pass
            raise RuntimeError(message) from error

        try:
            self.save_slot_meta(meta)
            if registry_changed:
                self.save_registry(registry)
        except Exception as metadata_error:
            restore_previous_state(
                metadata_error,
                "Body rollback was reverted because its metadata could not be persisted.",
            )

        try:
            self._record_head_change(
                slot_id=slot_id,
                before_commit=current_head,
                after_commit=restored_head,
                operation="restore_previous_healthy_commit",
                reason=reason,
                request_id=request_id,
                source_label="previous_healthy_commit",
            )
        except Exception as audit_error:
            restore_previous_state(
                audit_error,
                "Body rollback was reverted because its audit event could not be persisted.",
            )
        return meta

    def finalize_previous_healthy_commit_restore(
        self,
        slot_id: str,
        *,
        probe_report: Dict[str, Any],
    ) -> BodySlotMeta:
        meta = self.load_slot_meta(slot_id)
        rollback = dict(meta.rollback_in_progress or {})
        if not rollback:
            raise ValueError("No body improvement rollback is in progress for this slot.")

        probe_passed = bool(probe_report.get("overall_passed"))
        health_before = float(meta.health_score or 0.0)
        score_delta = -(health_before * 0.3)
        meta.health_score = max(0.0, health_before + score_delta)
        completed_at = datetime.now(timezone.utc).isoformat()
        rollback_result = {
            **rollback,
            "completed_at": completed_at,
            "probe_passed": probe_passed,
            "score_delta": score_delta,
            "health_score_before": health_before,
            "health_score_after": meta.health_score,
        }
        meta.health_history.append(
            {
                **rollback_result,
                "reason": "body_improvement_rollback",
                "reviewed_at": completed_at,
            }
        )
        meta.last_improvement_rollback = rollback_result
        meta.rollback_in_progress = None

        target_commit = str(rollback.get("target_commit") or "").strip()
        if probe_passed:
            prior_target = next(
                (
                    str(entry.get("baseline_commit") or "").strip()
                    for entry in reversed(meta.health_history[:-1])
                    if str(entry.get("commit_hash") or "").strip() == target_commit
                    and str(entry.get("baseline_commit") or "").strip()
                    != target_commit
                ),
                "",
            )
            meta.current_healthy_commit = target_commit
            meta.previous_healthy_commit = prior_target or None
            meta.body_state = "shell"
            meta.lease = None
            meta.changed_files = []
            meta.diff_summary = (
                f"Restored previous healthy commit {target_commit[:12]} after probe verification."
            )
        else:
            meta.body_state = "probe"
            meta.lease = "rollback_probe_failed"
        self.save_slot_meta(meta)

        registry = self.load_registry()
        if probe_passed:
            registry.shell_slot = slot_id
        elif registry.shell_slot == slot_id:
            registry.shell_slot = None
        self.save_registry(registry)
        return meta

    def prepare_slot_workspace(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
        clear_existing: bool = True,
        operation: str = "prepare_slot_workspace",
        reason: str = "explicit_workspace_materialization",
        request_id: Optional[str] = None,
    ) -> BodySlotMeta:
        """Materialize a slot and restore its prior state if preparation fails."""
        self._validate_slot_id(slot_id)
        meta_before = self.load_slot_meta(slot_id).model_copy(deep=True)
        manifest_path = self.slot_worktree_manifest_path(slot_id)
        old_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
        pointer_path = self.active_body_pointer_path()
        old_pointer = pointer_path.read_bytes() if pointer_path.is_file() else None

        worktree_root = Path(meta_before.worktree_path)
        runtime_root = Path(meta_before.runtime_path)
        logs_root = Path(meta_before.logs_path)
        previous_head = self._git_head_for_isolated_worktree(worktree_root)
        worktree_backup = None
        runtime_backup = None
        logs_backup = None
        backup_root = Path(tempfile.mkdtemp(prefix="voidcube-body-prepare-"))
        preparation_started = False
        try:
            if worktree_root.is_dir():
                backup_path = backup_root / "worktree"
                shutil.copytree(
                    worktree_root,
                    backup_path,
                    ignore=shutil.ignore_patterns(".git"),
                )
                worktree_backup = backup_path
            if runtime_root.is_dir():
                backup_path = backup_root / "runtime"
                shutil.copytree(runtime_root, backup_path)
                runtime_backup = backup_path
            if logs_root.is_dir():
                backup_path = backup_root / "logs"
                shutil.copytree(logs_root, backup_path)
                logs_backup = backup_path

            preparation_started = True
            return self._prepare_slot_workspace(
                slot_id,
                source_slot_id=source_slot_id,
                source_path=source_path,
                clear_existing=clear_existing,
                operation=operation,
                reason=reason,
                request_id=request_id,
            )
        except Exception as original_error:
            if not preparation_started:
                raise
            failed_head = self._git_head_for_isolated_worktree(worktree_root)
            rollback_errors: list[str] = []
            try:
                current_is_git_worktree = (
                    self._git_top_level_for_path(worktree_root) == worktree_root
                )
                source_root = None
                if current_is_git_worktree or previous_head:
                    source_root, _ = self._resolve_materialization_source(
                        slot_id,
                        source_slot_id=source_slot_id,
                        source_path=source_path,
                    )
                if previous_head and not current_is_git_worktree:
                    self._materialize_git_worktree(
                        source_root=source_root,
                        target_root=worktree_root,
                        source_commit=previous_head,
                        clear_existing=True,
                    )
                elif previous_head:
                    reset = self._run_git(
                        worktree_root,
                        ["reset", "--hard", previous_head],
                        timeout=30,
                    )
                    if reset.returncode != 0:
                        rollback_errors.append(
                            "failed to restore the previous worktree HEAD: "
                            + reset.stderr.strip()
                        )
                elif current_is_git_worktree:
                    removed = self._run_git(
                        source_root,
                        ["worktree", "remove", "--force", str(worktree_root)],
                        timeout=30,
                    )
                    if removed.returncode != 0:
                        rollback_errors.append(
                            "failed to remove the newly created worktree: "
                            + removed.stderr.strip()
                        )
                if worktree_backup is not None:
                    if worktree_root.exists():
                        self._clear_worktree_contents(worktree_root)
                    shutil.copytree(worktree_backup, worktree_root, dirs_exist_ok=True)
                elif not previous_head and worktree_root.exists():
                    if current_is_git_worktree:
                        self._clear_directory(worktree_root)
                    else:
                        self._clear_worktree_contents(worktree_root)
            except Exception as exc:
                rollback_errors.append(f"failed to restore the worktree: {exc}")

            for root, backup, label in (
                (runtime_root, runtime_backup, "runtime"),
                (logs_root, logs_backup, "logs"),
            ):
                try:
                    if backup is not None:
                        self._clear_directory(root)
                        shutil.copytree(backup, root, dirs_exist_ok=True)
                    elif preparation_started and root.exists():
                        self._clear_directory(root)
                except Exception as exc:
                    rollback_errors.append(f"failed to restore {label}: {exc}")

            try:
                self.save_slot_meta(meta_before)
            except Exception as exc:
                rollback_errors.append(f"failed to restore slot metadata: {exc}")
            try:
                if old_manifest is None:
                    manifest_path.unlink(missing_ok=True)
                else:
                    manifest_path.write_bytes(old_manifest)
            except Exception as exc:
                rollback_errors.append(f"failed to restore worktree manifest: {exc}")
            try:
                if old_pointer is None:
                    pointer_path.unlink(missing_ok=True)
                else:
                    pointer_path.write_bytes(old_pointer)
            except Exception as exc:
                rollback_errors.append(f"failed to restore active pointer: {exc}")

            if (
                failed_head
                and previous_head
                and failed_head.lower() != previous_head.lower()
                and not rollback_errors
                and self._head_change_event_exists(
                    slot_id=slot_id,
                    before_commit=previous_head,
                    after_commit=failed_head,
                    operation=operation,
                    request_id=request_id,
                )
            ):
                try:
                    self._record_head_change(
                        slot_id=slot_id,
                        before_commit=failed_head,
                        after_commit=previous_head,
                        operation=f"{operation}_compensation",
                        reason="failed_workspace_preparation_compensated",
                        request_id=request_id,
                        source_label="workspace_prepare_compensation",
                    )
                except Exception:
                    pass

            if rollback_errors:
                raise RuntimeError(
                    "Slot workspace preparation failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from original_error
            raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)

    def _prepare_slot_workspace(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
        clear_existing: bool = True,
        operation: str = "prepare_slot_workspace",
        reason: str = "explicit_workspace_materialization",
        request_id: Optional[str] = None,
    ) -> BodySlotMeta:
        """Materialize a child-agent slot worktree/runtime from a deterministic source."""
        self._validate_slot_id(slot_id)
        meta = self.load_slot_meta(slot_id)
        source_root, source_label = self._resolve_materialization_source(
            slot_id,
            source_slot_id=source_slot_id,
            source_path=source_path,
        )

        worktree_root = Path(meta.worktree_path)
        runtime_root = Path(meta.runtime_path)
        logs_root = Path(meta.logs_path)
        if source_root.resolve() == worktree_root.resolve():
            raise ValueError(
                f"Refusing to materialize slot {slot_id} from its own worktree source."
            )

        source_commit = self._git_head_for_path(source_root)
        source_branch = self._git_branch_for_path(source_root)
        previous_head = self._git_head_for_isolated_worktree(worktree_root)
        pending_head_change: tuple[Optional[str], Optional[str]] | None = None
        if source_commit:
            self._materialize_git_worktree(
                source_root=source_root,
                target_root=worktree_root,
                source_commit=source_commit,
                clear_existing=clear_existing,
            )
            materialization_mode = "git_worktree"
            materialized_head = self._git_head_for_isolated_worktree(worktree_root)
            if materialized_head and (
                not previous_head
                or previous_head.lower() != materialized_head.lower()
            ):
                pending_head_change = (previous_head, materialized_head)
        else:
            self._sync_directory(
                source_root,
                worktree_root,
                clear_existing=clear_existing,
            )
            materialization_mode = "directory_copy"
        self._bootstrap_runtime_directory(
            slot_id,
            runtime_root,
            logs_root,
            clear_existing=clear_existing,
        )

        now = datetime.utcnow()
        candidate_commit = self._git_head_for_path(worktree_root) or source_commit
        candidate_branch = self._git_branch_for_path(worktree_root) or source_branch
        registry = self.load_registry()
        effective_source_slot_id = source_slot_id
        if effective_source_slot_id is None and source_label.startswith("slot:"):
            effective_source_slot_id = source_label.removeprefix("slot:")
        source_meta = (
            self.load_slot_meta(effective_source_slot_id)
            if effective_source_slot_id
            else None
        )
        baseline_source_branch = source_branch or (
            source_meta.candidate_branch or source_meta.source_branch
            if source_meta
            else None
        )
        baseline_source_commit = source_commit or (
            source_meta.active_commit
            or source_meta.candidate_commit
            or source_meta.source_commit
            if source_meta
            else None
        )
        baseline_candidate_branch = candidate_branch or baseline_source_branch
        baseline_candidate_commit = candidate_commit or baseline_source_commit
        meta.materialized_from = source_label
        meta.last_materialized_at = now
        meta.runtime_bootstrapped_at = now
        if clear_existing and registry.active_slot != slot_id:
            self._reset_materialized_baseline(
                meta,
                source_meta=source_meta,
                source_branch=baseline_source_branch,
                source_commit=baseline_source_commit,
                candidate_branch=baseline_candidate_branch,
                candidate_commit=baseline_candidate_commit,
                generation=registry.current_generation,
            )
        else:
            if source_branch:
                meta.source_branch = source_branch
                meta.rollback_ref = meta.rollback_ref or source_branch
            if source_commit:
                meta.source_commit = source_commit
                meta.rollback_commit = meta.rollback_commit or source_commit
            if candidate_branch:
                meta.candidate_branch = candidate_branch
            if candidate_commit:
                meta.candidate_commit = candidate_commit
                meta.build_from_commit = meta.build_from_commit or candidate_commit
        if not meta.changed_files and not clear_existing:
            meta.changed_files = self._git_changed_files_for_path(
                worktree_root,
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            ) or self._git_changed_files_for_path(
                self.source_root,
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            )
        self.save_slot_meta(meta)
        self._write_worktree_manifest(
            slot_id,
            worktree_root,
            source_label=source_label,
            source_root=source_root,
            source_branch=baseline_source_branch,
            source_commit=baseline_source_commit,
            candidate_branch=baseline_candidate_branch,
            candidate_commit=baseline_candidate_commit,
            materialized_at=now,
            materialization_mode=materialization_mode,
        )
        if registry.active_slot == slot_id:
            self.write_active_body_pointer(slot_id)
        if pending_head_change is not None:
            self._record_head_change(
                slot_id=slot_id,
                before_commit=pending_head_change[0],
                after_commit=pending_head_change[1],
                operation=operation,
                reason=reason,
                request_id=request_id,
                source_label=source_label,
            )
        return meta

    def materialize_candidate_commit(
        self,
        slot_id: str,
        *,
        baseline_commit: str,
        candidate_commit: str,
        changed_files: Iterable[str],
        source_label: Optional[str] = None,
    ) -> BodySlotMeta:
        """Install one already-evaluated commit into the canonical shell slot.

        This is intentionally separate from startup layout repair.  It changes
        a worktree only after proving that the registry target is the shell,
        that the shell still points at the evaluated baseline, and that the
        candidate diff matches the immutable evaluation evidence.
        """
        self._validate_slot_id(slot_id)
        registry = self.load_registry()
        meta = self.load_slot_meta(slot_id)
        if registry.shell_slot != slot_id or meta.body_state != "shell":
            raise ValueError(
                f"Slot {slot_id} must be the registered shell slot before candidate materialization."
            )
        if registry.active_slot == slot_id:
            raise ValueError("The active body slot cannot receive a candidate commit.")

        worktree = Path(meta.worktree_path).resolve()
        if self._git_top_level_for_path(worktree) != worktree:
            raise ValueError("Candidate materialization requires an isolated Git worktree.")

        baseline = self._resolve_commit_in_worktree(worktree, baseline_commit, "baseline")
        candidate = self._resolve_commit_in_worktree(worktree, candidate_commit, "candidate")
        if baseline.lower() == candidate.lower():
            raise ValueError("Evaluated candidate commit must differ from its baseline.")

        status = self._run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all"],
            timeout=15,
        )
        if status.returncode != 0:
            raise ValueError("Unable to inspect the shell worktree before candidate materialization.")
        if status.stdout.strip():
            raise ValueError("Shell worktree must be clean before candidate materialization.")

        current_head = self._git_head_for_isolated_worktree(worktree)
        if not current_head:
            raise ValueError("Unable to resolve the shell worktree HEAD before candidate materialization.")
        if current_head.lower() not in {baseline.lower(), candidate.lower()}:
            raise ValueError(
                "Shell worktree HEAD does not match the evaluated candidate baseline."
            )

        if meta.candidate_commit and str(meta.candidate_commit).lower() not in {
            baseline.lower(),
            candidate.lower(),
        }:
            raise ValueError("Shell metadata points at a different candidate commit.")

        ancestry = self._run_git(
            worktree,
            ["merge-base", "--is-ancestor", baseline, candidate],
            timeout=15,
        )
        if ancestry.returncode != 0:
            raise ValueError("Evaluated candidate commit is not based on the shell baseline.")

        actual_changed_files = self._git_changed_files_between(
            worktree,
            baseline,
            candidate,
        )
        declared_changed_files = self._normalize_changed_files(changed_files)
        if actual_changed_files != declared_changed_files:
            raise ValueError(
                "Evaluated candidate changed files do not match the materialization evidence."
            )

        if current_head.lower() != candidate.lower():
            reset = self._run_git(
                worktree,
                ["reset", "--hard", candidate],
                timeout=30,
            )
            if reset.returncode != 0:
                raise ValueError(
                    "Git failed to materialize the evaluated candidate: "
                    + reset.stderr.strip()
                )

        materialized_head = self._git_head_for_isolated_worktree(worktree)
        if not materialized_head or materialized_head.lower() != candidate.lower():
            raise ValueError("Candidate materialization ended at an unexpected Git HEAD.")
        final_status = self._run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all"],
            timeout=15,
        )
        if final_status.returncode != 0 or final_status.stdout.strip():
            raise ValueError("Candidate materialization left the shell worktree dirty.")

        old_meta = meta.model_copy(deep=True)
        manifest_path = self.slot_worktree_manifest_path(slot_id)
        old_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
        original_head = current_head
        now = datetime.now(timezone.utc)
        previous_healthy = meta.current_healthy_commit or meta.previous_healthy_commit or baseline
        try:
            meta.source_commit = baseline
            meta.candidate_commit = candidate
            meta.build_from_commit = candidate
            meta.current_healthy_commit = candidate
            meta.previous_healthy_commit = (
                previous_healthy
                if previous_healthy.lower() != candidate.lower()
                else baseline
            )
            meta.rollback_commit = meta.previous_healthy_commit
            meta.changed_files = list(actual_changed_files)
            meta.diff_summary = self._git_diff_stat(worktree, baseline, candidate)
            meta.materialized_from = source_label or f"evaluated-candidate:{candidate}"
            meta.last_materialized_at = now
            meta.runtime_bootstrapped_at = meta.runtime_bootstrapped_at or now
            self.save_slot_meta(meta)
            self._write_worktree_manifest(
                slot_id,
                worktree,
                source_label=meta.materialized_from,
                source_root=self.source_root,
                source_branch=self._git_branch_for_path(worktree),
                source_commit=baseline,
                candidate_branch=self._git_branch_for_path(worktree),
                candidate_commit=candidate,
                materialized_at=now,
                materialization_mode="git_worktree",
            )

            from .supervisor.body_execution_readiness import inspect_body_execution_readiness

            readiness = inspect_body_execution_readiness(
                slot_id=slot_id,
                worktree_path=str(worktree),
                expected_body_state="shell",
            )
            if not readiness.get("ready"):
                raise ValueError(
                    "Materialized shell worktree is not execution-ready: "
                    + str(readiness.get("reason") or "unknown")
                )
            self._record_head_change(
                slot_id=slot_id,
                before_commit=original_head,
                after_commit=materialized_head,
                operation="materialize_candidate_commit",
                reason="evaluated_candidate_materialized",
                source_label=source_label or f"evaluated-candidate:{candidate}",
                changed_files=actual_changed_files,
            )
            return meta
        except Exception:
            failed_head = self._git_head_for_isolated_worktree(worktree)
            rollback_errors: list[str] = []
            if original_head.lower() != candidate.lower():
                rollback = self._run_git(
                    worktree,
                    ["reset", "--hard", original_head],
                    timeout=30,
                )
                if rollback.returncode != 0:
                    rollback_errors.append(
                        "failed to restore the original shell HEAD: "
                        + rollback.stderr.strip()
                    )
            try:
                self.save_slot_meta(old_meta)
            except Exception as exc:
                rollback_errors.append(f"failed to restore slot metadata: {exc}")
            try:
                if old_manifest is None:
                    manifest_path.unlink(missing_ok=True)
                else:
                    manifest_path.write_bytes(old_manifest)
            except Exception as exc:
                rollback_errors.append(f"failed to restore worktree manifest: {exc}")
            if (
                failed_head
                and failed_head.lower() != original_head.lower()
                and not rollback_errors
                and self._head_change_event_exists(
                    slot_id=slot_id,
                    before_commit=original_head,
                    after_commit=failed_head,
                    operation="materialize_candidate_commit",
                )
            ):
                try:
                    self._record_head_change(
                        slot_id=slot_id,
                        before_commit=failed_head,
                        after_commit=original_head,
                        operation="materialize_candidate_commit_compensation",
                        reason="failed_candidate_materialization_compensated",
                        source_label="candidate_materialization_compensation",
                        changed_files=actual_changed_files,
                    )
                except Exception:
                    pass
            if rollback_errors:
                raise RuntimeError("Candidate materialization failed and rollback was incomplete: " + "; ".join(rollback_errors)) from None
            raise

    def _prepare_slot_workspace_at_startup(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
    ) -> BodySlotMeta:
        """Repair missing startup materialization without discarding body work."""
        meta = self.load_slot_meta(slot_id)
        source_root, _ = self._resolve_materialization_source(
            slot_id,
            source_slot_id=source_slot_id,
            source_path=source_path,
        )
        self._assert_startup_rebuild_is_safe(meta, source_root=source_root)
        return self.prepare_slot_workspace(
            slot_id,
            source_slot_id=source_slot_id,
            source_path=source_path,
            clear_existing=True,
            operation="startup_workspace_repair",
            reason="startup_materialization_repair",
        )

    def _assert_startup_rebuild_is_safe(
        self,
        meta: BodySlotMeta,
        *,
        source_root: Path,
    ) -> None:
        """Reject implicit startup rebuilds that could erase body evolution."""
        worktree_root = Path(meta.worktree_path).resolve()
        if worktree_root.exists():
            isolated_top = self._git_top_level_for_path(worktree_root)
            if isolated_top == worktree_root:
                status = self._run_git(
                    worktree_root,
                    ["status", "--porcelain", "--untracked-files=all"],
                    timeout=15,
                )
                if status.returncode != 0:
                    raise BodyWorkspaceRecoveryRequired(
                        f"Body slot {meta.slot_id} worktree cannot be inspected safely "
                        "during startup; automatic rebuild was refused."
                    )
                if status.stdout.strip():
                    raise BodyWorkspaceRecoveryRequired(
                        f"Body slot {meta.slot_id} has uncommitted or untracked changes "
                        "and its materialization is invalid; automatic rebuild was refused "
                        "to preserve the changes."
                    )

                current_head = self._git_head_for_isolated_worktree(worktree_root)
                source_head = self._git_head_for_path(source_root)
                if current_head and source_head and current_head.lower() != source_head.lower():
                    raise BodyWorkspaceRecoveryRequired(
                        f"Body slot {meta.slot_id} is at commit {current_head[:12]}, "
                        f"not the startup baseline {source_head[:12]}; automatic rebuild "
                        "was refused to preserve the evolved commit."
                    )
            elif any(worktree_root.iterdir()):
                raise BodyWorkspaceRecoveryRequired(
                    f"Body slot {meta.slot_id} has existing files but no valid isolated "
                    "Git worktree; automatic rebuild was refused to preserve the body."
                )

    @staticmethod
    def _reset_materialized_baseline(
        meta: BodySlotMeta,
        *,
        source_meta: Optional[BodySlotMeta],
        source_branch: Optional[str],
        source_commit: Optional[str],
        candidate_branch: Optional[str],
        candidate_commit: Optional[str],
        generation: int,
    ) -> None:
        baseline_commit = candidate_commit or source_commit
        meta.body_version = source_meta.body_version if source_meta else "unknown"
        meta.generation = source_meta.generation if source_meta else generation
        meta.pid = None
        meta.lease = None
        meta.build_from_commit = baseline_commit
        meta.source_branch = source_branch
        meta.source_commit = source_commit
        meta.candidate_branch = candidate_branch
        meta.candidate_commit = candidate_commit
        meta.active_ref = None
        meta.active_commit = None
        meta.rollback_ref = source_branch
        meta.rollback_commit = source_commit
        meta.diff_summary = ""
        meta.changed_files = []
        meta.last_probe_result = None
        meta.switch_consent_request = None
        meta.switch_consent_requested_at = None
        meta.switch_consent_approved_at = None
        meta.health_score = 0.0
        meta.health_history = []
        meta.improvement_count = 0
        meta.last_improvement_at = None
        meta.current_healthy_commit = baseline_commit
        meta.previous_healthy_commit = None
        meta.decay_applied_at = None
        meta.rollback_in_progress = None
        meta.last_improvement_rollback = None

    def slot_root(self, slot_id: str) -> Path:
        self._validate_slot_id(slot_id)
        return self.slots_root / slot_id

    def slot_meta_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "meta.json"

    def slot_runtime_manifest_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "runtime" / "slot-runtime.json"

    def slot_worktree_manifest_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "worktree-origin.json"

    def active_body_pointer_path(self) -> Path:
        return self.state_root / "active.json"

    def build_launch_target(self, slot_id: str) -> BodyLaunchTarget:
        meta = self.load_slot_meta(slot_id)
        worktree_root = Path(meta.worktree_path).resolve()
        return BodyLaunchTarget(
            slot_id=meta.slot_id,
            body_state=meta.body_state,
            worktree_path=str(worktree_root),
            runtime_path=str(Path(meta.runtime_path).resolve()),
            logs_path=str(Path(meta.logs_path).resolve()),
            body_version=meta.body_version,
            generation=meta.generation,
            materialized_from=meta.materialized_from,
            active_ref=meta.active_ref,
            active_commit=meta.active_commit,
            candidate_branch=meta.candidate_branch,
            candidate_commit=meta.candidate_commit,
        )

    def write_active_body_pointer(self, slot_id: str) -> BodyLaunchTarget:
        target = self.build_launch_target(slot_id)
        atomic_json_write(
            self.active_body_pointer_path(),
            target.model_dump(mode="json"),
        )
        return target

    def load_active_body_pointer(self) -> BodyLaunchTarget:
        pointer_path = self.active_body_pointer_path()
        if not pointer_path.exists():
            registry = self.load_registry()
            if not registry.active_slot:
                raise FileNotFoundError("Active body pointer is missing and no active slot is registered.")
            return self.write_active_body_pointer(registry.active_slot)
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
        return BodyLaunchTarget.model_validate(data)

    def _load_or_create_registry(self) -> BodyRegistry:
        if self.registry_path.exists():
            return self.load_registry()
        registry = BodyRegistry(
            slot_ids=list(self.slot_ids),
            active_slot=self.slot_ids[0],
            shell_slot=self.slot_ids[1] if len(self.slot_ids) > 1 else None,
        )
        self.save_registry(registry)
        return registry

    def _validate_slot_id(self, slot_id: str) -> None:
        if slot_id not in self.slot_ids:
            raise ValueError(
                f"Unknown slot_id {slot_id!r}; expected one of {list(self.slot_ids)!r}"
            )

    def _resolve_materialization_source(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str],
        source_path: Optional[str | Path],
    ) -> tuple[Path, str]:
        if source_slot_id and source_path:
            raise ValueError("Specify either source_slot_id or source_path, not both.")

        if source_slot_id:
            self._validate_slot_id(source_slot_id)
            if source_slot_id == slot_id:
                raise ValueError("A slot cannot materialize from itself.")
            source_meta = self.load_slot_meta(source_slot_id)
            if not self._slot_workspace_is_materialized(source_meta):
                raise ValueError(
                    f"Source body slot {source_slot_id} has no materialized baseline."
                )
            return Path(source_meta.worktree_path), f"slot:{source_slot_id}"

        if source_path is not None:
            path = Path(source_path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Materialization source does not exist: {path}")
            return path, str(path)

        registry = self.load_registry()
        active_slot = registry.active_slot
        if active_slot and active_slot != slot_id:
            active_meta = self.load_slot_meta(active_slot)
            if not self._slot_workspace_is_materialized(active_meta):
                raise ValueError(
                    f"Active body slot {active_slot} has no materialized baseline."
                )
            return Path(active_meta.worktree_path), f"slot:{active_slot}"

        raise ValueError(
            "A non-active source_slot_id or source_path is required for materialization."
        )

    def _slot_workspace_is_materialized(self, meta: BodySlotMeta) -> bool:
        if meta.last_materialized_at is None:
            return False
        worktree_root = Path(meta.worktree_path).resolve()
        manifest_path = self.slot_worktree_manifest_path(meta.slot_id)
        if not worktree_root.is_dir() or not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if manifest.get("slot_id") != meta.slot_id:
            return False
        if Path(str(manifest.get("worktree_path") or "")).resolve() != worktree_root:
            return False
        mode = manifest.get("materialization_mode")
        if mode == "git_worktree":
            return self._git_top_level_for_path(worktree_root) == worktree_root
        return mode == "directory_copy"

    def _read_worktree_manifest(self, slot_id: str) -> dict[str, Any]:
        try:
            payload = json.loads(
                self.slot_worktree_manifest_path(slot_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _sync_directory(
        self,
        source_root: Path,
        target_root: Path,
        *,
        clear_existing: bool,
    ) -> None:
        source_root = source_root.resolve()
        if not source_root.exists():
            raise FileNotFoundError(f"Source root does not exist: {source_root}")

        target_root.mkdir(parents=True, exist_ok=True)
        if clear_existing:
            self._clear_directory(target_root)

        for child in source_root.iterdir():
            if self._should_ignore_materialized_path(child, target_root=target_root):
                continue

            destination = target_root / child.name
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, destination)

    def _materialize_git_worktree(
        self,
        *,
        source_root: Path,
        target_root: Path,
        source_commit: str,
        clear_existing: bool,
    ) -> None:
        source_root = source_root.resolve()
        target_root = target_root.resolve()
        target_root.parent.mkdir(parents=True, exist_ok=True)

        registered_worktrees = self._git_registered_worktrees(source_root)
        target_registered = target_root in registered_worktrees
        target_has_content = target_root.exists() and any(target_root.iterdir())
        if (target_registered or target_has_content) and not clear_existing:
            current_head = self._git_head_for_isolated_worktree(target_root)
            if current_head == source_commit:
                return
            raise ValueError(
                "Git worktree rematerialization requires clear_existing=True "
                "when the target is already populated."
            )

        if target_registered:
            removed = self._run_git(
                source_root,
                ["worktree", "remove", "--force", str(target_root)],
                timeout=30,
            )
            if removed.returncode != 0:
                raise ValueError(
                    "Failed to remove the existing linked worktree before rematerialization: "
                    + removed.stderr.strip()
                )
        elif target_root.exists():
            self._clear_directory(target_root)

        self._run_git(source_root, ["worktree", "prune"], timeout=15)
        added = self._run_git(
            source_root,
            ["worktree", "add", "--detach", "--force", str(target_root), source_commit],
            timeout=60,
        )
        if added.returncode != 0:
            raise ValueError(
                "Failed to materialize isolated Git worktree: " + added.stderr.strip()
            )

        isolated_top = self._git_top_level_for_path(target_root)
        if isolated_top != target_root:
            raise ValueError(
                f"Materialized slot is not an isolated Git worktree: {target_root}"
            )

    def _git_registered_worktrees(self, path: Path) -> set[Path]:
        result = self._run_git(path, ["worktree", "list", "--porcelain"], timeout=15)
        if result.returncode != 0:
            return set()
        worktrees: set[Path] = set()
        for line in result.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            raw_path = line.removeprefix("worktree ").strip()
            if raw_path:
                worktrees.add(Path(raw_path).resolve())
        return worktrees

    @staticmethod
    def _run_git(path: Path, args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"Git command failed in {path}: {exc}") from exc

    def _bootstrap_runtime_directory(
        self,
        slot_id: str,
        runtime_root: Path,
        logs_root: Path,
        *,
        clear_existing: bool,
    ) -> None:
        runtime_root.mkdir(parents=True, exist_ok=True)
        logs_root.mkdir(parents=True, exist_ok=True)
        if clear_existing:
            self._clear_directory(runtime_root)
            self._clear_directory(logs_root)

        for name in ("state", "tmp", "cache"):
            (runtime_root / name).mkdir(parents=True, exist_ok=True)

        atomic_json_write(
            self.slot_runtime_manifest_path(slot_id),
            {
                "slot_id": slot_id,
                "runtime_path": str(runtime_root.resolve()),
                "logs_path": str(logs_root.resolve()),
                "bootstrapped_at": datetime.utcnow().isoformat(),
            },
        )

    def _write_worktree_manifest(
        self,
        slot_id: str,
        worktree_root: Path,
        *,
        source_label: str,
        source_root: Path,
        source_branch: Optional[str],
        source_commit: Optional[str],
        candidate_branch: Optional[str],
        candidate_commit: Optional[str],
        materialized_at: datetime,
        materialization_mode: str,
    ) -> None:
        atomic_json_write(
            self.slot_worktree_manifest_path(slot_id),
            {
                "slot_id": slot_id,
                "worktree_path": str(worktree_root.resolve()),
                "source": source_label,
                "source_root": str(source_root.resolve()),
                "source_branch": source_branch,
                "source_commit": source_commit,
                "candidate_branch": candidate_branch,
                "candidate_commit": candidate_commit,
                "materialized_at": materialized_at.isoformat(),
                "materialization_mode": materialization_mode,
            },
        )

    def _git_top_level_for_path(self, path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return Path(result.stdout.strip()).resolve()

    def _git_head_for_isolated_worktree(self, path: Path) -> Optional[str]:
        if self._git_top_level_for_path(path) != path.resolve():
            return None
        return self._git_head_for_path(path)

    def _git_branch_for_path(self, path: Path) -> Optional[str]:
        # Git otherwise discovers a parent repository for arbitrary temporary
        # directories; those paths must use directory-copy materialization.
        if self._git_top_level_for_path(path) != path.resolve():
            return None
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        return branch or None

    def _git_head_for_path(self, path: Path) -> Optional[str]:
        if self._git_top_level_for_path(path) != path.resolve():
            return None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        commit = result.stdout.strip()
        return commit or None

    def _git_changed_files_for_path(
        self,
        path: Path,
        base_commit: Optional[str],
        candidate_commit: Optional[str],
    ) -> list[str]:
        if not base_commit or not candidate_commit or base_commit == candidate_commit:
            return []
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", base_commit, candidate_commit],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        changed_files = []
        seen = set()
        for line in result.stdout.splitlines():
            file_path = line.strip().replace("\\", "/")
            if not file_path or file_path in seen:
                continue
            seen.add(file_path)
            changed_files.append(file_path)
        return changed_files

    def _resolve_commit_in_worktree(
        self,
        worktree: Path,
        commit: str,
        label: str,
    ) -> str:
        value = str(commit or "").strip()
        if not value:
            raise ValueError(f"Evaluated {label} commit is required.")
        result = self._run_git(
            worktree,
            ["rev-parse", "--verify", f"{value}^{{commit}}"],
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError(f"Evaluated {label} commit is not available in Git.")
        return result.stdout.strip()

    def _git_changed_files_between(
        self,
        path: Path,
        base_commit: str,
        candidate_commit: str,
    ) -> list[str]:
        result = self._run_git(
            path,
            ["diff", "--name-only", base_commit, candidate_commit, "--"],
            timeout=15,
        )
        if result.returncode != 0:
            raise ValueError("Unable to inspect the evaluated candidate diff.")
        return self._normalize_changed_files(result.stdout.splitlines())

    @staticmethod
    def _normalize_changed_files(paths: Iterable[str]) -> list[str]:
        normalized = {
            str(path).strip().replace("\\", "/")
            for path in paths
            if str(path).strip()
        }
        return sorted(normalized)

    def _git_diff_stat(self, path: Path, base_commit: str, candidate_commit: str) -> str:
        result = self._run_git(
            path,
            ["diff", "--stat", base_commit, candidate_commit, "--"],
            timeout=15,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _clear_directory(self, root: Path) -> None:
        if not root.exists():
            return
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _clear_worktree_contents(self, root: Path) -> None:
        """Clear a linked worktree checkout while retaining its .git file."""
        if not root.exists():
            return
        for child in root.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _should_ignore_materialized_path(self, path: Path, *, target_root: Path) -> bool:
        resolved = path.resolve()
        target_root = target_root.resolve()
        if path.name in DEFAULT_SLOT_COPY_IGNORE_NAMES:
            return True
        if resolved in {
            self.slots_root.resolve(),
            self.registry_path.resolve(),
            self.active_body_pointer_path().resolve(),
        }:
            return True
        return target_root == resolved or target_root.is_relative_to(resolved)
