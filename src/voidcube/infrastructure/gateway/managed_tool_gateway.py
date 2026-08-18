"""
托管工具网关

"""

from typing import Optional, Any

class ManagedGatewayConfig:
    """托管网关配置"""
    def __init__(self, gateway_origin: str = "", nous_user_token: str = ""):
        self.gateway_origin = gateway_origin
        self.nous_user_token = nous_user_token

def resolve_managed_tool_gateway(
    vendor: str,
    token_reader: Optional[callable] = None
) -> Optional[ManagedGatewayConfig]:
    """解析托管工具网关（返回 None）"""
    return None

def build_vendor_gateway_url(vendor: str) -> str:
    """构建供应商网关 URL（返回空字符串）"""
    return ""

def read_nous_access_token() -> Optional[str]:
    """读取 Nous 访问令牌（返回 None）"""
    return None

def is_managed_tool_gateway_ready() -> bool:
    """检查托管工具网关是否就绪（始终返回 False）"""
    return False
