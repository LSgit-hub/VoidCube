#!/usr/bin/env python3
"""Agnes-AI image and video tools exposed through the shared media toolset."""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Iterable

import httpx

from agent.tool_execution import ToolExecutionResult
from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.media_generation_provider import (
    AGNES_IMAGE_MODEL,
    AGNES_VIDEO_MODEL,
    image_generation_configured,
    resolve_image_generation_config,
    resolve_video_generation_config,
    video_generation_configured,
)

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _error(message: str) -> str:
    return _json({"success": False, "error": str(message)})


def _image_provider():
    provider = resolve_image_generation_config()
    if not provider.configured:
        raise RuntimeError(
            "未配置 Agnes-AI 图像模型，请运行 /api 选择图像模型配置"
        )
    return provider


def _video_provider():
    provider = resolve_video_generation_config()
    if not provider.configured:
        raise RuntimeError(
            "未配置 Agnes-AI 视频模型，请运行 /api 选择视频模型配置"
        )
    return provider


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _effective_model(requested: str, configured: str) -> str:
    """Resolve generic Agent placeholders to the model selected in /api."""
    value = str(requested or "").strip()
    if not value or value.lower() in {"default", "auto", "configured"}:
        return configured
    return value


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = error.get("message") or error.get("detail")
        else:
            detail = error
        if not detail and isinstance(payload, dict):
            detail = payload.get("message") or payload.get("detail")
        if detail:
            return f"API 错误: {response.status_code}: {detail}"
    except Exception:
        pass
    return f"API 错误: {response.status_code}"


def _media_values(value: Any, keys: Iterable[str]) -> list[str]:
    """Collect media URLs/base64 values from common OpenAI-style envelopes."""
    found: list[str] = []
    key_set = set(keys)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in key_set and isinstance(item, str) and item.strip():
                    found.append(item.strip())
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return list(dict.fromkeys(found))


def _artifact(kind: str, uri: str, *, title: str, model: str) -> Artifact | None:
    uri = str(uri or "").strip()
    if not uri:
        return None
    mime = "video/mp4" if kind == "video" else "image/*"
    return Artifact(kind=kind, uri=uri, mime_type=mime, title=title, metadata={"model": model})


def _image_result(payload: Any, *, model: str, prompt: str, size: str, response_format: str) -> ToolExecutionResult:
    values = _media_values(payload, ("url", "image_url", "b64_json", "base64", "data_uri"))
    images: list[dict[str, str]] = []
    artifacts: list[Artifact] = []
    for value in values:
        if value.startswith("data:"):
            item = {"url": value} if response_format == "url" else {"b64_json": value.split(",", 1)[-1]}
            uri = value
        elif value.startswith(("http://", "https://")):
            item = {"url": value}
            uri = value
        else:
            item = {"b64_json": value}
            uri = f"data:image/png;base64,{value}"
        item.setdefault("revised_prompt", prompt)
        images.append(item)
        media_artifact = _artifact("image", uri, title="Agnes-AI generated image", model=model)
        if media_artifact:
            artifacts.append(media_artifact)
    return ToolExecutionResult(
        content=_json({"success": True, "model": model, "size": size, "images": images}),
        artifacts=tuple(artifacts),
    )


def image_generate(
    prompt: str,
    model: str = "",
    size: str = "1024x1024",
    quality: str = "standard",
    n: int = 1,
    response_format: str = "url",
    **kwargs: Any,
) -> str | ToolExecutionResult:
    """Generate one or more images with the configured Agnes-AI provider."""
    try:
        provider = _image_provider()
        model = _effective_model(model, provider.model)
        payload = {"model": model, "prompt": prompt, "n": n, "size": size, "quality": quality, **kwargs}
        response = httpx.post(
            provider.endpoint,
            json=payload,
            headers=_headers(provider.api_key),
            timeout=provider.request_timeout_seconds,
        )
        if response.is_error:
            return _error(_response_error(response))
        return _image_result(response.json(), model=model, prompt=prompt, size=size, response_format=response_format)
    except Exception as exc:
        logger.warning("image_generate failed: %s", exc)
        return _error(str(exc))


def _read_image(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith(("http://", "https://", "data:")):
        return value
    with open(value, "rb") as handle:
        return f"data:image/png;base64,{base64.b64encode(handle.read()).decode('ascii')}"


def image_edit(
    image_path: str,
    prompt: str,
    mask_path: str = "",
    model: str = "",
    size: str = "1024x1024",
    n: int = 1,
    response_format: str = "url",
    **kwargs: Any,
) -> str | ToolExecutionResult:
    """Edit an image through the provider's configured image edit endpoint."""
    try:
        provider = _image_provider()
        model = _effective_model(model, provider.model)
        payload = {
            "model": model,
            "prompt": prompt,
            "image": _read_image(image_path),
            "n": n,
            "size": size,
            **kwargs,
        }
        if mask_path:
            payload["mask"] = _read_image(mask_path)
        response = httpx.post(
            provider.edit_endpoint,
            json=payload,
            headers=_headers(provider.api_key),
            timeout=provider.request_timeout_seconds,
        )
        if response.is_error:
            return _error(_response_error(response))
        return _image_result(response.json(), model=model, prompt=prompt, size=size, response_format=response_format)
    except Exception as exc:
        logger.warning("image_edit failed: %s", exc)
        return _error(str(exc))


def _video_payload(payload: Any) -> tuple[str, str]:
    urls = _media_values(payload, ("url", "video_url", "download_url", "video_uri"))
    video_id_values = _media_values(payload, ("video_id", "task_id", "job_id", "id"))
    return (urls[0] if urls else "", video_id_values[0] if video_id_values else "")


def _video_status(payload: Any) -> str:
    values = _media_values(payload, ("status", "state"))
    return values[0].strip().lower() if values else ""


def _poll_video(provider: Any, video_id: str) -> tuple[str, Any | None, str]:
    deadline = time.monotonic() + max(0.0, provider.timeout_seconds)
    latest: Any = None
    while True:
        response = httpx.get(
            provider.result_endpoint,
            params={"video_id": video_id},
            headers=_headers(provider.api_key),
            timeout=provider.request_timeout_seconds,
        )
        if response.is_error:
            return "failed", None, _response_error(response)
        latest = response.json()
        url, _ = _video_payload(latest)
        status = _video_status(latest)
        if url:
            return "completed", latest, url
        if status in {"failed", "error", "cancelled", "canceled", "rejected"}:
            return "failed", latest, "视频生成失败"
        if status in {"completed", "succeeded", "success", "done", "finished"}:
            return "failed", latest, "视频任务完成但响应中没有视频 URL"
        if time.monotonic() >= deadline:
            return "timeout", latest, "视频生成轮询超时"
        interval = max(0.0, provider.poll_interval_seconds)
        if interval:
            time.sleep(interval)


def video_generate(
    prompt: str,
    model: str = "",
    duration: int = 5,
    resolution: str = "720p",
    fps: int = 24,
    **kwargs: Any,
) -> str | ToolExecutionResult:
    """Submit a video job and poll Agnes-AI until a playable URL is available."""
    try:
        provider = _video_provider()
        model = _effective_model(model, provider.model)
        payload = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "fps": fps,
            **kwargs,
        }
        response = httpx.post(
            provider.endpoint,
            json=payload,
            headers=_headers(provider.api_key),
            timeout=max(provider.request_timeout_seconds, 300.0),
        )
        if response.is_error:
            return _error(_response_error(response))
        result = response.json()
        video_url, video_id = _video_payload(result)
        if not video_url and video_id:
            state, result, detail = _poll_video(provider, video_id)
            if state != "completed":
                return _error(detail)
            video_url, _ = _video_payload(result)
        if not video_url:
            return _error("视频 API 响应中没有 video URL 或 video_id")
        artifact = _artifact("video", video_url, title="Agnes-AI generated video", model=model)
        return ToolExecutionResult(
            content=_json({
                "success": True,
                "model": model,
                "duration": duration,
                "resolution": resolution,
                "video_url": video_url,
                "video_id": video_id,
            }),
            artifacts=(artifact,) if artifact else (),
        )
    except Exception as exc:
        logger.warning("video_generate failed: %s", exc)
        return _error(str(exc))


from tools.registry import registry


IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": "使用已配置的 Agnes-AI 图像模型根据提示词生成图像。",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图像描述提示词"},
            "size": {"type": "string", "description": "图像尺寸"},
            "quality": {"type": "string", "description": "质量等级"},
            "n": {"type": "integer", "minimum": 1, "maximum": 4},
            "response_format": {"type": "string", "enum": ["url", "base64"]},
        },
        "required": ["prompt"],
    },
}

IMAGE_EDIT_SCHEMA = {
    "name": "image_edit",
    "description": "使用已配置的 Agnes-AI 图像模型编辑或变换输入图像。",
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "输入图像路径或 URL"},
            "prompt": {"type": "string", "description": "编辑提示词"},
            "mask_path": {"type": "string", "description": "可选遮罩路径"},
            "size": {"type": "string", "description": "输出尺寸"},
            "n": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "required": ["image_path", "prompt"],
    },
}

VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": "使用已配置的 Agnes-AI 视频模型生成短视频并等待可播放 URL。",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频描述提示词"},
            "duration": {"type": "integer", "default": 5},
            "resolution": {"type": "string", "default": "720p"},
            "fps": {"type": "integer", "default": 24},
        },
        "required": ["prompt"],
    },
}


registry.register(
    name="image_generate",
    toolset="media",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=lambda args, **kw: image_generate(**dict(args or {})),
    check_fn=image_generation_configured,
    emoji="🎨",
)
registry.register(
    name="image_edit",
    toolset="media",
    schema=IMAGE_EDIT_SCHEMA,
    handler=lambda args, **kw: image_edit(**dict(args or {})),
    check_fn=image_generation_configured,
    emoji="🖼️",
)
registry.register(
    name="video_generate",
    toolset="media",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=lambda args, **kw: video_generate(**dict(args or {})),
    check_fn=video_generation_configured,
    emoji="🎬",
)


__all__ = [
    "image_generate",
    "image_edit",
    "video_generate",
    "IMAGE_GENERATE_SCHEMA",
    "IMAGE_EDIT_SCHEMA",
    "VIDEO_GENERATE_SCHEMA",
]
