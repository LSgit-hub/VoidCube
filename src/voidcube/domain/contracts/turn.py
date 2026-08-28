"""UI-independent input and outcome contracts for application turns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


Message = dict[str, Any]
_SOURCE_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


def _tool_call_details(tool_call: Any) -> tuple[str, str]:
    if isinstance(tool_call, Mapping):
        function = tool_call.get("function")
        name = (
            str(function.get("name") or "").strip()
            if isinstance(function, Mapping)
            else str(tool_call.get("name") or "").strip()
        )
        return name, str(tool_call.get("id") or "").strip()
    function = getattr(tool_call, "function", None)
    return (
        str(getattr(function, "name", "") or "").strip(),
        str(getattr(tool_call, "id", "") or "").strip(),
    )


def tool_names_from_messages(messages: Sequence[Message]) -> tuple[str, ...]:
    names: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for tool_call in list(message.get("tool_calls") or []):
            name, _ = _tool_call_details(tool_call)
            if name and name not in names:
                names.append(name)
    return tuple(names)


def source_urls_from_messages(messages: Sequence[Message]) -> tuple[str, ...]:
    """Extract URLs only from recorded web-tool results."""
    tool_names_by_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        for tool_call in list(message.get("tool_calls") or []):
            name, call_id = _tool_call_details(tool_call)
            if name and call_id:
                tool_names_by_id[call_id] = name

    urls: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "").strip()
        tool_name = str(
            message.get("name")
            or message.get("tool_name")
            or tool_names_by_id.get(call_id)
            or ""
        ).strip()
        if tool_name not in {"web_search", "web_extract"}:
            continue
        content = message.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=True)
        for url in _SOURCE_URL_PATTERN.findall(str(content or "")):
            clean = url.rstrip(".,;:)]}")
            if clean and clean not in urls:
                urls.append(clean)
    return tuple(urls)


@dataclass(frozen=True, slots=True)
class TurnInput:
    user_message: Any
    prior_history: tuple[Message, ...]
    conversation_history: tuple[Message, ...]


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    conversation_history: tuple[Message, ...]
    response: str
    failed: bool
    partial: bool
    interrupted: bool
    error: str
    interrupt_message: Any = None
    response_previewed: bool = False
    last_reasoning: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.response) and not self.failed and not self.partial

    def response_or_error(self) -> str:
        if (self.failed or self.partial) and not self.response:
            return f"Error: {self.error or 'Unknown error'}"
        return self.response

    def observation(
        self,
        *,
        response: str | None = None,
        evidence_messages: Sequence[Message] | None = None,
    ) -> dict[str, Any]:
        observation = {
            "failed": self.failed,
            "partial": self.partial,
            "interrupted": self.interrupted,
            "error": self.error,
            "response": self.response if response is None else response,
        }
        messages = self.conversation_history if evidence_messages is None else evidence_messages
        tools_used = tool_names_from_messages(messages)
        source_urls = source_urls_from_messages(messages)
        if tools_used:
            observation["tools_used"] = list(tools_used)
        if source_urls:
            observation["source_urls"] = list(source_urls)
        return observation


def begin_turn(
    conversation_history: Sequence[Message],
    user_message: Any,
    *,
    attachments: Sequence[Mapping[str, Any]] = (),
) -> TurnInput:
    prior = tuple(conversation_history)
    user_turn: Message = {"role": "user", "content": user_message}
    if attachments:
        user_turn["attachments"] = [
            dict(attachment)
            for attachment in attachments
            if isinstance(attachment, Mapping)
        ]
    return TurnInput(
        user_message=user_message,
        prior_history=prior,
        conversation_history=(*prior, user_turn),
    )


def normalize_turn_outcome(
    result: Mapping[str, Any] | None,
    *,
    fallback_history: Sequence[Message],
) -> TurnOutcome:
    if result is None:
        return TurnOutcome(
            conversation_history=tuple(fallback_history),
            response="",
            failed=True,
            partial=False,
            interrupted=False,
            error="No result returned",
        )

    messages = result.get("messages")
    history = tuple(messages) if isinstance(messages, (list, tuple)) else tuple(fallback_history)
    return TurnOutcome(
        conversation_history=history,
        response=str(result.get("final_response") or ""),
        failed=bool(result.get("failed")),
        partial=bool(result.get("partial")),
        interrupted=bool(result.get("interrupted")),
        error=str(result.get("error") or ""),
        interrupt_message=result.get("interrupt_message"),
        response_previewed=bool(result.get("response_previewed")),
        last_reasoning=str(result.get("last_reasoning") or ""),
    )
