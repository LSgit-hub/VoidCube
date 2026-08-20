"""Manifest-based synchronization of bundled skills."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...infrastructure.config.runtime_paths import get_VoidCube_home


def _bundled_dir() -> Path:
    override = os.getenv("VOIDCUBE_BUNDLED_SKILLS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "skills"


def _paths() -> tuple[Path, Path]:
    skills_dir = get_VoidCube_home() / "skills"
    return skills_dir, skills_dir / ".bundled_manifest"


def _refresh_skill_registry(skills_dir: Path) -> None:
    """Refresh the index after sync completes; never mask sync success."""
    try:
        from .registry import REGISTRY_FILENAME, refresh_catalog_index

        refresh_catalog_index(
            extra_paths=(skills_dir,),
            path=skills_dir.parent / REGISTRY_FILENAME,
        )
    except Exception as exc:
        # Sync is the file authority; an unavailable index is recoverable.
        import logging

        logging.getLogger(__name__).debug(
            "Could not refresh skill registry after bundled sync: %s", exc
        )


def _read_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        result: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name, separator, digest = line.partition(":")
            result[name.strip()] = digest.strip() if separator else ""
        return result
    except OSError:
        return {}


def _write_manifest(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(f"{name}:{digest}" for name, digest in sorted(entries.items())) + "\n"
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".bundled_manifest_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _skill_name(path: Path, fallback: str) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return fallback
    in_frontmatter = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
        elif in_frontmatter and stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return fallback


def _discover(root: Path) -> list[tuple[str, Path]]:
    if not root.exists():
        return []
    result = []
    for skill_file in root.rglob("SKILL.md"):
        if any(part in {".git", ".github", ".hub"} for part in skill_file.parts):
            continue
        result.append((_skill_name(skill_file, skill_file.parent.name), skill_file.parent))
    return result


def _hash(directory: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    try:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(directory)).encode("utf-8"))
                digest.update(path.read_bytes())
    except OSError:
        pass
    return digest.hexdigest()


def sync_skills(*, quiet: bool = False) -> dict[str, Any]:
    """Copy or update bundled skills while preserving user modifications."""
    bundled = _bundled_dir()
    empty = {"copied": [], "updated": [], "skipped": 0, "user_modified": [], "cleaned": [], "total_bundled": 0}
    if not bundled.exists():
        return empty
    skills_dir, manifest_path = _paths()
    skills_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(manifest_path)
    discovered = _discover(bundled)
    names = {name for name, _ in discovered}
    copied: list[str] = []
    updated: list[str] = []
    modified: list[str] = []
    skipped = 0

    for name, source in discovered:
        destination = skills_dir / source.relative_to(bundled)
        source_hash = _hash(source)
        origin = manifest.get(name)
        if name not in manifest:
            if destination.exists():
                skipped += 1
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, destination)
                copied.append(name)
                if not quiet:
                    print(f"  + {name}")
            manifest[name] = source_hash
            continue
        if not destination.exists():
            skipped += 1
            continue
        current_hash = _hash(destination)
        if not origin:
            manifest[name] = current_hash
            skipped += 1
            continue
        if current_hash != origin:
            modified.append(name)
            skipped += 1
            if not quiet:
                print(f"  ~ {name} (user-modified, skipping)")
            continue
        if source_hash == origin:
            skipped += 1
            continue
        backup = destination.with_suffix(".bak")
        try:
            shutil.move(str(destination), str(backup))
            shutil.copytree(source, destination)
            shutil.rmtree(backup, ignore_errors=True)
            manifest[name] = source_hash
            updated.append(name)
            if not quiet:
                print(f"  ^ {name} (updated)")
        except OSError:
            if backup.exists() and not destination.exists():
                shutil.move(str(backup), str(destination))

    cleaned = sorted(set(manifest) - names)
    for name in cleaned:
        manifest.pop(name, None)
    for description in bundled.rglob("DESCRIPTION.md"):
        destination = skills_dir / description.relative_to(bundled)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(description, destination)
            except OSError:
                pass
    _write_manifest(manifest_path, manifest)
    _refresh_skill_registry(skills_dir)
    return {
        "copied": copied,
        "updated": updated,
        "skipped": skipped,
        "user_modified": modified,
        "cleaned": cleaned,
        "total_bundled": len(discovered),
    }


__all__ = ["sync_skills"]
