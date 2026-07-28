from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.media_tool import _supervisor_media_url, media_play


def test_media_url_uses_canonical_supervisor_config(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_MEDIA_URL", raising=False)
    config = SimpleNamespace(
        supervisor=SimpleNamespace(host="127.0.0.9", port=6002)
    )
    with patch("systems.config.load_config_from_env", return_value=config):
        assert _supervisor_media_url() == "http://127.0.0.9:6002/ui/media/enqueue"


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
