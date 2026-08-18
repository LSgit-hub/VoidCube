"""Read-only terminal status and indicator widgets for the CLI adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, Window


@dataclass(frozen=True, slots=True)
class IndicatorWidgetPorts:
    """Read-only view callbacks used to compose non-modal TUI widgets."""

    spinner_fragments: Callable[[], AnyFormattedText]
    spinner_height: Callable[[], int]
    hint_fragments: Callable[[], AnyFormattedText]
    hint_height: Callable[[], int]
    input_rule_height: Callable[[str], int]
    image_fragments: Callable[[], AnyFormattedText]
    images_visible: Callable[[], bool]
    voice_fragments: Callable[[], AnyFormattedText]
    voice_visible: Callable[[], bool]
    autonomous_fragments: Callable[[], AnyFormattedText]
    autonomous_visible: Callable[[], bool]
    status_fragments: Callable[[], AnyFormattedText]
    status_visible: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class IndicatorWidgets:
    spinner: Window
    spacer: Window
    input_rule_top: Window
    input_rule_bottom: Window
    image_bar: Window
    voice_status_bar: ConditionalContainer
    autonomous_execution_panel: ConditionalContainer
    status_bar: ConditionalContainer


def build_indicator_widgets(*, ports: IndicatorWidgetPorts) -> IndicatorWidgets:
    """Build static prompt-toolkit containers over explicit view callbacks."""
    return IndicatorWidgets(
        spinner=Window(
            content=FormattedTextControl(ports.spinner_fragments),
            height=ports.spinner_height,
        ),
        spacer=Window(
            content=FormattedTextControl(ports.hint_fragments),
            height=ports.hint_height,
        ),
        input_rule_top=Window(
            char="─",
            height=lambda: ports.input_rule_height("top"),
            style="class:input-rule",
        ),
        input_rule_bottom=Window(
            char="─",
            height=lambda: ports.input_rule_height("bottom"),
            style="class:input-rule",
        ),
        image_bar=Window(
            content=FormattedTextControl(ports.image_fragments),
            height=Condition(ports.images_visible),
        ),
        voice_status_bar=ConditionalContainer(
            Window(FormattedTextControl(ports.voice_fragments), height=1),
            filter=Condition(ports.voice_visible),
        ),
        autonomous_execution_panel=ConditionalContainer(
            Window(
                content=FormattedTextControl(ports.autonomous_fragments),
                dont_extend_height=True,
            ),
            filter=Condition(ports.autonomous_visible),
        ),
        status_bar=ConditionalContainer(
            Window(
                content=FormattedTextControl(ports.status_fragments),
                height=1,
                wrap_lines=False,
            ),
            filter=Condition(ports.status_visible),
        ),
    )
