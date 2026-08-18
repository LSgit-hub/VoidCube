"""Deterministic assembly for OpenAI-compatible streaming responses."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


@dataclass(frozen=True)
class StreamChunkUpdate:
    """New callback-relevant values produced by one streamed chunk."""

    reasoning: str | None = None
    content: str | None = None
    stream_content: bool = False
    started_tools: tuple[str, ...] = ()

    @property
    def starts_delivery(self) -> bool:
        return bool(
            self.reasoning
            or (self.content and self.stream_content)
            or self.started_tools
        )


class StreamingResponseAssembler:
    """Accumulate protocol chunks without owning transport or UI callbacks."""

    def __init__(self) -> None:
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._notified_tool_slots: set[int] = set()
        self._last_id_by_raw_index: dict[int, str] = {}
        self._active_slot_by_raw_index: dict[int, int] = {}
        self._finish_reason: str | None = None
        self._model: str | None = None
        self._usage: Any = None

    def add(self, chunk: Any) -> StreamChunkUpdate:
        """Consume one SDK chunk and return only newly observed callback data."""
        model = _value(chunk, "model")
        if model:
            self._model = model

        usage = _value(chunk, "usage")
        if usage:
            self._usage = usage

        choices = _value(chunk, "choices") or ()
        if not choices:
            return StreamChunkUpdate()

        choice = choices[0]
        delta = _value(choice, "delta")
        reasoning = _value(delta, "reasoning_content") or _value(
            delta, "reasoning"
        )
        if reasoning:
            self._reasoning_parts.append(reasoning)

        content = _value(delta, "content")
        stream_content = bool(content and not self._tool_calls)
        if content:
            self._content_parts.append(content)

        started_tools = self._add_tool_call_deltas(
            _value(delta, "tool_calls") or ()
        )

        finish_reason = _value(choice, "finish_reason")
        if finish_reason:
            self._finish_reason = finish_reason

        return StreamChunkUpdate(
            reasoning=reasoning or None,
            content=content or None,
            stream_content=stream_content,
            started_tools=started_tools,
        )

    def build_response(self, *, response_id: str | None = None) -> Any:
        """Build the non-streaming-compatible response consumed by the agent loop."""
        tool_calls, has_truncated_arguments = self._build_tool_calls()
        finish_reason = self._finish_reason or "stop"
        if has_truncated_arguments:
            finish_reason = "length"

        message = SimpleNamespace(
            role="assistant",
            content="".join(self._content_parts) or None,
            tool_calls=tool_calls,
            reasoning_content="".join(self._reasoning_parts) or None,
        )
        return SimpleNamespace(
            id=response_id or f"stream-{uuid.uuid4()}",
            model=self._model,
            choices=[
                SimpleNamespace(
                    index=0,
                    message=message,
                    finish_reason=finish_reason,
                )
            ],
            usage=self._usage,
        )

    @staticmethod
    def partial_delivery_response(model: str) -> Any:
        """Return a terminal stub after visible deltas made retries unsafe."""
        message = SimpleNamespace(
            role="assistant",
            content=None,
            tool_calls=None,
            reasoning_content=None,
        )
        return SimpleNamespace(
            id="partial-stream-stub",
            model=model,
            choices=[
                SimpleNamespace(
                    index=0,
                    message=message,
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    def _add_tool_call_deltas(self, deltas: Any) -> tuple[str, ...]:
        started_tools: list[str] = []
        for tool_delta in deltas:
            raw_index = _value(tool_delta, "index")
            raw_index = raw_index if raw_index is not None else 0
            delta_id = _value(tool_delta, "id") or ""

            if raw_index not in self._active_slot_by_raw_index:
                self._active_slot_by_raw_index[raw_index] = raw_index
            if (
                delta_id
                and raw_index in self._last_id_by_raw_index
                and delta_id != self._last_id_by_raw_index[raw_index]
            ):
                self._active_slot_by_raw_index[raw_index] = (
                    max(self._tool_calls, default=-1) + 1
                )
            if delta_id:
                self._last_id_by_raw_index[raw_index] = delta_id

            slot = self._active_slot_by_raw_index[raw_index]
            entry = self._tool_calls.setdefault(
                slot,
                {
                    "id": delta_id,
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                    "extra_content": None,
                },
            )
            if delta_id:
                entry["id"] = delta_id

            function = _value(tool_delta, "function")
            name_part = _value(function, "name")
            arguments_part = _value(function, "arguments")
            if name_part:
                entry["function"]["name"] += name_part
            if arguments_part:
                entry["function"]["arguments"] += arguments_part

            extra = _value(tool_delta, "extra_content")
            if extra is None:
                model_extra = _value(tool_delta, "model_extra") or {}
                extra = _value(model_extra, "extra_content")
            if extra is not None:
                model_dump = getattr(extra, "model_dump", None)
                entry["extra_content"] = model_dump() if callable(model_dump) else extra

            tool_name = entry["function"]["name"]
            if tool_name and slot not in self._notified_tool_slots:
                self._notified_tool_slots.add(slot)
                started_tools.append(tool_name)

        return tuple(started_tools)

    def _build_tool_calls(self) -> tuple[list[Any] | None, bool]:
        if not self._tool_calls:
            return None, False

        tool_calls: list[Any] = []
        has_truncated_arguments = False
        for slot in sorted(self._tool_calls):
            entry = self._tool_calls[slot]
            arguments = entry["function"]["arguments"]
            if arguments and arguments.strip():
                try:
                    json.loads(arguments)
                except json.JSONDecodeError:
                    has_truncated_arguments = True
            tool_calls.append(
                SimpleNamespace(
                    id=entry["id"],
                    type=entry["type"],
                    extra_content=entry["extra_content"],
                    function=SimpleNamespace(
                        name=entry["function"]["name"],
                        arguments=arguments,
                    ),
                )
            )
        return tool_calls, has_truncated_arguments
