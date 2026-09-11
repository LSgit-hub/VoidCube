"""Model-switch command orchestration with explicit CLI runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class ModelCommandPorts:
    parse_flags: Callable[[str], tuple[str, bool]]
    user_providers: Callable[[], Mapping[str, Any] | None]
    model: Callable[[], str]
    provider: Callable[[], str]
    base_url: Callable[[], str]
    api_key: Callable[[], str]
    provider_label: Callable[[str], str]
    list_current_models: Callable[..., list[str]]
    switch_model: Callable[..., Any]
    open_picker: Callable[[list[str], str, str, Mapping[str, Any] | None], None]
    apply_result: Callable[[Any, bool], None]
    emit: Callable[[str], None]
    confirm_capabilities: Callable[[str, str], Sequence[str] | None] | None = None


def handle_model_command(
    request: ParsedCliCommand,
    *,
    ports: ModelCommandPorts,
) -> None:
    """Route /model within the active provider.

    ``/model`` only selects a different model that the current provider already
    exposes. Provider changes go through the /api wizard, so the legacy
    ``--provider`` flag is rejected with a pointer to /api.
    """
    model_input, persist_global = ports.parse_flags(request.arguments)
    user_providers = ports.user_providers()
    current_provider = ports.provider()
    current_model = ports.model()

    if "--provider" in model_input.split():
        ports.emit("  /model no longer switches providers.")
        ports.emit("  Use /api to change the active provider, then /model to pick a model.")
        return

    if not model_input:
        try:
            models = ports.list_current_models(
                current_provider=current_provider,
                user_providers=user_providers,
                max_models=30,
            )
        except Exception:
            models = []

        if not models:
            ports.emit("  No models available for the current provider.")
            ports.emit("")
            ports.emit("  Run /api to configure a provider.")
            return

        ports.open_picker(
            models,
            current_model or "unknown",
            ports.provider_label(current_provider) if current_provider else "unknown",
            user_providers,
        )
        return

    result = ports.switch_model(
        raw_input=model_input,
        current_provider=current_provider,
        current_base_url=ports.base_url(),
        current_api_key=ports.api_key(),
        is_global=persist_global,
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
