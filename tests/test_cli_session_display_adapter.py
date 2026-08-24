from __future__ import annotations

from voidcube.interfaces.cli.session_runtime import (
    CliSessionDisplayAdapter,
    CliSessionDisplayPorts,
)


def _adapter(*, history, hydrated=None):
    output: list[str] = []
    state = {"hydrated": False}

    def hydrate_history() -> None:
        state["hydrated"] = True
        if hydrated is not None:
            hydrated.append(True)

    adapter = CliSessionDisplayAdapter(
        CliSessionDisplayPorts(
            list_sessions=lambda **_: (
                {"id": "active", "title": "current"},
                {"id": "old", "title": "older", "preview": "hello", "last_active": 0},
            ),
            active_session_id=lambda: "active",
            relative_time=lambda _: "now",
            conversation_history=lambda: history,
            resume_display=lambda: "full",
            terminal_width=lambda: 80,
            translate=lambda key, **_: key,
            emit=output.append,
            emit_blank_line=lambda: output.append(""),
            hydrate_history=hydrate_history,
        )
    )
    return adapter, output, state


def test_adapter_caches_browser_and_filters_active_session():
    adapter, _, _ = _adapter(history=[])

    first = adapter.browser_runtime()
    second = adapter.browser_runtime()

    assert first is second
    assert adapter.list_recent() == [
        {"id": "old", "title": "older", "preview": "hello", "last_active": 0}
    ]


def test_adapter_hydrates_before_rendering_history():
    hydrated: list[bool] = []
    adapter, output, state = _adapter(
        history=[
            {"role": "user", "content": "hello", "timestamp": 0},
            {"role": "assistant", "content": "world", "timestamp": 0},
        ],
        hydrated=hydrated,
    )

    adapter.display_history()

    assert state["hydrated"] is True
    assert hydrated == [True]
    assert any("previous_conversation" in line for line in output)
