"""Handle terminal paste gestures and large-text input compaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PasteRuntimePorts:
    """Clipboard, buffer and paste-storage operations supplied by the host."""

    should_attach_clipboard_image: Callable[[str], bool]
    attach_clipboard_image: Callable[[], bool]
    paste_directory: Path
    timestamp: Callable[[], str]
    invalidate: Callable[[Any], None]


class TuiPasteRuntime:
    """Own paste policy and transient compaction state."""

    def __init__(self, ports: PasteRuntimePorts) -> None:
        self.ports = ports
        self._paste_counter = 0
        self._previous_text_length = 0
        self._previous_newline_count = 0
        self._paste_just_collapsed = False

    def handle_bracketed_paste(self, event: Any) -> None:
        pasted_text = self._normalize(event.data or "")
        if (
            self.ports.should_attach_clipboard_image(pasted_text)
            and self.ports.attach_clipboard_image()
        ):
            self.ports.invalidate(event)

        if not pasted_text:
            return
        buffer = event.current_buffer
        line_count = pasted_text.count("\n")
        if line_count >= 5 and not self._is_slash_command(pasted_text):
            placeholder = self._compact(pasted_text)
            prefix = ""
            if (
                buffer.cursor_position > 0
                and buffer.text[buffer.cursor_position - 1] != "\n"
            ):
                prefix = "\n"
            self._paste_just_collapsed = True
            buffer.insert_text(prefix + placeholder)
            return
        buffer.insert_text(pasted_text)

    def handle_text_changed(self, buffer: Any) -> None:
        text = buffer.text
        chars_added = len(text) - self._previous_text_length
        self._previous_text_length = len(text)
        if self._paste_just_collapsed:
            self._paste_just_collapsed = False
            self._previous_newline_count = text.count("\n")
            return

        line_count = text.count("\n")
        newlines_added = line_count - self._previous_newline_count
        self._previous_newline_count = line_count
        is_paste = chars_added > 1 or newlines_added >= 4
        if line_count >= 5 and is_paste and not self._is_slash_command(text):
            self._paste_just_collapsed = True
            buffer.text = self._compact(text)
            buffer.cursor_position = len(buffer.text)

    def _compact(self, text: str) -> str:
        self._paste_counter += 1
        try:
            path = self._write_paste_file(text)
        except (OSError, RuntimeError):
            # Keep the pasted text intact instead of surfacing a filesystem
            # error through the TUI key/change event handlers.
            return text
        line_count = text.count("\n")
        return f"[Pasted text #{self._paste_counter}: {line_count + 1} lines \u2192 {path}]"

    @staticmethod
    def _is_slash_command(text: str) -> bool:
        return text.strip().startswith("/")

    def _write_paste_file(self, text: str) -> Path:
        directory = self.ports.paste_directory
        directory.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            path = directory / (
                f"paste_{self._paste_counter}_{self.ports.timestamp()}_{uuid4().hex[:12]}.txt"
            )
            try:
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(text)
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
                return path
            except FileExistsError:
                continue
        raise RuntimeError("could not allocate a unique paste file")

    @staticmethod
    def _normalize(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")
