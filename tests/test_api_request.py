from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from agent.api_request import ChatRequestConfig, build_chat_completion_kwargs


pytestmark = pytest.mark.unit


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


def test_github_reasoning_effort_is_clamped_to_supported_level(monkeypatch):
    monkeypatch.setattr(
        "VoidCube_cli.models.github_model_reasoning_efforts",
        lambda _model: ["low", "medium", "high"],
    )
    config = ChatRequestConfig(
        model="gpt-5",
        base_url="https://models.github.ai/inference",
        reasoning_config={"enabled": True, "effort": "xhigh"},
    )

    kwargs = build_chat_completion_kwargs(config, [{"role": "user", "content": "hi"}])

    assert kwargs["extra_body"]["reasoning"] == {"effort": "high"}


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
            {"type": "function", "function": {"name": "memory"}},
            {"type": "function", "function": {"name": "terminal"}},
        ),
        max_tokens=1024,
        request_overrides={"service_tier": "priority"},
        timeout=1800.0,
    )
    memory_tool = {"type": "function", "function": {"name": "memory"}}
    scoped = replace(
        original,
        tools=(memory_tool,),
        max_tokens=5120,
        request_overrides={"temperature": 0.3},
        timeout=45.0,
    )

    kwargs = build_chat_completion_kwargs(
        scoped,
        [{"role": "user", "content": "flush"}],
    )

    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["function"]["name"] == "memory"
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
