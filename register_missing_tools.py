
#!/usr/bin/env python3
"""
补充缺失工具的注册
"""
import sys
import json
sys.path.insert(0, str(sys.argv[0]).rsplit("\\", 1)[0])

from tools.registry import registry

# 先注册所有工具
import tools.model_tools

print("当前已注册的工具：", registry.list_tools())

print("\n补充缺失工具的注册...")

# 定义缺失的工具

# 1. clarify tool
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

def clarify_tool(question: str, options: list = None):
    return json.dumps({"success": True, "question": question, "options": options or []})

# 2. execute_code tool
EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": "Execute code in a sandbox. Supports Python, JavaScript, etc. Code runs in an isolated environment with CPU/memory limits and network access disabled for safety.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The code to execute"},
            "language": {"type": "string", "description": "The programming language to use (default: python)", "default": "python"}
        },
        "required": ["code"]
    }
}

def execute_code_tool(code: str, language: str = "python"):
    return json.dumps({"success": False, "error": "Code execution is currently disabled"})

# 3. process tool
PROCESS_SCHEMA = {
    "name": "process",
    "description": "List running processes on the system. Useful for monitoring, checking resource usage, or debugging.",
    "parameters": {}
}

def process_tool():
    import psutil
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
        try:
            processes.append({
                "pid": proc.pid,
                "name": proc.name(),
                "username": proc.username(),
                "cpu_percent": proc.cpu_percent(),
                "memory_percent": proc.memory_percent()
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return json.dumps({"success": True, "processes": processes})

# 4. session_search tool
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

def session_search_tool(query: str, limit: int = 10):
    return json.dumps({"success": False, "error": "Session search is currently disabled"})


# 注册这些工具
registry.register(
    name="clarify",
    toolset="assistant",
    schema=CLARIFY_SCHEMA,
    handler=lambda args, **kw: clarify_tool(args.get("question", ""), args.get("options", []))
)

registry.register(
    name="execute_code",
    toolset="code",
    schema=EXECUTE_CODE_SCHEMA,
    handler=lambda args, **kw: execute_code_tool(args.get("code", ""), args.get("language", "python"))
)

registry.register(
    name="process",
    toolset="system",
    schema=PROCESS_SCHEMA,
    handler=lambda args, **kw: process_tool()
)

registry.register(
    name="session_search",
    toolset="assistant",
    schema=SESSION_SEARCH_SCHEMA,
    handler=lambda args, **kw: session_search_tool(args.get("query", ""), args.get("limit", 10))
)

print("已补充注册工具：")
print("- clarify")
print("- execute_code")
print("- process")
print("- session_search")
print("\n现在所有工具：")
for tool in sorted(registry.list_tools()):
    print(f"- {tool}")
