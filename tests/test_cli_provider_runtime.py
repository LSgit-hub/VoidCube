from __future__ import annotations

import threading
from types import SimpleNamespace

from voidcube.interfaces.cli.provider_runtime import CliProviderRuntime
from voidcube.infrastructure.config import provider_selection


class _Host:
    def __init__(self) -> None:
        self._model_picker_state = None
        self._modal_lock = threading.Lock()
        self._snapshot_calls: list[str] = []
        self._invalidate_calls = 0
        self.model = "old-model"
        self.provider = "provider-a"
        self.requested_provider = "provider-a"
        self.base_url = "https://old.example/v1"
        self.api_key = "old-key"
        self._explicit_base_url = "https://old.example/v1"
        self._explicit_api_key = "old-key"
        self.agent = None

    def _capture_modal_input_snapshot(self) -> None:
        self._snapshot_calls.append("capture")

    def _restore_modal_input_snapshot(self) -> None:
        self._snapshot_calls.append("restore")

    def _invalidate(self, **_: object) -> None:
        self._invalidate_calls += 1


def _runtime(
    host: _Host,
    output: list[str] | None = None,
    *,
    persist_global_config=None,
) -> CliProviderRuntime:
    emit = output.append if output is not None else lambda _value: None
    return CliProviderRuntime(
        host,
        emit=emit,
        translate=lambda value, **_: value,
        persist_global_config=persist_global_config,
    )


def test_open_and_close_picker_own_modal_lifecycle() -> None:
    host = _Host()
    runtime = _runtime(host)
    providers = [
        {"slug": "provider-a", "is_current": False},
        {"slug": "provider-b", "is_current": True},
    ]

    runtime.open_picker(providers, "old-model", "Provider B", {"provider-b": {}})

    assert host._model_picker_state == {
        "stage": "provider",
        "providers": providers,
        "selected": 1,
        "current_model": "old-model",
        "current_provider": "Provider B",
        "user_provs": {"provider-b": {}},
    }
    assert host._snapshot_calls == ["capture"]
    assert host._invalidate_calls == 1

    runtime.close_picker()

    assert host._model_picker_state is None
    assert host._snapshot_calls == ["capture", "restore"]
    assert host._invalidate_calls == 2


def test_submit_picker_delegates_state_machine_and_preserves_session_scope(monkeypatch) -> None:
    host = _Host()
    host._model_picker_state = {
        "stage": "model",
        "selected": 0,
        "providers": [{"slug": "provider-b"}],
        "provider_data": {"slug": "provider-b"},
        "model_list": ["next-model"],
        "user_provs": {"provider-b": {}},
    }
    calls: list[tuple[str, object]] = []
    runtime = _runtime(host)
    runtime.apply_switch_result = lambda result, persist: calls.append(("apply", (result, persist)))
    runtime.close_picker = lambda: calls.append(("close", None))
    monkeypatch.setattr(
        "voidcube.interfaces.cli.model_switch.switch_model",
        lambda **kwargs: SimpleNamespace(
            success=True,
            new_model=kwargs["raw_input"],
            target_provider=kwargs["explicit_provider"],
        ),
    )

    # The picker runtime imports the switch function lazily; the host state and
    # resulting apply call are the contract exposed by this adapter.
    runtime.submit_picker(persist_global=False)

    assert calls[0] == ("close", None)
    assert calls[1][0] == "apply"
    result, persist = calls[1][1]
    assert result.new_model == "next-model"
    assert result.target_provider == "provider-b"
    assert persist is False


def test_apply_switch_result_updates_agent_and_skips_global_save_for_session_only() -> None:
    output: list[str] = []
    host = _Host()
    agent_calls: list[dict[str, object]] = []
    host.agent = SimpleNamespace(switch_model=lambda **kwargs: agent_calls.append(kwargs))
    runtime = _runtime(host, output)
    result = SimpleNamespace(
        success=True,
        new_model="next-model",
        target_provider="provider-b",
        api_key="next-key",
        base_url="https://next.example/v1",
        provider_label="Provider B",
        warning_message=None,
        model_info=SimpleNamespace(
            context_window=128_000,
            max_output=8_192,
            has_cost_data=lambda: False,
            format_capabilities=lambda: "tools",
        ),
    )

    runtime.apply_switch_result(result, persist_global=False)

    assert (host.model, host.provider, host.requested_provider) == (
        "next-model",
        "provider-b",
        "provider-b",
    )
    assert host.api_key == "next-key"
    assert host.base_url == "https://next.example/v1"
    assert agent_calls == [
        {
            "new_model": "next-model",
            "new_provider": "provider-b",
            "api_key": "next-key",
            "base_url": "https://next.example/v1",
        }
    ]
    assert any("session only" in line for line in output)


def test_apply_switch_result_persists_active_provider_and_model() -> None:
    output: list[str] = []
    host = _Host()
    saved: list[tuple[str, str]] = []
    runtime = _runtime(
        host,
        output,
        persist_global_config=lambda provider, model: saved.append((provider, model)),
    )

    result = SimpleNamespace(
        success=True,
        new_model="next-model",
        target_provider="provider-b",
        api_key=None,
        base_url=None,
        provider_label="Provider B",
        warning_message=None,
        model_info=None,
    )
    runtime.apply_switch_result(result, persist_global=True)

    assert saved == [("provider-b", "next-model")]
    assert any("Saved to config.yaml" in line for line in output)


def test_provider_selection_adapter_owns_canonical_config_write(monkeypatch) -> None:
    events: list[object] = []
    config = {"providers": {"provider-a": {}}}

    monkeypatch.setattr(provider_selection, "load_config", lambda: config)
    monkeypatch.setattr(
        provider_selection,
        "set_provider_model",
        lambda value, provider, model, *, make_active: events.append(
            ("model", value, provider, model, make_active)
        ) or {**value, "model": (provider, model)},
    )
    monkeypatch.setattr(
        provider_selection,
        "set_active_provider",
        lambda value, provider: events.append(("active", value, provider))
        or {**value, "active": provider},
    )
    monkeypatch.setattr(provider_selection, "save_config", lambda value: events.append(("save", value)))

    provider_selection.persist_provider_selection("provider-b", "next-model")

    assert events == [
        ("model", config, "provider-b", "next-model", True),
        ("active", {"providers": {"provider-a": {}}, "model": ("provider-b", "next-model")}, "provider-b"),
        ("save", {"providers": {"provider-a": {}}, "model": ("provider-b", "next-model"), "active": "provider-b"}),
    ]
