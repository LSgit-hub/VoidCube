from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voidcube.interfaces.cli.commands.catalog import (
    COMMAND_REGISTRY,
    SlashCommandAutoSuggest,
    SlashCommandCompleter,
)
from voidcube.interfaces.cli.commands.execution import BUILTIN_COMMAND_SPECS
from voidcube.interfaces.cli.i18n import get_i18n, init_i18n, set_locale


def _doc(text: str):
    return SimpleNamespace(text_before_cursor=text)


def test_tasks_subcommands_are_hidden_until_prefix_is_typed():
    completer = SlashCommandCompleter()

    completions = list(completer.get_completions(_doc("/tasks "), None))

    assert completions == []


def test_tasks_subcommands_are_suggested_after_explicit_prefix():
    completer = SlashCommandCompleter()

    completions = list(completer.get_completions(_doc("/tasks b"), None))

    assert len(completions) == 1
    assert completions[0].text == "bg"


def test_goal_subcommands_include_localized_completion_descriptions():
    init_i18n()
    original_locale = get_i18n().get_current_locale()
    try:
        set_locale("zh_CN")
        completions = list(
            SlashCommandCompleter().get_completions(_doc("/goal "), None)
        )
    finally:
        set_locale(original_locale)

    assert {item.text: item.display_meta_text for item in completions} == {
        "status": "查看当前目标状态",
        "complete": "将活动目标标记为完成",
        "blocked": "将活动目标标记为阻塞，后接原因",
        "clear": "清除已结束的目标",
    }


def test_command_completion_includes_voice_and_accepts_uppercase_prefix():
    completions = list(SlashCommandCompleter().get_completions(_doc("/V"), None))

    assert any(item.text == "voice" for item in completions)


def test_verbose_subcommand_completion_lists_explicit_modes():
    completions = {
        item.text: item.display_meta_text
        for item in SlashCommandCompleter().get_completions(_doc("/verbose "), None)
    }

    assert set(completions) == {"off", "new", "all", "verbose"}
    assert completions["off"]
    assert completions["verbose"]


def test_voice_subcommand_completion_includes_supervisor_target():
    completions = {
        item.text
        for item in SlashCommandCompleter().get_completions(_doc("/voice s"), None)
    }

    assert {"status", "supervisor", "session"} <= completions


def test_voice_subcommand_completion_has_localized_help_text():
    init_i18n()
    original_locale = get_i18n().get_current_locale()
    try:
        set_locale("zh_CN")
        completions = list(
            SlashCommandCompleter().get_completions(_doc("/voice h"), None)
        )
    finally:
        set_locale(original_locale)

    assert len(completions) == 1
    assert completions[0].text == "help"
    assert completions[0].display_meta_text == "显示语音命令帮助"


def test_voice_subcommand_completion_keeps_help_text_without_loaded_locale():
    command = next(command for command in COMMAND_REGISTRY if command.name == "voice")

    assert command.get_subcommand_description("help")


def test_slash_completion_includes_every_builtin_command():
    completed_names = {
        item.text.strip()
        for item in SlashCommandCompleter().get_completions(_doc("/"), None)
    }

    assert set(BUILTIN_COMMAND_SPECS) <= completed_names


def test_tasks_auto_suggest_is_hidden_until_prefix_is_typed():
    completer = SlashCommandCompleter()
    suggest = SlashCommandAutoSuggest(completer=completer)

    suggestion = suggest.get_suggestion(None, _doc("/tasks "))

    assert suggestion is None


def test_tasks_auto_suggest_returns_suffix_after_explicit_prefix():
    completer = SlashCommandCompleter()
    suggest = SlashCommandAutoSuggest(completer=completer)

    suggestion = suggest.get_suggestion(None, _doc("/tasks f"))

    assert suggestion is not None
    assert suggestion.text == "g"


def test_model_completion_reads_configured_provider_catalog_and_override(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config",
        lambda: {
            "providers": {
                "deepseek-v": {
                    "label": "DeepSeek Vision",
                    "selected_model": "deepseek-v4-flash-vision-exp",
                    "model_override": "deepseek-v4-flash-vision-exp",
                    "model_catalog": {
                        "models": ["deepseek-chat", "deepseek-reasoner"],
                    },
                }
            }
        },
    )

    completions = list(
        SlashCommandCompleter().get_completions(
            _doc("/model deepseek-v4"),
            None,
        )
    )

    assert [item.text for item in completions] == [
        "deepseek-v4-flash-vision-exp",
    ]
    assert completions[0].display_meta_text == (
        "DeepSeek Vision (deepseek-v)"
    )
