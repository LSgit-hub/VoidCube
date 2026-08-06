from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.media_tool import _supervisor_media_url, media_control, media_play


def test_media_url_uses_canonical_supervisor_config(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_MEDIA_URL", raising=False)
    config = SimpleNamespace(
        supervisor=SimpleNamespace(host="127.0.0.9", port=6002)
    )
    with patch("systems.config.load_config_from_env", return_value=config):
        assert _supervisor_media_url() == "http://127.0.0.9:6002/ui/media/enqueue"
        assert _supervisor_media_url("control") == "http://127.0.0.9:6002/ui/media/control"


def test_media_play_posts_to_supervisor_enqueue(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "queued": 1}

    with patch("tools.media_tool.httpx.post", return_value=response) as post:
        result = media_play(
            "https://example.com/test.mp3",
            title="测试音频",
            media_type="audio",
        )

    assert '"status": "ok"' in result
    post.assert_called_once_with(
        "http://127.0.0.1:6002/ui/media/enqueue",
        json={
            "url": "https://example.com/test.mp3",
            "title": "测试音频",
            "type": "audio",
            "auto_play": True,
        },
        timeout=10.0,
    )


def test_media_play_is_available_to_web_and_playback_agents() -> None:
    from tools.model_tools import get_tool_definitions

    web_names = {
        item["function"]["name"]
        for item in get_tool_definitions(["web"], quiet_mode=True)
    }
    playback_names = {
        item["function"]["name"]
        for item in get_tool_definitions(["playback"], quiet_mode=True)
    }
    assert "media_play" in web_names
    assert playback_names == {"media_play", "media_control"}


def test_default_toolset_exposes_reconnected_core_tools() -> None:
    from tools.model_tools import get_tool_definitions

    names = {
        item["function"]["name"]
        for item in get_tool_definitions(["voidcube"], quiet_mode=True)
    }

    assert {"delegate_task", "todo", "clarify", "check_dependencies"} <= names


def test_media_play_can_request_queue_mode(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "queued": 2}

    with patch("tools.media_tool.httpx.post", return_value=response) as post:
        media_play(
            "https://example.com/next.mp4",
            media_type="video",
            queue_mode="enqueue",
        )

    assert post.call_args.kwargs["json"]["queue_mode"] == "enqueue"


def test_media_control_posts_to_shared_supervisor_player(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "action": "pause"}

    with patch("tools.media_tool.httpx.post", return_value=response) as post:
        result = media_control("pause")

    assert '"action": "pause"' in result
    post.assert_called_once_with(
        "http://127.0.0.1:6002/ui/media/control",
        json={"action": "pause"},
        timeout=10.0,
    )
