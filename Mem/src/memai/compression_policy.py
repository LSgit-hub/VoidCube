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
    """Legacy maintenance policy with dormancy detection only.

    Tier 1 -> Tier 2 is the only content-compression boundary.  The former
    age-based Scene/Arc/Epoch actions remain part of the custom policy
    protocol, but the default policy no longer schedules them.
    """

    name = "heuristic"

    def __init__(
        self,
        *,
        dormancy_days: int = 30,
    ) -> None:
        self.dormancy_days = dormancy_days

    def decide(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime,
    ) -> CompressionPolicyDecision:
        dormant_arc_ids: list[str] = []
        notes: list[str] = []

        for arc in arcs:
            age = reference_time - arc.timespan_end
            if age > timedelta(days=self.dormancy_days) and arc.status == Status.ACTIVE:
                dormant_arc_ids.append(arc.id)

        notes.append(
            f"policy={self.name}; compression_actions=disabled; dormancy_window={self.dormancy_days}"
        )
        return CompressionPolicyDecision(
            compression_actions=[],
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
