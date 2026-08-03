"""Render the supervisor and subagent section of the CLI status bar."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


StatusFragment = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CliMiddleStatusPorts:
    """Status snapshots and display preferences supplied by the CLI host."""

    supervisor_snapshot: Callable[[], Mapping[str, Any]]
    memory_llm: Callable[[], Mapping[str, Any]]
    ascii_mode: Callable[[], bool]
    subagent_snapshot: Callable[[], Mapping[str, Any]]


class CliMiddleStatusRuntime:
    """Own middle status formatting without owning supervisor state."""

    _BACKGROUND = "bg:#1a1a2e"

    def __init__(self, ports: CliMiddleStatusPorts) -> None:
        self.ports = ports

    def build(self) -> list[StatusFragment]:
        ports = self.ports
        fragments: list[StatusFragment] = []
        ascii_mode = ports.ascii_mode()
        supervisor: Mapping[str, Any] = {}
        scene = "idle"
        supervisor_active = False
        memory_usage: Mapping[str, Any] = {}

        try:
            supervisor = ports.supervisor_snapshot()
            scene = str(supervisor.get("scene") or "idle").strip() or "idle"
            supervisor_active = bool(supervisor.get("is_active"))
            memory_usage = dict(supervisor.get("mem_usage") or {})
        except Exception:
            pass

        try:
            memory_config = ports.memory_llm()
            model = memory_config.get("model")
            if model:
                memory_model = str(model).rsplit("/", 1)[-1]
                if memory_model.endswith(".gguf"):
                    memory_model = memory_model[:-5]
                if len(memory_model) > 20:
                    memory_model = memory_model[:17] + "..."
            else:
                provider = str(memory_config.get("provider") or "Mem")
                memory_model = provider if len(provider) <= 12 else provider[:9] + "..."

            icon = "[M]" if ascii_mode else "🧠"
            fragments.append((f"{self._BACKGROUND} #6B7280", icon))
            if supervisor_active and memory_model:
                fragments.extend(self._marquee(memory_model))
            else:
                fragments.append((f"{self._BACKGROUND} #7CC9A0 bold", memory_model))

            percent = memory_usage.get("context_percent")
            if percent is not None and percent > 0:
                if percent >= 80:
                    color = "#FF6B6B"
                elif percent >= 60:
                    color = "#FFD700"
                else:
                    color = "#8FBC8F"
                fragments.append((f"{self._BACKGROUND} {color} bold", f" {percent}%"))
            else:
                fragments.append((f"{self._BACKGROUND} #6B7280", " --"))
        except Exception:
            pass

        fragments.extend(self._scene_fragments(scene, ascii_mode, supervisor, bool(fragments)))

        try:
            subagent = ports.subagent_snapshot()
            if subagent.get("active"):
                if fragments:
                    fragments.append((f"{self._BACKGROUND} #4B5563", " · "))
                icon = "[SA]" if ascii_mode else "🧩"
                fragments.append((f"{self._BACKGROUND} #F59E0B", icon))
                fragments.append(
                    (f"{self._BACKGROUND} #F59E0B bold", f" {subagent.get('counts_label', '0')}")
                )
                preview = str(subagent.get("compact_preview") or "").strip()
                if preview:
                    fragments.append((f"{self._BACKGROUND} #94A3B8", f" {preview}"))
        except Exception:
            pass

        return fragments

    @classmethod
    def _marquee(cls, text: str) -> list[StatusFragment]:
        import time

        position = int(time.time() * 9) % (len(text) + 4)
        fragments: list[StatusFragment] = []
        for index, char in enumerate(text):
            if index == position - 1:
                color = "#FFFFFF"
            elif index == position:
                color = "#C0FFC0"
            elif index == position + 1:
                color = "#80C080"
            else:
                color = "#7CC9A0"
            fragments.append((f"{cls._BACKGROUND} {color} bold", char))
        return fragments

    @classmethod
    def _scene_fragments(
        cls,
        scene: str,
        ascii_mode: bool,
        supervisor: Mapping[str, Any],
        has_prefix: bool,
    ) -> Sequence[StatusFragment]:
        if not scene:
            if has_prefix:
                return [
                    (f"{cls._BACKGROUND} #4B5563", " · "),
                    (f"{cls._BACKGROUND} #6B7280", "[x]" if ascii_mode else "⚙️"),
                    (f"{cls._BACKGROUND} #6B7280", "离线"),
                ]
            return []

        if ascii_mode:
            icons = {
                "idle": "(-)", "planning": "(?)", "memory": "(M)",
                "drive": "(D)", "handoff": "(>)", "maintenance": "(M)",
                "body_switch": "(S)",
            }
        else:
            icons = {
                "idle": "💤", "planning": "🤔", "memory": "🧠", "drive": "💡",
                "handoff": "📤", "maintenance": "🔧", "body_switch": "🔄",
            }
        colors = {
            "idle": "#8B8682", "planning": "#E07362", "memory": "#7CC9A0",
            "drive": "#E2B04A", "handoff": "#A78BFA", "maintenance": "#60A5FA",
            "body_switch": "#C084FC",
        }
        labels = {
            "idle": "辅助", "planning": "规划", "memory": "记忆", "drive": "驱动",
            "handoff": "交接", "maintenance": "维护", "body_switch": "切换",
        }
        color = colors.get(scene, "#9CA3AF")
        fragments: list[StatusFragment] = []
        if has_prefix:
            fragments.append((f"{cls._BACKGROUND} #4B5563", " · "))
        fragments.append((f"{cls._BACKGROUND} {color}", icons.get(scene, "●")))
        fragments.append((f"{cls._BACKGROUND} {color}", labels.get(scene, scene)))
        error_count = supervisor.get("error_count", 0)
        if error_count > 0:
            fragments.append((f"{cls._BACKGROUND} #FF6B6B bold", f" !{error_count}"))
        return fragments
