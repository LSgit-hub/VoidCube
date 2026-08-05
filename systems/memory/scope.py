"""Canonical ownership scope for memory records."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_OWNER_ID = "local-user"
DEFAULT_WORKSPACE_ID = "default"
# The interactive CLI and its Mem provider share this workspace.  Keep the
# integration value separate from the service-wide default used by other
# memory domains.
CLI_WORKSPACE_ID = "VoidCube"
GLOBAL_SCOPE_ID = "*"


def normalize_scope_value(value: object, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized or default


@dataclass(frozen=True, slots=True)
class MemoryScope:
    owner_id: str = DEFAULT_OWNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID

    @classmethod
    def create(
        cls,
        owner_id: object = None,
        workspace_id: object = None,
    ) -> "MemoryScope":
        return cls(
            owner_id=normalize_scope_value(owner_id, default=DEFAULT_OWNER_ID),
            workspace_id=normalize_scope_value(
                workspace_id,
                default=DEFAULT_WORKSPACE_ID,
            ),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
        }
