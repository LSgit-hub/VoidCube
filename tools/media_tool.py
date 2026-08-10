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


def account_status() -> str:
    """查询平台账号登录状态（B站等），返回已登录/已过期/未配置。

    Agent 在播放 B站视频前可调用此工具检查是否有可用登录态。
    如无登录态，可提示用户在账号中心添加 Cookie 以获得高清播放。
    """
    try:
        from systems.supervisor.account_store import account_for_api, load_accounts

        accounts = load_accounts()
        if not accounts:
            return json.dumps(
                {"status": "ok", "accounts": [], "hint": "暂无已配置的平台账号。请在 VoidCube 底部 dock 的账号中心添加 B站 Cookie 以获得高清播放。"},
                ensure_ascii=False,
            )
        result = {
            "status": "ok",
            "accounts": [account_for_api(a) for a in accounts],
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


def media_playlist(items: list[Dict[str, Any]], queue_mode: str = "replace") -> str:
    """一次性将整张歌单推送到 VoidCube 播放列表。成功后即可汇报完成。"""
    return _post_media(
        _supervisor_media_url("playlist"),
        {"items": items, "queue_mode": queue_mode},
        operation="media_playlist",
    )


def media_display(
    url: str = "",
    content: str = "",
    title: str = "",
    media_type: str = "auto",
    auto_play: bool = True,
    mime_type: str = "",
) -> str:
    """将任意内容推送到 VoidCube Web UI 多媒体展板展示。

    这是通用展示板工具，Agent 可以用它向用户展示各种类型的媒体内容。
    与 media_play 不同，media_display 专注于可视化展示而非后台播放。

    适用场景：
    - Agent 生成的图片：传 url + type="image"
    - 生成的报告/文档：传 content (HTML格式) + type="html"
    - 任意网页：传 url + type="webpage"
    - PDF 文档：传 url + type="document"
    - 音乐/视频：同 media_play，传 url + type="audio"/"video"

    Args:
        url: 媒体 URL（html 类型可省略，用 content 代替）
        content: 内联 HTML 或文本内容（仅 html 类型有效）
        title: 显示在展板上的标题
        media_type: "image" | "document" | "webpage" | "html" | "audio" | "video" | "bilibili" | "auto"
        auto_play: 是否自动展开展板，默认 True
        mime_type: 显式指定 MIME 类型（可选，如 text/html）

    Returns:
        JSON 字符串，包含 status 和展示状态
    """
    payload: Dict[str, Any] = {
        "url": url,
        "title": title or url or "Agent 展示内容",
        "type": media_type,
        "auto_play": auto_play,
    }
    if content:
        payload["content"] = content
    if mime_type:
        payload["mime_type"] = mime_type

    return _post_media(_supervisor_media_url(), payload, operation="media_display")


# ── Registry ──
from tools.registry import registry

MEDIA_PLAY_SCHEMA = {
    "name": "media_play",
    "description": (
        "将音乐或视频加入 VoidCube Web UI 播放队列。"
        "用户说'播放某首歌'或'放某个视频'时，先用 web_search 找到 URL，"
        "再调用此工具推送到小屋播放器。"
        "支持 B站视频页和 http/https 直链 mp3/mp4；不支持普通网页或搜索结果页。"
        "注意：B站高清播放和部分影视内容需要账号登录。如用户需要高清，"
        "可提示用户在账号中心（底部 dock → 🔑 账号）添加 B站 Cookie。"
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
                "enum": ["bilibili", "audio", "video", "image", "document", "webpage", "html", "auto"],
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

MEDIA_DISPLAY_SCHEMA = {
    "name": "media_display",
    "description": (
        "向 VoidCube Web UI 多媒体展板推送任意可视化内容。"
        "Agent 生成图片/文档/网页后调用此工具展示给用户。"
        "图片传 url+type='image'；HTML 报告传 content+type='html'；"
        "网页传 url+type='webpage'；PDF 传 url+type='document'。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "媒体 URL。html 类型可用 content 代替，其余类型必填",
            },
            "content": {
                "type": "string",
                "description": "内联 HTML 或文本内容。仅 html 类型有效，支持完整 HTML/CSS",
            },
            "title": {
                "type": "string",
                "description": "显示在展板上的标题",
            },
            "media_type": {
                "type": "string",
                "enum": ["image", "document", "webpage", "html", "audio", "video", "bilibili", "auto"],
                "description": "内容类型。image=图片, document=PDF, webpage=网页嵌入, html=富文本",
            },
            "auto_play": {
                "type": "boolean",
                "description": "是否自动展开展板面板，默认 true",
            },
            "mime_type": {
                "type": "string",
                "description": "显式 MIME 类型（可选），如 text/html",
            },
        },
        "required": [],
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

MEDIA_PLAYLIST_SCHEMA = {
    "name": "media_playlist",
    "description": (
        "一次性将多首已找到 URL 的音乐或视频推送到 VoidCube Web UI 播放列表。"
        "返回 status=ok 即表示整张歌单已接受；不要再调用 browser_navigate、browser_snapshot 或 check_port 验证。"
        "第一批使用 replace，后续追加使用 enqueue。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 200,
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "media_type": {"type": "string", "enum": ["bilibili", "audio", "video", "auto"]},
                        "auto_play": {"type": "boolean"},
                    },
                    "required": ["url"],
                },
            },
            "queue_mode": {"type": "string", "enum": ["replace", "enqueue"]},
        },
        "required": ["items"],
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
    name="media_playlist",
    toolset="playback",
    schema=MEDIA_PLAYLIST_SCHEMA,
    handler=lambda args, **kw: media_playlist(
        items=[
            {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "type": item.get("media_type", "auto"),
                "auto_play": item.get("auto_play", True),
            }
            for item in args.get("items", [])
        ],
        queue_mode=args.get("queue_mode", "replace"),
    ),
    emoji="🎶",
)
registry.register(
    name="media_display",
    toolset="media",
    schema=MEDIA_DISPLAY_SCHEMA,
    handler=lambda args, **kw: media_display(
        url=args.get("url", ""),
        content=args.get("content", ""),
        title=args.get("title", ""),
        media_type=args.get("media_type", "auto"),
        auto_play=args.get("auto_play", True),
        mime_type=args.get("mime_type", ""),
    ),
    emoji="🖼",
)
registry.register(
    name="media_control",
    toolset="web",
    schema=MEDIA_CONTROL_SCHEMA,
    handler=lambda args, **kw: media_control(args.get("action", "")),
    emoji="⏯",
)

ACCOUNT_STATUS_SCHEMA = {
    "name": "account_status",
    "description": (
        "查询 VoidCube 已配置的平台账号登录状态（B站等）。"
        "在播放 B站视频前可调用此工具检查是否有可用登录态，"
        "以确定能否获得高清播放和会员内容访问。"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

registry.register(
    name="account_status",
    toolset="web",
    schema=ACCOUNT_STATUS_SCHEMA,
    handler=lambda args, **kw: account_status(),
    emoji="🔑",
)
