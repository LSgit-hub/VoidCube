from __future__ import annotations

import json
from types import SimpleNamespace

from VoidCube_app.multimodal_provider import resolve_multimodal_provider
from tools import media_generation_tool as media


def test_multimodal_provider_normalizes_legacy_base_url_and_root_result_endpoint():
    provider = resolve_multimodal_provider(
        {
            "multimodal": {
                "api_key": "unused",
                "base_url": "https://api.agnes-ai.cn/v1/chat/completions",
            }
        }
    )

    assert provider.base_url == "https://api.agnes-ai.cn/v1"
    assert provider.endpoint(provider.videos_path) == "https://api.agnes-ai.cn/v1/videos"
    assert provider.origin_endpoint(provider.video_result_path) == "https://api.agnes-ai.cn/agnesapi"


def test_image_generate_returns_structured_artifact(monkeypatch):
    provider = SimpleNamespace(
        configured=True,
        image_model="agnes-image-2.1-flash",
        image_generations_path="/images/generations",
        request_timeout_seconds=10,
        api_key="test-key",
        endpoint=lambda path: "https://api.agnes-ai.cn/v1" + path,
    )
    response = SimpleNamespace(
        is_error=False,
        json=lambda: {"data": [{"url": "https://cdn.example/image.png"}]},
    )
    monkeypatch.setattr(media, "_provider", lambda: provider)
    monkeypatch.setattr(media.httpx, "post", lambda *args, **kwargs: response)

    result = media.image_generate("a test image")

    assert result.artifacts[0].uri == "https://cdn.example/image.png"
    assert json.loads(result.content)["images"][0]["url"] == "https://cdn.example/image.png"


def test_video_generate_polls_root_result_endpoint(monkeypatch):
    calls: list[tuple[str, dict]] = []
    provider = SimpleNamespace(
        configured=True,
        video_model="agnes-video-v2.0",
        videos_path="/videos",
        video_result_path="/agnesapi",
        request_timeout_seconds=10,
        video_timeout_seconds=1,
        video_poll_interval_seconds=0,
        api_key="test-key",
        endpoint=lambda path: "https://api.agnes-ai.cn/v1" + path,
        origin_endpoint=lambda path: "https://api.agnes-ai.cn" + path,
    )

    def fake_post(url, **kwargs):
        calls.append((url, {}))
        return SimpleNamespace(is_error=False, json=lambda: {"video_id": "vid-1"})

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        return SimpleNamespace(
            is_error=False,
            json=lambda: {"status": "completed", "url": "https://cdn.example/video.mp4"},
        )

    monkeypatch.setattr(media, "_provider", lambda: provider)
    monkeypatch.setattr(media.httpx, "post", fake_post)
    monkeypatch.setattr(media.httpx, "get", fake_get)

    result = media.video_generate("a test video")

    assert calls == [
        ("https://api.agnes-ai.cn/v1/videos", {}),
        ("https://api.agnes-ai.cn/agnesapi", {"video_id": "vid-1"}),
    ]
    assert result.artifacts[0].uri == "https://cdn.example/video.mp4"
