"""
工具结果存储
"""

from typing import Any, Dict

def maybe_persist_tool_result(
    content: Any = None,
    tool_name: str = "",
    tool_use_id: str = None,
    result: Any = None,
    env: str = None,
    **kwargs,
) -> Any:
    """持久化工具结果（不做任何操作，直接返回内容）"""
    return content if content is not None else result

def enforce_turn_budget(turn_tool_messages: list, env: str = None) -> bool:
    """强制执行轮次预算"""
    return True
