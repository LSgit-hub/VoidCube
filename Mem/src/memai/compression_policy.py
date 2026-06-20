from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence

from .schema import Arc, Epoch, Scene, Status, UTC


@dataclass(slots=True)
class CompressionActionSpec:
    action_type: str
    source_ids: list[str]
    reason: str
    target_layer: str

    def to_dict(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "source_ids": list(self.source_ids),
            "reason": self.reason,
            "target_layer": self.target_layer,
        }


@dataclass(slots=True)
class CompressionPolicyDecision:
    compression_actions: list[CompressionActionSpec]
    dormant_arc_ids: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "compression_actions": [
                item.to_dict() for item in self.compression_actions
            ],
            "dormant_arc_ids": list(self.dormant_arc_ids),
            "notes": list(self.notes),
        }


class CompressionPolicy(Protocol):
    name: str

    def decide(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime,
    ) -> CompressionPolicyDecision: ...


class AdaptiveCompressionClient(Protocol):
    def decide_compression(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime,
    ) -> CompressionPolicyDecision | dict[str, Any]: ...


class HeuristicCompressionPolicy:
    name = "heuristic"

    def __init__(
        self,
        *,
        scene_window_days: int = 30,
        arc_window_days: int = 180,
        dormancy_days: int = 30,
        epoch_window_days: int = 365,
    ) -> None:
        self.scene_window_days = scene_window_days
        self.arc_window_days = arc_window_days
        self.dormancy_days = dormancy_days
        self.epoch_window_days = epoch_window_days

    def decide(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime,
    ) -> CompressionPolicyDecision:
        compression_actions: list[CompressionActionSpec] = []
        dormant_arc_ids: list[str] = []
        notes: list[str] = []

        for scene in scenes:
            age = reference_time - scene.timespan_end
            if age > timedelta(days=self.scene_window_days):
                compression_actions.append(
                    CompressionActionSpec(
                        action_type="compress_scene",
                        source_ids=[scene.id],
                        reason=f"scene older than {self.scene_window_days} days",
                        target_layer="arc",
                    )
                )

        for arc in arcs:
            age = reference_time - arc.timespan_end
            if age > timedelta(days=self.arc_window_days):
                compression_actions.append(
                    CompressionActionSpec(
                        action_type="compress_arc",
                        source_ids=[arc.id],
                        reason=f"arc older than {self.arc_window_days} days",
                        target_layer="epoch",
                    )
                )
            elif (
                age > timedelta(days=self.dormancy_days) and arc.status == Status.ACTIVE
            ):
                dormant_arc_ids.append(arc.id)

        for epoch in epochs:
            age = reference_time - epoch.timespan_end
            if age > timedelta(days=self.epoch_window_days):
                compression_actions.append(
                    CompressionActionSpec(
                        action_type="compress_epoch",
                        source_ids=[epoch.id],
                        reason=f"epoch older than {self.epoch_window_days} days",
                        target_layer="epoch",
                    )
                )

        notes.append(
            f"policy={self.name}; scene_window={self.scene_window_days}; arc_window={self.arc_window_days}; dormancy_window={self.dormancy_days}; epoch_window={self.epoch_window_days}"
        )
        return CompressionPolicyDecision(
            compression_actions=compression_actions,
            dormant_arc_ids=sorted(set(dormant_arc_ids)),
            notes=notes,
        )


class AdaptiveCompressionPolicyAdapter:
    name = "adaptive"

    def __init__(self, client: AdaptiveCompressionClient) -> None:
        self.client = client
        self.fallback = HeuristicCompressionPolicy()

    def decide(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime,
    ) -> CompressionPolicyDecision:
        payload = self.client.decide_compression(
            scenes,
            arcs,
            epochs,
            reference_time.astimezone(UTC),
        )
        if isinstance(payload, CompressionPolicyDecision):
            return payload

        fallback = self.fallback.decide(scenes, arcs, epochs, reference_time)
        actions_payload = payload.get("compression_actions", [])
        actions = [
            CompressionActionSpec(
                action_type=item["action_type"],
                source_ids=list(item.get("source_ids", [])),
                reason=item["reason"],
                target_layer=item["target_layer"],
            )
            for item in actions_payload
        ]
        return CompressionPolicyDecision(
            compression_actions=actions or fallback.compression_actions,
            dormant_arc_ids=list(
                payload.get("dormant_arc_ids", fallback.dormant_arc_ids)
            ),
            notes=list(payload.get("notes", fallback.notes)),
        )
