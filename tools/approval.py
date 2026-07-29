"""Dangerous-command detection and application-level approval routing."""

from __future__ import annotations

from typing import Any

from VoidCube_app.interaction_contract import (
    ApprovalRequest,
    ApprovalSink,
    resolve_approval,
)


_DANGEROUS_PATTERNS = [
    "rm -rf",
    "rmdir",
    "del /",
    "format",
    "mkfs",
    "dd if=",
    "> /dev/",
    "chmod 777",
]


def check_dangerous_command(
    command: str,
    env_type: str = "local",
) -> dict[str, Any]:
    """Return whether a command matches a known dangerous pattern."""
    del env_type
    command_lower = command.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in command_lower:
            return {
                "safe": False,
                "reason": f"检测到危险命令模式: {pattern}",
                "requires_approval": True,
                "command": command,
            }
    return {
        "safe": True,
        "reason": "",
        "requires_approval": False,
        "command": command,
    }


def check_all_command_guards(
    command: str,
    env_type: str = "local",
    approval_sink: ApprovalSink | None = None,
) -> dict[str, Any]:
    """Check command guards and resolve required approval explicitly."""
    dangerous_check = check_dangerous_command(command, env_type)
    if dangerous_check["safe"]:
        return {
            "allowed": True,
            "reason": "",
            "approved": True,
            "command": command,
        }

    decision = resolve_approval(
        ApprovalRequest(
            command=command,
            description=dangerous_check["reason"],
        ),
        approval_sink,
    )
    return {
        "allowed": decision.approved,
        "reason": dangerous_check["reason"],
        "approved": decision.approved,
        "approval_status": decision.status.value,
        "approval_reason": decision.reason,
        "command": command,
    }
