"""
凭证管理器
"""

from typing import Optional, Dict, Any
import os

class CredentialManager:
    """凭证管理器"""
    
    def __init__(self):
        self._credentials: Dict[str, str] = {}
    
    def get(self, name: str) -> Optional[str]:
        """获取凭证"""
        # 优先从环境变量获取
        return os.getenv(name, self._credentials.get(name))
    
    def set(self, name: str, value: str) -> None:
        """设置凭证"""
        self._credentials[name] = value
    
    def delete(self, name: str) -> None:
        """删除凭证"""
        self._credentials.pop(name, None)
    
    def list_credentials(self) -> list:
        """列出凭证"""
        return list(self._credentials.keys())

credential_manager = CredentialManager()

def get_credential(name: str) -> Optional[str]:
    """获取凭证"""
    return credential_manager.get(name)

def set_credential(name: str, value: str) -> None:
    """设置凭证"""
    credential_manager.set(name, value)
