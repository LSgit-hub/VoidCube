"""External research evidence loading with explicit filesystem inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .endogenous_evidence import (
    normalize_external_research_entries,
    normalize_external_research_file_payload,
)


def build_external_research_evidence(
    *,
    enabled: bool,
    entries: List[Any],
    file_entries: List[Any],
    repo_root: Any = "./",
) -> List[Dict[str, Any]]:
    if not enabled:
        return []
    evidence_rows = normalize_external_research_entries(list(entries or []))
    evidence_rows.extend(
        load_external_research_files(
            list(file_entries or []),
            repo_root=repo_root,
        )
    )
    return evidence_rows[:16]


def load_external_research_files(
    file_entries: List[Any],
    *,
    repo_root: Any = "./",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw_path in list(file_entries or [])[:6]:
        path_text = str(raw_path or "").strip()
        if not path_text:
            continue
        rows.extend(
            load_external_research_file(
                path_text,
                repo_root=repo_root,
            )
        )
        if len(rows) >= 12:
            break
    return rows[:12]


def load_external_research_file(
    raw_path: str,
    *,
    repo_root: Any = "./",
) -> List[Dict[str, Any]]:
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path(repo_root or "./") / path).resolve()
        else:
            path = path.resolve()
        if not path.exists() or not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return normalize_external_research_file_payload(data, source_path=str(path))[:8]
