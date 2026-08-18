"""
工具后端辅助函数

"""

import os
from typing import Dict, Any, Optional

def managed_nous_tools_enabled() -> bool:
    """Nous 托管工具是否启用（始终返回 False）"""
    return False

def normalize_browser_cloud_provider(provider: str) -> str:
    """标准化浏览器云服务提供者（返回 local）"""
    return "local"

def get_web_backend() -> str:
    """获取网络工具后端"""
    backend = os.getenv("WEB_BACKEND", "local").lower()
    if backend in ("exa", "parallel", "firecrawl", "tavily"):
        return backend
    return "local"

def is_cloud_backend(backend: str) -> bool:
    """检查是否为云后端"""
    return backend in ("exa", "parallel", "firecrawl", "tavily", "browserbase", "browser-use")

# Modal 相关函数（存根）
def coerce_modal_mode(mode: str) -> str:
    """强制 Modal 模式（返回 local）"""
    return "local"

def has_direct_modal_credentials() -> bool:
    """检查是否有直接的 Modal 凭证"""
    return False

def resolve_modal_backend_state() -> Dict[str, Any]:
    """解析 Modal 后端状态"""
    return {
        "mode": "local",
        "available": False,
        "credentials": None,
    }

# 其他可能需要的辅助函数
def get_terminal_backend() -> str:
    """获取终端后端"""
    if "TERMINAL_ENV" in os.environ:
        return str(os.environ["TERMINAL_ENV"]).strip().lower() or "podman"
    try:
        from ...infrastructure.config.configuration import load_config

        terminal = load_config().get("terminal") or {}
        if isinstance(terminal, dict):
            return str(terminal.get("backend") or "podman").strip().lower()
    except Exception:
        pass
    return "podman"

def is_local_terminal() -> bool:
    """检查是否为本地终端"""
    return get_terminal_backend() == "local"
