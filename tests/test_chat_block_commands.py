from __future__ import annotations

from datetime import datetime
from pathlib import Path

from voidcube.interfaces.cli.chat.block_store import ChatBlock
from voidcube.interfaces.cli.commands.handlers.chat_blocks import (
    ChatBlockCommandPorts,
    handle_export_command,
    handle_find_command,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command


def _ports(tmp_path: Path, output: list[str], blocks: tuple[ChatBlock, ...]):
    return ChatBlockCommandPorts(
        blocks=lambda: blocks,
        session_id=lambda: "session-1",
        now=lambda: datetime(2026, 8, 7, 12, 0, 0),
        working_directory=lambda: tmp_path,
        emit=output.append,
    )


def test_find_command_reports_usage_and_hits(tmp_path) -> None:
    output: list[str] = []
    blocks = (ChatBlock("1", "assistant", "session-1", text="Need inspect app.py"),)
    ports = _ports(tmp_path, output, blocks)

    handle_find_command(parse_cli_command("/find app.py"), ports=ports)

    assert output[0].startswith("  Found 1")
    assert "app.py" in output[1]


def test_export_command_writes_json_and_handles_invalid_format(tmp_path) -> None:
    output: list[str] = []
    blocks = (ChatBlock("1", "user", "session-1", text="hello"),)
    ports = _ports(tmp_path, output, blocks)

    handle_export_command(parse_cli_command("/export json"), ports=ports)
    assert (tmp_path / "VoidCube_session_20260807_120000.json").exists()

    handle_export_command(parse_cli_command("/export yaml"), ports=ports)
    assert output[-1] == "  Usage: /export <markdown|json>"
