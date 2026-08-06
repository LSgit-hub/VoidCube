#!/usr/bin/env python3
"""
媒体播放工具 — 将媒体 URL 推送到 VoidCube Web UI 播放。

Agent 调用此工具后，Web UI 会自动弹出播放器。
支持 B站和 http/https 直链音频、视频。
"""

import json
import logging
from typing import Any, Dict
import httpx

logger = logging.getLogger(__name__)


def _supervisor_media_url(endpoint: str = "enqueue") -> str:
    """解析 supervisor 的媒体端点地址。

    优先使用环境变量 ``SUPERVISOR_MEDIA_URL``，否则读取 Supervisor 主配置。
    """
    import os

    env_url = os.getenv("SUPERVISOR_MEDIA_URL", "").strip()
    if env_url:
        return env_url.rstrip("/") + f"/ui/media/{endpoint}"

    # Supervisor 的主配置由 systems.config 统一解析，包含环境变量覆盖。
    try:
        from systems.config import load_config_from_env

        supervisor = load_config_from_env().supervisor
        return f"http://{supervisor.host}:{supervisor.port}/ui/media/{endpoint}"
    except Exception:
        pass

    return f"http://127.0.0.1:6002/ui/media/{endpoint}"


def _post_media(target: str, payload: Dict[str, Any], *, operation: str) -> str:
    try:
        resp = httpx.post(target, json=payload, timeout=10.0)
        resp.raise_for_status()
        result = resp.json()
        logger.info("%s OK: %s", operation, result)
        return json.dumps(result, ensure_ascii=False)
    except httpx.ConnectError:
        msg = f"无法连接 supervisor ({target})，请确认 VoidCube 已启动"
        logger.warning("%s: %s", operation, msg)
        return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)
    except Exception as exc:
        logger.warning("%s failed: %s", operation, exc)
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


def media_play(
    url: str,
    title: str = "",
    media_type: str = "auto",
    auto_play: bool = True,
    queue_mode: str = "replace",
) -> str:
    """将媒体 URL 加入 Web UI 播放队列。

    调用后 VoidCube 小屋会自动弹出底部播放条并开始播放。

    Args:
        url: B站视频页 URL，或直链 mp3/mp4 等媒体 URL
        title: 显示在播放器上的标题（可选，不传则显示 URL）
        media_type: "bilibili" | "audio" | "video" | "auto"
        auto_play: 是否自动展开播放，默认 True
        queue_mode: "replace" 立即替换当前播放，或 "enqueue" 加入队列

    Returns:
        JSON 字符串，包含 status 和 queued 数量
    """
    payload: Dict[str, Any] = {
        "url": url,
        "title": title or url,
        "type": media_type,
        "auto_play": auto_play,
    }
    if queue_mode != "replace":
        payload["queue_mode"] = queue_mode

    return _post_media(_supervisor_media_url(), payload, operation="media_play")


def media_control(action: str) -> str:
    """控制 VoidCube Web UI 当前播放器。"""
    return _post_media(
        _supervisor_media_url("control"),
        {"action": action},
        operation="media_control",
    )


# ── Registry ──
from tools.registry import registry

MEDIA_PLAY_SCHEMA = {
    "name": "media_play",
    "description": (
        "将音乐或视频加入 VoidCube Web UI 播放队列。"
        "用户说'播放某首歌'或'放某个视频'时，先用 web_search 找到 URL，"
        "再调用此工具推送到小屋播放器。"
        "支持 B站视频页和 http/https 直链 mp3/mp4；不支持普通网页或搜索结果页。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "B站视频页 URL，或可直接访问的 mp3/mp4 等媒体 URL",
            },
            "title": {
                "type": "string",
                "description": "显示在播放器上的标题，如'晴天 - 周杰伦'",
            },
            "media_type": {
                "type": "string",
                "enum": ["bilibili", "audio", "video", "auto"],
                "description": "媒体类型，默认 auto 自动识别",
            },
            "auto_play": {
                "type": "boolean",
                "description": "是否自动展开播放面板，默认 true",
            },
            "queue_mode": {
                "type": "string",
                "enum": ["replace", "enqueue"],
                "description": "replace 立即播放并清空旧队列；enqueue 加入当前队列",
            },
        },
        "required": ["url"],
    },
}

MEDIA_CONTROL_SCHEMA = {
    "name": "media_control",
    "description": "控制 VoidCube Web UI 当前播放器，可暂停、继续、切换下一项或停止并清空队列。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pause", "resume", "next", "stop"],
                "description": "对当前播放器执行的控制动作",
            }
        },
        "required": ["action"],
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
        queue_mode=args.get("queue_mode", "replace"),
    ),
    emoji="🎵",
)
registry.register(
    name="media_control",
    toolset="web",
    schema=MEDIA_CONTROL_SCHEMA,
    handler=lambda args, **kw: media_control(args.get("action", "")),
    emoji="⏯",
)
