from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.key_binding import KeyBindings

from voidcube.interfaces.cli.tui.keybindings import (
    accept_completion_or_suggestion,
    navigate_history,
    install_history_navigation_keybindings,
    install_text_editing_keybindings,
)
from voidcube.interfaces.cli.tui.modal_navigation import (
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

    install_text_editing_keybindings(bindings, normal_input_active=lambda: True)

    assert len(bindings.bindings) == 3


def test_text_editing_keybindings_are_filtered_while_a_modal_is_active() -> None:
    bindings = KeyBindings()

    install_text_editing_keybindings(bindings, normal_input_active=lambda: False)

    assert len(bindings.bindings) == 3
    for binding in bindings.bindings:
        assert binding.filter() is False


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
            update_selection=lambda mutate: mutate(),
            invalidate=lambda: invalidations.append(None),
        ),
    )

    assert len(bindings.bindings) == 6


def _binding_handler(bindings: KeyBindings, key: str, occurrence: int = 0) -> object:
    """Find the handler of the ``occurrence``-th (0-based) binding on ``key``.

    Registration order is clarify up/down, approval up/down, model-picker
    up/down, so ``("down", 0)`` is clarify_down and ``("down", 2)`` is
    model_picker_down.
    """
    matches = [b for b in bindings.bindings if key in b.keys]
    if occurrence >= len(matches):
        raise AssertionError(f"only {len(matches)} bindings for {key!r}")
    return matches[occurrence].handler


def test_modal_navigation_updates_run_through_update_selection_port() -> None:
    """Arrow-key handlers must mutate ``selected`` via the lock-safe port."""
    bindings = KeyBindings()
    mutator_calls: list[object] = []
    clarify = {"selected": 0, "choices": ["first", "second"]}

    install_modal_navigation_keybindings(
        bindings,
        ports=ModalNavigationPorts(
            clarify_state=lambda: clarify,
            clarify_freetext_active=lambda: False,
            approval_state=lambda: None,
            model_picker_state=lambda: None,
            update_selection=lambda mutate: mutator_calls.append(mutate),
            invalidate=lambda: None,
        ),
    )

    _binding_handler(bindings, "down")(object())  # type: ignore[call-arg]
    assert len(mutator_calls) == 1

    # The port receives a deferred closure, not an immediate mutation —
    # the host decides when (under its lock) to run it.
    assert clarify["selected"] == 0
    mutator_calls[0]()  # type: ignore[misc]
    assert clarify["selected"] == 1


def test_modal_navigation_selection_clamps_at_boundaries() -> None:
    bindings = KeyBindings()
    clarify = {"selected": 1, "choices": ["first", "second"]}
    approval = {"selected": 0, "choices": ["yes", "no"]}
    picker = {"selected": 2, "stage": "model", "model_list": ["a", "b"]}

    install_modal_navigation_keybindings(
        bindings,
        ports=ModalNavigationPorts(
            clarify_state=lambda: clarify,
            clarify_freetext_active=lambda: False,
            approval_state=lambda: approval,
            model_picker_state=lambda: picker,
            update_selection=lambda mutate: mutate(),
            invalidate=lambda: None,
        ),
    )

    # Down on the last clarify choice advances to the free-text slot
    # (selected == len(choices) is the trailing "type your own" entry).
    _binding_handler(bindings, "down", occurrence=0)(object())  # type: ignore[call-arg]
    assert clarify["selected"] == 2

    # Up on the first approval choice stays clamped at 0.
    _binding_handler(bindings, "up", occurrence=1)(object())  # type: ignore[call-arg]
    assert approval["selected"] == 0

    # Model picker in "model" stage clamps at len(model_list)+1.
    _binding_handler(bindings, "down", occurrence=2)(object())  # type: ignore[call-arg]
    assert picker["selected"] == 3
