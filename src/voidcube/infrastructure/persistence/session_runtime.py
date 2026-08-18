"""Session transcript persistence for the primary agent runtime."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ...domain.agent.effect_outcomes import EffectOutcome, failed_effect
from .session_db import (
    SessionDB,
    SessionSequenceConflictError,
)
from .file_store import atomic_json_write, interprocess_file_lock


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
    """Persist SQLite-authoritative transcripts and refresh their JSON mirror."""

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

    def set_session_id(self, session_id: str) -> None:
        """切换持久化目标，使会话生命周期切换与 Agent 保持一致。"""
        self._session_id = lambda: session_id

    @property
    def session_log_file(self) -> Path:
        return self.logs_dir / f"session_{self._session_id()}.json"

    @property
    def session_log_lock_file(self) -> Path:
        return self.logs_dir / f".session_{self._session_id()}.json.lock"

    def persist(
        self,
        messages: list[Message],
        conversation_history: Sequence[Message] | None = None,
    ) -> EffectOutcome:
        """Persist the current transcript to JSON and SQLite."""
        if not self.enabled:
            return EffectOutcome(
                status="skipped",
                details={"reason": "disabled"},
            )
        try:
            override_index, override = self._user_message_override()
            apply_user_message_override(messages, override_index, override)
            self.messages = messages
        except Exception as exc:
            return EffectOutcome(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                details={"stage": "prepare"},
            )

        database_outcome = self.flush_to_db(messages, conversation_history)
        if database_outcome.status != "succeeded":
            return EffectOutcome(
                status="failed",
                error=database_outcome.error or "Session database flush failed",
                details={
                    "database": database_outcome.as_dict(),
                    "json_mirror": {
                        "status": "skipped",
                        "details": {"reason": "database_not_committed"},
                    },
                },
            )

        mirror_outcome = self.refresh_json_mirror()
        return EffectOutcome(
            status=(
                "succeeded"
                if mirror_outcome.status == "succeeded"
                else "degraded"
            ),
            error=mirror_outcome.error,
            details={
                "database": database_outcome.as_dict(),
                "json_mirror": mirror_outcome.as_dict(),
            },
        )

    @staticmethod
    def _messages_for_storage(messages: Sequence[Message]) -> list[Message]:
        persisted_messages = []
        for message in messages:
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
            persisted_messages.append(
                {
                    "role": role,
                    "content": message.get("content"),
                    "tool_name": message.get("tool_name"),
                    "tool_calls": tool_calls,
                    "tool_call_id": message.get("tool_call_id"),
                    "action_refs": message.get("action_refs"),
                    "finish_reason": message.get("finish_reason"),
                    "reasoning": (
                        message.get("reasoning") if role == "assistant" else None
                    ),
                    "reasoning_details": (
                        message.get("reasoning_details")
                        if role == "assistant"
                        else None
                    ),
                }
            )
        return persisted_messages

    def flush_to_db(
        self,
        messages: list[Message],
        conversation_history: Sequence[Message] | None = None,
    ) -> EffectOutcome:
        """Append messages not yet written to the SQLite session store."""
        if not self.enabled:
            return EffectOutcome(
                status="skipped",
                details={"reason": "disabled"},
            )
        if not self.session_db:
            return EffectOutcome(
                status="failed",
                error="Session database is unavailable",
            )
        try:
            self.session_db.ensure_session(
                self._session_id(),
                source=self._platform() or "cli",
                model=self._model(),
            )
            del conversation_history
            for attempt in range(3):
                snapshot = self.session_db.get_transcript_snapshot(self._session_id())
                flush_from = snapshot["flush_sequence"]
                persisted_messages = self._messages_for_storage(messages)
                local_prefix_hash = SessionDB.transcript_hash(
                    persisted_messages[:flush_from]
                )
                if local_prefix_hash != snapshot["transcript_hash"]:
                    raise SessionSequenceConflictError(
                        f"local transcript diverges from committed prefix for "
                        f"{self._session_id()}"
                    )
                batch = persisted_messages[flush_from:]
                try:
                    self.session_db.append_messages_batch(
                        self._session_id(),
                        batch,
                        expected_flush_sequence=flush_from,
                        expected_revision=snapshot["transcript_revision"],
                        expected_prefix_hash=local_prefix_hash,
                        allocate_sequences=True,
                    )
                    return EffectOutcome(status="succeeded")
                except SessionSequenceConflictError:
                    if attempt == 2:
                        raise
            return EffectOutcome(
                status="failed",
                error="Session database conflict retries were exhausted",
            )
        except Exception as exc:
            logger.warning("Session DB batch append failed: %s", exc)
            return failed_effect(exc)

    def replace_transcript(self, messages: list[Message]) -> None:
        """Commit an explicit compression/history rewrite as one SQLite fact."""
        if not self.enabled or not self.session_db:
            return
        self.session_db.ensure_session(
            self._session_id(),
            source=self._platform() or "cli",
            model=self._model(),
        )
        snapshot = self.session_db.get_transcript_snapshot(self._session_id())
        persisted_messages = self._messages_for_storage(messages)
        self.session_db.replace_messages(
            self._session_id(),
            persisted_messages,
            expected_revision=snapshot["transcript_revision"],
            expected_transcript_hash=snapshot["transcript_hash"],
        )
        self.messages = messages
        self.refresh_json_mirror()

    def refresh_json_mirror(self) -> EffectOutcome:
        """Refresh the compatibility JSON mirror from committed SQLite rows."""
        if not self.enabled or not self.session_db:
            return EffectOutcome(
                status="skipped",
                details={"reason": "disabled_or_database_unavailable"},
            )
        try:
            with interprocess_file_lock(self.session_log_lock_file):
                while True:
                    snapshot = self.session_db.get_transcript_snapshot(
                        self._session_id()
                    )
                    existing_revision = self._existing_mirror_revision()
                    if existing_revision > snapshot["transcript_revision"]:
                        return EffectOutcome(status="succeeded")
                    self._write_log(
                        snapshot["messages"],
                        allow_truncate=True,
                        transcript_revision=snapshot["transcript_revision"],
                    )
                    current = self.session_db.get_session(self._session_id())
                    if current is None:
                        return EffectOutcome(status="succeeded")
                    if int(current["transcript_revision"] or 0) == snapshot[
                        "transcript_revision"
                    ]:
                        return EffectOutcome(status="succeeded")
        except Exception as exc:
            if self.verbose_logging:
                logger.warning("Failed to refresh session JSON mirror: %s", exc)
            return failed_effect(exc)

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
            with interprocess_file_lock(self.session_log_lock_file):
                self._write_log(active_messages, allow_truncate=allow_truncate)
        except Exception as exc:
            if self.verbose_logging:
                logger.warning("Failed to save session log: %s", exc)

    def _existing_mirror_revision(self) -> int:
        try:
            existing = json.loads(self.session_log_file.read_text(encoding="utf-8"))
            return int(existing.get("transcript_revision", -1))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return -1

    def _write_log(
        self,
        messages: Sequence[Message],
        *,
        allow_truncate: bool,
        transcript_revision: int | None = None,
    ) -> None:
        cleaned: list[Message] = []
        for message in messages:
            if message.get("role") == "assistant" and message.get("content"):
                message = dict(message)
                message["content"] = clean_session_content(message["content"])
            cleaned.append(message)

        session_log_file = self.session_log_file
        if session_log_file.exists() and transcript_revision is None:
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

        payload = {
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
        }
        if transcript_revision is not None:
            payload["transcript_revision"] = transcript_revision
        atomic_json_write(session_log_file, payload, indent=2, default=str)
