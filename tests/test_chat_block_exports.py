from __future__ import annotations

from datetime import datetime, timezone

import pytest

from voidcube.interfaces.cli.chat.block_exports import (
    export_chat_blocks,
    search_chat_blocks,
)
from voidcube.interfaces.cli.chat.block_store import ChatBlock


def _block(**kwargs) -> ChatBlock:
    defaults = dict(
        block_id="block-1",
        kind="tool_result",
        session_id="session-1",
        turn_id="turn-1",
        name="shell",
        result="done",
    )
    defaults.update(kwargs)
    return ChatBlock(**defaults)


def test_search_matches_tool_arguments_and_limits_results() -> None:
    blocks = (
        _block(block_id="1", arguments={"path": "src/main.py"}),
        _block(block_id="2", result="src/main.py again"),
    )
    matches = search_chat_blocks(blocks, "MAIN.PY", limit=1)
    assert [block.block_id for block in matches] == ["1"]


def test_json_export_redacts_sensitive_keys_and_values(tmp_path) -> None:
    destination = tmp_path / "session.json"
    export_chat_blocks(
        (_block(arguments={"api_key": "secret-value"}, result="Bearer abc-token"),),
        session_id="session-1",
        output_format="json",
        destination=destination,
        exported_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    content = destination.read_text(encoding="utf-8")
    assert "secret-value" not in content
    assert "abc-token" not in content
    assert "[REDACTED]" in content


def test_export_rejects_oversized_payload(tmp_path) -> None:
    with pytest.raises(ValueError, match="exceeds"):
        export_chat_blocks(
            (_block(result="x" * 1000),),
            session_id="session-1",
            output_format="markdown",
            destination=tmp_path / "session.md",
            exported_at=datetime.now(timezone.utc),
            max_bytes=100,
        )
