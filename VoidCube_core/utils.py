"""Shared utility functions for VoidCube-agent."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)


TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    """Coerce bool-ish values using the project's shared truthy string set."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def env_var_enabled(name: str, default: str = "") -> bool:
    """Return True when an environment variable is set to a truthy value."""
    return is_truthy_value(os.getenv(name, default), default=False)


def atomic_json_write(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    **dump_kwargs: Any,
) -> None:
    """Write JSON data to a file atomically.

    Uses temp file + fsync + os.replace to ensure the target file is never
    left in a partially-written state. If the process crashes mid-write,
    the previous version of the file remains intact.

    Args:
        path: Target file path (will be created or overwritten).
        data: JSON-serializable data to write.
        indent: JSON indentation (default 2).
        **dump_kwargs: Additional keyword args forwarded to json.dump(), such
            as default=str for non-native types.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=indent,
                ensure_ascii=False,
                **dump_kwargs,
            )
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        # Intentionally catch BaseException so temp-file cleanup still runs for
        # KeyboardInterrupt/SystemExit before re-raising the original signal.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_yaml_write(
    path: Union[str, Path],
    data: Any,
    *,
    default_flow_style: bool = False,
    sort_keys: bool = False,
    extra_content: str | None = None,
) -> None:
    """Write YAML data to a file atomically.

    Uses temp file + fsync + os.replace to ensure the target file is never
    left in a partially-written state.  If the process crashes mid-write,
    the previous version of the file remains intact.

    Args:
        path: Target file path (will be created or overwritten).
        data: YAML-serializable data to write.
        default_flow_style: YAML flow style (default False).
        sort_keys: Whether to sort dict keys (default False).
        extra_content: Optional string to append after the YAML dump
            (e.g. commented-out sections for user reference).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=default_flow_style, sort_keys=sort_keys)
            if extra_content:
                f.write(extra_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Match atomic_json_write: cleanup must also happen for process-level
        # interruptions before we re-raise them.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── JSON Helpers ─────────────────────────────────────────────────────────────


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Parse JSON, returning *default* on any parse error.

    Replaces the ``try: json.loads(x) except (JSONDecodeError, TypeError)``
    pattern duplicated across display.py,
    auxiliary_client.py, and others.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def read_json_file(path: Path, default: Any = None) -> Any:
    """Read and parse a JSON file, returning *default* on any error.

    Replaces the repeated ``try: json.loads(path.read_text()) except ...``
    pattern in auxiliary_client.py and credential_pool.py,
    and skill_utils.py.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, IOError, ValueError) as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return default


def read_jsonl(path: Path) -> List[dict]:
    """Read a JSONL file (one JSON object per line).

    Returns a list of parsed objects, skipping blank lines.
    """
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_jsonl(path: Path, entry: dict) -> None:
    """Append a single JSON object as a new line to a JSONL file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Environment Variable Helpers ─────────────────────────────────────────────


def env_str(key: str, default: str = "") -> str:
    """Read an environment variable, stripped of whitespace.

    Replaces the ``os.getenv("X", "").strip()`` pattern repeated 50+ times
    across runtime_provider.py, models.py, and related modules.
    """
    return os.getenv(key, default).strip()


def env_lower(key: str, default: str = "") -> str:
    """Read an environment variable, stripped and lowercased."""
    return os.getenv(key, default).strip().lower()


def env_int(key: str, default: int = 0) -> int:
    """Read an environment variable as an integer, with fallback."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    """Read an environment variable as a boolean."""
    return is_truthy_value(os.getenv(key, ""), default=default)


# ─── String Helpers ───────────────────────────────────────────────────────────


def normalize_str(value: Any, default: str = "") -> str:
    """Normalize string: strip whitespace and convert to lowercase.

    Replaces the repeated ``value.strip().lower()`` pattern found 100+ times
    across the codebase.

    Args:
        value: Any value to normalize (will be converted to string).
        default: Default value if input is None or empty.

    Returns:
        Normalized lowercase string.
    """
    return str(value or default).strip().lower()


# ─── Dict Helpers ──────────────────────────────────────────────────────────────


def safe_dict_get(data: dict, key: str, default: dict | None = None) -> dict:
    """Safely get a dict value, returning empty dict if not a dict.

    Replaces the repeated pattern:
        val = data.get("key") or {}
        if not isinstance(val, dict):
            val = {}

    Args:
        data: Source dictionary.
        key: Key to retrieve.
        default: Default dict if key missing or not a dict (default: empty dict).

    Returns:
        Dictionary value or default.
    """
    val = data.get(key)
    return val if isinstance(val, dict) else (default or {})


# ─── File Helpers ──────────────────────────────────────────────────────────────


def read_file_if_exists(
    path: Path,
    default: Any = None,
    loader: Any = None,
) -> Any:
    """Read file if exists, return default otherwise.

    Replaces the repeated pattern:
        if not path.exists():
            return default
        content = path.read_text()

    Args:
        path: File path to read.
        default: Value to return if file doesn't exist.
        loader: Optional function to process file content (e.g., json.loads).

    Returns:
        File content (processed by loader if provided) or default.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        content = path.read_text(encoding="utf-8")
        return loader(content) if loader else content
    except (OSError, IOError) as exc:
        logger.debug("Failed to read %s: %s", path, exc)
        return default
