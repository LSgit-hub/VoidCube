from __future__ import annotations

import base64
import json


def test_vision_tool_uses_shared_auxiliary_router(monkeypatch, tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    monkeypatch.setattr(
        "voidcube.extensions.tools.media.vision_tools._vision_backends_available",
        lambda: True,
    )

    class Message:
        content = "图中有一个测试按钮。"

    class Response:
        choices = [type("Choice", (), {"message": Message()})()]

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("voidcube.infrastructure.providers.auxiliary_client.call_llm", fake_call_llm)
    result = json.loads(
        __import__(
            "voidcube.extensions.tools.media.vision_tools",
            fromlist=["vision_analyze_tool"],
        ).vision_analyze_tool(image_path=str(image), prompt="描述图片")
    )

    assert result == {
        "success": True,
        "analysis": "图中有一个测试按钮。",
        "images": [str(image)],
    }
    assert calls[0]["task"] == "vision"
    assert calls[0]["messages"][0]["content"][1]["type"] == "image_url"


def test_vision_tool_reports_missing_backend(monkeypatch, tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        "voidcube.extensions.tools.media.vision_tools._vision_backends_available",
        lambda: False,
    )

    from voidcube.extensions.tools.media.vision_tools import vision_analyze_tool

    result = json.loads(vision_analyze_tool(image_path=str(image), prompt="描述图片"))

    assert result == {
        "success": False,
        "error": "No configured vision backend is available",
        "available": False,
    }


def test_vision_tool_accepts_legacy_image_url_and_user_prompt_aliases(monkeypatch, tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        "voidcube.extensions.tools.media.vision_tools._vision_backends_available",
        lambda: False,
    )

    from voidcube.extensions.tools.media.vision_tools import vision_analyze_tool

    result = json.loads(
        vision_analyze_tool(image_url=str(image), user_prompt="描述图片")
    )

    assert result["error"] == "No configured vision backend is available"


def test_startup_vision_configuration_check_does_not_probe_models(monkeypatch):
    from voidcube.infrastructure.providers import auxiliary_client

    monkeypatch.setenv("OPENROUTER_API_KEY", "configured-for-test")
    monkeypatch.setattr(auxiliary_client, "_read_main_provider", lambda: "")
    monkeypatch.setattr(
        auxiliary_client,
        "_first_live_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("startup must not query the model endpoint")
        ),
    )

    assert "openrouter" in auxiliary_client.get_configured_vision_backends()
