"""Keep the shared memai package bound to the canonical repository source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from VoidCube_core.utils import atomic_json_write


class CanonicalMemBindingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalMemBindingResult:
    source_path: Path
    pth_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "source_path": str(self.source_path),
            "pth_path": str(self.pth_path),
        }


def validate_canonical_mem_source(source_root: str | Path) -> Path:
    source = Path(source_root).resolve() / "Mem" / "src"
    required = (
        source / "memai" / "__init__.py",
        source / "memai" / "model_config.py",
        source / "memai" / "identity" / "founding_memory.json",
        source / "memai" / "identity" / "founding_story.md",
    )
    missing = [
        str(path.relative_to(source.parent.parent))
        for path in required
        if not path.is_file()
    ]
    if missing:
        raise CanonicalMemBindingError(
            "Canonical Mem package is incomplete; missing: " + ", ".join(missing)
        )
    model_config = (source / "memai" / "model_config.py").read_text(
        encoding="utf-8"
    )
    for symbol in ("def resolve_mem_llm_client", "def _resolve_mem_api_key"):
        if symbol not in model_config:
            raise CanonicalMemBindingError(
                "Canonical Mem package is missing required resolver: "
                + symbol.removeprefix("def ")
            )
    return source


def sync_canonical_mem_binding(
    *,
    source_root: str | Path,
    site_packages: str | Path,
    audit_path: str | Path | None = None,
) -> CanonicalMemBindingResult:
    source = validate_canonical_mem_source(source_root)
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

    result = CanonicalMemBindingResult(source_path=source, pth_path=pth_path)
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
