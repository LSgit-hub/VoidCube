"""Server-owned memory-domain authorization policy.

The caller declares its actor identity and requested domain.  It never declares
its own permissions: this module is the single authority for domain access.
"""

from __future__ import annotations

from enum import Enum


class MemoryDomain(str, Enum):
    AGENT_INTERACTION = "agent_interaction"
    COMPANION = "companion"
    EVOLUTION = "evolution"


class MemoryActor(str, Enum):
    API_A = "api_a"
    STELLAR_COMPANION = "stellar_companion"
    STELLAR_AUTO = "stellar_auto"
    MEMORY_MAINTENANCE = "memory_maintenance"
    GOVERNOR = "governor"
    EXECUTION = "execution"


DEFAULT_MEMORY_ACTOR = MemoryActor.API_A
DEFAULT_MEMORY_DOMAIN = MemoryDomain.AGENT_INTERACTION

_READ_DOMAINS: dict[MemoryActor, frozenset[MemoryDomain]] = {
    MemoryActor.API_A: frozenset({MemoryDomain.AGENT_INTERACTION}),
    MemoryActor.STELLAR_COMPANION: frozenset(
        {MemoryDomain.AGENT_INTERACTION, MemoryDomain.COMPANION}
    ),
    MemoryActor.STELLAR_AUTO: frozenset({MemoryDomain.EVOLUTION}),
    MemoryActor.MEMORY_MAINTENANCE: frozenset(MemoryDomain),
    MemoryActor.GOVERNOR: frozenset({MemoryDomain.EVOLUTION}),
    MemoryActor.EXECUTION: frozenset({MemoryDomain.EVOLUTION}),
}

_WRITE_DOMAINS: dict[MemoryActor, frozenset[MemoryDomain]] = {
    MemoryActor.API_A: frozenset({MemoryDomain.AGENT_INTERACTION}),
    MemoryActor.STELLAR_COMPANION: frozenset({MemoryDomain.COMPANION}),
    MemoryActor.STELLAR_AUTO: frozenset({MemoryDomain.EVOLUTION}),
    MemoryActor.MEMORY_MAINTENANCE: frozenset(MemoryDomain),
    MemoryActor.GOVERNOR: frozenset({MemoryDomain.EVOLUTION}),
    MemoryActor.EXECUTION: frozenset({MemoryDomain.EVOLUTION}),
}


class MemoryDomainAccessError(ValueError):
    """Raised when an actor requests a domain outside its fixed policy."""


def authorize_write(
    actor: MemoryActor | str,
    domain: MemoryDomain | str,
) -> MemoryDomain:
    resolved_actor = MemoryActor(actor)
    resolved_domain = MemoryDomain(domain)
    if resolved_domain not in _WRITE_DOMAINS[resolved_actor]:
        raise MemoryDomainAccessError(
            f"{resolved_actor.value} cannot write memory domain {resolved_domain.value}"
        )
    return resolved_domain


def authorize_identity_experience_verification(
    actor: MemoryActor | str,
    domain: MemoryDomain | str,
) -> MemoryDomain:
    """Authorize the narrow identity-verification write capability."""
    resolved_actor = MemoryActor(actor)
    resolved_domain = MemoryDomain(domain)
    if (
        resolved_actor is MemoryActor.STELLAR_COMPANION
        and resolved_domain is MemoryDomain.AGENT_INTERACTION
    ):
        return resolved_domain
    return authorize_write(resolved_actor, resolved_domain)


def authorize_read(
    actor: MemoryActor | str,
    requested_domains: tuple[MemoryDomain | str, ...] | list[MemoryDomain | str] | None,
) -> tuple[MemoryDomain, ...]:
    resolved_actor = MemoryActor(actor)
    allowed = _READ_DOMAINS[resolved_actor]
    requested = (
        tuple(MemoryDomain(item) for item in requested_domains)
        if requested_domains
        else tuple(sorted(allowed, key=lambda item: item.value))
    )
    requested = tuple(dict.fromkeys(requested))
    denied = tuple(domain for domain in requested if domain not in allowed)
    if denied:
        names = ", ".join(domain.value for domain in denied)
        raise MemoryDomainAccessError(
            f"{resolved_actor.value} cannot read memory domain(s): {names}"
        )
    return requested


def domain_values(domains: tuple[MemoryDomain, ...]) -> tuple[str, ...]:
    return tuple(domain.value for domain in domains)
