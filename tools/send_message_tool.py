"""
发送消息工具
"""

from typing import Optional
from tools.registry import tool_error

def send_message_tool(
    message: str,
    channel: Optional[str] = None,
    **kwargs
) -> str:
    """发送消息（不可用）"""
    return tool_error("Message sending disabled")
