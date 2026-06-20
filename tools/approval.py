"""
审批模块

包含所有必要的审批和安全检查函数。
"""

import os
from typing import Optional, Callable, Any, Dict

# ── 会话标识 ──────────────────────────────────────────────────────────
_current_session_key: str = ""


def get_current_session_key(default: str = "") -> str:
    """获取当前会话唯一标识。

    用于跟踪后台进程所属会话。
    优先级: 环境变量 VOIDCUBE_SESSION_KEY > 已设置的 key > default
    """
    global _current_session_key
    if not _current_session_key:
        _current_session_key = os.environ.get("VOIDCUBE_SESSION_KEY", default)
    return _current_session_key


class ApprovalGate:
    """审批门"""
    
    def __init__(self):
        self._auto_approve = False
    
    def request_approval(
        self,
        action: str,
        description: str = "",
        callback: Optional[Callable] = None
    ) -> bool:
        """请求审批"""
        if self._auto_approve:
            return True
        if callback:
            return callback(action=action, description=description)
        return False
    
    def set_auto_approve(self, enabled: bool) -> None:
        """设置自动批准"""
        self._auto_approve = enabled

# 全局审批门实例
approval_gate = ApprovalGate()

def request_approval(action: str, description: str = "", callback: Optional[Callable] = None) -> bool:
    """请求审批"""
    return approval_gate.request_approval(action, description, callback)

def is_auto_approve() -> bool:
    """检查是否自动批准"""
    return approval_gate._auto_approve

def set_approval_mode(auto: bool) -> None:
    """设置审批模式"""
    approval_gate.set_auto_approve(auto)

# 危险命令检查
_DANGEROUS_PATTERNS = [
    "rm -rf", "rmdir", "del /", "format",
    "mkfs", "dd if=", "> /dev/", "chmod 777",
]

def check_dangerous_command(command: str, env_type: str = "local") -> Dict[str, Any]:
    """检查危险命令
    
    Returns:
        dict with keys: safe (bool), reason (str), requires_approval (bool)
    """
    command_lower = command.lower()
    
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in command_lower:
            return {
                "safe": False,
                "reason": f"检测到危险命令模式: {pattern}",
                "requires_approval": True,
                "command": command,
            }
    
    # 默认安全
    return {
        "safe": True,
        "reason": "",
        "requires_approval": False,
        "command": command,
    }

def check_all_command_guards(
    command: str,
    env_type: str = "local",
    approval_callback: Optional[Callable] = None
) -> Dict[str, Any]:
    """检查所有命令保护
    
    Returns:
        dict with keys: allowed (bool), reason (str), approved (bool)
    """
    # 检查危险命令
    dangerous_check = check_dangerous_command(command, env_type)
    
    if not dangerous_check["safe"]:
        # 需要审批
        if approval_callback:
            approved = approval_callback(
                command=command,
                description=dangerous_check["reason"]
            )
        else:
            # 检查是否自动批准（默认是False）
            approved = is_auto_approve()
        
        return {
            "allowed": approved,
            "reason": dangerous_check["reason"],
            "approved": approved,
            "command": command,
        }
    
    # 默认允许
    return {
        "allowed": True,
        "reason": "",
        "approved": True,
        "command": command,
    }
