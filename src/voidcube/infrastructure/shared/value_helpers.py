"""Small, dependency-light value and text helpers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)
TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def env_var_enabled(name: str, default: str = "") -> bool:
    return is_truthy_value(os.getenv(name, default), default=False)


def safe_json_loads(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def read_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, IOError, ValueError) as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return default


def read_jsonl(path: Path) -> List[dict]:
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_jsonl(path: Path, entry: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def env_lower(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip().lower()


def env_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return is_truthy_value(os.getenv(key, ""), default=default)


def normalize_str(value: Any, default: str = "") -> str:
    return str(value or default).strip().lower()


def safe_dict_get(data: dict, key: str, default: dict | None = None) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else (default or {})


def read_file_if_exists(path: Path, default: Any = None, loader: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        content = path.read_text(encoding="utf-8")
        return loader(content) if loader else content
    except (OSError, IOError) as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return default


__all__ = [name for name in globals() if not name.startswith("_")]
