from __future__ import annotations

import pytest

from voidcube.interfaces.cli.clear_command_adapter import (
    ClearBannerState,
    ClearDisplayPorts,
    render_clear_display,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


class _Console:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    def print(self, value: object) -> None:
        self.events.append(("console", value))


def _ports(
    events: list[object],
    *,
    tui_active: bool,
    compact: bool = False,
    terminal_width: int = 100,
) -> ClearDisplayPorts:
    return ClearDisplayPorts(
        tui_active=lambda: tui_active,
        clear_tui_screen=lambda: events.append("clear-tui"),
        show_standalone_banner=lambda: events.append("standalone-banner"),
        compact=lambda: compact,
        terminal_width=lambda: terminal_width,
        banner_state=lambda: ClearBannerState(
            model="model-a",
            cwd="workspace",
            enabled_toolsets=("terminal",),
            session_id="new-id",
            context_length=128_000,
            conversation_history=(),
        ),
        emit_tui=lambda text: events.append(("tui", text)),
        emit_plain=lambda text: events.append(("plain", text)),
        fresh_start_message="fresh start",
        chat_console_factory=lambda: _Console(events),
        compact_banner_factory=lambda: "compact-banner",
        load_tools=lambda toolsets: events.append(("tools", tuple(toolsets))) or [],
    )


@pytest.mark.parametrize(
    ("compact", "terminal_width"),
    [(True, 120), (False, 79)],
)
def test_clear_display_uses_compact_banner_for_compact_or_narrow_tui(
    compact: bool,
    terminal_width: int,
) -> None:
    events: list[object] = []

    render_clear_display(
        _ports(
            events,
            tui_active=True,
            compact=compact,
            terminal_width=terminal_width,
        )
    )

    assert events == [
        "clear-tui",
        ("console", "compact-banner"),
        ("tui", "  fresh start\n"),
    ]


def test_clear_display_projects_full_tui_banner_from_snapshot(monkeypatch) -> None:
    events: list[object] = []

    def fake_banner(**kwargs):
        events.append(("banner", kwargs))

    monkeypatch.setattr(
        "voidcube.interfaces.cli.clear_command_adapter.build_welcome_banner",
        fake_banner,
    )

    render_clear_display(_ports(events, tui_active=True))

    assert events[0] == "clear-tui"
    assert events[1] == ("tools", ("terminal",))
    kind, banner = events[2]
    assert kind == "banner"
    assert banner["model"] == "model-a"
    assert banner["cwd"] == "workspace"
    assert banner["enabled_toolsets"] == ["terminal"]
    assert banner["session_id"] == "new-id"
    assert banner["context_length"] == 128_000
    assert banner["conversation_history"] == []
    assert events[3] == ("tui", "  fresh start\n")


def test_clear_display_uses_standalone_banner_without_tui_clear() -> None:
    events: list[object] = []

    render_clear_display(_ports(events, tui_active=False))

    assert events == [
        "standalone-banner",
        ("plain", "  fresh start\n"),
    ]
