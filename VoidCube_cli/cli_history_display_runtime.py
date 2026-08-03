"""Render a compact recap of a resumed conversation."""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliHistoryDisplayPorts:
    """Conversation data and terminal operations supplied by the CLI host."""

    conversation_history: Callable[[], Sequence[Mapping[str, Any]]]
    resume_display: Callable[[], str]
    terminal_width: Callable[[], int]
    translate: Callable[..., str]
    emit: Callable[[str], None]
    emit_blank_line: Callable[[], None]


class CliHistoryDisplayRuntime:
    """Own resumed-history filtering, compaction and terminal presentation."""

    _MAX_DISPLAY_EXCHANGES = 10
    _MAX_USER_LENGTH = 300
    _MAX_ASSISTANT_LENGTH = 200
    _MAX_ASSISTANT_LINES = 3

    def __init__(self, ports: CliHistoryDisplayPorts) -> None:
        self.ports = ports

    def run(self) -> None:
        ports = self.ports
        history = ports.conversation_history()
        if not history or ports.resume_display() == "minimal":
            return

        entries: list[tuple[str, str, Any]] = []
        last_assistant_index: int | None = None
        last_assistant_full: str | None = None
        first_timestamp = None
        last_timestamp = None

        for message in history:
            role = message.get("role", "")
            content = message.get("content")
            tool_calls = message.get("tool_calls") or []
            timestamp = message.get("timestamp")

            if not first_timestamp:
                first_timestamp = timestamp
            if role in {"system", "tool"}:
                continue

            if role == "user":
                text = self._user_text(content)
                text = _strip_ansi_codes(text)
                if len(text) > self._MAX_USER_LENGTH:
                    text = text[: self._MAX_USER_LENGTH] + "..."
                entries.append(("user", text, timestamp))
                continue

            if role != "assistant":
                continue

            text = "" if content is None else str(content)
            text = _strip_reasoning(_strip_ansi_codes(text))
            parts: list[str] = []
            full_parts: list[str] = []
            if text:
                full_parts.append(text)
                lines = text.splitlines()
                if len(lines) > self._MAX_ASSISTANT_LINES:
                    text = "\n".join(lines[: self._MAX_ASSISTANT_LINES]) + " ..."
                if len(text) > self._MAX_ASSISTANT_LENGTH:
                    text = text[: self._MAX_ASSISTANT_LENGTH] + "..."
                parts.append(text)
            if tool_calls:
                tool_summary = self._tool_summary(tool_calls)
                parts.append(tool_summary)
                full_parts.append(tool_summary)
            if not parts:
                continue

            entries.append(("assistant", " ".join(parts), timestamp))
            last_assistant_index = len(entries) - 1
            last_assistant_full = " ".join(full_parts)
            last_timestamp = timestamp

        if not entries:
            return

        skipped = 0
        if len(entries) > self._MAX_DISPLAY_EXCHANGES * 2:
            skipped = len(entries) - self._MAX_DISPLAY_EXCHANGES * 2
            last_timestamp = entries[0][2]
            entries = entries[skipped:]

        if last_assistant_index is not None and last_assistant_full:
            adjusted_index = last_assistant_index - skipped
            if 0 <= adjusted_index < len(entries):
                original_timestamp = entries[adjusted_index][2]
                entries[adjusted_index] = (
                    "assistant_last",
                    last_assistant_full,
                    original_timestamp,
                )

        self._render(entries, skipped, first_timestamp, last_timestamp)

    @staticmethod
    def _user_text(content: object) -> str:
        if not isinstance(content, list):
            return "" if content is None else str(content)
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                parts.append("[image]")
        return " ".join(parts)

    @staticmethod
    def _tool_summary(tool_calls: object) -> str:
        calls = tool_calls if isinstance(tool_calls, Sequence) else []
        names: list[str] = []
        for tool_call in calls:
            function = tool_call.get("function", {}) if isinstance(tool_call, Mapping) else {}
            name = function.get("name", "unknown") if isinstance(function, Mapping) else "unknown"
            if name not in names:
                names.append(str(name))
        names_text = ", ".join(names[:4])
        if len(names) > 4:
            names_text += ", ..."
        count = len(calls)
        noun = "call" if count == 1 else "calls"
        return f"[{count} tool {noun}: {names_text}]"

    def _render(
        self,
        entries: list[tuple[str, str, Any]],
        skipped: int,
        first_timestamp: Any,
        last_timestamp: Any,
    ) -> None:
        ports = self.ports
        width = ports.terminal_width()
        label = ports.translate(
            "prompts.previous_conversation",
            default="Previous Conversation",
        )
        ports.emit_blank_line()
        ports.emit(
            f"\033[38;2;218;165;32m── {label}"
            f"{'─' * ((width - 2) - 2 - len(label))}\033[0m"
        )

        if skipped and first_timestamp:
            first_time = datetime.datetime.fromtimestamp(first_timestamp).strftime("%m-%d %H:%M")
            last_time = datetime.datetime.fromtimestamp(last_timestamp).strftime("%m-%d %H:%M")
            ports.emit(
                f"     ... {skipped} {ports.translate('prompts.earlier_messages', default='earlier messages')} "
                f"({first_time} - {last_time}) ..."
            )
        elif skipped:
            ports.emit(
                f"     ... {skipped} {ports.translate('prompts.earlier_messages', default='earlier messages')} ..."
            )
            ports.emit_blank_line()

        for index, (role, text, timestamp) in enumerate(entries):
            time_text = (
                datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M")
                if timestamp
                else ""
            )
            suffix = f" {time_text}" if time_text else ""
            if role == "user":
                label = ports.translate("prompts.you", default="You")
                ports.emit(f"  ● {label}{suffix}: {text}")
            else:
                label = ports.translate("prompts.voidcube", default="Voidcube")
                ports.emit(f"  ◆ {label}{suffix}: {text}")
            if index < len(entries) - 1:
                ports.emit_blank_line()

        ports.emit_blank_line()
        ports.emit(f"\033[38;2;218;165;32m{'─' * (width - 2)}\033[0m")


def _strip_ansi_codes(text: str) -> str:
    ansi_escape = re.compile(
        r"\x1b"
        r"(?:"
        r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
        r"|\][\s\S]*?(?:\x07|\x1b\\)"
        r"|[PX^_][\s\S]*?(?:\x1b\\)"
        r"|[\x20-\x2f]+[\x30-\x7e]"
        r"|[\x30-\x7e]"
        r")"
        r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
        r"|\x9d[\s\S]*?(?:\x07|\x9c)"
        r"|[\x80-\x9f]",
        re.DOTALL,
    )
    return ansi_escape.sub("", text) if text else text


def _strip_reasoning(text: str) -> str:
    cleaned = re.sub(
        r"<REASONING_SCRATCHPAD>.*?</REASONING_SCRATCHPAD>\s*",
        "",
        text,
        flags=re.DOTALL,
    )
    return re.sub(r"<REASONING_SCRATCHPAD>.*$", "", cleaned, flags=re.DOTALL).strip()
