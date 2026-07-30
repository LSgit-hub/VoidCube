"""Mutable voice runtime state owned by one CLI adapter instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any


@dataclass
class CliVoiceRuntimeState:
    """Cross-thread state for the legacy terminal voice transport."""

    lock: Lock = field(default_factory=Lock)
    mode: bool = False
    tts: bool = False
    recorder: Any = None
    recording: bool = False
    processing: bool = False
    continuous: bool = False
    tts_done: Event = field(default_factory=Event)
    no_speech_count: int = 0
    stop_continuous: bool = False

    def __post_init__(self) -> None:
        self.tts_done.set()
