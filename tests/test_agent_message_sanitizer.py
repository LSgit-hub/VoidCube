import pytest

from agent.message_sanitizer import (
    sanitize_messages_non_ascii,
    sanitize_messages_surrogates,
    sanitize_surrogates,
)
from run_agent import AIAgent


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_sanitize_surrogates_replaces_lone_code_points():
    assert sanitize_surrogates("before\ud800after") == "before\ufffdafter"
    assert sanitize_surrogates("clean") == "clean"


def test_sanitize_messages_surrogates_covers_nested_tool_fields():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "bad\ud800text"}],
            "name": "name\ud800",
            "tool_calls": [
                {
                    "id": "call\ud800",
                    "function": {
                        "name": "tool\ud800",
                        "arguments": '{"value":"bad\ud800"}',
                    },
                }
            ],
        }
    ]

    assert sanitize_messages_surrogates(messages) is True
    assert messages[0]["content"][0]["text"] == "bad\ufffdtext"
    assert messages[0]["name"] == "name\ufffd"
    assert messages[0]["tool_calls"][0]["id"] == "call\ufffd"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "tool\ufffd"
    assert "\ud800" not in messages[0]["tool_calls"][0]["function"]["arguments"]


def test_sanitize_messages_non_ascii_updates_content_name_and_arguments():
    messages = [
        {
            "role": "assistant",
            "content": "hello 世界",
            "name": "工具",
            "tool_calls": [
                {"function": {"arguments": '{"城市":"北京"}'}}
            ],
        }
    ]

    assert sanitize_messages_non_ascii(messages) is True
    assert messages[0]["content"] == "hello "
    assert messages[0]["name"] == ""
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"":""}'


def test_trajectory_conversion_preserves_assistant_content():
    agent = AIAgent.__new__(AIAgent)
    agent.tools = []
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer", "tool_calls": []},
    ]

    trajectory = agent._convert_to_trajectory_format(messages, "question")

    assert trajectory[-1] == {
        "from": "gpt",
        "value": "<think>\n</think>\nanswer",
    }


def test_memory_flush_without_tool_schema_preserves_history():
    agent = AIAgent.__new__(AIAgent)
    agent._memory_flush_min_turns = 1
    agent._user_turn_count = 1
    agent.valid_tool_names = ["memory"]
    agent._memory_store = object()
    agent.tools = []
    agent._cached_system_prompt = ""
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "follow-up"},
    ]
    original_messages = [message.copy() for message in messages]

    agent.flush_memories(messages)

    assert messages == original_messages
