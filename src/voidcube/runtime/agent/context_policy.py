"""Shared context sizing and compression budget policy.

The policy is deliberately small: it centralizes model context resolution and
the derived token budgets consumed by the compressor and ``@`` references.
Adaptive model-specific tuning is represented but disabled by default.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import re

from ...infrastructure.providers.model_metadata import (
    MINIMUM_CONTEXT_LENGTH,
    resolve_model_context_length,
)


@dataclass(frozen=True, slots=True)
class ContextCompressionPolicy:
    """Resolved context limits and all compression-related derived budgets."""

    model: str
    context_length: int
    threshold_percent: float = 0.50
    target_ratio: float = 0.20
    protect_last_n: int = 20
    source: str = "fallback"
    adaptive_by_model: bool = False

    @property
    def threshold_tokens(self) -> int:
        return max(int(self.context_length * self.threshold_percent), MINIMUM_CONTEXT_LENGTH)

    @property
    def tail_token_budget(self) -> int:
        return int(self.threshold_tokens * self.target_ratio)

    @property
    def hard_reference_limit(self) -> int:
        return max(1, int(self.context_length * 0.50))

    @property
    def soft_reference_limit(self) -> int:
        return max(1, int(self.context_length * 0.25))

    @classmethod
    def for_model(
        cls,
        model: str,
        *,
        threshold_percent: float = 0.50,
        target_ratio: float = 0.20,
        protect_last_n: int = 20,
        base_url: str = "",
        api_key: str = "",
        config_context_length: int | None = None,
        provider: str = "",
        adaptive_by_model: bool = False,
        startup_probe: bool = False,
    ) -> "ContextCompressionPolicy":
        context_length, source = resolve_model_context_length(
            model,
            base_url=base_url,
            api_key=api_key,
            config_context_length=config_context_length,
            provider=provider,
            startup_probe=startup_probe,
        )
        return cls(
            model=model,
            context_length=context_length,
            threshold_percent=max(0.0, min(float(threshold_percent), 1.0)),
            target_ratio=max(0.10, min(float(target_ratio), 0.80)),
            protect_last_n=max(0, int(protect_last_n)),
            source=source,
            adaptive_by_model=bool(adaptive_by_model),
        )

    @property
    def detection_known(self) -> bool:
        """Whether the length came from a provider/configured source."""
        return self.source not in {
            "fallback",
            "fallback_endpoint",
            "fallback_local_unavailable",
            "probe",
            "probe_tier",
        }

    def with_context_length(
        self,
        context_length: int,
        *,
        model: str | None = None,
        source: str = "probe",
    ) -> "ContextCompressionPolicy":
        return replace(
            self,
            model=model or self.model,
            context_length=max(1, int(context_length)),
            source=source,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "context_length": self.context_length,
            "threshold_percent": self.threshold_percent,
            "threshold_tokens": self.threshold_tokens,
            "target_ratio": self.target_ratio,
            "tail_token_budget": self.tail_token_budget,
            "protect_last_n": self.protect_last_n,
            "source": self.source,
            "adaptive_by_model": self.adaptive_by_model,
        }


def configured_context_length(
    config: dict[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str = "",
) -> int | None:
    """Read provider/model context overrides from the normalized config."""
    providers = config.get("providers") if isinstance(config, dict) else None
    if not isinstance(providers, dict):
        return None
    entry = providers.get(provider)
    if not isinstance(entry, dict):
        for candidate in providers.values():
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("base_url") or "").rstrip("/") == base_url.rstrip("/"):
                entry = candidate
                break
    if not isinstance(entry, dict):
        return None
    # Accept the canonical map first, then the two structures emitted by
    # provider registries and model pickers.  All forms remain model-specific:
    # a provider-wide context value must never leak across model switches.
    model_contexts = entry.get("model_context_lengths")
    if not isinstance(model_contexts, dict):
        model_contexts = entry.get("context_lengths")
    candidate = model_contexts.get(model) if isinstance(model_contexts, dict) else None

    if candidate is None:
        model_entries = entry.get("models")
        if isinstance(model_entries, dict):
            model_entry = model_entries.get(model)
            if isinstance(model_entry, dict):
                candidate = _context_value_from_mapping(model_entry)
            elif model_entry is not None:
                candidate = model_entry

    if candidate is None:
        catalog = entry.get("model_catalog")
        catalog_models = catalog.get("models") if isinstance(catalog, dict) else None
        if isinstance(catalog_models, list):
            for model_entry in catalog_models:
                if not isinstance(model_entry, dict):
                    continue
                model_id = str(
                    model_entry.get("id")
                    or model_entry.get("name")
                    or model_entry.get("model")
                    or ""
                ).strip()
                if model_id == model:
                    candidate = _context_value_from_mapping(model_entry)
                    break

    if candidate is None:
        capabilities = entry.get("model_capabilities")
        model_capability = capabilities.get(model) if isinstance(capabilities, dict) else None
        if isinstance(model_capability, dict):
            candidate = _context_value_from_mapping(model_capability)

    if candidate is None:
        return None
    try:
        if isinstance(candidate, str):
            raw = candidate.strip().replace(",", "").replace(" ", "")
            match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMbB])?", raw)
            if match:
                multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
                    (match.group(2) or "").lower(), 1
                )
                value = int(float(match.group(1)) * multiplier)
            else:
                value = int(raw)
        else:
            value = int(candidate)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _context_value_from_mapping(value: dict[str, Any]) -> Any:
    """Return a context field from a model metadata/config mapping."""
    for key in (
        "context_length",
        "context_window",
        "context_window_size",
        "max_context_tokens",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
        "input_token_limit",
        "prompt_token_limit",
    ):
        if key in value and value[key] not in (None, ""):
            return value[key]
    return None
