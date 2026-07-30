from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.key_binding import KeyBindings

from VoidCube_cli.tui_keybindings import (
    accept_completion_or_suggestion,
    navigate_history,
    install_history_navigation_keybindings,
    install_text_editing_keybindings,
)
from VoidCube_cli.tui_modal_navigation import (
    ModalNavigationPorts,
    install_modal_navigation_keybindings,
)


class _Buffer:
    def __init__(
        self,
        *,
        completion: object | None = None,
        menu_open: bool = False,
        suggestion: str = "",
    ) -> None:
        self.complete_state = (
            SimpleNamespace(current_completion=completion) if menu_open or completion else None
        )
        self.suggestion = SimpleNamespace(text=suggestion) if suggestion else None
        self.applied: list[object] = []
        self.inserted: list[str] = []
        self.started = 0
        self.selected: list[int] = []

    def go_to_completion(self, index: int) -> None:
        self.selected.append(index)
        self.complete_state.current_completion = object()

    def apply_completion(self, completion: object) -> None:
        self.applied.append(completion)

    def insert_text(self, text: str) -> None:
        self.inserted.append(text)

    def start_completion(self) -> None:
        self.started += 1


def test_completion_binding_accepts_completion_suggestion_or_starts_menu() -> None:
    completion = object()
    selected = _Buffer(completion=completion)
    first_selected = _Buffer(menu_open=True)
    suggestion = _Buffer(suggestion=" remainder")
    empty = _Buffer()

    accept_completion_or_suggestion(selected)  # type: ignore[arg-type]
    accept_completion_or_suggestion(first_selected)  # type: ignore[arg-type]
    accept_completion_or_suggestion(suggestion)  # type: ignore[arg-type]
    accept_completion_or_suggestion(empty)  # type: ignore[arg-type]

    assert selected.applied == [completion]
    assert first_selected.selected == [0]
    assert len(first_selected.applied) == 1
    assert suggestion.inserted == [" remainder"]
    assert empty.started == 1


def test_text_editing_keybindings_register_without_cli_host() -> None:
    bindings = KeyBindings()

    install_text_editing_keybindings(bindings)

    assert len(bindings.bindings) == 3


def test_history_navigation_uses_buffer_navigation_without_host() -> None:
    calls: list[tuple[str, int]] = []
    buffer = SimpleNamespace(
        auto_up=lambda *, count: calls.append(("up", count)),
        auto_down=lambda *, count: calls.append(("down", count)),
    )

    navigate_history(buffer, direction="up", count=2)  # type: ignore[arg-type]
    navigate_history(buffer, direction="down", count=3)  # type: ignore[arg-type]

    assert calls == [("up", 2), ("down", 3)]


def test_history_navigation_registers_against_explicit_mode_predicate() -> None:
    bindings = KeyBindings()

    install_history_navigation_keybindings(bindings, normal_input_active=lambda: True)

    assert len(bindings.bindings) == 2


def test_modal_navigation_registers_with_explicit_modal_state_ports() -> None:
    bindings = KeyBindings()
    invalidations: list[None] = []
    clarify = {"selected": 0, "choices": ["first", "second"]}
    approval = {"selected": 0, "choices": ["yes", "no"]}
    picker = {"selected": 0, "stage": "model", "model_list": ["a", "b"]}

    install_modal_navigation_keybindings(
        bindings,
        ports=ModalNavigationPorts(
            clarify_state=lambda: clarify,
            clarify_freetext_active=lambda: False,
            approval_state=lambda: approval,
            model_picker_state=lambda: picker,
            invalidate=lambda: invalidations.append(None),
        ),
    )

    assert len(bindings.bindings) == 6
