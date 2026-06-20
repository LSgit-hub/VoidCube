"""
连接配置
"""

from typing import Dict, Any, Optional, List

def get_connection_profile(name: str) -> Optional[Dict[str, Any]]:
    return None

def list_connection_profiles() -> list:
    return []

def save_connection_profile(name: str, config: Dict[str, Any]) -> bool:
    return False

def list_profiles() -> List[Dict[str, Any]]:
    return []

def save_profile(name: str, config: Dict[str, Any]) -> bool:
    return False

def delete_profile(name: str) -> bool:
    return False

def set_active_profile(name: str) -> bool:
    return True

def clear_active_profile() -> None:
    pass

def get_active_profile() -> Optional[str]:
    return None

def get_profile(name: str) -> Optional[Dict[str, Any]]:
    return None

def test_profile(name: str) -> Dict[str, Any]:
    return {"success": False, "error": "Connection profiles disabled"}

def get_ssh_command(name: str) -> Dict[str, Any]:
    return {"success": False, "command": ""}
