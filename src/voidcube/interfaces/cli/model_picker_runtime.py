"""Drive the model picker selection state machine through explicit ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliModelPickerPorts:
    """Picker state and model-switch operations supplied by the CLI host."""

    state: Callable[[], Mapping[str, Any] | None]
    set_state: Callable[[dict[str, Any]], None]
    close_picker: Callable[[], None]
    invalidate: Callable[[], None]
    switch_model: Callable[..., Any]
    apply_switch_result: Callable[[Any, bool], None]
    current_provider: Callable[[], str]
    current_model: Callable[[], str]
    current_base_url: Callable[[], str]
    current_api_key: Callable[[], str]
    confirm_capabilities: Callable[[str, str], Sequence[str] | None] | None = None


class CliModelPickerRuntime:
    """Own provider/model picker navigation without owning CLI state."""

    def __init__(self, ports: CliModelPickerPorts) -> None:
        self.ports = ports

    def submit(self, persist_global: bool = True) -> None:
        state = self.ports.state()
        if not state:
            return

        selected = int(state.get("selected", 0))
        stage = state.get("stage")
        if stage == "provider":
            providers = list(state.get("providers") or [])
            if selected >= len(providers):
                self.ports.close_picker()
                return
            provider_data = providers[selected]
            next_state = dict(state)
            next_state.update(
                stage="model",
                provider_data=provider_data,
                model_list=list(provider_data.get("models") or []),
                selected=0,
            )
            self.ports.set_state(next_state)
            self.ports.invalidate()
            return

        if stage != "model":
            return

        provider_data = dict(state.get("provider_data") or {})
        model_list: Sequence[Any] = list(state.get("model_list") or [])
        back_index = len(model_list)
        cancel_index = len(model_list) + 1
        if selected == back_index:
            providers = list(state.get("providers") or [])
            provider_slug = provider_data.get("slug")
            provider_index = next(
                (
                    index
                    for index, provider in enumerate(providers)
                    if provider.get("slug") == provider_slug
                ),
                0,
            )
            next_state = dict(state)
            next_state.update(stage="provider", selected=provider_index)
            self.ports.set_state(next_state)
            self.ports.invalidate()
            return

        if selected >= cancel_index:
            self.ports.close_picker()
            return

        if selected < len(model_list):
            result = self.ports.switch_model(
                raw_input=model_list[selected],
                current_provider=self.ports.current_provider() or "",
                current_model=self.ports.current_model() or "",
                current_base_url=self.ports.current_base_url() or "",
                current_api_key=self.ports.current_api_key() or "",
                is_global=persist_global,
                explicit_provider=provider_data.get("slug"),
                user_providers=state.get("user_provs"),
            )
            self.ports.close_picker()
            if result.success and self.ports.confirm_capabilities is not None:
                native_modalities = self.ports.confirm_capabilities(
                    result.target_provider,
                    result.new_model,
                )
                if native_modalities is None:
                    return
                result.native_modalities = tuple(native_modalities)
            self.ports.apply_switch_result(result, persist_global)
            return

        self.ports.close_picker()
