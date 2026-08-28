from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from voidcube.infrastructure.llm.request import (
    ChatRequestConfig,
    build_chat_completion_kwargs,
    prepare_chat_messages,
    sanitize_chat_messages,
)


pytestmark = pytest.mark.unit


def test_prepare_chat_messages_builds_api_copy_and_preserves_history():
    history = [
        {"role": "user", "content": "  question  "},
        {
            "role": "assistant",
            "content": " answer ",
            "reasoning": "private plan",
            "finish_reason": "tool_calls",
            "_thinking_prefill": True,
            "_flush_sentinel": "internal-marker",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "inspect",
                        "arguments": '{"b": 2, "a": 1}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": " result ",
            "action_refs": [{"action_id": "act-1", "state": "succeeded"}],
        },
    ]

    prepared = prepare_chat_messages(
        history,
        system_prompt="base policy",
        ephemeral_system_prompt="turn policy",
        prefill_messages=({"role": "user", "content": " example "},),
        user_message_index=0,
        user_contexts=("memory context", "plugin context"),
    )

    assert prepared[0] == {
        "role": "system",
        "content": "base policy\n\nturn policy",
    }
    assert prepared[1] == {"role": "user", "content": "example"}
    assert prepared[2]["content"] == (
        "question\n\nmemory context\n\nplugin context"
    )
    assert prepared[3]["reasoning_content"] == "private plan"
    assert "reasoning" not in prepared[3]
    assert "finish_reason" not in prepared[3]
    assert "_thinking_prefill" not in prepared[3]
    assert "_flush_sentinel" not in prepared[3]
    assert prepared[3]["tool_calls"][0]["function"]["arguments"] == (
        '{"a":1,"b":2}'
    )
    assert history[0]["content"] == "  question  "
    assert history[1]["tool_calls"][0]["function"]["arguments"] == (
        '{"b": 2, "a": 1}'
    )
    assert "action_refs" not in prepared[4]
    assert history[2]["action_refs"][0]["action_id"] == "act-1"


def test_sanitize_chat_messages_repairs_tool_pairs_and_invalid_roles():
    messages = [
        {"role": "invalid", "content": "drop"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "missing", "function": {"name": "inspect", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "orphan", "content": "drop"},
    ]

    sanitized = sanitize_chat_messages(messages)

    assert sanitized == [
        messages[1],
        {
            "role": "tool",
            "content": "[Result unavailable - see context summary above]",
            "tool_call_id": "missing",
        },
    ]


def test_direct_openai_request_uses_developer_role_and_completion_limit():
    messages = [{"role": "system", "content": "policy"}]
    config = ChatRequestConfig(
        model="gpt-5",
        base_url="https://api.openai.com/v1",
        max_tokens=2048,
        timeout=30.0,
    )

    kwargs = build_chat_completion_kwargs(config, messages)

    assert kwargs["messages"][0]["role"] == "developer"
    assert messages[0]["role"] == "system"
    assert kwargs["max_completion_tokens"] == 2048
    assert "max_tokens" not in kwargs
    assert kwargs["timeout"] == 30.0


def test_openrouter_request_collects_preferences_tools_and_reasoning():
    tool = {
        "type": "function",
        "function": {
            "name": "inspect_state",
            "description": "Inspect state",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    config = ChatRequestConfig(
        model="qwen/qwen3.6-plus",
        base_url="https://openrouter.ai/api/v1",
        tools=(tool,),
        providers_allowed=("provider-a",),
        providers_ignored=("provider-b",),
        providers_order=("provider-a", "provider-c"),
        provider_sort="throughput",
        provider_require_parameters=True,
        provider_data_collection="deny",
        request_overrides={"temperature": 0.2},
    )

    kwargs = build_chat_completion_kwargs(
        config,
        [{"role": "user", "content": "inspect"}],
    )

    assert kwargs["tools"] == [tool]
    assert kwargs["temperature"] == 0.2
    assert kwargs["extra_body"]["reasoning"] == {
        "enabled": True,
        "effort": "medium",
    }
    assert kwargs["extra_body"]["provider"] == {
        "only": ["provider-a"],
        "ignore": ["provider-b"],
        "order": ["provider-a", "provider-c"],
        "sort": "throughput",
        "require_parameters": True,
        "data_collection": "deny",
    }


def test_nous_request_omits_explicitly_disabled_reasoning_but_keeps_tags():
    config = ChatRequestConfig(
        model="qwen3.5-plus",
        base_url="https://inference-api.nousresearch.com/v1",
        reasoning_config={"enabled": False},
    )

    kwargs = build_chat_completion_kwargs(config, [{"role": "user", "content": "hi"}])

    assert "reasoning" not in kwargs["extra_body"]
    assert kwargs["extra_body"]["tags"] == ["product=VoidCube-agent"]


def test_qwen_request_normalizes_content_without_mutating_history():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": ["hello", {"type": "text", "text": "world"}]},
    ]
    config = ChatRequestConfig(
        model="qwen3.5-plus",
        base_url="https://portal.qwen.ai/v1",
        session_id="session-1",
    )

    kwargs = build_chat_completion_kwargs(config, messages)

    assert messages[0]["content"] == "policy"
    system_part = kwargs["messages"][0]["content"][0]
    assert system_part == {
        "type": "text",
        "text": "policy",
        "cache_control": {"type": "ephemeral"},
    }
    assert kwargs["messages"][1]["content"][0] == {"type": "text", "text": "hello"}
    assert kwargs["metadata"]["sessionId"] == "session-1"
    uuid.UUID(kwargs["metadata"]["promptId"])
    assert kwargs["max_tokens"] == 65536
    assert kwargs["extra_body"]["vl_high_resolution_images"] is True


def test_local_request_only_adds_configured_context_option():
    config = ChatRequestConfig(
        model="local-model",
        base_url="http://127.0.0.1:11434/v1",
        ollama_num_ctx=32768,
    )

    kwargs = build_chat_completion_kwargs(config, [{"role": "user", "content": "hi"}])

    assert kwargs["extra_body"] == {"options": {"num_ctx": 32768}}


def test_summary_request_can_exclude_tools_and_turn_overrides():
    config = ChatRequestConfig(
        model="qwen/qwen3.6-plus",
        base_url="https://openrouter.ai/api/v1",
        tools=({"name": "inspect_state", "description": "Inspect", "parameters": {}},),
        request_overrides={"service_tier": "priority"},
    )

    kwargs = build_chat_completion_kwargs(
        config,
        [{"role": "user", "content": "summarize"}],
        include_tools=False,
        include_request_overrides=False,
    )

    assert "tools" not in kwargs
    assert "service_tier" not in kwargs


def test_request_rejects_retired_model_endpoint_route_and_override():
    markers = (
        "".join(("anthro", "pic")),
        "".join(("clau", "de")),
        "".join(("co", "dex")),
    )
    configs = (
        ChatRequestConfig(model=f"vendor/{markers[0]}-model"),
        ChatRequestConfig(
            model="safe-model",
            base_url=f"https://api.{markers[1]}.example/v1",
        ),
        ChatRequestConfig(
            model="safe-model",
            providers_allowed=(markers[0],),
        ),
        ChatRequestConfig(
            model="safe-model",
            request_overrides={"model": f"vendor/{markers[2]}-model"},
        ),
    )

    for config in configs:
        with pytest.raises(ValueError, match="retired by project policy"):
            build_chat_completion_kwargs(
                config,
                [{"role": "user", "content": "hello"}],
            )


def test_scoped_memory_request_replaces_tools_limits_overrides_and_timeout():
    original = ChatRequestConfig(
        model="gpt-5",
        base_url="https://api.openai.com/v1",
        tools=(
            {"type": "function", "function": {"name": "mem_remember"}},
            {"type": "function", "function": {"name": "terminal"}},
        ),
        max_tokens=1024,
        request_overrides={"service_tier": "priority"},
        timeout=1800.0,
    )
    remember_tool = {"type": "function", "function": {"name": "mem_remember"}}
    scoped = replace(
        original,
        tools=(remember_tool,),
        max_tokens=5120,
        request_overrides={"temperature": 0.3},
        timeout=45.0,
    )

    kwargs = build_chat_completion_kwargs(
        scoped,
        [{"role": "user", "content": "remember"}],
    )

    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["function"]["name"] == "mem_remember"
    assert kwargs["max_completion_tokens"] == 5120
    assert kwargs["temperature"] == 0.3
    assert kwargs["timeout"] == 45.0
    assert "service_tier" not in kwargs


def test_request_can_explicitly_omit_default_reasoning_payload():
    kwargs = build_chat_completion_kwargs(
        ChatRequestConfig(
            model="qwen/qwen3.6-plus",
            base_url="https://openrouter.ai/api/v1",
            include_reasoning=False,
        ),
        [{"role": "user", "content": "summarize"}],
    )

    assert "extra_body" not in kwargs


def test_native_image_input_expands_persisted_reference_only_for_api_request(tmp_path):
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnative-image")
    attachment = {
        "kind": "local_image",
        "path": str(image),
        "mime_type": "image/png",
        "detail": "high",
    }
    history = [{"role": "user", "content": "describe it", "attachments": [attachment]}]

    prepared = prepare_chat_messages(history, native_image_input=True)

    assert history[0]["attachments"] == [attachment]
    assert "attachments" not in prepared[0]
    assert prepared[0]["content"][0] == {"type": "text", "text": "describe it"}
    assert prepared[0]["content"][1]["type"] == "image_url"
    assert prepared[0]["content"][1]["image_url"]["detail"] == "high"
    assert prepared[0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_text_model_request_drops_attachment_metadata_without_expanding_image(tmp_path):
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\ntext-only")

    prepared = prepare_chat_messages(
        [
            {
                "role": "user",
                "content": "describe it",
                "attachments": [{"kind": "local_image", "path": str(image)}],
            }
        ],
        native_image_input=False,
    )

    assert prepared == [{"role": "user", "content": "describe it"}]


def test_native_audio_and_video_inputs_are_projected_without_persisting_bytes(
    tmp_path,
):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 12)
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    history = [
        {
            "role": "user",
            "content": "inspect media",
            "attachments": [
                {
                    "kind": "local_audio",
                    "path": str(audio),
                    "mime_type": "audio/wav",
                },
                {
                    "kind": "local_video",
                    "path": str(video),
                    "mime_type": "video/mp4",
                },
            ],
        }
    ]

    prepared = prepare_chat_messages(
        history,
        native_input_modalities={"audio", "video"},
    )

    assert history[0]["attachments"][0]["kind"] == "local_audio"
    assert "attachments" not in prepared[0]
    assert [part["type"] for part in prepared[0]["content"]] == [
        "text",
        "input_audio",
        "video_url",
    ]
