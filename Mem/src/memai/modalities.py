from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence

from .schema import TranscriptTurn


@dataclass(slots=True)
class MemorySignal:
    signal_id: str
    modality: str
    timestamp: datetime
    content: str
    speaker: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_turn(self) -> TranscriptTurn:
        return TranscriptTurn(
            turn_id=self.signal_id,
            speaker=self.speaker or self.modality,
            text=self.content,
            timestamp=self.timestamp,
        )


class ModalityAdapter(Protocol):
    modality: str

    def adapt(self, items: Sequence[Any]) -> list[MemorySignal]: ...


class TextTurnAdapter:
    modality = "text"

    def adapt(self, items: Sequence[TranscriptTurn]) -> list[MemorySignal]:
        return [
            MemorySignal(
                signal_id=item.turn_id,
                modality=self.modality,
                timestamp=item.timestamp,
                content=item.text,
                speaker=item.speaker,
                metadata={"source": "transcript_turn"},
            )
            for item in items
        ]


class AudioSegmentAdapter:
    modality = "audio"

    def adapt(self, items: Sequence[dict[str, Any]]) -> list[MemorySignal]:
        return [
            MemorySignal(
                signal_id=item["segment_id"],
                modality=self.modality,
                timestamp=item["timestamp"],
                content=item["transcript"],
                speaker=item.get("speaker"),
                metadata={
                    "source": "audio_segment",
                    "duration_seconds": item.get("duration_seconds"),
                    "confidence": item.get("confidence"),
                },
            )
            for item in items
        ]


class ImageCaptionAdapter:
    modality = "image"

    def adapt(self, items: Sequence[dict[str, Any]]) -> list[MemorySignal]:
        return [
            MemorySignal(
                signal_id=item["image_id"],
                modality=self.modality,
                timestamp=item["timestamp"],
                content=item["caption"],
                speaker=item.get("speaker"),
                metadata={
                    "source": "image_caption",
                    "objects": item.get("objects", []),
                    "location": item.get("location"),
                },
            )
            for item in items
        ]
