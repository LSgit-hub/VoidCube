"""Stable contracts shared by skill catalogs, sources, and installers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillMeta:
    name: str
    description: str
    source: str
    identifier: str
    trust_level: str
    repo: str | None = None
    path: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillBundle:
    name: str
    files: dict[str, str | bytes]
    source: str
    identifier: str
    trust_level: str
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["SkillBundle", "SkillMeta"]
