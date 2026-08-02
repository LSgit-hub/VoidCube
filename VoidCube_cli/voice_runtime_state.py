"""Mutable voice runtime state owned by one CLI adapter instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class CliVoiceRuntimeState:
    """Cross-thread display state for one terminal voice adapter."""

    lock: Lock = field(default_factory=Lock)
    mode: bool = False
    recording: bool = False
    processing: bool = False
    continuous: bool = False
    no_speech_count: int = 0
    stop_continuous: bool = False
