"""Dangerous-command detection and application-level approval routing."""

from __future__ import annotations

import re
import shlex
from typing import Any

from ...domain.contracts.interaction import (
    ApprovalRequest,
    ApprovalSink,
    resolve_approval,
)


_COMMAND_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
_COMMAND_WRAPPERS = frozenset({"command", "doas", "env", "exec", "sudo"})
_SHELL_INTERPRETERS = frozenset(
    {
        "bash",
        "cmd",
        "dash",
        "fish",
        "ksh",
        "pwsh",
        "powershell",
        "sh",
        "zsh",
    }
)
_SAFE_DEVICE_TARGETS = frozenset(
    {
        "/dev/null",
        "/dev/ptmx",
        "/dev/random",
        "/dev/stderr",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/tty",
        "/dev/urandom",
        "/dev/zero",
    }
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPER_OPTIONS_WITH_VALUE = frozenset(
    {
        "-a",
        "-c",
        "-g",
        "-h",
        "-p",
        "-u",
        "-C",
        "--chdir",
        "--group",
        "--host",
        "--prompt",
        "--user",
    }
)


def check_dangerous_command(
    command: str,
    env_type: str = "local",
) -> dict[str, Any]:
    """Return whether an executed command matches a dangerous operation.

    Detection is command-aware: words inside quoted arguments, help text, and
    option values are not treated as commands. This avoids approving prompts
    for commands such as ``git log --format='rm -rf'`` or
    ``echo 'chmod 777'`` while retaining the existing dangerous operations.
    """
    del env_type
    reason = _find_dangerous_pattern(command)
    if reason:
        return {
            "safe": False,
            "reason": f"检测到危险命令模式: {reason}",
            "requires_approval": True,
            "command": command,
        }
    return {
        "safe": True,
        "reason": "",
        "requires_approval": False,
        "command": command,
    }


def _find_dangerous_pattern(command: str) -> str | None:
    """Return the first dangerous pattern represented by the shell syntax."""
    if not command or not command.strip():
        return None

    try:
        tokens = _shell_tokens(command)
    except ValueError:
        # An unterminated quote is invalid shell syntax. Keep a narrow,
        # command-head fallback so an obvious destructive command still fails
        # closed without restoring substring matching for arbitrary text.
        if re.search(r"^\s*(?:sudo\s+)?rm\s+-[rf]{2}\b", command, re.IGNORECASE):
            return "rm -rf"
        return _dangerous_redirect_target(command)

    for segment in _command_segments(tokens):
        pattern = _find_segment_danger(segment)
        if pattern:
            return pattern

    return _dangerous_redirect_target(command)


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _COMMAND_SEPARATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _find_segment_danger(tokens: list[str]) -> str | None:
    command_index = _command_index(tokens)
    if command_index >= len(tokens):
        return None

    executable = _command_basename(tokens[command_index])
    args = tokens[command_index + 1 :]

    if executable == "rm" and _rm_has_recursive_force(args):
        return "rm -rf"
    if executable == "rmdir":
        return "rmdir"
    if executable == "del" and _windows_delete_has_target(args):
        return "del /"
    if executable == "format":
        return "format"
    if executable == "mkfs" or executable.startswith("mkfs."):
        return "mkfs"
    if executable == "dd" and _dd_writes_a_target(args):
        return "dd if="
    if executable == "chmod" and "777" in args:
        return "chmod 777"

    # Recurse only through shell interpreters. A string passed to Python or
    # another general-purpose program is data, not evidence of shell syntax.
    nested = _shell_payload(executable, args)
    if nested:
        return _find_dangerous_pattern(nested)
    return None


def _command_index(tokens: list[str]) -> int:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        basename = _command_basename(token)
        if _ASSIGNMENT.match(token) and not token.startswith("-"):
            index += 1
            continue
        if basename not in _COMMAND_WRAPPERS:
            return index
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            if tokens[index] == "--":
                index += 1
                break
            option = tokens[index]
            index += 1
            if (
                basename in {"doas", "env", "exec", "sudo"}
                and option in _WRAPPER_OPTIONS_WITH_VALUE
                and index < len(tokens)
            ):
                index += 1
        if basename == "env":
            while index < len(tokens) and _ASSIGNMENT.match(tokens[index]):
                index += 1
    return index


def _command_basename(token: str) -> str:
    basename = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".com", ".cmd", ".bat"):
        if basename.endswith(suffix):
            return basename[: -len(suffix)]
    return basename


def _rm_has_recursive_force(args: list[str]) -> bool:
    recursive = False
    force = False
    for arg in args:
        if arg == "--":
            break
        if arg == "--recursive":
            recursive = True
        elif arg == "--force":
            force = True
        elif arg.startswith("-") and not arg.startswith("--"):
            recursive = recursive or "r" in arg[1:]
            force = force or "f" in arg[1:]
        elif not arg.startswith("-"):
            continue
    return recursive and force


def _windows_delete_has_target(args: list[str]) -> bool:
    return any(
        arg.startswith("/") and arg.lower() != "/?"
        for arg in args
    )


def _dd_writes_a_target(args: list[str]) -> bool:
    has_input = any(arg.lower().startswith("if=") for arg in args)
    has_output = any(arg.lower().startswith("of=") for arg in args)
    return has_input or has_output


def _shell_payload(executable: str, args: list[str]) -> str | None:
    if executable not in _SHELL_INTERPRETERS:
        return None
    for index, arg in enumerate(args[:-1]):
        if arg.lower() in {"-c", "/c", "-command"}:
            payload = args[index + 1 :]
            # cmd.exe and PowerShell consume the remainder of the argument
            # vector for /c and -Command; POSIX shells consume one string.
            if executable in {"cmd", "powershell", "pwsh"}:
                return " ".join(payload)
            return payload[0]
    return None


def _dangerous_redirect_target(command: str) -> str | None:
    """Detect unquoted writes to real device nodes, excluding pseudo-devices."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char != ">" or command[index + 1 : index + 2] == ">":
            continue
        target_start = index + 1
        while target_start < len(command) and command[target_start].isspace():
            target_start += 1
        target = command[target_start:].split(None, 1)[0] if target_start < len(command) else ""
        target = target.rstrip(";&|")
        if target.lower().startswith("/dev/") and target.lower() not in _SAFE_DEVICE_TARGETS:
            return "> /dev/"
    return None


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
            "approval_required": False,
            "approval_status": "approved",
            "reason": "",
            "approval_reason": "",
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
        "approval_required": True,
        "approval_status": decision.status.value,
        "reason": dangerous_check["reason"],
        "approval_reason": decision.reason,
        "command": command,
    }
