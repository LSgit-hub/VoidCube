"""HTTP and SSE adapters for Supervisor UI runtime callbacks."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

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
    interval_seconds: float = 0.5,
) -> StreamingResponse:
    last_revision = 0

    async def event_stream():
        nonlocal last_revision
        while True:
            if await request.is_disconnected():
                break
            current = current_media()
            if current:
                revision = int(current.get("_revision") or 0)
                if revision != last_revision:
                    last_revision = revision
                    yield format_supervisor_ui_event(
                        "play",
                        {
                            "url": current.get("url", ""),
                            "title": current.get("title", ""),
                            "type": current.get("type", "auto"),
                            "auto_play": current.get("auto_play", True),
                            "enqueued_at": current.get("_enqueued_at", ""),
                            "revision": revision,
                            "queue_remaining": 0,
                        },
                    )
            await asyncio.sleep(interval_seconds)

    return _sse_response(event_stream())


def normalize_media_enqueue_body(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 字段")
    return {
        "url": url,
        "title": (body.get("title") or "").strip() or url,
        "type": (body.get("type") or "auto").strip(),
        "auto_play": body.get("auto_play", True),
    }


async def enqueue_media_request(
    request: Request,
    *,
    enqueue_media: Callable[[Dict[str, Any]], None],
    current_revision: Callable[[], int],
) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
    enqueue_media(normalize_media_enqueue_body(body))
    return {"status": "ok", "queued": 1, "revision": current_revision()}
