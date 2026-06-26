from __future__ import annotations

import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "firecrawl" not in sys.modules:
    sys.modules["firecrawl"] = types.SimpleNamespace(Firecrawl=object)
if "tools.web_tools_local" not in sys.modules:
    sys.modules["tools.web_tools_local"] = types.SimpleNamespace(local_web_extract=lambda url: {})

from tools.model_tools import handle_function_call
import tools.web_tools as web_tools


def test_handle_function_call_forwards_main_runtime_to_registry(monkeypatch):
    captured = {}

    def fake_dispatch(name, args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return json.dumps({"success": True})

    monkeypatch.setattr("tools.model_tools.registry.dispatch", fake_dispatch)

    runtime = {
        "provider": "custom",
        "model": "agnes-2.0-flash",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "api_key": "runtime-key",
        "api_mode": "chat_completions",
    }

    handle_function_call(
        "web_extract",
        {"urls": ["https://example.com"]},
        task_id="task-1",
        session_id="session-1",
        user_task="inspect page",
        main_runtime=runtime,
    )

    assert captured["name"] == "web_extract"
    assert captured["kwargs"]["task_id"] == "task-1"
    assert captured["kwargs"]["user_task"] == "inspect page"
    assert captured["kwargs"]["main_runtime"] == runtime


@pytest.mark.asyncio
@pytest.mark.unit
async def test_web_extract_uses_main_runtime_for_auxiliary_resolution(monkeypatch):
    captured = {}

    monkeypatch.setattr(web_tools, "_get_backend", lambda: "local")
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda *, main_runtime=None: True)

    def fake_local_web_extract(url: str):
        return {
            "url": url,
            "title": "Example",
            "content": "X" * 7000,
            "metadata": {"title": "Example"},
            "success": True,
        }

    def fake_resolve(model=None, *, main_runtime=None):
        captured["model"] = model
        captured["main_runtime"] = main_runtime
        return object(), "runtime-web-model", {}

    async def fake_async_call_llm(**kwargs):
        captured["call_kwargs"] = kwargs

        class _Msg:
            content = "processed summary"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr(sys.modules["tools.web_tools_local"], "local_web_extract", fake_local_web_extract)
    monkeypatch.setattr(web_tools, "_resolve_web_extract_auxiliary", fake_resolve)
    monkeypatch.setattr(web_tools, "async_call_llm", fake_async_call_llm)

    runtime = {
        "provider": "custom",
        "model": "agnes-2.0-flash",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "api_key": "runtime-key",
        "api_mode": "chat_completions",
    }

    payload = json.loads(
        await web_tools.web_extract_tool(
            ["https://example.com"],
            use_llm_processing=True,
            main_runtime=runtime,
        )
    )

    assert captured["main_runtime"] == runtime
    assert captured["call_kwargs"]["main_runtime"] == runtime
    assert payload["results"][0]["content"] == "processed summary"
