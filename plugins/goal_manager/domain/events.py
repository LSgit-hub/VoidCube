"""Audit event serialization helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any


def new_id(prefix: str = "") -> str:
    value = str(uuid.uuid4())
    return f"{prefix}{value}" if prefix else value


def dump_json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def load_json(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
