"""Thread-safe structured records for one CLI chat session."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import RLock
from collections.abc import Sequence
from typing import Any, Mapping
from uuid import uuid4

from VoidCube_app.contracts.events import (
    MessageDelta,
    SessionEvent,
    SessionEventKind,
    TurnEvent,
    TurnEventKind,
)
from VoidCube_app.tool_events import ToolEvent, ToolEventKind


@dataclass(frozen=True, slots=True)
class ChatBlock:
    """A renderer-neutral piece of one CLI conversation."""

    block_id: str
    kind: str
    session_id: str
    turn_id: str = ""
    call_id: str = ""
    name: str = ""
    text: str = ""
    status: str = "completed"
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result: str = ""
    duration: float = 0.0
    is_error: bool = False
    visible: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ChatBlockStore:
    """Maintain bounded, queryable UI blocks without owning application state."""

    def __init__(self, *, max_blocks: int = 2000, id_factory=uuid4) -> None:
        if max_blocks < 1:
            raise ValueError("max_blocks must be positive")
        self._max_blocks = int(max_blocks)
        self._id_factory = id_factory
        self._lock = RLock()
        self._session_id = ""
        self._active_turn_id = ""
        self._blocks: list[ChatBlock] = []

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session_id

    def bind_session(self, session_id: str, *, clear: bool = True) -> None:
        session_id = str(session_id or "").strip()
        with self._lock:
            if clear or session_id != self._session_id:
                self._blocks.clear()
                self._active_turn_id = ""
            self._session_id = session_id

    def reset(self) -> None:
        with self._lock:
            self._blocks.clear()
            self._active_turn_id = ""

    def blocks(self, *, turn_id: str | None = None) -> tuple[ChatBlock, ...]:
        with self._lock:
            if turn_id is None:
                return tuple(self._blocks)
            return tuple(block for block in self._blocks if block.turn_id == turn_id)

    def record_user_message(self, text: Any, *, turn_id: str = "") -> ChatBlock:
        return self._append(kind="user", turn_id=turn_id, text=_text(text))

    def hydrate_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        """Rebuild display records from the canonical resumed conversation."""
        with self._lock:
            self._blocks.clear()
            self._active_turn_id = ""
            turn_number = 0
            for message in history:
                role = str(message.get("role") or "").strip().lower()
                content = _text(message.get("content"))
                if role == "user":
                    turn_number += 1
                    turn_id = f"history-{turn_number}"
                    self._append_locked(ChatBlock(
                        block_id=self._new_id(), kind="user",
                        session_id=self._session_id, turn_id=turn_id, text=content,
                    ))
                elif role == "assistant":
                    turn_id = f"history-{max(1, turn_number)}"
                    self._append_locked(ChatBlock(
                        block_id=self._new_id(), kind="assistant",
                        session_id=self._session_id, turn_id=turn_id, text=content,
                    ))
                elif role == "tool":
                    turn_id = f"history-{max(1, turn_number)}"
                    self._append_locked(ChatBlock(
                        block_id=self._new_id(), kind="tool_result",
                        session_id=self._session_id, turn_id=turn_id,
                        call_id=str(message.get("tool_call_id") or ""),
                        name=str(message.get("name") or ""), result=content,
                    ))

    def consume(self, event: object) -> None:
        if isinstance(event, SessionEvent):
            if event.kind in {SessionEventKind.STARTED, SessionEventKind.RESUMED}:
                self.bind_session(event.session_id)
            return
        if isinstance(event, TurnEvent):
            if not self._accept_session(event.session_id):
                return
            self._consume_turn(event)
            return
        if isinstance(event, MessageDelta):
            if not self._accept_session(event.session_id):
                return
            self._consume_delta(event)
            return
        if isinstance(event, ToolEvent):
            self._consume_tool(event)

    def _consume_turn(self, event: TurnEvent) -> None:
        with self._lock:
            if event.kind is TurnEventKind.STARTED:
                self._active_turn_id = event.turn_id
                return
            turn_id = event.turn_id or self._active_turn_id
            terminal_status = {
                TurnEventKind.COMPLETED: "completed",
                TurnEventKind.FAILED: "failed",
                TurnEventKind.INTERRUPTED: "interrupted",
            }.get(event.kind)
            if terminal_status is None:
                return
            self._update_turn_blocks(turn_id, terminal_status)
            if event.error:
                self._append_locked(
                    ChatBlock(
                        block_id=self._new_id(),
                        kind="error",
                        session_id=self._session_id,
                        turn_id=turn_id,
                        text=event.error,
                        status="completed",
                    )
                )
            if turn_id == self._active_turn_id:
                self._active_turn_id = ""

    def _consume_delta(self, event: MessageDelta) -> None:
        with self._lock:
            turn_id = event.turn_id or self._active_turn_id
            index = self._find_index(kind="assistant", turn_id=turn_id)
            if index is None:
                self._append_locked(
                    ChatBlock(
                        block_id=self._new_id(),
                        kind="assistant",
                        session_id=event.session_id or self._session_id,
                        turn_id=turn_id,
                        text=event.text,
                        status="streaming",
                    )
                )
                return
            block = self._blocks[index]
            self._blocks[index] = replace(
                block,
                text=block.text + event.text,
                status="streaming",
                updated_at=_now(),
            )

    def _consume_tool(self, event: ToolEvent) -> None:
        with self._lock:
            turn_id = self._active_turn_id
            if event.kind is ToolEventKind.REASONING:
                if event.text:
                    self._append_locked(
                        ChatBlock(
                            block_id=self._new_id(),
                            kind="reasoning",
                            session_id=self._session_id,
                            turn_id=turn_id,
                            text=event.text,
                            status="completed",
                            visible=False,
                        )
                    )
                return
            if event.kind is ToolEventKind.SUBAGENT_PROGRESS:
                if event.text:
                    self._append_locked(
                        ChatBlock(
                            block_id=self._new_id(),
                            kind="status",
                            session_id=self._session_id,
                            turn_id=turn_id,
                            text=event.text,
                            status="completed",
                            metadata={"source": "subagent"},
                        )
                    )
                return
            if event.kind is ToolEventKind.STARTED:
                existing = self._find_index(kind="tool_call", call_id=event.call_id)
                if existing is not None:
                    block = self._blocks[existing]
                    self._blocks[existing] = replace(
                        block,
                        name=event.name or block.name,
                        arguments=dict(event.arguments) or block.arguments,
                        text=event.preview or block.text,
                        status="running",
                        updated_at=_now(),
                    )
                    return
                self._append_locked(
                    ChatBlock(
                        block_id=self._new_id(),
                        kind="tool_call",
                        session_id=self._session_id,
                        turn_id=turn_id,
                        call_id=event.call_id,
                        name=event.name,
                        arguments=dict(event.arguments),
                        text=event.preview,
                        status="running",
                    )
                )
                return
            if event.kind is ToolEventKind.COMPLETED:
                index = self._find_index(kind="tool_call", call_id=event.call_id)
                status = "failed" if event.is_error else "completed"
                if index is None:
                    result_index = self._find_index(kind="tool_result", call_id=event.call_id)
                    if result_index is not None:
                        return
                    self._append_locked(
                        ChatBlock(
                            block_id=self._new_id(),
                            kind="tool_result",
                            session_id=self._session_id,
                            turn_id=turn_id,
                            call_id=event.call_id,
                            name=event.name,
                            result=event.result,
                            duration=event.duration,
                            is_error=event.is_error,
                            status="orphaned",
                            metadata={"artifacts": _artifact_records(event)},
                        )
                    )
                    return
                block = self._blocks[index]
                self._blocks[index] = replace(
                    block,
                    kind="tool_result",
                    result=event.result,
                    duration=event.duration,
                    is_error=event.is_error,
                    status=status,
                    metadata={**block.metadata, "artifacts": _artifact_records(event)},
                    updated_at=_now(),
                )

    def _update_turn_blocks(self, turn_id: str, status: str) -> None:
        for index, block in enumerate(self._blocks):
            if block.turn_id == turn_id and block.status in {"running", "streaming"}:
                self._blocks[index] = replace(block, status=status, updated_at=_now())

    def _append(self, *, kind: str, turn_id: str = "", text: str = "") -> ChatBlock:
        with self._lock:
            block = ChatBlock(
                block_id=self._new_id(),
                kind=kind,
                session_id=self._session_id,
                turn_id=turn_id,
                text=text,
            )
            self._append_locked(block)
            return block

    def _append_locked(self, block: ChatBlock) -> None:
        self._blocks.append(block)
        overflow = len(self._blocks) - self._max_blocks
        if overflow > 0:
            del self._blocks[:overflow]

    def _find_index(self, *, kind: str, turn_id: str = "", call_id: str = "") -> int | None:
        for index in range(len(self._blocks) - 1, -1, -1):
            block = self._blocks[index]
            if block.kind == kind and (not turn_id or block.turn_id == turn_id) and (not call_id or block.call_id == call_id):
                return index
        return None

    def _new_id(self) -> str:
        return str(self._id_factory())

    def _accept_session(self, session_id: str) -> bool:
        session_id = str(session_id or "").strip()
        with self._lock:
            return not self._session_id or not session_id or session_id == self._session_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else str(value or "")


def _artifact_records(event: ToolEvent) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "kind": artifact.kind,
            "uri": artifact.uri,
            "mime_type": artifact.mime_type,
            "title": artifact.title,
            "metadata": dict(artifact.metadata),
        }
        for artifact in event.artifacts
    )


__all__ = ["ChatBlock", "ChatBlockStore"]
