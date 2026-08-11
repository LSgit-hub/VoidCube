from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.media_tool import (
    _supervisor_delivery_url,
    _supervisor_media_url,
    media_control,
    media_display,
    media_play,
    media_playlist,
)


def test_media_url_uses_canonical_supervisor_config(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_MEDIA_URL", raising=False)
    config = SimpleNamespace(
        supervisor=SimpleNamespace(host="127.0.0.9", port=6002)
    )
    with patch("systems.config.load_config_from_env", return_value=config):
        assert _supervisor_media_url() == "http://127.0.0.9:6002/ui/media/enqueue"
        assert _supervisor_media_url("control") == "http://127.0.0.9:6002/ui/media/control"
        assert _supervisor_delivery_url() == "http://127.0.0.9:6002/ui/delivery/push"


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
    assert "account_status" in web_names
    assert playback_names == {"media_play", "media_playlist", "media_display", "media_control", "account_status"}


def test_media_playlist_posts_one_batch(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "accepted": 2}
    items = [{"url": "https://example.com/a.mp3", "type": "audio"}, {"url": "https://www.bilibili.com/video/BV1", "type": "bilibili"}]
    with patch("tools.media_tool.httpx.post", return_value=response) as post:
        result = media_playlist(items)
    assert '"accepted": 2' in result
    post.assert_called_once_with("http://127.0.0.1:6002/ui/media/playlist", json={"items": items, "queue_mode": "replace"}, timeout=10.0)


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


def test_media_display_pushes_inline_content_to_delivery_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"status": "ok", "current": {"delivery_id": "d1"}}

    with patch("tools.media_tool.httpx.post", return_value=response) as post:
        result = media_display(
            content="<h1>交付报告</h1>",
            title="日报",
            media_type="html",
            view_mode="fit",
        )

    assert '"status": "ok"' in result
    post.assert_called_once_with(
        "http://127.0.0.1:6002/ui/delivery/push",
        json={
            "url": "",
            "title": "日报",
            "type": "html",
            "auto_open": True,
            "view_mode": "fit",
            "content": "<h1>交付报告</h1>",
        },
        timeout=10.0,
    )


def test_media_display_uploads_local_file_before_delivery(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SUPERVISOR_MEDIA_URL", "http://127.0.0.1:6002")
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7\ncontent")
    uploaded = Mock()
    uploaded.raise_for_status.return_value = None
    uploaded.json.return_value = {
        "status": "ok",
        "artifact_id": "a" * 32,
        "filename": "report.pdf",
        "byte_size": source.stat().st_size,
        "mime_type": "application/pdf",
        "type": "document",
        "url": "http://127.0.0.1:6002/ui/delivery/assets/id/report.pdf",
    }
    pushed = Mock()
    pushed.raise_for_status.return_value = None
    pushed.json.return_value = {"status": "ok", "current": {"delivery_id": "d1"}}

    with patch("tools.media_tool.httpx.post", side_effect=[uploaded, pushed]) as post:
        result = media_display(file_path=str(source), title="PDF 报告")

    assert '"status": "ok"' in result
    upload_call, push_call = post.call_args_list
    assert upload_call.args[0] == "http://127.0.0.1:6002/ui/delivery/assets"
    assert upload_call.kwargs["headers"] == {
        "Content-Type": "application/pdf",
        "X-Artifact-Filename": "report.pdf",
    }
    assert upload_call.kwargs["timeout"] == 120.0
    assert push_call.args[0] == "http://127.0.0.1:6002/ui/delivery/push"
    assert push_call.kwargs["json"] == {
        "url": "http://127.0.0.1:6002/ui/delivery/assets/id/report.pdf",
        "title": "PDF 报告",
        "type": "document",
        "auto_open": True,
        "view_mode": "fit",
        "mime_type": "application/pdf",
        "artifact_id": "a" * 32,
        "filename": "report.pdf",
        "byte_size": source.stat().st_size,
    }


def test_media_display_rejects_mixed_local_and_remote_sources(tmp_path) -> None:
    source = tmp_path / "image.png"
    source.write_bytes(b"png")

    result = media_display(file_path=str(source), url="https://example.com/image.png")

    assert '"status": "error"' in result
    assert "不能与 url 或 content 同时使用" in result
