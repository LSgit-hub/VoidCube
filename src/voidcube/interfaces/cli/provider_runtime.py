"""CLI provider/model picker and switch-result adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model_picker_runtime import (
    CliModelPickerPorts,
    CliModelPickerRuntime,
)


class CliProviderRuntime:
    """Own Provider/model interaction while mutating only the supplied host."""

    def __init__(
        self,
        host: Any,
        *,
        emit: Callable[[str], None],
        translate: Callable[..., str],
        persist_global_config: Callable[[str, str], None] | None = None,
    ) -> None:
        self.host = host
        self.emit = emit
        self.translate = translate
        self.persist_global_config = persist_global_config

    def open_picker(
        self,
        providers: list,
        current_model: str,
        current_provider: str,
        user_providers: Any = None,
    ) -> None:
        host = self.host
        host._capture_modal_input_snapshot()
        default_idx = next(
            (index for index, provider in enumerate(providers) if provider.get("is_current")),
            0,
        )
        host._model_picker_state = {
            "stage": "provider",
            "providers": providers,
            "selected": default_idx,
            "current_model": current_model,
            "current_provider": current_provider,
            "user_provs": user_providers,
        }
        host._invalidate(min_interval=0.0)

    def close_picker(self) -> None:
        self.host._model_picker_state = None
        self.host._restore_modal_input_snapshot()
        self.host._invalidate(min_interval=0.0)

    def submit_picker(self, persist_global: bool = True) -> None:
        host = self.host

        def switch_model(**kwargs: Any) -> Any:
            from .model_switch import switch_model

            return switch_model(**kwargs)

        CliModelPickerRuntime(
            CliModelPickerPorts(
                state=lambda: host._model_picker_state,
                set_state=lambda value: setattr(host, "_model_picker_state", value),
                close_picker=self.close_picker,
                invalidate=lambda: host._invalidate(min_interval=0.0),
                switch_model=switch_model,
                apply_switch_result=self.apply_switch_result,
                current_provider=lambda: host.provider,
                current_model=lambda: host.model,
                current_base_url=lambda: host.base_url or "",
                current_api_key=lambda: host.api_key or "",
            )
        ).submit(persist_global=persist_global)

    def apply_switch_result(self, result: Any, persist_global: bool) -> None:
        host = self.host
        if not result.success:
            self.emit(f"  ✗ {result.error_message}")
            return

        old_model = host.model
        host.model = result.new_model
        host.provider = result.target_provider
        host.requested_provider = result.target_provider
        if result.api_key:
            host.api_key = result.api_key
            host._explicit_api_key = result.api_key
        if result.base_url:
            host.base_url = result.base_url
            host._explicit_base_url = result.base_url

        if host.agent is not None:
            try:
                host.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                )
            except Exception as exc:
                self.emit(f"  ⚠ Agent swap failed ({exc}); change applied to next session.")

        host._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            "Adjust your self-identification accordingly.]"
        )

        provider_label = result.provider_label or result.target_provider
        self.emit(f"  ✓ Model switched: {result.new_model}")
        self.emit(f"    Provider: {provider_label}")

        model_info = result.model_info
        if model_info:
            context_window = getattr(model_info, "context_window", None) or getattr(
                model_info, "context_length", None
            )
            if context_window:
                self.emit(f"    Context: {context_window:,} tokens")
            max_output = getattr(model_info, "max_output", None) or getattr(
                model_info, "max_completion_tokens", None
            )
            if max_output:
                self.emit(f"    Max output: {max_output:,} tokens")
            if callable(getattr(model_info, "has_cost_data", None)) and model_info.has_cost_data():
                self.emit(f"    Cost: {model_info.format_cost()}")
            if callable(getattr(model_info, "format_capabilities", None)):
                self.emit(f"    Capabilities: {model_info.format_capabilities()}")
        else:
            try:
                from ...infrastructure.providers.model_metadata import get_model_context_length

                context_length = get_model_context_length(
                    result.new_model,
                    base_url=result.base_url or host.base_url,
                    api_key=result.api_key or host.api_key,
                    provider=result.target_provider,
                )
                self.emit(f"    Context: {context_length:,} tokens")
            except Exception:
                pass

        if result.warning_message:
            self.emit(f"    ⚠ {result.warning_message}")
        if not persist_global:
            self.emit(self.translate("    (session only — won't persist after restart)"))
            return

        try:
            if self.persist_global_config is None:
                raise RuntimeError("provider config persistence adapter is not configured")
            self.persist_global_config(result.target_provider, result.new_model)
        except Exception as exc:
            self.emit(f"    ⚠ Failed to save config: {exc}")
            return
        self.emit(self.translate("    Saved to config.yaml"))


__all__ = ["CliProviderRuntime"]
