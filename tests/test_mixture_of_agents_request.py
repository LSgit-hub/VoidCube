from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.integration_policy import RETIRED_INTEGRATION_MARKERS
from tools import mixture_of_agents_tool as moa


pytestmark = pytest.mark.unit


def test_moa_request_uses_shared_limits_reasoning_and_temperature():
    kwargs = moa._build_openrouter_request(
        model="qwen/qwen3.6-plus",
        messages=[{"role": "user", "content": "solve"}],
        temperature=0.6,
        max_tokens=32000,
    )

    assert kwargs["model"] == "qwen/qwen3.6-plus"
    assert kwargs["max_tokens"] == 32000
    assert kwargs["temperature"] == 0.6
    assert kwargs["extra_body"]["reasoning"] == {
        "enabled": True,
        "effort": "xhigh",
    }


def test_moa_request_omits_temperature_for_namespaced_gpt_model():
    kwargs = moa._build_openrouter_request(
        model="openai/gpt-5.4-pro",
        messages=[{"role": "user", "content": "solve"}],
        temperature=0.4,
        max_tokens=None,
    )

    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


def test_moa_request_rejects_retired_model_before_client_access():
    marker = RETIRED_INTEGRATION_MARKERS[2]

    with pytest.raises(ValueError, match="retired by project policy"):
        moa._build_openrouter_request(
            model=f"vendor/{marker}-model",
            messages=[{"role": "user", "content": "solve"}],
            temperature=0.4,
            max_tokens=100,
        )


@pytest.mark.asyncio
async def test_aggregator_uses_requested_model(monkeypatch):
    captured: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            message = SimpleNamespace(content="synthesized", reasoning_content=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )
    monkeypatch.setattr(moa, "_get_openrouter_client", lambda: client)

    result = await moa._run_aggregator_model(
        "qwen/custom-aggregator",
        "system",
        "user",
        max_tokens=777,
    )

    assert result == "synthesized"
    assert captured[0]["model"] == "qwen/custom-aggregator"
    assert captured[0]["max_tokens"] == 777
