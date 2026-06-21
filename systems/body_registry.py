from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, Field

from VoidCube_core.utils import atomic_json_write

BodyState = Literal["shell", "candidate", "probe", "active", "retired"]

DEFAULT_SLOT_IDS: tuple[str, str] = ("slot-A", "slot-B")
DEFAULT_SLOT_COPY_IGNORE_NAMES: tuple[str, ...] = (
    ".git",
    ".body-slots",
    ".body-registry.json",
    ".pytest_cache",
    ".pytest_tmp",
    ".mypy_cache",
    ".venv",
    "__pycache__",
    ".soul-runtime",
)

ALLOWED_STATE_TRANSITIONS: dict[str, set[str]] = {
    "shell": {"candidate"},
    "candidate": {"probe"},
    "probe": {"active", "shell"},
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


class BodyRegistry(BaseModel):
    slot_ids: list[str] = Field(default_factory=lambda: list(DEFAULT_SLOT_IDS))
    active_slot: Optional[str] = None
    shell_slot: Optional[str] = None
    retired_slot: Optional[str] = None
    current_generation: int = 0
    watch_window: WatchWindowState = Field(default_factory=WatchWindowState)
    last_switch_result: Optional[Dict[str, Any]] = None


class BodyLaunchTarget(BaseModel):
    """Resolved launch contract for the currently selected child agent body."""

    slot_id: str
    body_state: BodyState
    worktree_path: str
    runtime_path: str
    logs_path: str
    launch_script_path: str
    launch_cwd: str
    body_version: str
    generation: int
    materialized_from: Optional[str] = None
    active_ref: Optional[str] = None
    active_commit: Optional[str] = None
    candidate_branch: Optional[str] = None
    candidate_commit: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BodyRegistryManager:
    """Manage the on-disk registry for the dual child-agent body layout.

    The registry keeps the global role assignment (`active`, `shell`,
    `retired`) while each child agent slot keeps its own `worktree`,
    `runtime`, `logs`, and `meta.json` with state and paths.
    """

    def __init__(
        self,
        repo_root: str | Path,
        *,
        slot_ids: Iterable[str] = DEFAULT_SLOT_IDS,
        slots_dir_name: str = ".body-slots",
        registry_file_name: str = ".body-registry.json",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.slot_ids = tuple(slot_ids)
        if len(self.slot_ids) < 2:
            raise ValueError("At least two body slots are required")
        self.slots_root = self.repo_root / slots_dir_name
        self.registry_path = self.repo_root / registry_file_name

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
        if registry.shell_slot is None and len(self.slot_ids) > 1:
            registry.shell_slot = self.slot_ids[1]
        self.save_registry(registry)
        if registry.active_slot:
            self.write_active_body_pointer(registry.active_slot)
        return registry

    def list_slots(self) -> dict[str, BodySlotMeta]:
        return {slot_id: self.load_slot_meta(slot_id) for slot_id in self.slot_ids}

    def load_registry(self) -> BodyRegistry:
        if not self.registry_path.exists():
            raise FileNotFoundError(f"Body registry not found: {self.registry_path}")
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return BodyRegistry.model_validate(data)

    def save_registry(self, registry: BodyRegistry) -> None:
        atomic_json_write(
            self.registry_path,
            registry.model_dump(mode="json"),
        )

    def load_slot_meta(self, slot_id: str) -> BodySlotMeta:
        self._validate_slot_id(slot_id)
        meta_path = self.slot_meta_path(slot_id)
        if not meta_path.exists():
            raise FileNotFoundError(f"Slot metadata not found: {meta_path}")
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return BodySlotMeta.model_validate(data)

    def save_slot_meta(self, meta: BodySlotMeta) -> None:
        self._validate_slot_id(meta.slot_id)
        atomic_json_write(
            self.slot_meta_path(meta.slot_id),
            meta.model_dump(mode="json"),
        )

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
        meta = self.transition_slot(slot_id, "candidate", save_meta=False)
        auto_commit = self._git_head_for_path(Path(meta.worktree_path)) or self._git_head_for_path(self.repo_root)
        auto_branch = self._git_branch_for_path(Path(meta.worktree_path)) or self._git_branch_for_path(self.repo_root)
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
                self.repo_root,
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
        if target.body_state not in {"probe", "retired"}:
            raise ValueError(
                f"Slot {slot_id} must be in probe or retired before activation; "
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
        target.generation = registry.current_generation + 1
        target.active_ref = target.active_ref or f"body/{slot_id}"
        target.active_commit = (
            target.candidate_commit
            or target.build_from_commit
            or self._git_head_for_path(Path(target.worktree_path))
            or self._git_head_for_path(self.repo_root)
        )
        self.save_slot_meta(target)
        self._write_launch_manifest(target)

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

    def recycle_retired_slot(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
        clear_existing: bool = True,
    ) -> BodyRegistry:
        """Return a retired slot to shell state after sync and watch window completion."""
        self._validate_slot_id(slot_id)
        registry = self.load_registry()
        meta = self.load_slot_meta(slot_id)
        if meta.body_state != "retired":
            raise ValueError(
                f"Slot {slot_id} must be retired before recycling; got {meta.body_state!r}"
            )
        if source_slot_id is not None or source_path is not None:
            synced_meta = self.prepare_slot_workspace(
                slot_id,
                source_slot_id=source_slot_id,
                source_path=source_path,
                clear_existing=clear_existing,
            )
            meta = self.load_slot_meta(slot_id)
            meta.materialized_from = synced_meta.materialized_from
            meta.last_materialized_at = synced_meta.last_materialized_at
            meta.runtime_bootstrapped_at = synced_meta.runtime_bootstrapped_at
        meta.body_state = "shell"
        meta.pid = None
        meta.lease = None
        meta.last_probe_result = None
        meta.last_retired_at = datetime.utcnow()
        self.save_slot_meta(meta)

        registry.shell_slot = slot_id
        if registry.retired_slot == slot_id:
            registry.retired_slot = None
        if registry.watch_window.slot_id == registry.active_slot:
            registry.watch_window.status = "completed"
        self.save_registry(registry)
        return registry

    def transition_slot(
        self,
        slot_id: str,
        new_state: BodyState,
        *,
        save_meta: bool = True,
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

    def prepare_slot_workspace(
        self,
        slot_id: str,
        *,
        source_slot_id: Optional[str] = None,
        source_path: Optional[str | Path] = None,
        clear_existing: bool = True,
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

        self._sync_directory(
            source_root,
            worktree_root,
            clear_existing=clear_existing,
        )
        self._bootstrap_runtime_directory(
            slot_id,
            runtime_root,
            logs_root,
            clear_existing=clear_existing,
        )

        now = datetime.utcnow()
        source_commit = self._git_head_for_path(source_root)
        source_branch = self._git_branch_for_path(source_root)
        candidate_commit = self._git_head_for_path(worktree_root) or source_commit
        candidate_branch = self._git_branch_for_path(worktree_root) or source_branch
        meta.materialized_from = source_label
        meta.last_materialized_at = now
        meta.runtime_bootstrapped_at = now
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
        if not meta.changed_files:
            meta.changed_files = self._git_changed_files_for_path(
                worktree_root,
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            ) or self._git_changed_files_for_path(
                self.repo_root,
                meta.rollback_commit or meta.source_commit,
                meta.candidate_commit,
            )
        self.save_slot_meta(meta)
        self._write_launch_manifest(meta)
        self._write_worktree_manifest(
            slot_id,
            worktree_root,
            source_label=source_label,
            source_root=source_root,
            materialized_at=now,
        )
        return meta

    def slot_root(self, slot_id: str) -> Path:
        self._validate_slot_id(slot_id)
        return self.slots_root / slot_id

    def slot_meta_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "meta.json"

    def slot_runtime_manifest_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "runtime" / "slot-runtime.json"

    def slot_worktree_manifest_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "worktree" / ".body-origin.json"

    def slot_launch_manifest_path(self, slot_id: str) -> Path:
        return self.slot_root(slot_id) / "runtime" / "slot-launch.json"

    def active_body_pointer_path(self) -> Path:
        return self.repo_root / ".body-active.json"

    def build_launch_target(self, slot_id: str) -> BodyLaunchTarget:
        meta = self.load_slot_meta(slot_id)
        worktree_root = Path(meta.worktree_path).resolve()
        launch_script = (worktree_root / "systems" / "agent" / "run_agent_instance.py").resolve()
        return BodyLaunchTarget(
            slot_id=meta.slot_id,
            body_state=meta.body_state,
            worktree_path=str(worktree_root),
            runtime_path=str(Path(meta.runtime_path).resolve()),
            logs_path=str(Path(meta.logs_path).resolve()),
            launch_script_path=str(launch_script),
            launch_cwd=str(worktree_root),
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
            active_worktree = Path(active_meta.worktree_path)
            if any(active_worktree.iterdir()):
                return active_worktree, f"slot:{active_slot}"

        return self.repo_root, "repo_root"

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
            if self._should_ignore_materialized_name(child.name):
                continue

            destination = target_root / child.name
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, destination)

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

    def _write_launch_manifest(self, meta: BodySlotMeta) -> None:
        target = self.build_launch_target(meta.slot_id)
        atomic_json_write(
            self.slot_launch_manifest_path(meta.slot_id),
            target.model_dump(mode="json"),
        )

    def _write_worktree_manifest(
        self,
        slot_id: str,
        worktree_root: Path,
        *,
        source_label: str,
        source_root: Path,
        materialized_at: datetime,
    ) -> None:
        atomic_json_write(
            self.slot_worktree_manifest_path(slot_id),
            {
                "slot_id": slot_id,
                "worktree_path": str(worktree_root.resolve()),
                "source": source_label,
                "source_root": str(source_root.resolve()),
                "source_branch": self._git_branch_for_path(source_root),
                "source_commit": self._git_head_for_path(source_root),
                "candidate_branch": self._git_branch_for_path(worktree_root),
                "candidate_commit": self._git_head_for_path(worktree_root),
                "materialized_at": materialized_at.isoformat(),
            },
        )

    def _git_branch_for_path(self, path: Path) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
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
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(path.resolve()),
                capture_output=True,
                text=True,
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

    def _clear_directory(self, root: Path) -> None:
        if not root.exists():
            return
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _should_ignore_materialized_name(self, name: str) -> bool:
        return name in DEFAULT_SLOT_COPY_IGNORE_NAMES
