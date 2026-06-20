"""
沙盒执行器
"""

from __future__ import annotations

from typing import Any, Optional


def execute_in_sandbox(command: str, **kwargs) -> Optional[Any]:
    """在沙盒中执行命令。"""
    from tools.code_execution_tool import _execute_code_impl

    return _execute_code_impl(
        code=command,
        language=kwargs.get("language", "python"),
        task_id=kwargs.get("task_id"),
    )
