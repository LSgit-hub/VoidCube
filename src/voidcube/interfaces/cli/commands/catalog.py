"""Slash command definitions and autocomplete for the Voidcube CLI.

Central registry for all slash commands. Every consumer -- CLI help, gateway
dispatch, autocomplete -- derives its data from ``COMMAND_REGISTRY``.

To add a command: add a ``CommandDef`` entry to ``COMMAND_REGISTRY``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# prompt_toolkit is an optional CLI dependency — only needed for
# SlashCommandCompleter and SlashCommandAutoSuggest.  Gateway and test
# environments that lack it must still be able to import this module
# for resolve_command, gateway_help_lines, and COMMAND_REGISTRY.
try:
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.completion import Completer, Completion
except ImportError:  # pragma: no cover
    AutoSuggest = object  # type: ignore[assignment,misc]
    Completer = object    # type: ignore[assignment,misc]
    Suggestion = None     # type: ignore[assignment]
    Completion = None     # type: ignore[assignment]


# ---------------------------------------------------------------------------
# CommandDef dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandDef:
    """Definition of a single slash command."""

    name: str                          # canonical name without slash: "background"
    description: str                   # human-readable description
    category: str                      # "Session", "Configuration", etc.
    args_hint: str = ""                # argument placeholder: "<prompt>", "[name]"
    subcommands: tuple[str, ...] = ()  # tab-completable subcommands
    cli_only: bool = False             # only available in CLI
    gateway_only: bool = False         # only available in gateway/messaging
    gateway_config_gate: str | None = None  # config dotpath; when truthy, overrides cli_only for gateway
    defer_subcommands_until_prefix: bool = False  # hide default subcommand suggestions until user starts typing one
    
    def get_description(self) -> str:
        """Get translated description."""
        try:
            from ..i18n import t
            key = f"commands.{self.name}.description"
            return t(key, default=self.description)
        except Exception:
            return self.description
    
    def get_category(self) -> str:
        """Get translated category."""
        try:
            from ..i18n import t
            key = f"categories.{self.category}"
            return t(key, default=self.category)
        except Exception:
            return self.category

    def get_subcommand_description(self, subcommand: str) -> str:
        """Get a translated completion description for one subcommand."""
        try:
            from ..i18n import t

            key = f"commands.{self.name}.subcommands.{subcommand}"
            return t(key, default="")
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Central registry -- single source of truth
# ---------------------------------------------------------------------------

COMMAND_REGISTRY: list[CommandDef] = [
    # Session - 会话管理
    CommandDef("new", "开始新会话", "会话管理"),
    CommandDef("clear", "清屏并开始新会话", "会话管理",
               cli_only=True),
    CommandDef("history", "显示对话历史", "会话管理",
               cli_only=True),
    CommandDef("goal", "管理当前会话目标", "会话管理",
               cli_only=True, args_hint="[objective|status|complete|blocked|clear]",
               subcommands=("status", "complete", "blocked", "clear")),
    CommandDef("find", "搜索当前会话的结构化记录", "会话管理",
               cli_only=True, args_hint="<keyword>"),
    CommandDef("export", "导出当前会话的结构化记录", "会话管理",
               cli_only=True, args_hint="<markdown|json>",
               subcommands=("markdown", "json")),
    CommandDef("save", "保存当前对话", "会话管理",
               cli_only=True),
    CommandDef("retry", "重试上一条消息", "会话管理"),
    CommandDef("undo", "撤销上一轮对话", "会话管理"),
    CommandDef("status", "显示会话状态", "会话管理"),
    CommandDef("cancel", "取消当前用户任务", "会话管理",
               cli_only=True),
    CommandDef("tasks", "显示当前子代理状态", "会话管理",
               cli_only=True, args_hint="[task] [log]",
               subcommands=("bg", "fg"),
               defer_subcommands_until_prefix=True),
    CommandDef("resume", "恢复之前的会话", "会话管理",
               args_hint="[name]"),

    # Configuration - 配置管理
    CommandDef("config", "显示当前配置", "配置管理",
               cli_only=True),
    CommandDef("model", "切换LLM模型", "配置管理", args_hint="[model] [--global]"),
    CommandDef("provider", "显示已配置 API Provider",
               "配置管理"),
    CommandDef("language", "显示或更改显示语言", "配置管理",
               cli_only=True, args_hint="[-CN|-EN]",
               subcommands=("-CN", "-EN")),
    # Server Management - 服务器管理
    CommandDef("tools", "查看可用工具列表", "服务器管理",
               cli_only=True),
    CommandDef("toolsets", "列出可用工具集", "服务器管理",
               cli_only=True),
    CommandDef("preset", "应用部署预设 (LNMP, Docker等)",
               "服务器管理", cli_only=True, args_hint="[list|apply|show] [name]",
               subcommands=("list", "apply", "show")),

    # Info - 帮助信息
    CommandDef("help", "显示可用命令帮助", "帮助信息"),
    
    # Doctor - 诊断配置问题
    CommandDef("doctor", "诊断配置问题", "诊断",
               cli_only=True),
    
    # API - API 配置向导
    CommandDef("api", "配置 API 设置", "API配置",
               cli_only=True),

    # Auto - 自主规划
    CommandDef("auto", "临时启用 API-B 规划并把获准任务派给员工代理", "会话管理",
               args_hint="[focus]"),
    CommandDef("auto-q", "临时停用自主链路，保留主 CLI 用户交互", "会话管理",
               cli_only=True),

    # Exit - 退出
    CommandDef("quit", "退出CLI", "退出",
               cli_only=True),
]


# ---------------------------------------------------------------------------
# Derived lookups -- rebuilt once at import time, refreshed by rebuild_lookups()
# ---------------------------------------------------------------------------

def _build_command_lookup() -> dict[str, CommandDef]:
    """Map each canonical command name to its definition."""
    lookup: dict[str, CommandDef] = {}
    for cmd in COMMAND_REGISTRY:
        lookup[cmd.name] = cmd
    return lookup


_COMMAND_LOOKUP: dict[str, CommandDef] = _build_command_lookup()


def resolve_command(name: str) -> CommandDef | None:
    """Resolve a canonical command name to its CommandDef.

    Accepts names with or without the leading slash.
    """
    return _COMMAND_LOOKUP.get(name.lower().lstrip("/"))


def rebuild_lookups() -> None:
    """Rebuild all derived lookup dicts from the current COMMAND_REGISTRY.

    Called after plugin commands are registered so they appear in help,
    autocomplete, and gateway dispatch.
    """
    global GATEWAY_KNOWN_COMMANDS

    _COMMAND_LOOKUP.clear()
    _COMMAND_LOOKUP.update(_build_command_lookup())

    COMMANDS.clear()
    for cmd in COMMAND_REGISTRY:
        if not cmd.gateway_only:
            COMMANDS[f"/{cmd.name}"] = _build_description(cmd)

    COMMANDS_BY_CATEGORY.clear()
    for cmd in COMMAND_REGISTRY:
        if not cmd.gateway_only:
            cat = COMMANDS_BY_CATEGORY.setdefault(cmd.get_category(), {})
            cat[f"/{cmd.name}"] = COMMANDS[f"/{cmd.name}"]

    SUBCOMMANDS.clear()
    for cmd in COMMAND_REGISTRY:
        if cmd.subcommands:
            SUBCOMMANDS[f"/{cmd.name}"] = list(cmd.subcommands)
    for cmd in COMMAND_REGISTRY:
        key = f"/{cmd.name}"
        if key in SUBCOMMANDS or not cmd.args_hint:
            continue
        m = _PIPE_SUBS_RE.search(cmd.args_hint)
        if m:
            SUBCOMMANDS[key] = m.group(0).split("|")

    GATEWAY_KNOWN_COMMANDS = frozenset(
        cmd.name
        for cmd in COMMAND_REGISTRY
        if not cmd.cli_only or cmd.gateway_config_gate
    )


def _build_description(cmd: CommandDef) -> str:
    """Build a CLI-facing description string including usage hint."""
    description = cmd.get_description()
    if cmd.args_hint:
        try:
            from ..i18n import t
            usage_label = t('commands.usage_label', default='usage')
            return f"{description} ({usage_label}: /{cmd.name} {cmd.args_hint})"
        except Exception:
            return f"{description} (usage: /{cmd.name} {cmd.args_hint})"
    return description


# Backwards-compatible flat dict: "/command" -> description
COMMANDS: dict[str, str] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        COMMANDS[f"/{_cmd.name}"] = _build_description(_cmd)

# Backwards-compatible categorized dict
COMMANDS_BY_CATEGORY: dict[str, dict[str, str]] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        _cat = COMMANDS_BY_CATEGORY.setdefault(_cmd.get_category(), {})
        _cat[f"/{_cmd.name}"] = COMMANDS[f"/{_cmd.name}"]


# Subcommands lookup: "/cmd" -> ["sub1", "sub2", ...]
SUBCOMMANDS: dict[str, list[str]] = {}
for _cmd in COMMAND_REGISTRY:
    if _cmd.subcommands:
        SUBCOMMANDS[f"/{_cmd.name}"] = list(_cmd.subcommands)

# Also extract subcommands hinted in args_hint via pipe-separated patterns
# e.g. args_hint="[on|off|tts|status]" for commands that don't have explicit subcommands.
# NOTE: If a command already has explicit subcommands, this fallback is skipped.
# Use the `subcommands` field on CommandDef for intentional tab-completable args.
_PIPE_SUBS_RE = re.compile(r"[a-z]+(?:\|[a-z]+)+")
for _cmd in COMMAND_REGISTRY:
    key = f"/{_cmd.name}"
    if key in SUBCOMMANDS or not _cmd.args_hint:
        continue
    m = _PIPE_SUBS_RE.search(_cmd.args_hint)
    if m:
        SUBCOMMANDS[key] = m.group(0).split("|")


# ---------------------------------------------------------------------------
# Gateway helpers
# ---------------------------------------------------------------------------

# Set of all canonical command names recognized by the gateway.
# Includes config-gated commands so the gateway can dispatch them
# (the handler checks the config gate at runtime).
GATEWAY_KNOWN_COMMANDS: frozenset[str] = frozenset(
    cmd.name
    for cmd in COMMAND_REGISTRY
    if not cmd.cli_only or cmd.gateway_config_gate
)


def _resolve_config_gates() -> set[str]:
    """Return canonical names of commands whose ``gateway_config_gate`` is truthy.

    Reads ``config.yaml`` and walks the dot-separated key path for each
    config-gated command.  Returns an empty set on any error so callers
    degrade gracefully.
    """
    gated = [c for c in COMMAND_REGISTRY if c.gateway_config_gate]
    if not gated:
        return set()
    try:
        from ....infrastructure.config.configuration import read_raw_config
        cfg = read_raw_config()
    except Exception:
        return set()
    result: set[str] = set()
    for cmd in gated:
        val: Any = cfg
        for key in cmd.gateway_config_gate.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            else:
                val = None
                break
        if val:
            result.add(cmd.name)
    return result


def _is_gateway_available(cmd: CommandDef, config_overrides: set[str] | None = None) -> bool:
    """Check if *cmd* should appear in gateway surfaces (help, menus, mappings).

    Unconditionally available when ``cli_only`` is False.  When ``cli_only``
    is True but ``gateway_config_gate`` is set, the command is available only
    when the config value is truthy.  Pass *config_overrides* (from
    ``_resolve_config_gates()``) to avoid re-reading config for every command.
    """
    if not cmd.cli_only:
        return True
    if cmd.gateway_config_gate:
        overrides = config_overrides if config_overrides is not None else _resolve_config_gates()
        return cmd.name in overrides
    return False


def gateway_help_lines() -> list[str]:
    """Generate gateway help text lines from the registry."""
    overrides = _resolve_config_gates()
    lines: list[str] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        args = f" {cmd.args_hint}" if cmd.args_hint else ""
        lines.append(f"`/{cmd.name}{args}` -- {cmd.get_description()}")
    return lines




# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

class SlashCommandCompleter(Completer):
    """Autocomplete for built-in slash commands, subcommands, and skill commands."""

    def __init__(
        self,
        skill_commands_provider: Callable[[], Mapping[str, dict[str, Any]]] | None = None,
        command_filter: Callable[[str], bool] | None = None,
    ) -> None:
        self._skill_commands_provider = skill_commands_provider
        self._command_filter = command_filter

    def _command_allowed(self, slash_command: str) -> bool:
        if self._command_filter is None:
            return True
        try:
            return bool(self._command_filter(slash_command))
        except Exception:
            return True

    @staticmethod
    def _defer_subcommands(base_cmd: str) -> bool:
        cmd = resolve_command(base_cmd)
        return bool(cmd and cmd.defer_subcommands_until_prefix)

    @staticmethod
    def _subcommand_description(base_cmd: str, subcommand: str) -> str:
        cmd = resolve_command(base_cmd)
        return cmd.get_subcommand_description(subcommand) if cmd else ""

    def _iter_skill_commands(self) -> Mapping[str, dict[str, Any]]:
        if self._skill_commands_provider is None:
            return {}
        try:
            return self._skill_commands_provider() or {}
        except Exception:
            return {}

    @staticmethod
    def _completion_text(cmd_name: str, word: str) -> str:
        """Return replacement text for a completion.

        When the user has already typed the full command exactly (``/help``),
        returning ``help`` would be a no-op and prompt_toolkit suppresses the
        menu. Appending a trailing space keeps the dropdown visible and makes
        backspacing retrigger it naturally.
        """
        return f"{cmd_name} " if cmd_name == word else cmd_name

    @staticmethod
    def _extract_path_word(text: str) -> str | None:
        """Extract the current word if it looks like a file path.

        Returns the path-like token under the cursor, or None if the
        current word doesn't look like a path.  A word is path-like when
        it starts with ``./``, ``../``, ``~/``, ``/``, or contains a
        ``/`` separator (e.g. ``src/main.py``).
        """
        if not text:
            return None
        # Walk backwards to find the start of the current "word".
        # Words are delimited by spaces, but paths can contain almost anything.
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word:
            return None
        # Only trigger path completion for path-like tokens
        if word.startswith(("./", "../", "~/", "/")) or "/" in word:
            return word
        return None

    @staticmethod
    def _path_completions(word: str, limit: int = 30):
        """Yield Completion objects for file paths matching *word*."""
        expanded = os.path.expanduser(word)
        # Split into directory part and prefix to match inside it
        if expanded.endswith("/"):
            search_dir = expanded
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = prefix.lower()
        for entry in sorted(entries):
            if prefix and not entry.lower().startswith(prefix_lower):
                continue
            if count >= limit:
                break

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            # Build the completion text (what replaces the typed word)
            if word.startswith("~"):
                display_path = "~/" + os.path.relpath(full_path, os.path.expanduser("~"))
            elif os.path.isabs(word):
                display_path = full_path
            else:
                # Keep relative
                display_path = os.path.relpath(full_path)

            if is_dir:
                display_path += "/"

            suffix = "/" if is_dir else ""
            meta = "dir" if is_dir else _file_size_label(full_path)

            yield Completion(
                display_path,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    @staticmethod
    def _extract_context_word(text: str) -> str | None:
        """Extract a bare ``@`` token for context reference completions."""
        if not text:
            return None
        # Walk backwards to find the start of the current word
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word.startswith("@"):
            return None
        return word

    @staticmethod
    def _context_completions(word: str, limit: int = 30):
        """Yield @ context completions.

        Bare ``@`` or ``@partial`` shows static references and matching
        files/folders.  ``@file:path`` and ``@folder:path`` are handled
        by the existing path completion path.
        """
        lowered = word.lower()

        # Static context references
        _STATIC_REFS = (
            ("@diff", "Git working tree diff"),
            ("@staged", "Git staged diff"),
            ("@file:", "Attach a file"),
            ("@folder:", "Attach a folder"),
            ("@git:", "Git log with diffs (e.g. @git:5)"),
            ("@url:", "Fetch web content"),
        )
        for candidate, meta in _STATIC_REFS:
            if candidate.lower().startswith(lowered) and candidate.lower() != lowered:
                yield Completion(
                    candidate,
                    start_position=-len(word),
                    display=candidate,
                    display_meta=meta,
                )

        # If the user typed @file: or @folder:, delegate to path completions
        for prefix in ("@file:", "@folder:"):
            if word.startswith(prefix):
                path_part = word[len(prefix):] or "."
                expanded = os.path.expanduser(path_part)
                if expanded.endswith("/"):
                    search_dir, match_prefix = expanded, ""
                else:
                    search_dir = os.path.dirname(expanded) or "."
                    match_prefix = os.path.basename(expanded)

                try:
                    entries = os.listdir(search_dir)
                except OSError:
                    return

                count = 0
                prefix_lower = match_prefix.lower()
                for entry in sorted(entries):
                    if match_prefix and not entry.lower().startswith(prefix_lower):
                        continue
                    if count >= limit:
                        break
                    full_path = os.path.join(search_dir, entry)
                    is_dir = os.path.isdir(full_path)
                    display_path = os.path.relpath(full_path)
                    suffix = "/" if is_dir else ""
                    kind = "folder" if is_dir else "file"
                    meta = "dir" if is_dir else _file_size_label(full_path)
                    completion = f"@{kind}:{display_path}{suffix}"
                    yield Completion(
                        completion,
                        start_position=-len(word),
                        display=entry + suffix,
                        display_meta=meta,
                    )
                    count += 1
                return

        # Bare @ or @partial — show matching files/folders from cwd
        query = word[1:]  # strip the @
        if not query:
            search_dir, match_prefix = ".", ""
        else:
            expanded = os.path.expanduser(query)
            if expanded.endswith("/"):
                search_dir, match_prefix = expanded, ""
            else:
                search_dir = os.path.dirname(expanded) or "."
                match_prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = match_prefix.lower()
        for entry in sorted(entries):
            if match_prefix and not entry.lower().startswith(prefix_lower):
                continue
            if entry.startswith("."):
                continue  # skip hidden files in bare @ mode
            if count >= limit:
                break
            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)
            display_path = os.path.relpath(full_path)
            suffix = "/" if is_dir else ""
            kind = "folder" if is_dir else "file"
            meta = "dir" if is_dir else _file_size_label(full_path)
            completion = f"@{kind}:{display_path}{suffix}"
            yield Completion(
                completion,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    def _model_completions(self, sub_text: str, sub_lower: str):
        """Yield completions for /model from config aliases + built-in aliases."""
        seen = set()
        # Config-based direct aliases (preferred — include provider info)
        try:
            from ..model_switch import (
                _ensure_direct_aliases, DIRECT_ALIASES, MODEL_ALIASES,
            )
            _ensure_direct_aliases()
            for name, da in DIRECT_ALIASES.items():
                if name.startswith(sub_lower) and name != sub_lower:
                    seen.add(name)
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{da.model} ({da.provider})",
                    )
            # Built-in catalog aliases not already covered
            for name in sorted(MODEL_ALIASES.keys()):
                if name in seen:
                    continue
                if name.startswith(sub_lower) and name != sub_lower:
                    identity = MODEL_ALIASES[name]
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{identity.vendor}/{identity.family}",
                    )
        except Exception:
            pass

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            # Try @ context completion.
            ctx_word = self._extract_context_word(text)
            if ctx_word is not None:
                yield from self._context_completions(ctx_word)
                return
            # Try file path completion for non-slash input
            path_word = self._extract_path_word(text)
            if path_word is not None:
                yield from self._path_completions(path_word)
            return

        # Check if we're completing a subcommand (base command already typed)
        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()
        if len(parts) > 1 or (len(parts) == 1 and text.endswith(" ")):
            sub_text = parts[1] if len(parts) > 1 else ""
            sub_lower = sub_text.lower()

            # Dynamic model alias completions for /model
            if " " not in sub_text and base_cmd == "/model":
                yield from self._model_completions(sub_text, sub_lower)
                return

            # Static subcommand completions
            if " " not in sub_text and base_cmd in SUBCOMMANDS and self._command_allowed(base_cmd):
                if not sub_text and self._defer_subcommands(base_cmd):
                    return
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        yield Completion(
                            sub,
                            start_position=-len(sub_text),
                            display=sub,
                            display_meta=self._subcommand_description(base_cmd, sub),
                        )
            return

        word = text[1:]

        for cmd, desc in COMMANDS.items():
            if not self._command_allowed(cmd):
                continue
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )

        for cmd, info in self._iter_skill_commands().items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                description = str(info.get("description", "技能命令"))
                try:
                    from ..i18n import t
                    description = t('commands.skill_command', default=description)
                except Exception:
                    pass
                short_desc = description[:50] + ("..." if len(description) > 50 else "")
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=f"⚡ {short_desc}",
                )


# ---------------------------------------------------------------------------
# Inline auto-suggest (ghost text) for slash commands
# ---------------------------------------------------------------------------

class SlashCommandAutoSuggest(AutoSuggest):
    """Inline ghost-text suggestions for slash commands and their subcommands.

    Shows the rest of a command or subcommand in dim text as you type.
    Falls back to history-based suggestions for non-slash input.
    """

    def __init__(
        self,
        history_suggest: AutoSuggest | None = None,
        completer: SlashCommandCompleter | None = None,
    ) -> None:
        self._history = history_suggest
        self._completer = completer  # Reuse its model cache

    def get_suggestion(self, buffer, document):
        text = document.text_before_cursor

        # Only suggest for slash commands
        if not text.startswith("/"):
            # Fall back to history for regular text
            if self._history:
                return self._history.get_suggestion(buffer, document)
            return None

        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()

        if len(parts) == 1 and not text.endswith(" "):
            # Still typing the command name: /upd → suggest "ate"
            word = text[1:].lower()
            for cmd in COMMANDS:
                if self._completer is not None and not self._completer._command_allowed(cmd):
                    continue
                cmd_name = cmd[1:]  # strip leading /
                if cmd_name.startswith(word) and cmd_name != word:
                    return Suggestion(cmd_name[len(word):])
            return None

        # Command is complete — suggest subcommands or model names
        sub_text = parts[1] if len(parts) > 1 else ""
        sub_lower = sub_text.lower()

        # Static subcommands
        if self._completer is not None and not self._completer._command_allowed(base_cmd):
            return None
        if base_cmd in SUBCOMMANDS and SUBCOMMANDS[base_cmd]:
            if " " not in sub_text:
                if not sub_text and self._completer is not None and self._completer._defer_subcommands(base_cmd):
                    return None
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        return Suggestion(sub[len(sub_text):])

        # Fall back to history
        if self._history:
            return self._history.get_suggestion(buffer, document)
        return None


def _file_size_label(path: str) -> str:
    """Return a compact human-readable file size, or '' on error."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"
