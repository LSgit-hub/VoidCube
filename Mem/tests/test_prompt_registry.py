from __future__ import annotations

from pathlib import Path

from memai import OpenAICompatibleLLMClient
from memai.prompt_registry import BUILTIN_PROMPT_PACKS, PromptRegistry


def test_prompt_registry_loads_default_prompt_pack() -> None:
    registry = PromptRegistry.default()
    prompt = registry.get("extractor.events")

    assert "timeline-worthy changes" in prompt


def test_prompt_registry_override_takes_priority(tmp_path: Path) -> None:
    (tmp_path / "events.txt").write_text("file prompt", encoding="utf-8")
    registry = PromptRegistry.from_path(tmp_path).with_override(
        "extractor.events",
        "override prompt",
    )

    assert registry.get("extractor.events") == "override prompt"


def test_prompt_registry_loads_builtin_variant() -> None:
    registry = PromptRegistry.builtin("conservative")
    prompt = registry.get("scholar.arc")

    assert "conservative arc analyst" in prompt
    assert set(BUILTIN_PROMPT_PACKS) >= {
        "default",
        "conservative",
        "high-recall",
        "scholar-heavy",
    }


def test_client_complete_json_uses_prompt_registry_key() -> None:
    seen = {}

    def fake_transport(url, headers, payload):
        seen["system_prompt"] = payload["messages"][0]["content"]
        return {"choices": [{"message": {"content": '{"events": []}'}}]}

    registry = PromptRegistry.default().with_override(
        "extractor.events",
        "custom registry prompt",
    )
    client = OpenAICompatibleLLMClient(
        model="test-model",
        api_key="test-key",
        prompt_registry=registry,
        transport=fake_transport,
    )

    client.extract_events([])

    assert seen["system_prompt"] == "custom registry prompt"
