"""Model-switch command orchestration with explicit CLI runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class ModelCommandPorts:
    parse_flags: Callable[[str], tuple[str, str, bool]]
    user_providers: Callable[[], Mapping[str, Any] | None]
    model: Callable[[], str]
    provider: Callable[[], str]
    base_url: Callable[[], str]
    api_key: Callable[[], str]
    provider_label: Callable[[str], str]
    list_configured_providers: Callable[..., list[dict[str, Any]]]
    switch_model: Callable[..., Any]
    open_picker: Callable[[list[dict[str, Any]], str, str, Mapping[str, Any] | None], None]
    apply_result: Callable[[Any, bool], None]
    emit: Callable[[str], None]
    confirm_capabilities: Callable[[str, str], Sequence[str] | None] | None = None


def handle_model_command(
    request: ParsedCliCommand,
    *,
    ports: ModelCommandPorts,
) -> None:
    """Route /model while leaving resolution, persistence, and UI ownership separate."""
    model_input, explicit_provider, persist_global = ports.parse_flags(
        request.arguments
    )
    user_providers = ports.user_providers()
    current_provider = ports.provider()
    current_model = ports.model()

    if not model_input and not explicit_provider:
        try:
            providers = ports.list_configured_providers(
                current_provider=current_provider,
                user_providers=user_providers,
                max_models=30,
            )
        except Exception:
            providers = []

        if not providers:
            ports.emit("  No configured providers found.")
            ports.emit("")
            ports.emit("  Run /api first to add a provider.")
            return

        ports.open_picker(
            providers,
            current_model or "unknown",
            ports.provider_label(current_provider) if current_provider else "unknown",
            user_providers,
        )
        return

    result = ports.switch_model(
        raw_input=model_input,
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=ports.base_url(),
        current_api_key=ports.api_key(),
        is_global=persist_global,
        explicit_provider=explicit_provider,
        user_providers=user_providers,
    )
    if result.success and ports.confirm_capabilities is not None:
        native_modalities = ports.confirm_capabilities(
            result.target_provider,
            result.new_model,
        )
        if native_modalities is None:
            ports.emit("  No change.")
            return
        result.native_modalities = tuple(native_modalities)
    ports.apply_result(result, persist_global)
