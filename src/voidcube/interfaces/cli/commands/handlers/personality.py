"""Personality command mutation with explicit prompt and agent ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class PersonalityCommandPorts:
    personalities: Callable[[], Mapping[str, object]]
    set_system_prompt: Callable[[str], None]
    reset_agent: Callable[[], None]
    save_system_prompt: Callable[[str], bool]
    emit: Callable[[str], None]


def resolve_personality_prompt(value: object) -> str:
    """Convert a string or structured personality definition into a prompt."""
    if isinstance(value, Mapping):
        parts = [str(value.get("system_prompt") or "")]
        if value.get("tone"):
            parts.append(f"Tone: {value['tone']}")
        if value.get("style"):
            parts.append(f"Style: {value['style']}")
        return "\n".join(part for part in parts if part)
    return str(value)


def handle_personality_command(
    request: ParsedCliCommand,
    *,
    ports: PersonalityCommandPorts,
) -> None:
    """List, clear, or persist a personality overlay without owning the host."""
    personality_name = request.arguments.strip().lower()
    personalities = ports.personalities()

    if not personality_name:
        _render_personalities(personalities, emit=ports.emit)
        return

    if personality_name in {"none", "default", "neutral"}:
        ports.set_system_prompt("")
        ports.reset_agent()
        if ports.save_system_prompt(""):
            ports.emit("(^_^)b Personality cleared (saved to config)")
        else:
            ports.emit("(^_^) Personality cleared (session only)")
        ports.emit("  No personality overlay — using base agent behavior.")
        return

    if personality_name not in personalities:
        ports.emit(f"(._.) Unknown personality: {personality_name}")
        ports.emit(f"  Available: none, {', '.join(personalities.keys())}")
        return

    value = personalities[personality_name]
    system_prompt = resolve_personality_prompt(value)
    ports.set_system_prompt(system_prompt)
    ports.reset_agent()
    if ports.save_system_prompt(system_prompt):
        ports.emit(f"(^_^)b Personality set to '{personality_name}' (saved to config)")
    else:
        ports.emit(f"(^_^) Personality set to '{personality_name}' (session only)")
    preview = f"{system_prompt[:60]}{'...' if len(system_prompt) > 60 else ''}"
    ports.emit(f'  "{preview}"')


def _render_personalities(
    personalities: Mapping[str, object],
    *,
    emit: Callable[[str], None],
) -> None:
    emit("")
    emit("+" + "-" * 50 + "+")
    emit("|" + " " * 12 + "(^o^)/ Personalities" + " " * 15 + "|")
    emit("+" + "-" * 50 + "+")
    emit("")
    emit(f"  {'none':<12} - (no personality overlay)")
    for name, prompt in personalities.items():
        if isinstance(prompt, Mapping):
            preview = prompt.get("description") or str(prompt.get("system_prompt") or "")[:50]
        else:
            preview = str(prompt)[:50]
        emit(f"  {name:<12} - {preview}")
    emit("")
    emit("  Usage: /personality <name>")
    emit("")
