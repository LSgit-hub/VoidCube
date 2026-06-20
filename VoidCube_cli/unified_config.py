"""
统一配置
"""

from typing import Dict, Any

class UnifiedConfigLoader:
    """统一配置加载器"""
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
    
    def load(self) -> Dict[str, Any]:
        """加载配置"""
        return self._config
    
    def save(self, config: Dict[str, Any]) -> None:
        """保存配置"""
        self._config = config
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._config.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置配置值"""
        self._config[key] = value
