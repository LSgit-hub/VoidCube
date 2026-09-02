"""Provider fallback-chain policy for auxiliary calls."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from ...domain.contracts.integration_policy import RetiredIntegrationError
from .auxiliary_policy import normalize_auxiliary_provider


def provider_chain(candidates: Iterable[tuple[str, Callable[[], tuple[Any, str | None]]]]) -> list[tuple[str, Callable[[], tuple[Any, str | None]]]]:
    """Materialize a provider chain at call time so adapters remain patchable."""
    return list(candidates)


def fallback_chain_label(
    provider: str,
    *,
    named_custom_provider: Callable[[str], Any] | None = None,
) -> str:
    normalized = normalize_auxiliary_provider(provider)
    if normalized in {"openrouter", "nous", "api-key"}:
        return normalized
    if normalized in {"custom", "local/custom"}:
        return "local/custom"
    if named_custom_provider is not None:
        try:
            if named_custom_provider(normalized):
                return "local/custom"
        except Exception:
            pass
    return "api-key"


def try_provider_fallback(
    failed_provider: str,
    *,
    task: str | None,
    reason: str,
    chain: Iterable[tuple[str, Callable[[], tuple[Any, str | None]]]],
    named_custom_provider: Callable[[str], Any] | None = None,
    log: Any,
) -> tuple[Any, str | None, str]:
    skip_label = fallback_chain_label(
        failed_provider,
        named_custom_provider=named_custom_provider,
    )
    tried: list[str] = []
    for label, try_fn in chain:
        if label == skip_label:
            continue
        try:
            client, model = try_fn()
        except RetiredIntegrationError:
            # A configured fallback can become retired between config refreshes.
            # Treat that candidate as unavailable and continue through the chain;
            # retired-policy failures must never abort auxiliary summarization.
            tried.append(f"{label} (retired)")
            log.warning(
                "Auxiliary %s: skipping retired fallback candidate %s",
                task or "call",
                label,
            )
            continue
        if client is not None:
            log.info(
                "Auxiliary %s: %s on %s - falling back to %s (%s)",
                task or "call",
                reason,
                failed_provider,
                label,
                model or "default",
            )
            return client, model, label
        tried.append(label)
    log.warning(
        "Auxiliary %s: %s on %s and no fallback available (tried: %s)",
        task or "call",
        reason,
        failed_provider,
        ", ".join(tried),
    )
    return None, None, ""


__all__ = ["fallback_chain_label", "provider_chain", "try_provider_fallback"]
