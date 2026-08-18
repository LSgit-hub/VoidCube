from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
import uuid

from ..infrastructure.runtime.layout import get_legacy_project_runtime_layout
from systems.body_registry import BodyLaunchTarget, BodyRegistry, BodySlotMeta


class BodyRuntimeMigrationConflict(RuntimeError):
    """Raised when canonical and legacy Body state both exist."""


class IncompleteLegacyBodyRuntime(RuntimeError):
    """Raised when only part of the legacy Body state bundle exists."""


@dataclass(frozen=True, slots=True)
class BodyRuntimeMigrationResult:
    status: str
    source_root: Path
    target_root: Path
    files_verified: int = 0
    linked_worktrees_repaired: int = 0


def migrate_body_runtime(
    *,
    source_root: str | Path,
    target_root: str | Path,
) -> BodyRuntimeMigrationResult:
    """Move legacy Body state to canonical storage after full verification."""
    source_repo = Path(source_root).resolve()
    target = Path(target_root).resolve()
    legacy = get_legacy_project_runtime_layout(source_repo)
    legacy_paths = {
        "slots": legacy.body_slots_root,
        "registry": legacy.body_registry,
        "active_pointer": legacy.body_active_pointer,
    }
    present = {name: path.exists() for name, path in legacy_paths.items()}
    has_legacy = any(present.values())

    if target.exists():
        if has_legacy:
            raise BodyRuntimeMigrationConflict(
                "Both canonical and legacy Body runtime state exist: "
                f"{target} and {source_repo}. Refusing to choose or merge."
            )
        return BodyRuntimeMigrationResult(
            status="target_exists",
            source_root=source_repo,
            target_root=target,
        )
    if not has_legacy:
        return BodyRuntimeMigrationResult(
            status="source_missing",
            source_root=source_repo,
            target_root=target,
        )
    if not all(present.values()):
        missing = [name for name, exists in present.items() if not exists]
        raise IncompleteLegacyBodyRuntime(
            "Legacy Body runtime bundle is incomplete; missing: "
            + ", ".join(missing)
        )
    if not legacy.body_slots_root.is_dir():
        raise IncompleteLegacyBodyRuntime(
            f"Legacy Body slots path is not a directory: {legacy.body_slots_root}"
        )
    if not legacy.body_registry.is_file() or not legacy.body_active_pointer.is_file():
        raise IncompleteLegacyBodyRuntime(
            "Legacy Body registry and active pointer must both be files"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.migrating-{uuid.uuid4().hex}")
    published = False
    try:
        temporary.mkdir()
        shutil.copytree(
            legacy.body_slots_root,
            temporary / "slots",
            symlinks=True,
        )
        shutil.copy2(legacy.body_registry, temporary / "registry.json")
        shutil.copy2(legacy.body_active_pointer, temporary / "active.json")

        source_snapshot = _snapshot_legacy_bundle(
            slots_root=legacy.body_slots_root,
            registry_path=legacy.body_registry,
            active_pointer_path=legacy.body_active_pointer,
        )
        copied_snapshot = _snapshot_tree(temporary)
        if source_snapshot != copied_snapshot:
            raise RuntimeError(
                "Copied Body runtime failed file-set or checksum verification"
            )

        _rewrite_body_state_paths(
            staged_root=temporary,
            legacy_slots_root=legacy.body_slots_root.resolve(),
            target_slots_root=target / "slots",
        )
        _validate_staged_body_runtime(temporary, target)
        os.replace(temporary, target)
        published = True

        repaired = _repair_linked_worktrees(
            source_repo=source_repo,
            slots_root=target / "slots",
        )
        _remove_legacy_body_bundle(legacy_paths)
    except Exception:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise

    return BodyRuntimeMigrationResult(
        status="migrated",
        source_root=source_repo,
        target_root=target,
        files_verified=len(copied_snapshot),
        linked_worktrees_repaired=repaired,
    )


def _snapshot_legacy_bundle(
    *,
    slots_root: Path,
    registry_path: Path,
    active_pointer_path: Path,
) -> dict[str, str]:
    snapshot = {
        "registry.json": _digest_path(registry_path),
        "active.json": _digest_path(active_pointer_path),
    }
    for path in sorted(slots_root.rglob("*")):
        if path.is_file() or path.is_symlink():
            relative = (Path("slots") / path.relative_to(slots_root)).as_posix()
            snapshot[relative] = _digest_path(path)
    return snapshot


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() or path.is_symlink():
            snapshot[path.relative_to(root).as_posix()] = _digest_path(path)
    return snapshot


def _digest_path(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rewrite_body_state_paths(
    *,
    staged_root: Path,
    legacy_slots_root: Path,
    target_slots_root: Path,
) -> None:
    for path in staged_root.rglob("*.json"):
        relative_parts = path.relative_to(staged_root).parts
        if len(relative_parts) >= 3 and relative_parts[0] == "slots":
            if relative_parts[2] == "worktree":
                continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rewritten = _rewrite_value(
            data,
            old_root=legacy_slots_root,
            new_root=target_slots_root,
        )
        if rewritten != data:
            path.write_text(
                json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def _rewrite_value(value: Any, *, old_root: Path, new_root: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: _rewrite_value(item, old_root=old_root, new_root=new_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_value(item, old_root=old_root, new_root=new_root)
            for item in value
        ]
    if not isinstance(value, str):
        return value

    normalized = value.replace("\\", "/")
    old_normalized = old_root.as_posix().rstrip("/")
    folded = normalized.casefold()
    old_folded = old_normalized.casefold()
    if folded == old_folded:
        return str(new_root)
    prefix = old_folded + "/"
    if not folded.startswith(prefix):
        return value
    suffix = normalized[len(old_normalized) :].lstrip("/")
    return str(new_root / Path(suffix))


def _validate_staged_body_runtime(staged_root: Path, final_root: Path) -> None:
    registry = BodyRegistry.model_validate_json(
        (staged_root / "registry.json").read_text(encoding="utf-8")
    )
    pointer = BodyLaunchTarget.model_validate_json(
        (staged_root / "active.json").read_text(encoding="utf-8")
    )
    if registry.active_slot != pointer.slot_id:
        raise RuntimeError(
            "Body active pointer does not match the registry active slot"
        )

    for slot_id in registry.slot_ids:
        slot_root = staged_root / "slots" / slot_id
        meta = BodySlotMeta.model_validate_json(
            (slot_root / "meta.json").read_text(encoding="utf-8")
        )
        expected_root = final_root / "slots" / slot_id
        expected_paths = {
            "worktree_path": expected_root / "worktree",
            "runtime_path": expected_root / "runtime",
            "logs_path": expected_root / "logs",
        }
        for field_name, expected in expected_paths.items():
            actual = Path(getattr(meta, field_name)).resolve()
            if actual != expected.resolve():
                raise RuntimeError(
                    f"Body slot {slot_id} has invalid {field_name}: {actual}"
                )
            if not (slot_root / field_name.removesuffix("_path")).is_dir():
                raise RuntimeError(
                    f"Body slot {slot_id} is missing {field_name.removesuffix('_path')}"
                )

        origin = json.loads(
            (slot_root / "worktree-origin.json").read_text(encoding="utf-8")
        )
        if Path(str(origin.get("worktree_path") or "")).resolve() != (
            expected_root / "worktree"
        ).resolve():
            raise RuntimeError(f"Body slot {slot_id} worktree manifest path is stale")

        runtime = json.loads(
            (slot_root / "runtime" / "slot-runtime.json").read_text(
                encoding="utf-8"
            )
        )
        if Path(str(runtime.get("runtime_path") or "")).resolve() != (
            expected_root / "runtime"
        ).resolve():
            raise RuntimeError(f"Body slot {slot_id} runtime manifest path is stale")
        if Path(str(runtime.get("logs_path") or "")).resolve() != (
            expected_root / "logs"
        ).resolve():
            raise RuntimeError(f"Body slot {slot_id} logs manifest path is stale")

    active_slot_root = final_root / "slots" / pointer.slot_id
    if Path(pointer.worktree_path).resolve() != (
        active_slot_root / "worktree"
    ).resolve():
        raise RuntimeError("Body active pointer worktree path is stale")
    if Path(pointer.runtime_path).resolve() != (
        active_slot_root / "runtime"
    ).resolve():
        raise RuntimeError("Body active pointer runtime path is stale")
    if Path(pointer.logs_path).resolve() != (
        active_slot_root / "logs"
    ).resolve():
        raise RuntimeError("Body active pointer logs path is stale")


def _repair_linked_worktrees(*, source_repo: Path, slots_root: Path) -> int:
    worktrees = sorted(
        path.parent
        for path in slots_root.glob("*/worktree/.git")
        if path.is_file()
    )
    if not worktrees:
        return 0
    result = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo),
            "worktree",
            "repair",
            *(str(path) for path in worktrees),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to repair migrated Body linked worktrees: "
            + result.stderr.strip()
        )
    for worktree in worktrees:
        verification = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if verification.returncode != 0 or Path(
            verification.stdout.strip()
        ).resolve() != worktree.resolve():
            raise RuntimeError(
                f"Migrated Body worktree failed Git verification: {worktree}"
            )
    return len(worktrees)


def _remove_legacy_body_bundle(paths: dict[str, Path]) -> None:
    cleanup_errors: list[str] = []
    for name in ("slots", "registry", "active_pointer"):
        path = paths[name]
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{path}: {exc}")
    if cleanup_errors:
        raise RuntimeError(
            "Canonical Body runtime was created but legacy cleanup failed: "
            + "; ".join(cleanup_errors)
        )
