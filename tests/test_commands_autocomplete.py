from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from VoidCube_cli.commands import SlashCommandAutoSuggest, SlashCommandCompleter
from VoidCube_cli.i18n import get_i18n, init_i18n, set_locale


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
