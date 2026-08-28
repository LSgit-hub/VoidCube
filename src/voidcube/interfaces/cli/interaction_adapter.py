"""Prompt-toolkit interaction adapters for shared approval and clarify ports."""

from __future__ import annotations

import queue
import time
from typing import Any, Callable

from ...domain.contracts.interaction import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationStatus,
)
from .terminal_text_layout import (
    append_blank_panel_line as _append_blank_panel_line,
    append_panel_line as _append_panel_line,
    display_width,
    panel_box_width as _panel_box_width,
    modal_panel_max_height as _modal_max_height,
    trim_to_width,
    wrap_text as _wrap_panel_text,
)


def clarification_sink(
    host: Any,
    request: ClarificationRequest,
    *,
    timeout: float,
    notify_timeout: Callable[[float], None],
) -> ClarificationDecision:
    """Present a clarification request and wait for the CLI response.

    The lock guards state mutation only — the blocking ``response_queue.get``
    loop runs lock-free so the UI thread's arrow-key navigation and submit
    callbacks can acquire the lock to update ``selected`` or deliver a value
    without deadlocking.
    """
    response_queue: queue.Queue = queue.Queue()
    with host._modal_lock:
        host._clarify_state = {
            "request": request,
            "choices": list(request.options),
            "selected": 0,
            "response_queue": response_queue,
        }
        host._clarify_deadline = time.monotonic() + timeout
        host._clarify_freetext = not request.options
    host._invalidate()

    last_refresh = time.monotonic()
    while True:
        try:
            result = response_queue.get(timeout=1)
            with host._modal_lock:
                host._clarify_deadline = 0
            if isinstance(result, ClarificationDecision):
                return result
            return ClarificationDecision(
                ClarificationStatus.ANSWERED,
                answer=result,
            )
        except queue.Empty:
            now = time.monotonic()
            if host._clarify_deadline - now <= 0:
                break
            if now - last_refresh >= 5:
                last_refresh = now
                host._invalidate()

    with host._modal_lock:
        host._clarify_state = None
        host._clarify_freetext = False
        host._clarify_deadline = 0
    host._invalidate()
    notify_timeout(timeout)
    return ClarificationDecision(
        ClarificationStatus.TIMED_OUT,
        reason=f"No response within {timeout:g} seconds",
    )


def approval_sink(
    host: Any,
    request: ApprovalRequest,
    *,
    timeout: float,
    notify_timeout: Callable[[], None],
) -> ApprovalDecision:
    """Present a dangerous-command request and fail closed on timeout.

    The lock guards state mutation only — the blocking ``response_queue.get``
    loop runs lock-free so the UI thread's ``handle_approval_selection`` can
    acquire the lock to deliver a decision without deadlocking.
    """
    with host._modal_lock:
        response_queue: queue.Queue = queue.Queue()
        host._approval_state = {
            "request": request,
            "choices": approval_choices(request.command),
            "selected": 0,
            "response_queue": response_queue,
        }
        host._approval_deadline = time.monotonic() + timeout
    host._invalidate()

    last_refresh = time.monotonic()
    while True:
        try:
            result = response_queue.get(timeout=1)
            with host._modal_lock:
                host._approval_state = None
                host._approval_deadline = 0
            host._invalidate()
            return ApprovalDecision(ApprovalStatus(result))
        except queue.Empty:
            now = time.monotonic()
            if host._approval_deadline - now <= 0:
                break
            if now - last_refresh >= 5:
                last_refresh = now
                host._invalidate()

    with host._modal_lock:
        host._approval_state = None
        host._approval_deadline = 0
    host._invalidate()
    notify_timeout()
    return ApprovalDecision(
        ApprovalStatus.DENIED,
        reason="Approval timed out",
    )


def approval_choices(command: str) -> list[str]:
    choices = [ApprovalStatus.APPROVED.value, ApprovalStatus.DENIED.value]
    # Measure rendered cell width, not raw codepoints — wide CJK commands
    # would otherwise exceed the 70-cell preview threshold undetected.
    if display_width(command) > 70:
        choices.append("view")
    return choices


def handle_approval_selection(host: Any) -> None:
    with host._modal_lock:
        state = host._approval_state
        if not state:
            return
        selected = state.get("selected", 0)
        choices = state.get("choices") or []
        if not 0 <= selected < len(choices):
            return

        chosen = choices[selected]
        if chosen == "view":
            state["show_full"] = True
            state["choices"] = [choice for choice in choices if choice != "view"]
            if state["selected"] >= len(state["choices"]):
                state["selected"] = max(0, len(state["choices"]) - 1)
            host._invalidate()
            return

        state["response_queue"].put(chosen)
        host._approval_state = None
    host._invalidate()


def approval_display_fragments(host: Any) -> list[tuple[str, str]]:
    """Render the dangerous-command approval panel."""
    state = host._approval_state
    if not state:
        return []
    state = dict(state)

    request = state["request"]
    command = request.command
    description = request.description
    choices = state["choices"]
    selected = state.get("selected", 0)
    show_full = state.get("show_full", False)
    title = "[!] Dangerous Command"
    command_display = (
        command
        if show_full or display_width(command) <= 70
        else trim_to_width(command, 70)
    )
    labels = {
        ApprovalStatus.APPROVED.value: "Approve",
        ApprovalStatus.DENIED.value: "Deny",
        "view": "Show full command",
    }

    preview_lines = _wrap_panel_text(description, 60)
    preview_lines.extend(_wrap_panel_text(command_display, 60))
    for index, choice in enumerate(choices):
        prefix = "❯ " if index == selected else "  "
        preview_lines.extend(
            _wrap_panel_text(
                f"{prefix}{labels.get(choice, choice)}",
                60,
                subsequent_indent="  ",
            )
        )

    box_width = _panel_box_width(title, preview_lines)
    text_width = max(8, box_width - 2)
    lines: list[tuple[str, str]] = [
        ("class:approval-border", "╭─ "),
        ("class:approval-title", title),
        (
            "class:approval-border",
            " " + ("─" * max(0, box_width - display_width(title) - 3)) + "╮\n",
        ),
    ]
    _append_blank_panel_line(lines, "class:approval-border", box_width)
    for wrapped in _wrap_panel_text(description, text_width):
        _append_panel_line(
            lines,
            "class:approval-border",
            "class:approval-desc",
            wrapped,
            box_width,
        )
    for wrapped in _wrap_panel_text(command_display, text_width):
        _append_panel_line(
            lines,
            "class:approval-border",
            "class:approval-cmd",
            wrapped,
            box_width,
        )
    _append_blank_panel_line(lines, "class:approval-border", box_width)
    for index, choice in enumerate(choices):
        style = (
            "class:approval-selected"
            if index == selected
            else "class:approval-choice"
        )
        prefix = "❯ " if index == selected else "  "
        for wrapped in _wrap_panel_text(
            f"{prefix}{labels.get(choice, choice)}",
            text_width,
            subsequent_indent="  ",
        ):
            _append_panel_line(
                lines, "class:approval-border", style, wrapped, box_width
            )
    _append_blank_panel_line(lines, "class:approval-border", box_width)
    lines.append(
        ("class:approval-border", "╰" + ("─" * box_width) + "╯\n")
    )
    return _limit_approval_panel_lines(lines, _modal_max_height(), box_width)


def _split_visual_lines(
    fragments: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """Group formatted fragments into rows so height limits are cell-accurate."""
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
    flat: list[tuple[str, str]] = []
    for visual_line in visual_lines:
        for index, (style, text) in enumerate(visual_line):
            flat.append((style, text + ("\n" if index == len(visual_line) - 1 else "")))
    return flat


def _limit_approval_panel_lines(
    lines: list[tuple[str, str]],
    max_lines: int,
    box_width: int,
) -> list[tuple[str, str]]:
    """Trim long approval details without hiding the decision choices."""
    visual_lines = _split_visual_lines(lines)
    if len(visual_lines) <= max_lines:
        return lines

    closing = visual_lines[-1]
    choice_rows = [
        row
        for row in visual_lines[:-1]
        if any(
            style in {"class:approval-choice", "class:approval-selected"}
            for style, _text in row
        )
    ]
    # Keep every decision row when possible.  The body is the expendable part.
    available_head = max(1, max_lines - len(choice_rows) - 2)
    head = visual_lines[:available_head]
    tail = choice_rows[-max(1, max_lines - len(head) - 2) :]
    remaining = len(visual_lines) - len(head) - len(tail) - 1
    if remaining <= 0:
        return lines

    indicator = [
        ("class:approval-border", "│"),
        (
            "class:approval-desc",
            _pad_panel_text(
                f"  … {remaining} more line{'s' if remaining != 1 else ''}",
                box_width,
            ),
        ),
        ("class:approval-border", "│"),
    ]
    return _flatten_visual_lines([*head, indicator, *tail, closing])


def _pad_panel_text(text: str, box_width: int) -> str:
    from .terminal_text_layout import pad_to_width

    return pad_to_width(text, max(0, box_width))
