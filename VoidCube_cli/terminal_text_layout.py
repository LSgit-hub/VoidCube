"""Terminal-cell width helpers shared by CLI presentation adapters."""

from __future__ import annotations


def display_width(text: str) -> int:
    try:
        from prompt_toolkit.utils import get_cwidth

        return get_cwidth(text or "")
    except Exception:
        return len(text or "")


def trim_to_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text

    ellipsis = "..."
    ellipsis_width = display_width(ellipsis)
    if max_width <= ellipsis_width:
        return ellipsis[:max_width]

    output: list[str] = []
    current_width = 0
    for char in text:
        char_width = display_width(char)
        if current_width + char_width + ellipsis_width > max_width:
            break
        output.append(char)
        current_width += char_width
    return "".join(output).rstrip() + ellipsis


def pad_to_width(text: str, width: int) -> str:
    text = trim_to_width(text, width)
    return text + (" " * max(0, width - display_width(text)))


__all__ = ["display_width", "pad_to_width", "trim_to_width"]
