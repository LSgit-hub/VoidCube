"""Search and safe export services for structured CLI chat blocks."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from VoidCube_cli.chat_block_store import ChatBlock


_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "cookie", "credential",
    "password", "secret", "token",
}
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*([^\s,;]+)"),
)


def search_chat_blocks(
    blocks: Sequence[ChatBlock], query: str, *, limit: int = 20,
) -> tuple[ChatBlock, ...]:
    """Return ordered case-insensitive matches across block fields."""
    needle = str(query or "").strip().casefold()
    if not needle or limit < 1:
        return ()
    matches: list[ChatBlock] = []
    for block in blocks:
        haystack = "\n".join((
            block.kind, block.name, block.text, block.result,
            _searchable(block.arguments), _searchable(block.metadata),
        )).casefold()
        if needle in haystack:
            matches.append(block)
            if len(matches) >= limit:
                break
    return tuple(matches)


def render_search_result(block: ChatBlock, *, preview_limit: int = 180) -> str:
    """Render one compact search hit for the inline transcript."""
    source = block.text or block.result or block.name or block.kind
    preview = " ".join(str(source).split())
    if len(preview) > preview_limit:
        preview = preview[: preview_limit - 3] + "..."
    turn = block.turn_id[:8] if block.turn_id else "session"
    return f"  [{block.kind} · {turn}] {preview}"


def export_chat_blocks(
    blocks: Sequence[ChatBlock], *, session_id: str, output_format: str,
    destination: Path, exported_at: datetime, max_bytes: int = 5 * 1024 * 1024,
) -> Path:
    """Write a redacted Markdown or JSON export within a bounded size."""
    normalized = str(output_format or "").strip().lower()
    if normalized not in {"json", "markdown"}:
        raise ValueError("format must be 'markdown' or 'json'")
    payload = [_block_record(block) for block in blocks]
    if normalized == "json":
        content = json.dumps({
            "session_id": session_id,
            "exported_at": exported_at.isoformat(),
            "blocks": payload,
        }, ensure_ascii=False, indent=2)
    else:
        content = _render_markdown(payload, session_id=session_id, exported_at=exported_at)
    if len(content.encode("utf-8")) > max_bytes:
        raise ValueError(f"export exceeds {max_bytes} bytes")
    destination.write_text(content, encoding="utf-8")
    return destination


def default_export_path(
    directory: Path, *, output_format: str, exported_at: datetime,
) -> Path:
    suffix = "md" if output_format == "markdown" else "json"
    timestamp = exported_at.strftime("%Y%m%d_%H%M%S")
    return directory / f"VoidCube_session_{timestamp}.{suffix}"


def _block_record(block: ChatBlock) -> dict[str, Any]:
    record = asdict(block)
    record["created_at"] = block.created_at.isoformat()
    record["updated_at"] = block.updated_at.isoformat()
    return _redact(record)


def _redact(value: Any, *, key: str = "") -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        marker in normalized_key
        for marker in ("api_key", "password", "secret", "access_token", "auth_token")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.groups == 2:
                redacted = pattern.sub(r"\1=[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value


def _render_markdown(
    records: Sequence[Mapping[str, Any]], *, session_id: str, exported_at: datetime,
) -> str:
    lines = [
        "# VoidCube Session Export", "", f"- Session: {session_id}",
        f"- Exported: {exported_at.isoformat()}", "",
    ]
    for record in records:
        kind = str(record.get("kind") or "block")
        status = str(record.get("status") or "")
        name = str(record.get("name") or "")
        title = f"## {kind}" + (f": {name}" if name else "")
        if status:
            title += f" ({status})"
        lines.extend((title, ""))
        text = str(record.get("text") or record.get("result") or "")
        if text:
            lines.extend((text, ""))
        arguments = record.get("arguments")
        if arguments:
            lines.extend((
                "```json", json.dumps(arguments, ensure_ascii=False, indent=2),
                "```", "",
            ))
    return "\n".join(lines).rstrip() + "\n"


def _searchable(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = [
    "default_export_path", "export_chat_blocks", "render_search_result",
    "search_chat_blocks",
]
