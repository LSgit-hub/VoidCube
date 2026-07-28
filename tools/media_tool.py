#!/usr/bin/env python3
"""
媒体播放工具 — 将媒体 URL 推送到 VoidCube Web UI 播放。

Agent 调用此工具后，Web UI 会自动弹出播放器。
支持 YouTube、B站、直链音频/视频。
"""

import json
import logging
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)


def _supervisor_media_url() -> str:
    """解析 supervisor 的 media enqueue 端点地址。

    优先使用环境变量 ``SUPERVISOR_MEDIA_URL``，否则从网关地址推导。
    """
    import os

    env_url = os.getenv("SUPERVISOR_MEDIA_URL", "").strip()
    if env_url:
        return env_url.rstrip("/") + "/ui/media/enqueue"

    # 从 config 推导: gateway 地址 + /ui/media/enqueue
    try:
        from VoidCube_cli.config import load_config
        cfg = load_config()
        supervisor_cfg = cfg.get("supervisor", {})
        if isinstance(supervisor_cfg, dict):
            host = supervisor_cfg.get("host", "127.0.0.1")
            port = supervisor_cfg.get("port", 6102)
            return f"http://{host}:{port}/ui/media/enqueue"
    except Exception:
        pass

    # 最后 fallback: 默认 supervisor 端口
    return "http://127.0.0.1:6102/ui/media/enqueue"


def media_play(
    url: str,
    title: str = "",
    media_type: str = "auto",
    auto_play: bool = True,
) -> str:
    """将媒体 URL 加入 Web UI 播放队列。

    调用后 VoidCube 小屋会自动弹出底部播放条并开始播放。

    Args:
        url: 媒体 URL（YouTube、B站、或直链 mp3/mp4 等）
        title: 显示在播放器上的标题（可选，不传则显示 URL）
        media_type: "youtube" | "bilibili" | "audio" | "video" | "auto"
        auto_play: 是否自动展开播放，默认 True

    Returns:
        JSON 字符串，包含 status 和 queued 数量
    """
    payload: Dict[str, Any] = {
        "url": url,
        "title": title or url,
        "type": media_type,
        "auto_play": auto_play,
    }

    target = _supervisor_media_url()
    try:
        resp = httpx.post(target, json=payload, timeout=10.0)
        resp.raise_for_status()
        result = resp.json()
        logger.info("media_play OK: %s → %s", title or url, result)
        return json.dumps(result, ensure_ascii=False)
    except httpx.ConnectError:
        msg = f"无法连接 supervisor ({target})，请确认 VoidCube 已启动"
        logger.warning("media_play: %s", msg)
        return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)
    except Exception as e:
        logger.warning("media_play failed: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


# ── Registry ──
from tools.registry import registry

MEDIA_PLAY_SCHEMA = {
    "name": "media_play",
    "description": (
        "将音乐或视频加入 VoidCube Web UI 播放队列。"
        "用户说'播放某首歌'或'放某个视频'时，先用 web_search 找到 URL，"
        "再调用此工具推送到小屋播放器。"
        "支持 YouTube、B站、直链 mp3/mp4 等。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "媒体文件的 URL（YouTube/B站视频链接，或直链 mp3/mp4）",
            },
            "title": {
                "type": "string",
                "description": "显示在播放器上的标题，如'晴天 - 周杰伦'",
            },
            "media_type": {
                "type": "string",
                "enum": ["youtube", "bilibili", "audio", "video", "auto"],
                "description": "媒体类型，默认 auto 自动识别",
            },
            "auto_play": {
                "type": "boolean",
                "description": "是否自动展开播放面板，默认 true",
            },
        },
        "required": ["url"],
    },
}

registry.register(
    name="media_play",
    toolset="web",
    schema=MEDIA_PLAY_SCHEMA,
    handler=lambda args, **kw: media_play(
        url=args.get("url", ""),
        title=args.get("title", ""),
        media_type=args.get("media_type", "auto"),
        auto_play=args.get("auto_play", True),
    ),
    emoji="🎵",
)
