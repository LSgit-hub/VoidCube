from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from memai import (
    OpenAICompatibleLLMClient,
    PROTOCOL_VERSION,
    TranscriptTurn,
    load_provider_capabilities_profile,
    resolve_provider_capabilities,
)
from memai.cli import build_parser
from memai.host_integration import MemHostIntegration, configure_mem_host_integration


def test_openai_compatible_client_parses_json_response() -> None:
    def fake_transport(url, headers, payload):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        assert payload["model"] == "test-model"
        assert f'"version": "{PROTOCOL_VERSION}"' in payload["messages"][1]["content"]
        assert '"task": "extractor.events"' in payload["messages"][1]["content"]
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"events": [{"title": "Decision", "summary": "A durable decision.", "event_kind": "decision", "impact_scope": "arc", "topics": ["memory-system"], "entities": ["user"], "source_turns": ["turn_001"], "time_hint": "today", "importance": 0.8, "confidence": 0.9, "main_or_side": "main", "novelty": 0.8}]}'
                    }
                }
            ]
        }

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        transport=fake_transport,
    )
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="今天我们决定把这个项目做成时间优先的记忆系统。",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        )
    ]

    payloads = client.extract_events(turns)

    assert len(payloads) == 1
    assert payloads[0]["event_kind"] == "decision"


def test_openai_compatible_client_parses_fenced_json() -> None:
    def fake_transport(url, headers, payload):
        return {"choices": [{"message": {"content": '```json\n{"events": []}\n```'}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        transport=fake_transport,
    )
    payloads = client.extract_events([])

    assert payloads == []


def test_openai_compatible_client_accepts_segmented_content() -> None:
    def fake_transport(url, headers, payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"task": "extractor.events", "result": {"events": [',
                            },
                            {
                                "type": "output_text",
                                "text": '{"title": "Decision", "summary": "Segmented.", "source_turns": ["turn_001"]}',
                            },
                            {"type": "output_text", "text": ']}}'},
                        ]
                    }
                }
            ]
        }

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        transport=fake_transport,
    )

    payload = client.safe_complete_json(
        system_prompt="Return JSON",
        user_payload={"turns": []},
        task="extractor.events",
    )

    assert payload["events"][0]["summary"] == "Segmented."


def test_openai_compatible_client_safe_complete_json_falls_back_on_bad_payload() -> None:
    def fake_transport(url, headers, payload):
        return {"choices": [{"message": {"content": "not-json"}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        transport=fake_transport,
    )

    payload = client.safe_complete_json(
        system_prompt="Return JSON",
        user_payload={"turns": []},
    )

    assert payload == {}


def test_openai_compatible_client_uses_provider_capability_profile() -> None:
    def fake_transport(url, headers, payload):
        assert url.endswith("/custom/chat")
        assert "response_format" not in payload
        return {"choices": [{"message": {"content": '{"events": []}'}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        base_url="https://example.com/api",
        provider_capabilities=resolve_provider_capabilities(
            "legacy-compatible",
            chat_completions_path="/custom/chat",
        ),
        transport=fake_transport,
    )

    payloads = client.extract_events([])

    assert payloads == []


def test_openai_compatible_client_supports_developer_role_profile() -> None:
    def fake_transport(url, headers, payload):
        assert payload["messages"][0]["role"] == "developer"
        return {"choices": [{"message": {"content": '{"events": []}'}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        provider_capabilities=resolve_provider_capabilities("developer-role"),
        transport=fake_transport,
    )

    assert client.extract_events([]) == []


def test_openai_compatible_client_supports_inline_user_system_prompt() -> None:
    def fake_transport(url, headers, payload):
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert "Follow these system instructions" in payload["messages"][0]["content"]
        assert '"task": "extractor.events"' in payload["messages"][0]["content"]
        return {"choices": [{"message": {"content": '{"events": []}'}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        provider_capabilities=resolve_provider_capabilities("user-only"),
        transport=fake_transport,
    )

    assert client.extract_events([]) == []


def test_openai_compatible_client_supports_string_response_format_override() -> None:
    def fake_transport(url, headers, payload):
        assert payload["response_format"] == "json_object"
        return {"choices": [{"message": {"content": '{"events": []}'}}]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        provider_capabilities=resolve_provider_capabilities(
            "openai",
            response_format_style="json_object_string",
        ),
        transport=fake_transport,
    )

    assert client.extract_events([]) == []


def test_openai_compatible_client_reads_choice_text_responses() -> None:
    def fake_transport(url, headers, payload):
        return {"choices": [{"text": '{"events": [{"summary": "choice text"}]}' }]}

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        provider_capabilities=resolve_provider_capabilities("text-choice"),
        transport=fake_transport,
    )

    payload = client.safe_complete_json(
        system_prompt="Return JSON",
        user_payload={"turns": []},
    )

    assert payload["events"][0]["summary"] == "choice text"


def test_openai_compatible_client_reads_output_text_responses() -> None:
    def fake_transport(url, headers, payload):
        return {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"events": [{"summary": "output text"}]}'}
                    ]
                }
            ]
        }

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        provider_capabilities=resolve_provider_capabilities("output-text"),
        transport=fake_transport,
    )

    payload = client.safe_complete_json(
        system_prompt="Return JSON",
        user_payload={"turns": []},
    )

    assert payload["events"][0]["summary"] == "output text"


def test_openai_compatible_client_streams_json_content_and_reasoning_tags() -> None:
    updates: list[str] = []
    seen_payloads: list[dict] = []

    def fake_stream_transport(url, headers, payload):
        seen_payloads.append(payload)
        return [
            {"choices": [{"delta": {"content": '{"reply_text":"<think>'}}]},
            {"choices": [{"delta": {"content": "先检查上下文"}}]},
            {"choices": [{"delta": {"content": "</think>已确认。"}}]},
            {"choices": [{"delta": {"content": '","reason":"ok"}'}}]},
        ]

    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        stream_transport=fake_stream_transport,
    )

    payload = client.complete_json_stream(
        system_prompt="Return JSON",
        user_payload={"message": "继续"},
        task="companion.direct_dialogue",
        on_content=updates.append,
    )

    assert payload == {
        "reply_text": "<think>先检查上下文</think>已确认。",
        "reason": "ok",
    }
    assert "".join(updates) == '{"reply_text":"<think>先检查上下文</think>已确认。","reason":"ok"}'
    assert seen_payloads[0]["stream"] is True
    assert seen_payloads[0]["stream_options"] == {"include_usage": True}


def test_openai_compatible_client_auto_streams_api_b_think_updates() -> None:
    thinking_updates: list[str] = []
    seen_payloads: list[dict] = []

    def fake_stream_transport(url, headers, payload):
        seen_payloads.append(payload)
        return [
            {"choices": [{"delta": {"content": '{"reply_text":"<think>先'}}]},
            {"choices": [{"delta": {"content": "检查上下文</think>完成。"}}]},
            {"choices": [{"delta": {"content": '","reason":"ok"}'}}]},
        ]

    configure_mem_host_integration(
        MemHostIntegration(api_b_thinking_sink=thinking_updates.append)
    )
    try:
        client = OpenAICompatibleLLMClient(
            model="test-model",
            api_key="test-key",
            stream_transport=fake_stream_transport,
            api_b_thinking_enabled=True,
        )

        payload = client.complete_json(
            system_prompt="Return JSON",
            user_payload={"message": "继续"},
            task="companion.direct_dialogue",
        )
    finally:
        configure_mem_host_integration(MemHostIntegration())

    assert payload["reply_text"] == "<think>先检查上下文</think>完成。"
    assert thinking_updates == ["先", "先检查上下文"]
    assert seen_payloads[0]["stream"] is True


def test_openai_compatible_client_from_env_applies_provider_overrides(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_PROVIDER_PROFILE", "legacy-compatible")
    monkeypatch.setenv("OPENAI_CHAT_COMPLETIONS_PATH", "/vendor/chat")
    monkeypatch.setenv("OPENAI_SYSTEM_PROMPT_STYLE", "developer")
    monkeypatch.setenv("OPENAI_RESPONSE_FORMAT_STYLE", "json_object_string")
    monkeypatch.setenv("OPENAI_RESPONSE_CONTENT_STYLE", "choices_text")

    client = OpenAICompatibleLLMClient.from_env(model="env-model")

    assert client.provider_capabilities.profile_name == "legacy-compatible"
    assert client.provider_capabilities.chat_completions_path == "/vendor/chat"
    assert client.provider_capabilities.system_prompt_style == "developer"
    assert client.provider_capabilities.response_format_style == "json_object_string"
    assert client.provider_capabilities.response_content_style == "choices_text"
    assert client.provider_capabilities.supports_response_format_json_object is True


def test_resolve_provider_capabilities_loads_custom_profile_file(tmp_path: Path) -> None:
    profile_path = tmp_path / "provider-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "extends": "legacy-compatible",
                "profile_name": "vendor-gateway",
                "chat_completions_path": "vendor/chat",
                "system_prompt_style": "developer",
                "response_format_style": "json_object_string",
                "response_content_style": "choices_text",
            }
        ),
        encoding="utf-8",
    )

    capabilities = resolve_provider_capabilities(profile_path=profile_path)

    assert capabilities.profile_name == "vendor-gateway"
    assert capabilities.chat_completions_path == "/vendor/chat"
    assert capabilities.system_prompt_style == "developer"
    assert capabilities.response_format_style == "json_object_string"
    assert capabilities.response_content_style == "choices_text"

    loaded = load_provider_capabilities_profile(profile_path)

    assert loaded.profile_name == "vendor-gateway"


def test_resolve_provider_capabilities_selects_named_profile_from_file(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "provider-profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "vendor-a": {
                        "extends": "openai",
                        "chat_completions_path": "/vendor-a/chat",
                    },
                    "vendor-b": {
                        "extends": "user-only",
                        "response_content_style": "output_text",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    capabilities = resolve_provider_capabilities(
        "vendor-b",
        profile_path=profile_path,
    )

    assert capabilities.profile_name == "vendor-b"
    assert capabilities.system_prompt_style == "inline_user"
    assert capabilities.response_content_style == "output_text"


def test_from_env_loads_custom_provider_profile_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "provider-profiles.json"
    profile_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "vendor-b": {
                        "extends": "legacy-compatible",
                        "chat_completions_path": "/vendor-b/chat",
                        "response_content_style": "choices_text",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_PROVIDER_PROFILE", "vendor-b")
    monkeypatch.setenv("OPENAI_PROVIDER_PROFILE_FILE", str(profile_path))

    client = OpenAICompatibleLLMClient.from_env(model="env-model")

    assert client.provider_capabilities.profile_name == "vendor-b"
    assert client.provider_capabilities.chat_completions_path == "/vendor-b/chat"
    assert client.provider_capabilities.response_format_style == "none"
    assert client.provider_capabilities.response_content_style == "choices_text"


def test_cli_parser_accepts_custom_provider_profile_name() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "ingest",
            "benchmarks/fixtures/sample_transcript.json",
            "--backend",
            "llm",
            "--provider-profile",
            "vendor-b",
        ]
    )

    assert args.provider_profile == "vendor-b"
