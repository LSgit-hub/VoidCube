"""
澄清工具
"""

from typing import Optional
from tools.registry import registry, tool_error

CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": "Ask for clarification or more details. Use this tool to get more information when the user's request is unclear, ambiguous, or incomplete.",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The clarification question to ask the user"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional list of options to present to the user"}
        },
        "required": ["question"]
    }
}

def clarify_tool(
    question: str,
    options: Optional[list] = None,
    **kwargs
) -> str:
    """澄清问题"""
    import json
    result = {
        "success": True,
        "question": question,
        "options": options or [],
        "message": "请提供更多信息"
    }
    return json.dumps(result, ensure_ascii=False)

def _handle_clarify(args, **kw):
    return clarify_tool(question=args.get("question", ""), options=args.get("options"))

registry.register(name="clarify", toolset="assistant", schema=CLARIFY_SCHEMA, handler=_handle_clarify)
