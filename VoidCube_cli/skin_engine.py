"""
皮肤引擎
"""

from typing import Optional, Dict, Any, List

class Skin:
    """皮肤"""
    def __init__(self, name: str = "default"):
        self.name = name
        self.tool_prefix = "┊"
    
    def get_color(self, key: str, default: str) -> str:
        return default
    
    def get_branding(self, key: str, default: str) -> str:
        return default

_default_skin = Skin("default")

def init_skin_from_config(config: Dict[str, Any] = None) -> None:
    """从配置初始化皮肤"""
    pass

def get_active_skin() -> Skin:
    """获取活动皮肤"""
    return _default_skin

def get_active_help_header() -> str:
    """获取帮助头"""
    return ""

def get_active_goodbye() -> str:
    """获取告别语"""
    return "再见！"

def get_active_prompt_symbol() -> str:
    """获取提示符号"""
    return ">"

def get_prompt_toolkit_style_overrides() -> Dict[str, Any]:
    """获取 prompt_toolkit 样式覆盖"""
    return {}

def list_skins() -> List[Dict[str, str]]:
    """列出皮肤"""
    return [{"name": "default", "source": "built-in", "description": "Default skin"}]

def set_active_skin(name: str) -> bool:
    """设置活动皮肤"""
    return True

def get_active_skin_name() -> str:
    """获取活动皮肤名称"""
    return "default"
