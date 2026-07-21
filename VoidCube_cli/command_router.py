from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any, Literal

from VoidCube_cli.commands import resolve_command


DynamicRouteKind = Literal[
    "quick_exec",
    "quick_alias",
    "quick_invalid",
    "plugin",
    "skill",
    "redirect",
    "ambiguous",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ParsedCliCommand:
    original: str
    normalized: str
    base_token: str
    name: str
    canonical: str
    arguments: str
    suffix: str


@dataclass(frozen=True, slots=True)
class DynamicCommandRoute:
    kind: DynamicRouteKind
    request: ParsedCliCommand
    executable: str = ""
    redirect_command: str = ""
    quick_type: str = ""
    matches: tuple[str, ...] = ()


def looks_like_slash_command(text: str) -> bool:
    """Return whether text is a slash command rather than an absolute path."""
    if not text or not text.startswith("/"):
        return False
    first_word = text.split()[0]
    return "/" not in first_word[1:]


def parse_cli_command(command: str) -> ParsedCliCommand:
    """Normalize a CLI command while preserving argument spelling and spacing."""
    original = command.strip()
    normalized = original.lower()
    parts = original.split(maxsplit=1)
    raw_base = parts[0] if parts else ""
    base_token = raw_base.lower()
    name = base_token.lstrip("/")
    command_def = resolve_command(name)
    canonical = command_def.name if command_def else name
    arguments = parts[1].strip() if len(parts) > 1 else ""
    suffix = original[len(raw_base) :] if raw_base else ""
    return ParsedCliCommand(
        original=original,
        normalized=normalized,
        base_token=base_token,
        name=name,
        canonical=canonical,
        arguments=arguments,
        suffix=suffix,
    )


def slow_command_status(command: str | ParsedCliCommand) -> str:
    request = command if isinstance(command, ParsedCliCommand) else parse_cli_command(command)
    normalized = request.normalized
    if normalized.startswith("/skills search"):
        return "Searching skills..."
    if normalized.startswith("/skills browse"):
        return "Loading skills..."
    if normalized.startswith("/skills inspect"):
        return "Inspecting skill..."
    if normalized.startswith("/skills install"):
        return "Installing skill..."
    if request.canonical == "skills":
        return "Processing skills command..."
    if request.canonical == "reload-mcp":
        return "Reloading MCP servers..."
    if request.canonical == "browser":
        return "Configuring browser..."
    return "Processing command..."


def resolve_dynamic_command(
    request: ParsedCliCommand,
    *,
    quick_commands: Mapping[str, Mapping[str, Any]],
    plugin_names: Set[str],
    skill_commands: Mapping[str, Any],
    known_commands: Set[str],
) -> DynamicCommandRoute:
    """Resolve non-built-in CLI command sources in execution priority order."""
    quick = quick_commands.get(request.name)
    if quick is not None:
        quick_type = str(quick.get("type") or "")
        if quick_type == "exec":
            executable = str(quick.get("command") or "")
            return DynamicCommandRoute(
                "quick_exec" if executable else "quick_invalid",
                request,
                executable=executable,
                quick_type=quick_type,
            )
        if quick_type == "alias":
            target = str(quick.get("target") or "").strip()
            if target:
                target = target if target.startswith("/") else f"/{target}"
                return DynamicCommandRoute(
                    "quick_alias",
                    request,
                    redirect_command=f"{target}{request.suffix}",
                    quick_type=quick_type,
                )
        return DynamicCommandRoute(
            "quick_invalid",
            request,
            quick_type=quick_type,
        )

    if request.name in plugin_names:
        return DynamicCommandRoute("plugin", request)

    if request.base_token in skill_commands:
        return DynamicCommandRoute("skill", request)

    matches = _prefix_matches(
        request.base_token,
        known_commands | set(skill_commands),
    )
    if len(matches) == 1:
        full_name = matches[0]
        if full_name != request.base_token:
            return DynamicCommandRoute(
                "redirect",
                request,
                redirect_command=f"{full_name}{request.suffix}",
            )
        return DynamicCommandRoute("unknown", request)
    if len(matches) > 1:
        return DynamicCommandRoute("ambiguous", request, matches=matches)
    return DynamicCommandRoute("unknown", request)


def _prefix_matches(typed: str, known: Set[str]) -> tuple[str, ...]:
    matches = sorted(command for command in known if command.startswith(typed))
    if len(matches) <= 1:
        return tuple(matches)
    exact = [command for command in matches if command == typed]
    if len(exact) == 1:
        return tuple(exact)
    min_length = min(len(command) for command in matches)
    shortest = [command for command in matches if len(command) == min_length]
    return tuple(shortest) if len(shortest) == 1 else tuple(matches)
