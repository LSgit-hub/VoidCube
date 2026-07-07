from __future__ import annotations

from typing import Any

_LEGACY_STATUS_ALIASES = {
    "queued": "planned",
}

_OBSERVATION_STATUS_LABELS = {
    "planned": "待审核",
    "awaiting_review": "待审查",
    "approved": "待执行",
    "running": "执行中",
    "retry": "重试",
    "deferred": "已推迟",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

_TRACE_STATUS_LABELS = {
    **_OBSERVATION_STATUS_LABELS,
    "retry": "重试中",
    "deferred": "已延后",
    "completed": "已写回",
    "failed": "执行失败",
}


def normalize_autonomous_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return _LEGACY_STATUS_ALIASES.get(normalized, normalized)


def observation_status_label(value: Any, *, default: str = "待定") -> str:
    normalized = normalize_autonomous_status(value)
    return _OBSERVATION_STATUS_LABELS.get(normalized, normalized or default)


def trace_status_label(value: Any, *, default: str = "状态未识别") -> str:
    normalized = normalize_autonomous_status(value)
    return _TRACE_STATUS_LABELS.get(normalized, str(value or "").strip() or default)
