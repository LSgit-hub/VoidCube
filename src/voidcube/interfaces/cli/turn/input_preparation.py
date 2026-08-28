"""Prepare one CLI turn before agent execution through explicit ports."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ....domain.contracts.turn import TurnInput, begin_turn


_DIM = "\033[2m"
_RST = "\033[0m"


@dataclass(frozen=True, slots=True)
class CliTurnInputPreparationPorts:
    """Input, context and terminal operations supplied by the CLI host."""

    message: Any
    images: Sequence[Any] | None
    conversation_history: Sequence[Mapping[str, Any]]
    preprocess_images: Callable[[str, Sequence[Any]], str]
    model: str
    base_url: str
    api_key: str
    cwd: Callable[[], str]
    should_emit: Callable[[], bool]
    emit: Callable[[str], None]
    context_length: Optional[Callable[[str, str, str], int]] = None
    expand_context: Optional[Callable[..., Any]] = None
    sanitize: Optional[Callable[[str], str]] = None
    begin_turn: Optional[Callable[[Any], TurnInput]] = None
    native_input_modalities: Optional[Callable[[str, str], Sequence[str]]] = None
    preprocess_attachments: Optional[Callable[[str, Sequence[Any]], str]] = None
    build_attachments: Optional[
        Callable[[Sequence[Any]], Sequence[Mapping[str, Any]]]
    ] = None


@dataclass(frozen=True, slots=True)
class PreparedCliTurnInput:
    """Prepared message and turn contract, or a blocked context response."""

    message: Any
    turn_input: TurnInput | None
    attachments: tuple[dict[str, Any], ...] = ()
    blocked_response: str | None = None


class CliTurnInputPreparationRuntime:
    """Own input preparation while leaving CLI conversation state host-owned."""

    def __init__(self, ports: CliTurnInputPreparationPorts) -> None:
        self.ports = ports

    def prepare(self) -> PreparedCliTurnInput:
        ports = self.ports
        message = ports.message
        attachments: tuple[dict[str, Any], ...] = ()
        if ports.images:
            native_modalities = self._native_input_modalities()
            if native_modalities:
                builder = (
                    ports.build_attachments
                    or self._default_build_attachments
                )
                candidate_attachments = tuple(
                    dict(attachment)
                    for attachment in builder(ports.images)
                    if isinstance(attachment, Mapping)
                )
                native_attachments = []
                fallback_paths = []
                for attachment, path in zip(candidate_attachments, ports.images):
                    from ....infrastructure.llm.multimodal import (
                        attachment_modality,
                        native_attachment_supported,
                    )

                    if (
                        attachment_modality(attachment) in native_modalities
                        and native_attachment_supported(attachment)
                    ):
                        native_attachments.append(attachment)
                    else:
                        fallback_paths.append(path)
                attachments = tuple(native_attachments)
                if fallback_paths:
                    message = self._preprocess_attachments(
                        message if isinstance(message, str) else "",
                        fallback_paths,
                    )
            else:
                message = self._preprocess_attachments(
                    message if isinstance(message, str) else "",
                    ports.images,
                )

        if isinstance(message, str) and "@" in message:
            context_result = self._expand_context(message)
            if context_result is not None and (
                context_result.expanded or context_result.blocked
            ):
                if context_result.references and ports.should_emit():
                    ports.emit(
                        f"  {_DIM}[@ context: {len(context_result.references)} ref(s), "
                        f"{context_result.injected_tokens} tokens]{_RST}"
                    )
                for warning in context_result.warnings:
                    if ports.should_emit():
                        ports.emit(f"  {_DIM}⚠ {warning}{_RST}")
                if context_result.blocked:
                    return PreparedCliTurnInput(
                        message=message,
                        turn_input=None,
                        blocked_response=(
                            "\n".join(context_result.warnings)
                            or "Context injection refused."
                        ),
                    )
                message = context_result.message

        if isinstance(message, str):
            sanitize = ports.sanitize or self._default_sanitize
            message = sanitize(message)

        turn_builder = ports.begin_turn
        turn_input = (
            turn_builder(message, attachments=attachments)
            if turn_builder is not None
            else begin_turn(
                ports.conversation_history,
                message,
                attachments=attachments,
            )
        )
        return PreparedCliTurnInput(
            message=message,
            turn_input=turn_input,
            attachments=attachments,
        )

    def _native_input_modalities(self) -> frozenset[str]:
        checker = self.ports.native_input_modalities
        if checker is not None:
            return frozenset(checker(self.ports.model, self.ports.base_url))
        try:
            from ....infrastructure.llm.multimodal import native_input_modalities

            return native_input_modalities("", self.ports.model)
        except Exception:
            return frozenset()

    def _preprocess_attachments(
        self,
        message: str,
        attachments: Sequence[Any],
    ) -> str:
        processor = self.ports.preprocess_attachments
        if processor is not None:
            return processor(message, attachments)
        return self.ports.preprocess_images(message, attachments)

    def _expand_context(self, message: str) -> Any:
        ports = self.ports
        context_length = ports.context_length or self._default_context_length
        expand_context = ports.expand_context or self._default_expand_context
        try:
            return expand_context(
                message,
                cwd=ports.cwd(),
                context_length=context_length(
                    ports.model,
                    ports.base_url or "",
                    ports.api_key or "",
                ),
            )
        except Exception as error:
            logging.debug("@ context reference expansion failed: %s", error)
            return None

    @staticmethod
    def _default_context_length(model: str, base_url: str, api_key: str) -> int:
        from ....infrastructure.providers.model_metadata import get_model_context_length

        return get_model_context_length(
            model,
            base_url=base_url,
            api_key=api_key,
        )

    @staticmethod
    def _default_expand_context(message: str, **kwargs: Any) -> Any:
        from ....runtime.agent.context_references import preprocess_context_references

        return preprocess_context_references(message, **kwargs)

    @staticmethod
    def _default_build_attachments(
        images: Sequence[Any],
    ) -> Sequence[Mapping[str, Any]]:
        from ....infrastructure.llm.multimodal import attachments_from_paths

        return attachments_from_paths(images)

    @staticmethod
    def _default_sanitize(message: str) -> str:
        from ....domain.agent.message_sanitizer import sanitize_surrogates

        return sanitize_surrogates(message)
