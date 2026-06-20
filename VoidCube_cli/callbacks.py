"""
回调模块 - 简化版存根

轻量化版本，移除复杂的交互回调逻辑。
"""

from typing import Optional


def prompt_for_secret(prompt: str = "Enter secret: ") -> Optional[str]:
    """提示输入密钥"""
    import getpass
    try:
        return getpass.getpass(prompt)
    except Exception:
        return None


def prompt_for_input(prompt: str = "") -> Optional[str]:
    """提示输入"""
    try:
        return input(prompt)
    except Exception:
        return None


def confirm_action(message: str = "Continue?") -> bool:
    """确认操作"""
    try:
        response = input(f"{message} [y/N]: ").strip().lower()
        return response in ["y", "yes"]
    except Exception:
        return False
