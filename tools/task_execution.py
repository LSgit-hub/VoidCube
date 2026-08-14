"""Strict task-scoped execution contracts for autonomous code work."""

from __future__ import annotations

import ntpath
import posixpath
import threading
from datetime import datetime, timezone
from typing import Literal, Mapping, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


ExecutionBackendName = Literal[
    "local",
    "docker",
    "podman",
    "singularity",
    "modal",
    "daytona",
    "ssh",
]
ExecutionStateName = Literal[
    "configured",
    "starting",
    "ready",
    "blocked",
    "releasing",
    "released",
]
RuntimeToolName = Literal["git", "python", "pytest", "node", "npm"]


class ExecutionBackend(Protocol):
    """Runtime surface shared by terminal and file operations."""

    cwd: str

    def execute(self, command: str, **kwargs: object) -> Mapping[str, object]: ...

    def cleanup(self) -> None: ...


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class TaskExecutionContract(_FrozenModel):
    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1)
    backend: ExecutionBackendName
    validation_scope: Literal["host", "container", "remote"]
    host_workspace_path: str | None = None
    execution_workspace_path: str = Field(min_length=1)
    allowed_execution_paths: tuple[str, ...] = Field(min_length=1)
    allowed_environment_variables: tuple[str, ...] = ()
    command_timeout_seconds: int = Field(gt=0, le=3600)
    max_output_chars: int = Field(gt=0, le=1_000_000)
    fallback_to_local: Literal[False] = False
    required_tools: tuple[RuntimeToolName, ...] = ()
    required_platforms: tuple[str, ...] = ()
    lifecycle_owner: Literal["conversation", "executor"] = "conversation"

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        _require_unique("allowed execution path", self.allowed_execution_paths)
        _require_unique(
            "allowed environment variable", self.allowed_environment_variables
        )
        _require_unique("required tool", self.required_tools)
        _require_unique("required platform", self.required_platforms)
        if not is_execution_path_allowed(self, self.execution_workspace_path):
            raise ValueError("execution workspace must be inside an allowed path")
        if self.validation_scope == "container" and self.backend not in {
            "docker",
            "podman",
            "singularity",
            "modal",
            "daytona",
        }:
            raise ValueError("container validation requires a container backend")
        if self.validation_scope == "host" and self.backend != "local":
            raise ValueError("host validation requires the local backend")
        return self


class TaskExecutionState(_FrozenModel):
    task_id: str = Field(min_length=1)
    status: ExecutionStateName
    contract: TaskExecutionContract
    active_backend: ExecutionBackendName | None = None
    block_code: str | None = None
    block_reason: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.task_id != self.contract.task_id:
            raise ValueError("execution state task_id must match its contract")
        if self.status == "ready" and self.active_backend is None:
            raise ValueError("ready execution state requires an active backend")
        if self.status == "blocked" and (not self.block_code or not self.block_reason):
            raise ValueError("blocked execution state requires code and reason")
        if self.status != "blocked" and (self.block_code or self.block_reason):
            raise ValueError("only blocked execution state can carry a block reason")
        return self


class TaskExecutionBlocked(RuntimeError):
    def __init__(self, task_id: str, code: str, reason: str) -> None:
        super().__init__(reason)
        self.task_id = task_id
        self.code = code
        self.reason = reason

    def as_payload(self) -> dict[str, object]:
        return {
            "output": "",
            "exit_code": -1,
            "error": self.reason,
            "status": "blocked",
            "block_code": self.code,
            "task_id": self.task_id,
        }


_state_lock = threading.RLock()
_states: dict[str, TaskExecutionState] = {}


def configure_task_execution(contract: TaskExecutionContract) -> TaskExecutionState:
    state = TaskExecutionState(
        task_id=contract.task_id,
        status="configured",
        contract=contract,
        updated_at=_now(),
    )
    with _state_lock:
        existing = _states.get(contract.task_id)
        if existing is not None and existing.status not in {"released", "blocked"}:
            if existing.contract == contract:
                return existing
            raise ValueError("task execution is already configured with another contract")
        _states[contract.task_id] = state
    return state


def get_task_execution_state(task_id: str) -> TaskExecutionState | None:
    with _state_lock:
        return _states.get(str(task_id or "").strip())


def get_task_execution_contract(task_id: str) -> TaskExecutionContract | None:
    state = get_task_execution_state(task_id)
    return state.contract if state is not None else None


def begin_task_execution(task_id: str) -> TaskExecutionState | None:
    with _state_lock:
        state = _states.get(task_id)
        if state is None:
            return None
        if state.status == "ready":
            return state
        if state.status in {"blocked", "releasing", "released"}:
            raise TaskExecutionBlocked(
                task_id,
                state.block_code or "execution_scope_unavailable",
                state.block_reason or f"task execution is {state.status}",
            )
        return _replace_state(state, status="starting")


def mark_task_execution_ready(
    task_id: str,
    *,
    active_backend: str,
) -> TaskExecutionState | None:
    with _state_lock:
        state = _states.get(task_id)
        if state is None:
            return None
        if active_backend != state.contract.backend:
            reason = (
                f"requested backend {state.contract.backend!r} became "
                f"{active_backend!r}; strict task execution forbids fallback"
            )
            _replace_state(
                state,
                status="blocked",
                block_code="backend_fallback_forbidden",
                block_reason=reason,
            )
            raise TaskExecutionBlocked(
                task_id,
                "backend_fallback_forbidden",
                reason,
            )
        return _replace_state(
            state,
            status="ready",
            active_backend=active_backend,
        )


def block_task_execution(
    task_id: str,
    *,
    code: str,
    reason: str,
) -> TaskExecutionState | None:
    with _state_lock:
        state = _states.get(task_id)
        if state is None:
            return None
        return _replace_state(
            state,
            status="blocked",
            block_code=str(code).strip(),
            block_reason=str(reason).strip(),
        )


def release_task_execution(task_id: str) -> TaskExecutionState | None:
    with _state_lock:
        state = _states.get(task_id)
        if state is None:
            return None
        releasing = _replace_state(state, status="releasing")
        return _replace_state(releasing, status="released")


def clear_task_execution_state(task_id: str | None = None) -> None:
    """Test/maintenance helper; normal lifecycle should retain released state."""
    with _state_lock:
        if task_id is None:
            _states.clear()
        else:
            _states.pop(task_id, None)


def ensure_task_execution_request(
    task_id: str,
    *,
    requested_backend: str,
    workdir: str | None,
    timeout_seconds: int,
    environment_variables: tuple[str, ...] = (),
    fallback_to_local: bool,
) -> TaskExecutionContract | None:
    state = get_task_execution_state(task_id)
    if state is None:
        return None
    if state.status in {"blocked", "releasing", "released"}:
        raise TaskExecutionBlocked(
            task_id,
            state.block_code or "execution_scope_unavailable",
            state.block_reason or f"task execution is {state.status}",
        )
    contract = state.contract
    if requested_backend != contract.backend:
        raise _block_and_error(
            task_id,
            "backend_mismatch",
            f"task requires backend {contract.backend!r}, got {requested_backend!r}",
        )
    if fallback_to_local:
        raise _block_and_error(
            task_id,
            "backend_fallback_forbidden",
            "strict task execution cannot fall back to the local backend",
        )
    if timeout_seconds > contract.command_timeout_seconds:
        raise _block_and_error(
            task_id,
            "timeout_exceeds_contract",
            (
                f"command timeout {timeout_seconds}s exceeds task limit "
                f"{contract.command_timeout_seconds}s"
            ),
        )
    if workdir and not is_execution_path_allowed(contract, workdir):
        raise _block_and_error(
            task_id,
            "workdir_outside_allowed_paths",
            f"workdir is outside the task execution scope: {workdir}",
        )
    unexpected_env = sorted(
        set(environment_variables) - set(contract.allowed_environment_variables)
    )
    if unexpected_env:
        raise _block_and_error(
            task_id,
            "environment_variable_not_allowed",
            "task environment contains undeclared variables: "
            + ", ".join(unexpected_env),
        )
    return contract


def ensure_task_execution_path(task_id: str, path: str) -> None:
    contract = get_task_execution_contract(task_id)
    if contract is None:
        return
    if not is_execution_path_allowed(contract, path):
        raise _block_and_error(
            task_id,
            "path_outside_allowed_paths",
            f"path is outside the task execution scope: {path}",
        )


def validate_task_environment_manifest(
    task_id: str,
    manifest: object,
) -> None:
    contract = get_task_execution_contract(task_id)
    if contract is None:
        return
    backend = str(_field(manifest, "backend") or "").strip()
    scope = str(_field(manifest, "validation_scope") or "").strip()
    workspace = str(_field(manifest, "execution_workspace_path") or "").strip()
    if backend != contract.backend or scope != contract.validation_scope:
        raise _block_and_error(
            task_id,
            "environment_manifest_mismatch",
            "environment manifest does not match the task execution contract",
        )
    if _normalize_path(workspace) != _normalize_path(
        contract.execution_workspace_path
    ):
        raise _block_and_error(
            task_id,
            "workspace_mapping_mismatch",
            "environment manifest workspace does not match the task contract",
        )
    platforms = {str(item).strip().lower() for item in (_field(manifest, "validated_platforms") or ())}
    missing_platforms = sorted(set(contract.required_platforms) - platforms)
    if missing_platforms:
        raise _block_and_error(
            task_id,
            "required_platform_unavailable",
            "environment is missing required platforms: " + ", ".join(missing_platforms),
        )
    required_scope = "host" if contract.validation_scope == "host" else "execution"
    available_tools = {
        str(_field(item, "name") or "").strip()
        for item in (_field(manifest, "tools") or ())
        if str(_field(item, "scope") or "").strip() == required_scope
        and bool(_field(item, "available"))
    }
    missing_tools = sorted(set(contract.required_tools) - available_tools)
    if missing_tools:
        raise _block_and_error(
            task_id,
            "required_tool_unavailable",
            "environment is missing required tools: " + ", ".join(missing_tools),
        )


def is_execution_path_allowed(contract: TaskExecutionContract, path: str) -> bool:
    candidate = _normalize_path(path)
    return any(
        _is_same_or_child(candidate, _normalize_path(root))
        for root in contract.allowed_execution_paths
    )


def _block_and_error(task_id: str, code: str, reason: str) -> TaskExecutionBlocked:
    block_task_execution(task_id, code=code, reason=reason)
    return TaskExecutionBlocked(task_id, code, reason)


def _replace_state(
    state: TaskExecutionState,
    *,
    status: ExecutionStateName,
    active_backend: str | None = None,
    block_code: str | None = None,
    block_reason: str | None = None,
) -> TaskExecutionState:
    next_state = TaskExecutionState(
        task_id=state.task_id,
        status=status,
        contract=state.contract,
        active_backend=active_backend,
        block_code=block_code,
        block_reason=block_reason,
        updated_at=_now(),
    )
    _states[state.task_id] = next_state
    return next_state


def _normalize_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    if value.startswith("/"):
        return posixpath.normpath(value)
    return ntpath.normcase(ntpath.normpath(value))


def _is_same_or_child(candidate: str, root: str) -> bool:
    if not candidate or not root:
        return False
    path_module = posixpath if root.startswith("/") else ntpath
    try:
        return path_module.commonpath((candidate, root)) == root
    except ValueError:
        return False


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _require_unique(label: str, values: object) -> None:
    items = [str(item) for item in values]
    if len(items) != len(set(items)):
        raise ValueError(f"{label} values must be unique")


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ExecutionBackend",
    "TaskExecutionBlocked",
    "TaskExecutionContract",
    "TaskExecutionState",
    "begin_task_execution",
    "block_task_execution",
    "clear_task_execution_state",
    "configure_task_execution",
    "ensure_task_execution_path",
    "ensure_task_execution_request",
    "get_task_execution_contract",
    "get_task_execution_state",
    "is_execution_path_allowed",
    "mark_task_execution_ready",
    "release_task_execution",
    "validate_task_environment_manifest",
]
