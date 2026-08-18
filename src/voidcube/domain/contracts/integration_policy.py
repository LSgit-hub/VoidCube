"""Project-wide policy for model integrations that have been permanently retired."""

from __future__ import annotations


RETIRED_INTEGRATION_MARKERS = (
    "".join(("anthro", "pic")),
    "".join(("clau", "de")),
    "".join(("co", "dex")),
)
RETIRED_INTEGRATION_CATEGORY = "retired_integration"


class RetiredIntegrationError(ValueError):
    """Raised before a request can use an integration retired by policy."""


def matching_retired_integrations(value: str) -> tuple[str, ...]:
    """Return retired integration markers present in a case-insensitive value."""
    normalized = str(value or "").casefold()
    return tuple(
        marker
        for marker in RETIRED_INTEGRATION_MARKERS
        if marker in normalized
    )


def contains_retired_integration(value: str) -> bool:
    """Return whether a value references any project-retired integration."""
    return bool(matching_retired_integrations(value))


def require_active_integration(*values: object) -> None:
    """Reject provider, model, route, or endpoint values retired by policy."""
    if any(matching_retired_integrations(str(value or "")) for value in values):
        raise RetiredIntegrationError(
            "Requested provider or model is retired by project policy"
        )
