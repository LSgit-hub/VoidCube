from __future__ import annotations

import json
from types import SimpleNamespace

from voidcube.infrastructure.providers.media_generation import (
    resolve_image_generation_config,
    resolve_video_generation_config,
)
import voidcube.extensions.tools.media.media_generation_tool as media


def test_image_and_video_generation_use_independent_endpoints():
    config = {
        "image_generation": {
            "endpoint": "https://images.example/v1/images/generations",
            "model": "image-model",
        },
        "video_generation": {
            "endpoint": "https://videos.example/v1/videos",
            "result_endpoint": "https://videos.example/agnesapi",
            "model": "video-model",
        },
    }

    image = resolve_image_generation_config(config)
    video = resolve_video_generation_config(config)

    assert image.endpoint == "https://images.example/v1/images/generations"
    assert image.model == "image-model"
    assert video.endpoint == "https://videos.example/v1/videos"
    assert video.result_endpoint == "https://videos.example/agnesapi"
    assert video.model == "video-model"


def test_legacy_multimodal_section_is_not_used():
    config = {
        "multimodal": {
            "base_url": "https://legacy.example/v1",
            "image_model": "legacy-image",
            "video_model": "legacy-video",
        }
    }

    image = resolve_image_generation_config(config)
    video = resolve_video_generation_config(config)

    assert image.model == "agnes-image-2.1-flash"
    assert video.model == "agnes-video-v2.0"


def test_image_generate_posts_to_configured_image_endpoint(monkeypatch):
    provider = SimpleNamespace(
        configured=True,
        model="agnes-image-2.1-flash",
        endpoint="https://api.agnes-ai.cn/v1/images/generations",
        request_timeout_seconds=10,
        api_key="test-key",
    )
    calls = []
    response = SimpleNamespace(
        is_error=False,
        json=lambda: {"data": [{"url": "https://cdn.example/image.png"}]},
    )
    monkeypatch.setattr(media, "_image_provider", lambda: provider)
    monkeypatch.setattr(
        media.httpx,
        "post",
        lambda url, **kwargs: calls.append(url) or response,
    )

    result = media.image_generate("a test image")

    assert calls == ["https://api.agnes-ai.cn/v1/images/generations"]
    assert result.artifacts[0].uri == "https://cdn.example/image.png"
    assert json.loads(result.content)["images"][0]["url"] == "https://cdn.example/image.png"


def test_video_generate_posts_and_polls_configured_video_endpoints(monkeypatch):
    calls: list[tuple[str, dict]] = []
    provider = SimpleNamespace(
        configured=True,
        model="agnes-video-v2.0",
        endpoint="https://api.agnes-ai.cn/v1/videos",
        result_endpoint="https://api.agnes-ai.cn/agnesapi",
        request_timeout_seconds=10,
        timeout_seconds=1,
        poll_interval_seconds=0,
        api_key="test-key",
    )

    def fake_post(url, **kwargs):
        calls.append((url, kwargs.get("json") or {}))
        return SimpleNamespace(is_error=False, json=lambda: {"video_id": "vid-1"})

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        return SimpleNamespace(
            is_error=False,
            json=lambda: {"status": "completed", "url": "https://cdn.example/video.mp4"},
        )

    monkeypatch.setattr(media, "_video_provider", lambda: provider)
    monkeypatch.setattr(media.httpx, "post", fake_post)
    monkeypatch.setattr(media.httpx, "get", fake_get)

    result = media.video_generate("a test video", model="default")

    assert calls[0][0] == "https://api.agnes-ai.cn/v1/videos"
    assert calls[0][1]["model"] == "agnes-video-v2.0"
    assert calls[1] == (
        "https://api.agnes-ai.cn/agnesapi",
        {"video_id": "vid-1"},
    )
    assert result.artifacts[0].uri == "https://cdn.example/video.mp4"


def test_image_generate_uses_configured_model_for_generic_placeholder(monkeypatch):
    provider = SimpleNamespace(
        configured=True,
        model="agnes-image-2.1-flash",
        endpoint="https://api.agnes-ai.cn/v1/images/generations",
        request_timeout_seconds=10,
        api_key="test-key",
    )
    payloads = []
    response = SimpleNamespace(
        is_error=False,
        json=lambda: {"data": [{"url": "https://cdn.example/image.png"}]},
    )
    monkeypatch.setattr(media, "_image_provider", lambda: provider)
    monkeypatch.setattr(
        media.httpx,
        "post",
        lambda url, **kwargs: payloads.append(kwargs["json"]) or response,
    )

    media.image_generate("a test image", model="auto")

    assert payloads[0]["model"] == "agnes-image-2.1-flash"
