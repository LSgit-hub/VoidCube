"""HTTP and SSE adapters for Supervisor UI runtime callbacks."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from systems.supervisor.ui_projection import format_supervisor_ui_event


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _sse_response(stream: Any) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


def supervisor_state_events(
    request: Request,
    *,
    load_state: Callable[[], Awaitable[Dict[str, Any]]],
    interval_seconds: float,
) -> StreamingResponse:
    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            state = await load_state()
            yield format_supervisor_ui_event("state", state)
            await asyncio.sleep(interval_seconds)

    return _sse_response(event_stream())


def voice_level_events(
    request: Request,
    *,
    realtime_status: Callable[[], Dict[str, Any]],
    interval_seconds: float = 0.1,
) -> StreamingResponse:
    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            yield format_supervisor_ui_event("level", realtime_status())
            await asyncio.sleep(interval_seconds)

    return _sse_response(event_stream())


def media_events(
    request: Request,
    *,
    current_media: Callable[[], Optional[Dict[str, Any]]],
    current_revision: Callable[[], int] = lambda: 0,
    queue_length: Callable[[], int] = lambda: 0,
    interval_seconds: float = 0.5,
) -> StreamingResponse:
    last_revision = -1

    async def event_stream():
        nonlocal last_revision
        while True:
            if await request.is_disconnected():
                break
            current = current_media()
            revision = int(
                current_revision() or (current or {}).get("_revision") or 0
            )
            if revision != last_revision:
                last_revision = revision
                if current:
                    yield format_supervisor_ui_event(
                        "play",
                        {
                            "url": current.get("url", ""),
                            "title": current.get("title", ""),
                            "type": current.get("type", "auto"),
                            "auto_play": current.get("auto_play", True),
                            "playback": current.get("playback", "playing"),
                            "media_id": current.get("media_id", ""),
                            "enqueued_at": current.get("_enqueued_at", ""),
                            "revision": revision,
                            "queue_remaining": queue_length(),
                        },
                    )
                else:
                    yield format_supervisor_ui_event(
                        "stop",
                        {"revision": revision, "queue_remaining": 0},
                    )
            await asyncio.sleep(interval_seconds)

    return _sse_response(event_stream())


def normalize_media_enqueue_body(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 字段")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="媒体 URL 必须使用 http 或 https")
    media_type = str(body.get("type") or "auto").strip().lower()
    if media_type not in {"auto", "bilibili", "audio", "video"}:
        raise HTTPException(status_code=400, detail="不支持的媒体类型")
    queue_mode = str(body.get("queue_mode") or "replace").strip().lower()
    if queue_mode not in {"replace", "enqueue"}:
        raise HTTPException(status_code=400, detail="不支持的队列模式")
    auto_play = body.get("auto_play", True)
    if not isinstance(auto_play, bool):
        raise HTTPException(status_code=400, detail="auto_play 必须是布尔值")
    normalized = {
        "url": url,
        "title": (body.get("title") or "").strip() or url,
        "type": media_type,
        "auto_play": auto_play,
    }
    if "queue_mode" in body:
        normalized["queue_mode"] = queue_mode
    return normalized


async def enqueue_media_request(
    request: Request,
    *,
    enqueue_media: Callable[[Dict[str, Any]], Dict[str, Any]],
    current_revision: Callable[[], int],
    queue_length: Callable[[], int] = lambda: 0,
    current_media: Callable[[], Optional[Dict[str, Any]]] = lambda: None,
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
    enqueue_media(normalize_media_enqueue_body(body))
    pending_count = queue_length()
    current = current_media()
    return {
        "status": "ok",
        "queued": pending_count + (1 if current else 0),
        "queue_length": pending_count,
        "current": current,
        "revision": current_revision(),
    }


async def control_media_request(
    request: Request,
    *,
    control_media: Callable[..., Optional[Dict[str, Any]]],
    current_revision: Callable[[], int],
    queue_length: Callable[[], int],
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    action = str(body.get("action") or "").strip().lower()
    if action not in {"pause", "resume", "next", "ended", "stop", "clear"}:
        raise HTTPException(status_code=400, detail="不支持的媒体控制动作")
    current = control_media(action, str(body.get("media_id") or "").strip())
    return {
        "status": "ok",
        "action": action,
        "current": current,
        "queue_length": queue_length(),
        "revision": current_revision(),
    }
