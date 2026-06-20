"""
视觉工具
"""

from typing import Optional, Dict, Any
from tools.registry import tool_error

def vision_analyze_tool(
    image_path: str,
    task: str = "",
    **kwargs
) -> str:
    """视觉分析（不可用）"""
    return tool_error("Vision analysis disabled")

def _is_image_size_error(error: Exception) -> bool:
    """检查是否为图像大小错误"""
    return False

def _resize_image_for_vision(image_path: str) -> Optional[str]:
    """调整图像大小用于视觉分析"""
    return image_path

_RESIZE_TARGET_BYTES = 20 * 1024 * 1024
