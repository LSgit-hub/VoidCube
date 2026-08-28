"""Interactive modal presentation widgets for the terminal adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, Window

from ....domain.contracts.interaction import ClarificationRequest
from ..terminal_text_layout import (
    append_blank_panel_line as _append_blank_panel_line,
    append_panel_line as _append_panel_line,
    display_width,
    modal_panel_max_height as _modal_max_height,
    pad_to_width,
    panel_box_width as _panel_box_width,
    wrap_text as _wrap_panel_text,
)


ModalState = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ModalWidgetPorts:
    """Read-only state and fragment ports used by modal widgets."""

    clarify_state: Callable[[], ModalState | None]
    clarify_freetext_active: Callable[[], bool]
    sudo_state: Callable[[], object | None]
    secret_state: Callable[[], ModalState | None]
    approval_state: Callable[[], object | None]
    approval_fragments: Callable[[], AnyFormattedText]
    model_picker_state: Callable[[], ModalState | None]


@dataclass(frozen=True, slots=True)
class ModalWidgets:
    clarify: ConditionalContainer
    sudo: ConditionalContainer
    secret: ConditionalContainer
    approval: ConditionalContainer
    model_picker: ConditionalContainer


def build_modal_widgets(*, ports: ModalWidgetPorts) -> ModalWidgets:
    """Create read-only prompt-toolkit widgets for interactive modal state."""

    def clarify_display() -> list[tuple[str, str]]:
        state = ports.clarify_state()
        if not state:
            return []
        state = dict(state)

        request = cast(ClarificationRequest, state["request"])
        question = request.question
        choices = _string_sequence(state.get("choices"))
        selected = _as_int(state.get("selected"))
        freetext_active = ports.clarify_freetext_active()
        preview_lines = _wrap_panel_text(question, 60)
        for index, choice in enumerate(choices):
            prefix = "❯ " if index == selected and not freetext_active else "  "
            preview_lines.extend(_wrap_panel_text(f"{prefix}{choice}", 60, subsequent_indent="  "))
        other_label = (
            "❯ Other (type below)"
            if freetext_active
            else "❯ Other (type your answer)"
            if selected == len(choices)
            else "  Other (type your answer)"
        )
        preview_lines.extend(_wrap_panel_text(other_label, 60, subsequent_indent="  "))
        box_width = _panel_box_width("Voidcube needs your input", preview_lines)
        inner_text_width = max(8, box_width - 2)
        lines: list[tuple[str, str]] = [
            ("class:clarify-border", "╭─ "),
            ("class:clarify-title", "Voidcube needs your input"),
            (
                "class:clarify-border",
                " " + ("─" * max(0, box_width - display_width("Voidcube needs your input") - 3)) + "╮\n",
            ),
        ]
        _append_blank_panel_line(lines, "class:clarify-border", box_width)
        for wrapped in _wrap_panel_text(question, inner_text_width):
            _append_panel_line(lines, "class:clarify-border", "class:clarify-question", wrapped, box_width)
        _append_blank_panel_line(lines, "class:clarify-border", box_width)

        if freetext_active and not choices:
            for wrapped in _wrap_panel_text("Type your answer in the prompt below, then press Enter.", inner_text_width):
                _append_panel_line(lines, "class:clarify-border", "class:clarify-choice", wrapped, box_width)
            _append_blank_panel_line(lines, "class:clarify-border", box_width)

        for index, choice in enumerate(choices):
            style = "class:clarify-selected" if index == selected and not freetext_active else "class:clarify-choice"
            prefix = "❯ " if index == selected and not freetext_active else "  "
            for wrapped in _wrap_panel_text(f"{prefix}{choice}", inner_text_width, subsequent_indent="  "):
                _append_panel_line(lines, "class:clarify-border", style, wrapped, box_width)

        if choices:
            if selected == len(choices) and not freetext_active:
                other_style, other_label = "class:clarify-selected", "❯ Other (type your answer)"
            elif freetext_active:
                other_style, other_label = "class:clarify-active-other", "❯ Other (type below)"
            else:
                other_style, other_label = "class:clarify-choice", "  Other (type your answer)"
            for wrapped in _wrap_panel_text(other_label, inner_text_width, subsequent_indent="  "):
                _append_panel_line(lines, "class:clarify-border", other_style, wrapped, box_width)

        _append_blank_panel_line(lines, "class:clarify-border", box_width)
        lines.append(("class:clarify-border", "╰" + ("─" * box_width) + "╯\n"))
        return _limit_panel_lines(
            lines,
            _modal_max_height(),
            border_style="class:clarify-border",
            content_style="class:clarify-hint",
            box_width=box_width,
        )

    def sudo_display() -> list[tuple[str, str]]:
        if not ports.sudo_state():
            return []
        return _simple_panel(
            title="🔐 Sudo Password Required",
            content=["Enter password below (hidden), or press Enter to skip"],
        )

    def secret_display() -> list[tuple[str, str]]:
        state = ports.secret_state()
        if not state:
            return []
        state = dict(state)
        prompt = str(state.get("prompt") or f"Enter value for {state.get('var_name', 'secret')}")
        metadata = state.get("metadata")
        help_text = metadata.get("help") if isinstance(metadata, Mapping) else None
        body = "Enter secret below (hidden), or press Enter to skip"
        content_lines = [prompt, body]
        if help_text:
            content_lines.insert(1, str(help_text))
        box_width = _panel_box_width("🔑 Skill Setup Required", content_lines)
        inner_text_width = max(8, box_width - 2)
        lines: list[tuple[str, str]] = [
            ("class:sudo-border", "╭─ "),
            ("class:sudo-title", "🔑 Skill Setup Required"),
            (
                "class:sudo-border",
                " " + ("─" * max(0, box_width - display_width("🔑 Skill Setup Required") - 3)) + "╮\n",
            ),
        ]
        _append_blank_panel_line(lines, "class:sudo-border", box_width)
        for wrapped in _wrap_panel_text(prompt, inner_text_width):
            _append_panel_line(lines, "class:sudo-border", "class:sudo-text", wrapped, box_width)
        if help_text:
            for wrapped in _wrap_panel_text(str(help_text), inner_text_width):
                _append_panel_line(lines, "class:sudo-border", "class:sudo-text", wrapped, box_width)
        _append_blank_panel_line(lines, "class:sudo-border", box_width)
        for wrapped in _wrap_panel_text(body, inner_text_width):
            _append_panel_line(lines, "class:sudo-border", "class:sudo-text", wrapped, box_width)
        _append_blank_panel_line(lines, "class:sudo-border", box_width)
        lines.append(("class:sudo-border", "╰" + ("─" * box_width) + "╯\n"))
        return _limit_panel_lines(
            lines,
            _modal_max_height(),
            border_style="class:sudo-border",
            content_style="class:sudo-text",
            box_width=box_width,
        )

    def model_picker_display() -> list[tuple[str, str]]:
        state = ports.model_picker_state()
        if not state:
            return []
        state = dict(state)
        title, hint, choices = _model_picker_content(state)
        max_visible = 10
        box_width = _panel_box_width(title, [hint] + choices[:max_visible], min_width=46, max_width=84)
        inner_text_width = max(8, box_width - 6)
        lines: list[tuple[str, str]] = [
            ("class:clarify-border", "╭─ "),
            ("class:clarify-title", title),
            ("class:clarify-border", " " + ("─" * max(0, box_width - display_width(title) - 3)) + "╮\n"),
        ]
        _append_blank_panel_line(lines, "class:clarify-border", box_width)
        _append_panel_line(lines, "class:clarify-border", "class:clarify-hint", hint, box_width)
        _append_blank_panel_line(lines, "class:clarify-border", box_width)

        selected = _as_int(state.get("selected"))
        total_choices = len(choices)
        if total_choices > max_visible:
            window_start = _visible_window_start(selected, total_choices, max_visible)
            window_end = min(window_start + max_visible, total_choices)
            if window_start > 0:
                _append_scroll_indicator(
                    lines,
                    box_width,
                    border_style="class:clarify-border",
                    content_style="class:clarify-choice",
                )
            for index in range(window_start, window_end):
                _append_picker_choice(lines, choices[index], index == selected, inner_text_width, box_width)
            if window_end < total_choices:
                _append_scroll_indicator(
                    lines,
                    box_width,
                    border_style="class:clarify-border",
                    content_style="class:clarify-choice",
                )
            _append_panel_line(
                lines,
                "class:clarify-border",
                "class:clarify-hint",
                f" {selected + 1}/{total_choices} ".center(inner_text_width),
                box_width,
            )
        else:
            for index, choice in enumerate(choices):
                _append_picker_choice(lines, choice, index == selected, inner_text_width, box_width)

        _append_blank_panel_line(lines, "class:clarify-border", box_width)
        lines.append(("class:clarify-border", "╰" + ("─" * box_width) + "╯\n"))
        return _limit_panel_lines(
            lines,
            _modal_max_height(),
            border_style="class:clarify-border",
            content_style="class:clarify-hint",
            box_width=box_width,
        )

    return ModalWidgets(
        clarify=ConditionalContainer(Window(FormattedTextControl(cast(AnyFormattedText, clarify_display)), wrap_lines=True), filter=Condition(lambda: ports.clarify_state() is not None)),
        sudo=ConditionalContainer(Window(FormattedTextControl(cast(AnyFormattedText, sudo_display)), wrap_lines=True), filter=Condition(lambda: ports.sudo_state() is not None)),
        secret=ConditionalContainer(Window(FormattedTextControl(cast(AnyFormattedText, secret_display)), wrap_lines=True), filter=Condition(lambda: ports.secret_state() is not None)),
        approval=ConditionalContainer(Window(FormattedTextControl(ports.approval_fragments), wrap_lines=True), filter=Condition(lambda: ports.approval_state() is not None)),
        model_picker=ConditionalContainer(Window(FormattedTextControl(cast(AnyFormattedText, model_picker_display)), wrap_lines=True), filter=Condition(lambda: ports.model_picker_state() is not None)),
    )


def _simple_panel(*, title: str, content: list[str]) -> list[tuple[str, str]]:
    box_width = _panel_box_width(title, content)
    inner_text_width = max(8, box_width - 2)
    lines: list[tuple[str, str]] = [
        ("class:sudo-border", "╭─ "),
        ("class:sudo-title", title),
        ("class:sudo-border", " " + ("─" * max(0, box_width - display_width(title) - 3)) + "╮\n"),
    ]
    _append_blank_panel_line(lines, "class:sudo-border", box_width)
    for text in content:
        for wrapped in _wrap_panel_text(text, inner_text_width):
            _append_panel_line(lines, "class:sudo-border", "class:sudo-text", wrapped, box_width)
    _append_blank_panel_line(lines, "class:sudo-border", box_width)
    lines.append(("class:sudo-border", "╰" + ("─" * box_width) + "╯\n"))
    return _limit_panel_lines(
        lines,
        _modal_max_height(),
        border_style="class:sudo-border",
        content_style="class:sudo-text",
        box_width=box_width,
    )


def _model_picker_content(state: ModalState) -> tuple[str, str, list[str]]:
    if state.get("stage", "provider") == "provider":
        choices = []
        for provider in _mapping_sequence(state.get("providers")):
            models = _string_sequence(provider.get("models"))
            count = _as_int(provider.get("total_models", len(models)))
            label = f"{provider['name']} ({count} model{'s' if count != 1 else ''})"
            if provider.get("is_current"):
                label += "  ← current"
            choices.append(label)
        choices.append("Cancel")
        return (
            "> Model Picker — Select Provider",
            f"Current: {state.get('current_model', 'unknown')} on {state.get('current_provider', 'unknown')}",
            choices,
        )

    provider_data = state.get("provider_data")
    provider = provider_data if isinstance(provider_data, Mapping) else {}
    models = _string_sequence(state.get("model_list"))
    title = f"> Model Picker — {provider.get('name', provider.get('slug', 'Provider'))}"
    choices = models + ["← Back", "Cancel"]
    hint = f"Select a model ({len(models)} available)" if models else "No models listed for this provider. Use Back or Cancel."
    return title, hint, choices


def _append_scroll_indicator(
    lines: list[tuple[str, str]],
    box_width: int,
    *,
    border_style: str = "class:clarify-border",
    content_style: str = "class:clarify-choice",
) -> None:
    """Append a scrolled-outside-the-window row, styled by the caller.

    The indicator is generic: it only draws a bordered row with an ellipsis,
    so the panel that owns it must pass its own style classes instead of
    inheriting the model-picker/clarify styles by accident.
    """
    lines.extend(
        [
            (border_style, "│"),
            (content_style, pad_to_width("  ...", max(0, box_width))),
            (border_style, "│\n"),
        ]
    )


def _split_visual_lines(
    fragments: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """Group a flat fragment list into visual lines at newline boundaries."""
    visual_lines: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for style, text in fragments:
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if part:
                current.append((style, part))
            if index < len(parts) - 1:
                visual_lines.append(current)
                current = []
    if current:
        visual_lines.append(current)
    return visual_lines


def _flatten_visual_lines(
    visual_lines: list[list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    """Rebuild a flat fragment list, restoring one newline per visual line."""
    flat: list[tuple[str, str]] = []
    for visual_line in visual_lines:
        for index, (style, text) in enumerate(visual_line):
            if index == len(visual_line) - 1:
                text = text + "\n"
            flat.append((style, text))
    return flat


def _limit_panel_lines(
    lines: list[tuple[str, str]],
    max_lines: int,
    *,
    border_style: str,
    content_style: str,
    box_width: int,
) -> list[tuple[str, str]]:
    """Keep panel actions visible while indicating truncated middle content."""
    visual_lines = _split_visual_lines(lines)
    if len(visual_lines) <= max_lines:
        return lines
    closing = visual_lines[-1]
    usable = max(1, max_lines - 2)
    head_count = (
        1
        if usable == 1
        else min(2, max(0, usable - 1), usable // 2)
    )
    tail_count = usable - head_count
    head = visual_lines[:head_count]
    tail = visual_lines[-1 - tail_count : -1] if tail_count else []
    remaining = len(visual_lines) - len(head) - len(tail) - 1
    indicator = f"  … {remaining} more line{'s' if remaining != 1 else ''}"
    visible = head
    visible.append(
        [
            (border_style, "│"),
            (content_style, pad_to_width(indicator, max(0, box_width))),
            (border_style, "│"),
        ]
    )
    visible.extend(tail)
    visible.append(closing)
    return _flatten_visual_lines(visible)


def _append_picker_choice(lines: list[tuple[str, str]], choice: str, selected: bool, width: int, box_width: int) -> None:
    style = "class:clarify-selected" if selected else "class:clarify-choice"
    prefix = "❯ " if selected else "  "
    for wrapped in _wrap_panel_text(prefix + choice, width, subsequent_indent="  "):
        _append_panel_line(lines, "class:clarify-border", style, wrapped, box_width)


def _visible_window_start(selected: int, total: int, visible: int) -> int:
    if selected < visible // 2:
        return 0
    if selected > total - visible // 2 - 1:
        return max(0, total - visible)
    return selected - visible // 2


def _as_int(value: object, default: int = 0) -> int:
    """Coerce an untyped modal-state value to int, preserving ``int(...)`` semantics."""
    if isinstance(value, int):
        return value
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return default


def _string_sequence(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [str(item) for item in value]


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if isinstance(item, Mapping)]
