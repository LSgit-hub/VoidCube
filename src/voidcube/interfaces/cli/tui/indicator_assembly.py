"""Assemble CLI-owned indicator projections for the interactive TUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.formatted_text import AnyFormattedText

from .image_indicator import (
    CliTuiImageIndicatorPorts,
    CliTuiImageIndicatorRuntime,
)
from .dynamic_text_runtime import TuiDynamicTextRuntime


@dataclass(frozen=True, slots=True)
class CliTuiIndicatorPorts:
    """Callbacks consumed by the generic indicator widget factory."""

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
class CliTuiIndicatorAssemblyPorts:
    """CLI projections needed to compose the indicator callback bundle."""

    dynamic_text: TuiDynamicTextRuntime
    layout_input_rule_height: Callable[[str], int]
    image: CliTuiImageIndicatorPorts
    voice_fragments: Callable[[], AnyFormattedText]
    voice_visible: Callable[[], bool]
    autonomous_fragments: Callable[[], AnyFormattedText]
    autonomous_visible: Callable[[], bool]
    status_fragments: Callable[[], AnyFormattedText]
    status_visible: Callable[[], bool]


class CliTuiIndicatorAssemblyRuntime:
    """Compose indicator projections without reading CLI host state."""

    def __init__(self, ports: CliTuiIndicatorAssemblyPorts) -> None:
        self.ports = ports

    def build(self) -> CliTuiIndicatorPorts:
        ports = self.ports
        image_runtime = CliTuiImageIndicatorRuntime(ports.image)
        dynamic_text = ports.dynamic_text
        return CliTuiIndicatorPorts(
            spinner_fragments=dynamic_text.spinner_fragments,
            spinner_height=dynamic_text.spinner_widget_height,
            hint_fragments=dynamic_text.hint_fragments,
            hint_height=dynamic_text.hint_height,
            input_rule_height=ports.layout_input_rule_height,
            image_fragments=image_runtime.fragments,
            images_visible=image_runtime.visible,
            voice_fragments=ports.voice_fragments,
            voice_visible=ports.voice_visible,
            autonomous_fragments=ports.autonomous_fragments,
            autonomous_visible=ports.autonomous_visible,
            status_fragments=ports.status_fragments,
            status_visible=ports.status_visible,
        )
