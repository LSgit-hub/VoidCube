"""
会话搜索工具
"""

from typing import List, Dict, Any
from tools.registry import registry, tool_error

SESSION_SEARCH_SCHEMA = {
    "name": "session_search",
    "description": "Search within the current session history. Find past conversations, messages, or content from earlier in the session.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query to find in the session history"},
            "limit": {"type": "integer", "description": "Maximum number of results to return (default: 10)", "default": 10}
        },
        "required": ["query"]
    }
}

def session_search_tool(
    query: str,
    limit: int = 10,
    **kwargs
) -> str:
    """会话搜索（不可用）"""
    return tool_error("Session search disabled")

def search_sessions(query: str) -> List[Dict[str, Any]]:
    """搜索会话"""
    return []

def _handle_session_search(args, **kw):
    return session_search_tool(query=args.get("query", ""), limit=args.get("limit", 10))

registry.register(name="session_search", toolset="session_search", schema=SESSION_SEARCH_SCHEMA, handler=_handle_session_search)
