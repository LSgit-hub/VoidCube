"""UI-independent artifacts produced by application capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Artifact:
    """A concrete user-consumable file or media result."""

    kind: str
    uri: str
    mime_type: str = ""
    title: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        uri = str(self.uri or "").strip()
        if not kind:
            raise ValueError("artifact kind is required")
        if not uri:
            raise ValueError("artifact uri is required")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "mime_type", str(self.mime_type or "").strip())
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


__all__ = ["Artifact"]
