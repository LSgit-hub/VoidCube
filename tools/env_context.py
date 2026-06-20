"""
环境上下文
"""

from typing import Dict, Any

def get_env_context() -> Dict[str, Any]:
    """获取环境上下文"""
    import os
    return {
        "python_version": os.sys.version,
        "platform": os.sys.platform,
        "cwd": os.getcwd(),
    }
