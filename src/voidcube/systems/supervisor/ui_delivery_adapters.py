"""HTTP, storage, and SSE adapters for the Agent delivery panel."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .ui_projection import format_supervisor_ui_event


JsonDict = Dict[str, Any]
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
VALID_DELIVERY_TYPES = {
    "auto",
    "audio",
    "document",
    "file",
    "html",
    "image",
    "text",
    "video",
    "webpage",
}
VALID_VIEW_MODES = {"fit", "actual", "fill"}
AUTONOMOUS_DELIVERY_MODES = {"auto", "auto_evolution", "autonomous"}
AUTONOMOUS_DELIVERY_SOURCES = {"autonomous_worker", "autonomous_chain", "auto_evolution"}
MAX_INLINE_CONTENT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_ARTIFACT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_UNSAFE_FILENAME_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")


def infer_delivery_type(*, url: str = "", mime_type: str = "", filename: str = "") -> str:
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "document"
    if mime == "text/html":
        return "html" if filename or not url else "webpage"
    if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
        return "text"

    path = str(filename or urlparse(url).path or "").lower()
    suffix = Path(path).suffix
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico", ".avif"}:
        return "image"
    if suffix in {".mp4", ".webm", ".mov", ".m4v"}:
        return "video"
    if suffix in {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus"}:
        return "audio"
    if suffix == ".pdf":
        return "document"
    if suffix in {".html", ".htm"}:
        return "html" if filename or not url else "webpage"
    if suffix in {
        ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".json",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".py", ".js",
        ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".ps1",
    }:
        return "text"
    if suffix in {
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
        ".odp", ".rtf", ".epub", ".zip", ".7z", ".rar", ".tar", ".gz",
    }:
        return "file"
    return "webpage" if url and not filename else "file"


def sanitize_artifact_filename(value: str) -> str:
    filename = Path(unquote(str(value or "artifact"))).name.strip().strip(".")
    filename = _UNSAFE_FILENAME_RE.sub("_", filename)
    if not filename:
        filename = "artifact"
    if len(filename) > 140:
        suffix = Path(filename).suffix[:20]
        filename = filename[: 140 - len(suffix)].rstrip() + suffix
    return filename


def normalize_delivery_body(body: Any) -> JsonDict:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    url = str(body.get("url") or "").strip()
    content = str(body.get("content") or "").strip()
    mime_type = str(body.get("mime_type") or "").strip()
    filename = sanitize_artifact_filename(str(body.get("filename") or "")) if body.get("filename") else ""
    requested_type = str(body.get("type") or "auto").strip().lower()
    mode = str(body.get("mode") or body.get("stellar_mode") or "").strip().lower()
    requested_via = str(body.get("requested_via") or "").strip().lower()
    source_kind = str(body.get("source_kind") or body.get("source_lane") or "").strip().lower()
    if (
        mode in AUTONOMOUS_DELIVERY_MODES
        or requested_via in AUTONOMOUS_DELIVERY_SOURCES
        or source_kind in AUTONOMOUS_DELIVERY_SOURCES
        or str(body.get("autonomous_task_id") or "").strip()
    ):
        raise HTTPException(
            status_code=403,
            detail="Auto 员工结果必须回写 Mem，不得进入交付面板",
        )
    if requested_type not in VALID_DELIVERY_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的交付类型: {requested_type}")
    delivery_type = (
        infer_delivery_type(url=url, mime_type=mime_type, filename=filename)
        if requested_type == "auto"
        else requested_type
    )
    if not url and not content:
        raise HTTPException(status_code=400, detail="交付内容必须提供 url 或 content")
    if content and len(content.encode("utf-8")) > MAX_INLINE_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="内联内容不能超过 2 MiB，请改用文件上传")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="交付 URL 必须使用 http 或 https")

    view_mode = str(body.get("view_mode") or "fit").strip().lower()
    if view_mode not in VALID_VIEW_MODES:
        raise HTTPException(status_code=400, detail=f"不支持的查看模式: {view_mode}")
    auto_open = body.get("auto_open", body.get("auto_play", True))
    if not isinstance(auto_open, bool):
        raise HTTPException(status_code=400, detail="auto_open 必须是布尔值")

    normalized: JsonDict = {
        "url": url,
        "title": str(body.get("title") or "").strip() or filename or url or "Agent 交付内容",
        "type": delivery_type,
        "auto_open": auto_open,
        "view_mode": view_mode,
    }
    for key, value in (
        ("mode", mode),
        ("requested_via", requested_via),
        ("source_kind", source_kind),
    ):
        if value:
            normalized[key] = value
    source_task_id = str(body.get("source_task_id") or "").strip()
    if source_task_id:
        normalized["source_task_id"] = source_task_id[:120]
    if content:
        normalized["content"] = content
    if mime_type:
        normalized["mime_type"] = mime_type
    if filename:
        normalized["filename"] = filename
    for key in ("artifact_id", "source_url"):
        value = str(body.get(key) or "").strip()
        if value:
            normalized[key] = value
    try:
        byte_size = max(int(body.get("byte_size") or 0), 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="byte_size 必须是整数") from None
    if byte_size:
        normalized["byte_size"] = byte_size
    for key in ("width", "height"):
        try:
            value = max(int(body.get(key) or 0), 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} 必须是整数") from None
        if value:
            normalized[key] = value
    try:
        aspect_ratio = float(body.get("aspect_ratio") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="aspect_ratio 必须是数字") from None
    if aspect_ratio:
        if not 0.05 <= aspect_ratio <= 20:
            raise HTTPException(status_code=400, detail="aspect_ratio 超出有效范围")
        normalized["aspect_ratio"] = aspect_ratio
    return normalized


def _delivery_summary(item: JsonDict) -> JsonDict:
    return {key: value for key, value in item.items() if key != "content"}


def delivery_events(
    request: Request,
    *,
    current_delivery: Callable[[], Optional[JsonDict]],
    current_revision: Callable[[], int],
    delivery_items: Callable[[], list[JsonDict]],
    interval_seconds: float = 0.5,
) -> StreamingResponse:
    last_revision = -1

    async def event_stream():
        nonlocal last_revision
        while True:
            if await request.is_disconnected():
                break
            revision = int(current_revision())
            if revision != last_revision:
                last_revision = revision
                yield format_supervisor_ui_event(
                    "delivery",
                    {
                        "current": current_delivery(),
                        "history": [_delivery_summary(item) for item in delivery_items()],
                        "revision": revision,
                    },
                )
            await asyncio.sleep(interval_seconds)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=dict(SSE_HEADERS)
    )


async def push_delivery_request(
    request: Request,
    *,
    push_delivery: Callable[[JsonDict], JsonDict],
    delivery_items: Callable[[], list[JsonDict]],
    current_revision: Callable[[], int],
) -> JsonDict:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
    current = push_delivery(normalize_delivery_body(body))
    return {
        "status": "ok",
        "current": current,
        "history": [_delivery_summary(item) for item in delivery_items()],
        "revision": current_revision(),
    }


async def control_delivery_request(
    request: Request,
    *,
    select_delivery: Callable[[str], Optional[JsonDict]],
    clear_deliveries: Callable[[], None],
    delivery_items: Callable[[], list[JsonDict]],
    current_revision: Callable[[], int],
) -> JsonDict:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON")
    action = str(body.get("action") or "").strip().lower()
    if action == "select":
        current = select_delivery(str(body.get("delivery_id") or ""))
    elif action == "clear":
        clear_deliveries()
        current = None
    else:
        raise HTTPException(status_code=400, detail="不支持的交付面板动作")
    return {
        "status": "ok",
        "action": action,
        "current": current,
        "history": [_delivery_summary(item) for item in delivery_items()],
        "revision": current_revision(),
    }


async def upload_delivery_asset(
    request: Request,
    *,
    artifact_root: Path,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> JsonDict:
    filename = sanitize_artifact_filename(request.headers.get("x-artifact-filename", "artifact"))
    mime_type = str(request.headers.get("content-type") or "application/octet-stream").split(";", 1)[0]
    artifact_id = uuid4().hex
    target_dir = artifact_root / artifact_id
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / filename
    byte_size = 0
    try:
        with target.open("wb") as output:
            async for chunk in request.stream():
                byte_size += len(chunk)
                if byte_size > max_bytes:
                    raise HTTPException(status_code=413, detail="单个交付文件不能超过 256 MiB")
                output.write(chunk)
        if byte_size == 0:
            raise HTTPException(status_code=400, detail="交付文件不能为空")
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    relative_url = f"/ui/delivery/assets/{artifact_id}/{quote(filename)}"
    absolute_url = str(request.base_url).rstrip("/") + relative_url
    return {
        "status": "ok",
        "artifact_id": artifact_id,
        "filename": filename,
        "byte_size": byte_size,
        "mime_type": mime_type,
        "type": infer_delivery_type(url=absolute_url, mime_type=mime_type, filename=filename),
        "url": absolute_url,
    }


def serve_delivery_asset(
    artifact_id: str,
    filename: str,
    *,
    artifact_root: Path,
) -> FileResponse:
    normalized_id = str(artifact_id or "").strip().lower()
    safe_filename = sanitize_artifact_filename(filename)
    if not _ARTIFACT_ID_RE.fullmatch(normalized_id) or safe_filename != filename:
        raise HTTPException(status_code=404, detail="交付文件不存在")
    root = artifact_root.resolve()
    target = (root / normalized_id / safe_filename).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="交付文件不存在")
    media_type = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    encoded_name = quote(safe_filename)
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        "X-Content-Type-Options": "nosniff",
    }
    if media_type in {"text/html", "image/svg+xml"}:
        headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src * data:; "
            "style-src 'unsafe-inline'; font-src data:"
        )
    return FileResponse(
        target,
        media_type=media_type,
        headers=headers,
    )


def remove_delivery_assets(items: list[JsonDict], *, artifact_root: Path) -> None:
    """Delete only managed copies explicitly referenced by cleared deliveries."""
    root = artifact_root.resolve()
    artifact_ids = {
        str(item.get("artifact_id") or "").strip().lower() for item in items
    }
    for artifact_id in artifact_ids:
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            continue
        target = (root / artifact_id).resolve()
        if root in target.parents and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "control_delivery_request",
    "delivery_events",
    "infer_delivery_type",
    "normalize_delivery_body",
    "push_delivery_request",
    "remove_delivery_assets",
    "sanitize_artifact_filename",
    "serve_delivery_asset",
    "upload_delivery_asset",
]
