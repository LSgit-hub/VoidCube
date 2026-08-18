"""Assemble the interactive status bar from explicit display ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from VoidCube_cli.terminal_text_layout import display_width, trim_to_width


StatusFragment = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CliStatusBarPorts:
    """Status data and rendering callbacks supplied by the CLI host."""

    status_bar_visible: Callable[[], bool]
    model_picker_open: Callable[[], bool]
    snapshot: Callable[[], Mapping[str, Any]]
    terminal_width: Callable[[], int]
    agent_active: Callable[[], bool]
    middle_fragments: Callable[[bool], Sequence[StatusFragment]]
    git_fragments: Callable[[], Sequence[StatusFragment]]
    fallback_text: Callable[[], str]
    closing: Callable[[], bool] | None = None


class CliStatusBarRuntime:
    """Own status-bar fragment assembly without owning CLI state."""

    _BACKGROUND = "bg:#1a1a2e"

    def __init__(self, ports: CliStatusBarPorts) -> None:
        self.ports = ports

    def build(self) -> list[StatusFragment]:
        ports = self.ports
        if not ports.status_bar_visible() or ports.model_picker_open():
            return []

        try:
            if ports.closing is not None and ports.closing():
                return [
                    (
                        "class:status-bar-warn",
                        trim_to_width(" 退出中 ", ports.terminal_width()),
                    )
                ]
            snapshot = ports.snapshot()
            width = ports.terminal_width()
            active = ports.agent_active()
            percent = snapshot.get("context_percent")
            if percent is not None:
                if percent >= 80:
                    percent_color = "#FF6B6B"
                elif percent >= 60:
                    percent_color = "#FFD700"
                else:
                    percent_color = "#8FBC8F"
                percent_label = f"{percent}%"
            else:
                percent_color = "#8B8682"
                percent_label = "--"

            model_name = str(snapshot.get("model_short") or "Voidcube")
            left = self._model_fragments(
                model_name,
                percent_label,
                percent_color,
                active,
            )
            middle = list(ports.middle_fragments(active))
            git = list(ports.git_fragments())

            if git:
                return self._layout_with_git(left, middle, git, width)
            return self._layout_without_git(left, middle, width)
        except Exception:
            return [("class:status-bar", f" {ports.fallback_text()} ")]

    @classmethod
    def _model_fragments(
        cls,
        model_name: str,
        percent_label: str,
        percent_color: str,
        active: bool,
    ) -> list[StatusFragment]:
        if active and model_name:
            import time

            position = int(time.time() * 9) % (len(model_name) + 4)
            fragments: list[StatusFragment] = []
            for index, char in enumerate(model_name):
                if index == position - 1:
                    color = "#FFFFFF"
                elif index == position:
                    color = "#C0C0C0"
                elif index == position + 1:
                    color = "#808080"
                else:
                    color = "#1E40AF"
                fragments.append((f"{cls._BACKGROUND} {color} bold", char))
            fragments.append(("class:status-bar", "  "))
            fragments.append((f"{cls._BACKGROUND} {percent_color} bold", percent_label))
            return fragments

        return [
            (f"{cls._BACKGROUND} #1E40AF bold", model_name),
            ("class:status-bar", "  "),
            (f"{cls._BACKGROUND} {percent_color} bold", percent_label),
        ]

    @classmethod
    def _layout_with_git(
        cls,
        left: list[StatusFragment],
        middle: list[StatusFragment],
        git: list[StatusFragment],
        width: int,
    ) -> list[StatusFragment]:
        left_width = cls._fragment_width(left)
        middle_width = cls._fragment_width(middle)
        git_width = cls._fragment_width(git)
        available = width - left_width - git_width - 6

        if middle_width > 0 and available > 20:
            left_pad = max(1, (available - middle_width) // 2)
            right_pad = max(1, available - middle_width - left_pad)
            fragments = left.copy()
            fragments.append(("class:status-bar", " " * left_pad))
            fragments.extend(middle)
            fragments.append(("class:status-bar", " " * right_pad))
            fragments.extend(git)
        elif middle_width > 0 and available > 0:
            fragments = left.copy()
            fragments.append(("class:status-bar", "  "))
            fragments.extend(middle)
            fragments.append(("class:status-bar", "  "))
            fragments.extend(git)
        else:
            padding = width - left_width - git_width - 4
            fragments = left.copy()
            fragments.append(("class:status-bar", " " * padding if padding > 0 else "  --  "))
            fragments.extend(git)

        return cls._fit(fragments, width)

    @classmethod
    def _layout_without_git(
        cls,
        left: list[StatusFragment],
        middle: list[StatusFragment],
        width: int,
    ) -> list[StatusFragment]:
        fragments = left.copy()
        if middle:
            middle_width = cls._fragment_width(middle)
            left_width = cls._fragment_width(left)
            padding = width - left_width - middle_width - 4
            if padding > 4:
                fragments.append(("class:status-bar", " " * (padding // 2)))
                fragments.extend(middle)
                fragments.append(("class:status-bar", " " * (padding - padding // 2)))
            elif padding > 0:
                fragments.append(("class:status-bar", "  "))
                fragments.extend(middle)
        return cls._fit(fragments, width)

    @classmethod
    def _fragment_width(cls, fragments: Sequence[StatusFragment]) -> int:
        return sum(cls._display_width(text) for _, text in fragments)

    @classmethod
    def _fit(cls, fragments: list[StatusFragment], width: int) -> list[StatusFragment]:
        if cls._fragment_width(fragments) <= width:
            return fragments
        return [("class:status-bar", cls._trim("".join(text for _, text in fragments), width))]

    @staticmethod
    def _display_width(text: str) -> int:
        return display_width(text)

    @classmethod
    def _trim(cls, text: str, max_width: int) -> str:
        return trim_to_width(text, max_width)
