"""跨会话历史搜索工具。"""

import json
from typing import Any

from .registry import registry, tool_error

SESSION_SEARCH_SCHEMA = {
    "name": "session_search",
    "description": "Search message transcripts across current and previous sessions. Use this to recall prior decisions, task progress, or conversation details.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Full-text query for conversation transcripts"},
            "role_filter": {
                "type": "array",
                "items": {"type": "string", "enum": ["system", "user", "assistant", "tool"]},
                "description": "Optional message roles to include"
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum number of matches (default: 10)",
                "default": 10
            }
        },
        "required": ["query"]
    }
}

def session_search(
    query: str,
    *,
    db: Any,
    role_filter: list[str] | None = None,
    limit: int = 10,
    current_session_id: str | None = None,
) -> str:
    """使用 SessionDB 的 FTS 索引搜索消息并返回结构化结果。"""
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return tool_error("query is required")
    if db is None or not callable(getattr(db, "search_messages", None)):
        return tool_error("Session database not available.")

    normalized_roles = None
    if role_filter:
        normalized_roles = [str(role).strip() for role in role_filter if str(role).strip()]

    try:
        normalized_limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        return tool_error("limit must be an integer")

    try:
        matches = db.search_messages(
            query=normalized_query,
            role_filter=normalized_roles,
            limit=normalized_limit,
        )
    except Exception as exc:
        return tool_error(f"Session search failed: {exc}")

    results = []
    for match in matches:
        result = dict(match)
        if current_session_id:
            result["is_current_session"] = result.get("session_id") == current_session_id
        results.append(result)

    return json.dumps(
        {"success": True, "count": len(results), "results": results},
        ensure_ascii=False,
    )


def _handle_session_search(args: dict[str, Any], **kwargs: Any) -> str:
    """Registry 适配器；Agent 路由通过 kwargs 注入会话级依赖。"""
    return session_search(
        query=args.get("query", ""),
        role_filter=args.get("role_filter"),
        limit=args.get("limit", 10),
        db=kwargs.get("db"),
        current_session_id=kwargs.get("current_session_id"),
    )


registry.register(
    name="session_search",
    toolset="session_search",
    schema=SESSION_SEARCH_SCHEMA,
    handler=_handle_session_search,
    effect="read_only",
)
