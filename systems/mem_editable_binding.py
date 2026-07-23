"""Keep the memai editable import bound to the active Body workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from VoidCube_core.utils import atomic_json_write


class MemEditableBindingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MemEditableBindingResult:
    slot_id: str
    source_path: Path
    pth_path: Path
    fallback: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "slot_id": self.slot_id,
            "source_path": str(self.source_path),
            "pth_path": str(self.pth_path),
            "fallback": self.fallback,
        }


def validate_mem_source(worktree_path: str | Path) -> Path:
    source = Path(worktree_path).resolve() / "Mem" / "src"
    required = (
        source / "memai" / "__init__.py",
        source / "memai" / "model_config.py",
        source / "memai" / "identity" / "founding_memory.json",
        source / "memai" / "identity" / "founding_story.md",
    )
    missing = [str(path.relative_to(source.parent.parent)) for path in required if not path.is_file()]
    if missing:
        raise MemEditableBindingError(
            "Body Mem package is incomplete; missing: " + ", ".join(missing)
        )
    model_config = (source / "memai" / "model_config.py").read_text(encoding="utf-8")
    for symbol in ("def resolve_mem_llm_client", "def _resolve_mem_api_key"):
        if symbol not in model_config:
            raise MemEditableBindingError(
                f"Body Mem package is missing required resolver: {symbol.removeprefix('def ')}"
            )
    return source


def sync_mem_editable_binding(
    *,
    slot_id: str,
    worktree_path: str | Path,
    source_root: str | Path,
    site_packages: str | Path,
    allow_source_fallback: bool,
    audit_path: str | Path | None = None,
) -> MemEditableBindingResult:
    fallback = False
    try:
        source = validate_mem_source(worktree_path)
    except MemEditableBindingError:
        if not allow_source_fallback:
            raise
        source = validate_mem_source(source_root)
        fallback = True

    site_root = Path(site_packages).resolve()
    site_root.mkdir(parents=True, exist_ok=True)
    pth_candidates = list(_memai_editable_pths(site_root))
    pth_path = (
        pth_candidates[0]
        if pth_candidates
        else site_root / "__editable__.memai-0.1.0.pth"
    )
    _atomic_text_write(pth_path, str(source) + "\n")
    for stale in pth_candidates[1:]:
        stale.unlink()

    result = MemEditableBindingResult(
        slot_id=slot_id,
        source_path=source,
        pth_path=pth_path,
        fallback=fallback,
    )
    if audit_path is not None:
        atomic_json_write(Path(audit_path), result.to_dict())
    return result


def _memai_editable_pths(site_root: Path) -> Iterable[Path]:
    return sorted(site_root.glob("__editable__.memai-*.pth"))


def _atomic_text_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
