"""Modal-selection keybindings for the terminal adapter."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent


ModalState = MutableMapping[str, object]


@dataclass(frozen=True, slots=True)
class ModalNavigationPorts:
    clarify_state: Callable[[], ModalState | None]
    clarify_freetext_active: Callable[[], bool]
    approval_state: Callable[[], ModalState | None]
    model_picker_state: Callable[[], ModalState | None]
    # Execute a selection mutation under the host's modal-state lock, so the
    # read-modify-write of ``selected`` stays atomic against the request
    # thread creating/clearing modal state (timeouts, resets).
    update_selection: Callable[[Callable[[], None]], None]
    invalidate: Callable[[], None]


def _navigate(
    state_getter: Callable[[], ModalState | None],
    *,
    delta: int,
    maximum: Callable[[ModalState], int],
) -> Callable[[], None]:
    """Return a lock-safe read-modify-write of the modal ``selected`` index."""

    def _update() -> None:
        state = state_getter()
        if state is not None:
            _move_selection(state, delta=delta, maximum=maximum(state))

    return _update


def install_modal_navigation_keybindings(
    key_bindings: KeyBindings,
    *,
    ports: ModalNavigationPorts,
) -> None:
    """Install arrow-key selection controls using only modal-state ports."""
    clarify_active = Condition(
        lambda: bool(ports.clarify_state()) and not ports.clarify_freetext_active()
    )

    @key_bindings.add("up", filter=clarify_active)
    def clarify_up(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.clarify_state,
                delta=-1,
                maximum=lambda state: _choice_count(state, "choices"),
            )
        )
        ports.invalidate()

    @key_bindings.add("down", filter=clarify_active)
    def clarify_down(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.clarify_state,
                delta=1,
                maximum=lambda state: _choice_count(state, "choices"),
            )
        )
        ports.invalidate()

    approval_active = Condition(lambda: bool(ports.approval_state()))

    @key_bindings.add("up", filter=approval_active)
    def approval_up(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.approval_state,
                delta=-1,
                maximum=lambda state: _last_choice_index(state, "choices"),
            )
        )
        ports.invalidate()

    @key_bindings.add("down", filter=approval_active)
    def approval_down(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.approval_state,
                delta=1,
                maximum=lambda state: _last_choice_index(state, "choices"),
            )
        )
        ports.invalidate()

    model_picker_active = Condition(lambda: bool(ports.model_picker_state()))

    @key_bindings.add("up", filter=model_picker_active)
    def model_picker_up(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.model_picker_state,
                delta=-1,
                maximum=_model_picker_maximum,
            )
        )
        ports.invalidate()

    @key_bindings.add("down", filter=model_picker_active)
    def model_picker_down(_event: KeyPressEvent) -> None:
        ports.update_selection(
            _navigate(
                ports.model_picker_state,
                delta=1,
                maximum=_model_picker_maximum,
            )
        )
        ports.invalidate()


def _move_selection(state: ModalState, *, delta: int, maximum: int) -> None:
    current = int(state.get("selected", 0))
    state["selected"] = min(maximum, max(0, current + delta))


def _choice_count(state: ModalState, key: str) -> int:
    value = state.get(key)
    return len(value) if isinstance(value, Sequence) and not isinstance(value, str) else 0


def _last_choice_index(state: ModalState, key: str) -> int:
    return max(0, _choice_count(state, key) - 1)


def _model_picker_maximum(state: ModalState) -> int:
    if state.get("stage") == "provider":
        return _choice_count(state, "providers")
    return _choice_count(state, "model_list") + 1
