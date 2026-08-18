"""
服务器管理CLI平台注册表

精简版：仅保留CLI交互模式，移除所有消息平台集成
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """平台元数据"""
    label: str
    default_toolset: str


PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli", PlatformInfo(label="🖥️  CLI终端", default_toolset="voidcube")),
])


def platform_label(key: str, default: str = "") -> str:
    """返回平台显示标签"""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
