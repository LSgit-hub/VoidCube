"""
进程注册表
"""

from typing import Dict, Any, Optional


class ProcessRegistry:
    """进程注册表"""
    
    def __init__(self):
        self._processes: Dict[str, Any] = {}
    
    def register(self, pid: str, info: Any) -> None:
        self._processes[pid] = info
    
    def unregister(self, pid: str) -> None:
        self._processes.pop(pid, None)
    
    def get(self, pid: str) -> Optional[Any]:
        return self._processes.get(pid)
    
    def has_active_processes(self, task_id: str) -> bool:
        """检查是否有活跃的进程"""
        # 简化版：检查是否有进程属于这个 task_id
        for pid, info in self._processes.items():
            if isinstance(info, dict) and info.get('task_id') == task_id:
                return True
        return False


process_registry = ProcessRegistry()
