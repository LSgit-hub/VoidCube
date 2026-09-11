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
    current_base_url: Callable[[], str]
    current_api_key: Callable[[], str]
    confirm_capabilities: Callable[[str, str], Sequence[str] | None] | None = None


class CliModelPickerRuntime:
    """Own model picker selection without owning CLI state.

    The picker is single-stage: it lists only the models of the active
    provider, so selecting an entry never changes the provider.
    """

    def __init__(self, ports: CliModelPickerPorts) -> None:
        self.ports = ports

    def submit(self, persist_global: bool = True) -> None:
        state = self.ports.state()
        if not state:
            return

        selected = int(state.get("selected", 0))
        model_list: Sequence[Any] = list(state.get("model_list") or [])
        if selected >= len(model_list):
            # "Cancel" row (or an out-of-range index) closes the picker.
            self.ports.close_picker()
            return

        result = self.ports.switch_model(
            raw_input=model_list[selected],
            current_provider=self.ports.current_provider() or "",
            current_base_url=self.ports.current_base_url() or "",
            current_api_key=self.ports.current_api_key() or "",
            is_global=persist_global,
            user_providers=state.get("user_provs"),
        )
        self.ports.close_picker()
        if (
            getattr(result, "success", False)
            and self.ports.confirm_capabilities is not None
        ):
            native_modalities = self.ports.confirm_capabilities(
                result.target_provider,
                result.new_model,
            )
            if native_modalities is None:
                return
            result.native_modalities = tuple(native_modalities)
        self.ports.apply_switch_result(result, persist_global)
