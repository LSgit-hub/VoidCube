"""Session transcript persistence for the primary agent runtime."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from VoidCube_core.utils import atomic_json_write


logger = logging.getLogger(__name__)

Message = dict[str, Any]


def apply_user_message_override(
    messages: list[Message],
    index: int | None,
    override: Any,
) -> None:
    """Restore the persisted form of an API-only user message in place."""
    if override is None or index is None or not 0 <= index < len(messages):
        return
    message = messages[index]
    if isinstance(message, dict) and message.get("role") == "user":
        message["content"] = override


def clean_session_content(content: str) -> str:
    """Normalize whitespace around existing think blocks."""
    if not content:
        return content
    content = re.sub(r"\n+(<think>)", r"\n\1", content)
    content = re.sub(r"(</think>)\n+", r"\1\n", content)
    return content.strip()


class SessionPersistence:
    """Own JSON transcript writes and the SQLite incremental write cursor."""

    def __init__(
        self,
        *,
        enabled: bool,
        logs_dir: Path,
        session_db: Any,
        session_start: datetime,
        session_id: Callable[[], str],
        model: Callable[[], str],
        base_url: Callable[[], str],
        platform: Callable[[], str | None],
        system_prompt: Callable[[], str | None],
        tools: Callable[[], Sequence[Mapping[str, Any]] | None],
        user_message_override: Callable[[], tuple[int | None, Any]],
        verbose_logging: bool = False,
    ) -> None:
        self.enabled = enabled
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.session_db = session_db
        self.session_start = session_start
        self._session_id = session_id
        self._model = model
        self._base_url = base_url
        self._platform = platform
        self._system_prompt = system_prompt
        self._tools = tools
        self._user_message_override = user_message_override
        self.verbose_logging = verbose_logging
        self.messages: list[Message] = []
        self._last_flushed_db_idx = 0

    @property
    def session_log_file(self) -> Path:
        return self.logs_dir / f"session_{self._session_id()}.json"

    def reset_flush_cursor(self) -> None:
        """Start SQLite persistence at the first message of a new session."""
        self._last_flushed_db_idx = 0

    def set_flush_cursor(self, persisted_message_count: int) -> None:
        """Align incremental persistence after application history mutation."""
        self._last_flushed_db_idx = max(0, int(persisted_message_count))

    def persist(
        self,
        messages: list[Message],
        conversation_history: Sequence[Message] | None = None,
    ) -> None:
        """Persist the current transcript to JSON and SQLite."""
        if not self.enabled:
            return
        override_index, override = self._user_message_override()
        apply_user_message_override(messages, override_index, override)
        self.messages = messages
        self.save_log(messages)
        self.flush_to_db(messages, conversation_history)

    def flush_to_db(
        self,
        messages: list[Message],
        conversation_history: Sequence[Message] | None = None,
    ) -> None:
        """Append messages not yet written to the SQLite session store."""
        if not self.enabled or not self.session_db:
            return
        try:
            self.session_db.ensure_session(
                self._session_id(),
                source=self._platform() or "cli",
                model=self._model(),
            )
            history_length = len(conversation_history) if conversation_history else 0
            flush_from = max(history_length, self._last_flushed_db_idx)
            for message in messages[flush_from:]:
                role = message.get("role", "unknown")
                tool_calls = None
                if hasattr(message, "tool_calls") and message.tool_calls:
                    tool_calls = [
                        {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        }
                        for tool_call in message.tool_calls
                    ]
                elif isinstance(message.get("tool_calls"), list):
                    tool_calls = message["tool_calls"]
                self.session_db.append_message(
                    session_id=self._session_id(),
                    role=role,
                    content=message.get("content"),
                    tool_name=message.get("tool_name"),
                    tool_calls=tool_calls,
                    tool_call_id=message.get("tool_call_id"),
                    finish_reason=message.get("finish_reason"),
                    reasoning=message.get("reasoning") if role == "assistant" else None,
                    reasoning_details=(
                        message.get("reasoning_details")
                        if role == "assistant"
                        else None
                    ),
                )
            self._last_flushed_db_idx = len(messages)
        except Exception as exc:
            logger.warning("Session DB append_message failed: %s", exc)

    def save_log(
        self,
        messages: list[Message] | None = None,
        *,
        allow_truncate: bool = False,
    ) -> None:
        """Write the latest complete raw transcript to its JSON session log."""
        if not self.enabled:
            return
        if messages is not None:
            self.messages = messages
        active_messages = self.messages if messages is None else messages
        if not active_messages and not allow_truncate:
            return

        try:
            cleaned: list[Message] = []
            for message in active_messages:
                if message.get("role") == "assistant" and message.get("content"):
                    message = dict(message)
                    message["content"] = clean_session_content(message["content"])
                cleaned.append(message)

            session_log_file = self.session_log_file
            if session_log_file.exists():
                try:
                    existing = json.loads(session_log_file.read_text(encoding="utf-8"))
                    existing_count = existing.get(
                        "message_count", len(existing.get("messages", []))
                    )
                    if existing_count > len(cleaned) and not allow_truncate:
                        logger.debug(
                            "Skipping session log overwrite: existing has %d messages, current has %d",
                            existing_count,
                            len(cleaned),
                        )
                        return
                except Exception:
                    pass

            atomic_json_write(
                session_log_file,
                {
                    "session_id": self._session_id(),
                    "model": self._model(),
                    "base_url": self._base_url(),
                    "platform": self._platform(),
                    "session_start": self.session_start.isoformat(),
                    "last_updated": datetime.now().isoformat(),
                    "system_prompt": self._system_prompt() or "",
                    "tools": list(self._tools() or []),
                    "message_count": len(cleaned),
                    "messages": cleaned,
                },
                indent=2,
                default=str,
            )
        except Exception as exc:
            if self.verbose_logging:
                logger.warning("Failed to save session log: %s", exc)
