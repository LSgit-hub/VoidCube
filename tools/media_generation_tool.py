#!/usr/bin/env python3
"""
媒体生成工具 — 文生图、图生图、视频生成

使用 Agnes AI API 进行图像和视频生成。
"""

import json
import logging
import os
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

# Agnes AI API 配置
AGNES_API_BASE_URL = "https://api.agnes-ai.cn/v1"
AGNES_IMAGE_MODEL = "Agnes Image 2.1 Flash"
AGNES_VIDEO_MODEL = "agnes-video-v2.0"


def _get_api_key() -> str:
    """获取 API Key。"""
    # 优先从环境变量读取
    api_key = os.getenv("AGNES_API_KEY", "").strip()
    if api_key:
        return api_key
    
    # 尝试从配置文件读取
    try:
        from VoidCube_app.config import load_config
        cfg = load_config()
        providers = cfg.get("providers", {})
        
        # 查找 agnes 提供商
        for provider_name, provider in providers.items():
            if "agnes" in provider_name.lower():
                key = provider.get("api_key", "")
                if key:
                    return str(key).strip()
        
        # 检查 active_provider
        active = cfg.get("runtime", {}).get("active_provider", "")
        if active and active in providers:
            key = providers[active].get("api_key", "")
            if key:
                return str(key).strip()
    except Exception as e:
        logger.debug("Failed to load config for API key: %s", e)
    
    return ""


def image_generate(
    prompt: str,
    model: str = AGNES_IMAGE_MODEL,
    size: str = "1024x1024",
    quality: str = "standard",
    n: int = 1,
    response_format: str = "url",
    **kwargs
) -> str:
    """文生图工具。

    Args:
        prompt: 图像描述提示词
        model: 模型名称 (默认: Agnes Image 2.1 Flash)
        size: 图像尺寸 (默认: 1024x1024)
        quality: 质量等级 (standard/high)
        n: 生成数量
        response_format: 返回格式 (url/base64)

    Returns:
        JSON 字符串，包含生成的图像 URL 或 base64 数据
    """
    api_key = _get_api_key()
    
    if not api_key:
        return json.dumps({
            "success": False,
            "error": "未配置 Agnes API Key，请在 ~/.VoidCube/config.yaml 中配置 providers"
        }, ensure_ascii=False)
    
    payload = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
        **kwargs
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    url = f"{AGNES_API_BASE_URL}/images/generations"
    
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        result = resp.json()
        
        # 标准化返回格式
        output = {
            "success": True,
            "model": model,
            "size": size,
            "images": []
        }
        
        for img in result.get("data", []):
            if response_format == "url":
                output["images"].append({
                    "url": img.get("url", ""),
                    "revised_prompt": img.get("revised_prompt", prompt)
                })
            else:
                output["images"].append({
                    "b64_json": img.get("b64_json", "")
                })
        
        logger.info("image_generate OK: %s → %d images", prompt[:50], len(output["images"]))
        return json.dumps(output, ensure_ascii=False, indent=2)
        
    except httpx.HTTPStatusError as e:
        error_msg = f"API 错误: {e.response.status_code}"
        try:
            error_detail = e.response.json().get("error", {}).get("message", "")
            if error_detail:
                error_msg += f": {error_detail}"
        except Exception:
            pass
        logger.warning("image_generate failed: %s", error_msg)
        return json.dumps({"success": False, "error": error_msg}, ensure_ascii=False)
    except Exception as e:
        logger.warning("image_generate failed: %s", e)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def image_edit(
    image_path: str,
    prompt: str,
    mask_path: str = "",
    model: str = AGNES_IMAGE_MODEL,
    size: str = "1024x1024",
    n: int = 1,
    response_format: str = "url",
    **kwargs
) -> str:
    """图生图工具（图像编辑/变体生成）。

    Args:
        image_path: 输入图像路径或 URL
        prompt: 编辑提示词
        mask_path: 遮罩路径（可选）
        model: 模型名称
        size: 输出尺寸
        n: 生成数量
        response_format: 返回格式

    Returns:
        JSON 字符串，包含生成的图像
    """
    api_key = _get_api_key()
    
    if not api_key:
        return json.dumps({
            "success": False,
            "error": "未配置 Agnes API Key"
        }, ensure_ascii=False)
    
    # 准备请求体
    payload = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
        **kwargs
    }
    
    # 处理图像输入
    if image_path.startswith(("http://", "https://")):
        payload["image"] = image_path
    else:
        # 本地文件 - 需要上传或转换为 base64
        try:
            with open(image_path, "rb") as f:
                import base64
                payload["image"] = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
        except FileNotFoundError:
            return json.dumps({
                "success": False,
                "error": f"图像文件不存在: {image_path}"
            }, ensure_ascii=False)
    
    # 处理遮罩
    if mask_path:
        try:
            with open(mask_path, "rb") as f:
                import base64
                payload["mask"] = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
        except FileNotFoundError:
            logger.warning("Mask file not found: %s", mask_path)
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    url = f"{AGNES_API_BASE_URL}/images/edits"
    
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        result = resp.json()
        
        output = {
            "success": True,
            "model": model,
            "size": size,
            "source_image": image_path,
            "images": []
        }
        
        for img in result.get("data", []):
            if response_format == "url":
                output["images"].append({
                    "url": img.get("url", ""),
                    "revised_prompt": img.get("revised_prompt", prompt)
                })
            else:
                output["images"].append({
                    "b64_json": img.get("b64_json", "")
                })
        
        logger.info("image_edit OK: %s → %d images", image_path, len(output["images"]))
        return json.dumps(output, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.warning("image_edit failed: %s", e)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def video_generate(
    prompt: str,
    model: str = AGNES_VIDEO_MODEL,
    duration: int = 5,
    resolution: str = "720p",
    fps: int = 24,
    **kwargs
) -> str:
    """视频生成工具。

    Args:
        prompt: 视频描述提示词
        model: 模型名称
        duration: 视频时长（秒）
        resolution: 分辨率 (480p/720p/1080p)
        fps: 帧率

    Returns:
        JSON 字符串，包含生成的视频 URL
    """
    api_key = _get_api_key()
    
    if not api_key:
        return json.dumps({
            "success": False,
            "error": "未配置 Agnes API Key"
        }, ensure_ascii=False)
    
    payload = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "fps": fps,
        **kwargs
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    url = f"{AGNES_API_BASE_URL}/videos"
    
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=300.0)
        resp.raise_for_status()
        result = resp.json()
        
        output = {
            "success": True,
            "model": model,
            "duration": duration,
            "resolution": resolution,
            "video_url": result.get("url", ""),
            "thumbnail_url": result.get("thumbnail_url", ""),
            "expires_at": result.get("expires_at", "")
        }
        
        logger.info("video_generate OK: %s", prompt[:50])
        return json.dumps(output, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.warning("video_generate failed: %s", e)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ── Registry ──
from tools.registry import registry

IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": (
        "文生图工具：根据文本描述生成图像。"
        "使用 Agnes Image 2.1 Flash 模型。"
        "返回图像 URL 或 base64 编码数据。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图像描述提示词，越详细越好"
            },
            "model": {
                "type": "string",
                "description": "模型名称，默认 Agnes Image 2.1 Flash"
            },
            "size": {
                "type": "string",
                "description": "图像尺寸",
                "enum": ["256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"]
            },
            "quality": {
                "type": "string",
                "description": "质量等级",
                "enum": ["standard", "high"]
            },
            "n": {
                "type": "integer",
                "description": "生成数量，1-4",
                "minimum": 1,
                "maximum": 4
            },
            "response_format": {
                "type": "string",
                "description": "返回格式",
                "enum": ["url", "base64"]
            }
        },
        "required": ["prompt"]
    }
}

registry.register(
    name="image_generate",
    toolset="media",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=lambda args, **kw: image_generate(
        prompt=args.get("prompt", ""),
        model=args.get("model", AGNES_IMAGE_MODEL),
        size=args.get("size", "1024x1024"),
        quality=args.get("quality", "standard"),
        n=args.get("n", 1),
        response_format=args.get("response_format", "url"),
    ),
    emoji="🎨",
)

IMAGE_EDIT_SCHEMA = {
    "name": "image_edit",
    "description": (
        "图生图工具：基于输入图像和提示词生成新图像。"
        "可用于图像编辑、风格迁移、变体生成等。"
        "支持遮罩编辑（局部修改）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "输入图像路径或 URL"
            },
            "prompt": {
                "type": "string",
                "description": "编辑提示词，描述期望的修改"
            },
            "mask_path": {
                "type": "string",
                "description": "遮罩图像路径（可选，用于局部编辑）"
            },
            "model": {
                "type": "string",
                "description": "模型名称"
            },
            "size": {
                "type": "string",
                "description": "输出尺寸"
            },
            "n": {
                "type": "integer",
                "description": "生成数量"
            }
        },
        "required": ["image_path", "prompt"]
    }
}

registry.register(
    name="image_edit",
    toolset="media",
    schema=IMAGE_EDIT_SCHEMA,
    handler=lambda args, **kw: image_edit(
        image_path=args.get("image_path", ""),
        prompt=args.get("prompt", ""),
        mask_path=args.get("mask_path", ""),
        model=args.get("model", AGNES_IMAGE_MODEL),
        size=args.get("size", "1024x1024"),
        n=args.get("n", 1),
        response_format=args.get("response_format", "url"),
    ),
    emoji="🖼️",
)

VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": (
        "视频生成工具：根据文本描述生成短视频。"
        "使用 agnes-video-v2.0 模型。"
        "支持不同时长、分辨率和帧率设置。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "视频描述提示词"
            },
            "model": {
                "type": "string",
                "description": "模型名称，默认 agnes-video-v2.0"
            },
            "duration": {
                "type": "integer",
                "description": "视频时长（秒）",
                "default": 5
            },
            "resolution": {
                "type": "string",
                "description": "分辨率",
                "enum": ["480p", "720p", "1080p"]
            },
            "fps": {
                "type": "integer",
                "description": "帧率",
                "default": 24
            }
        },
        "required": ["prompt"]
    }
}

registry.register(
    name="video_generate",
    toolset="media",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=lambda args, **kw: video_generate(
        prompt=args.get("prompt", ""),
        model=args.get("model", AGNES_VIDEO_MODEL),
        duration=args.get("duration", 5),
        resolution=args.get("resolution", "720p"),
        fps=args.get("fps", 24),
    ),
    emoji="🎬",
)
