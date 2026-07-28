#!/usr/bin/env python3
"""
Toolsets Module - Server Management Focused (精简版)
"""

from typing import List, Dict, Any, Set, Optional


_VOIDCUBE_CORE_TOOLS = [
    "web_search", "web_extract",
    "terminal", "process",
    "read_file", "write_file", "patch", "search_files",
    "skills_list", "skill_view", "skill_manage",
    "execute_code",
    "session_search",
    "clarify",
    "system_info", "cpu_stats", "memory_stats", "disk_usage", "top_processes",
    "ping", "check_port", "dns_lookup", "curl_check",
    "read_log", "log_errors", "analyze_log",
]


TOOLSETS = {
    "web": {
        "description": "Web research and content extraction tools",
        "tools": ["web_search", "web_extract"],
        "includes": []
    },
    
    "search": {
        "description": "Web search only",
        "tools": ["web_search"],
        "includes": []
    },
    
    "terminal": {
        "description": "Terminal/command execution and process management tools",
        "tools": ["terminal", "process"],
        "includes": []
    },
    
    "skills": {
        "description": "Access, create, edit, and manage skill documents",
        "tools": ["skills_list", "skill_view", "skill_manage"],
        "includes": []
    },
    
    "file": {
        "description": "File manipulation tools: read, write, patch, search",
        "tools": ["read_file", "write_file", "patch", "search_files"],
        "includes": []
    },
    
    "session_search": {
        "description": "Search through your conversation history",
        "tools": ["session_search"],
        "includes": []
    },

    "scheduling": {
        "description": "Manage scheduled tasks executed later by the main CLI API-A agent",
        "tools": ["scheduled_task"],
        "includes": [],
    },
    
    "code_execution": {
        "description": "Execute Python code in a sandboxed environment",
        "tools": ["execute_code"],
        "includes": []
    },
    
    "system": {
        "description": "System monitoring and info tools",
        "tools": ["system_info", "cpu_stats", "memory_stats", "disk_usage", "top_processes"],
        "includes": []
    },
    
    "network": {
        "description": "Network diagnostics and tools",
        "tools": ["ping", "check_port", "dns_lookup", "curl_check"],
        "includes": []
    },
    
    "logs": {
        "description": "Log analysis and viewing tools",
        "tools": ["read_log", "log_errors", "analyze_log"],
        "includes": []
    },
    
    "browser": {
        "description": "Browser automation — navigate, snapshot, click, type, scroll",
        "tools": [
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images", "browser_vision",
            "browser_console",
        ],
        "includes": []
    },

    "ops": {
        "description": "All server operations tools",
        "tools": [],
        "includes": ["system", "network", "logs"]
    },

    "voidcube": {
        "description": "Core tools for server management",
        "tools": [],
        "includes": ["web", "browser", "terminal", "file", "skills", "scheduling", "code_execution", "ops"]
    },

    "voidcube-cli": {
        "description": "Core tools for server management (alias)",
        "tools": [],
        "includes": ["web", "browser", "terminal", "file", "skills", "scheduling", "code_execution", "ops"]
    },
    
    "mini": {
        "description": "Minimal toolset for quick tasks",
        "tools": ["web_search", "terminal", "read_file", "write_file"],
        "includes": []
    },
    
    "full": {
        "description": "All available tools",
        "tools": [],
        "includes": ["voidcube", "session_search"]
    },
    "learn": {
        "description": (
            "Research-only tools for self-learning subagents: web search, "
            "file reading, terminal, code execution, and browser automation. "
            "No file writes, no skill mutations, no memory writes, no delegation."
        ),
        "tools": [
            "web_search", "web_extract",
            "read_file", "search_files",
            "terminal",
            "execute_code",
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_extract",
            "browser_wait", "browser_close", "browser_screenshot",
        ],
        "includes": [],
    },
}


def get_toolset(name: str) -> Optional[Dict[str, Any]]:
    """Get toolset definition by name."""
    name = name.lower().strip()
    return TOOLSETS.get(name)


def resolve_toolset(name: str) -> List[str]:
    """Resolve a toolset to get all tool names (including from composed toolsets)."""
    resolved: Set[str] = set()
    visited: Set[str] = set()
    
    def _resolve(toolset_name: str):
        if toolset_name in visited:
            return
        visited.add(toolset_name)
        
        toolset = get_toolset(toolset_name)
        if not toolset:
            return
        
        resolved.update(toolset.get("tools", []))
        
        for included in toolset.get("includes", []):
            _resolve(included)
    
    _resolve(name)
    return sorted(resolved)


def get_all_toolsets() -> List[str]:
    """Get all toolset names."""
    return sorted(TOOLSETS.keys())


def get_toolset_description(name: str) -> str:
    """Get toolset description."""
    toolset = get_toolset(name)
    return toolset.get("description", "") if toolset else ""


def is_valid_toolset(name: str) -> bool:
    """Check if a toolset name is valid."""
    name = name.lower().strip()
    return name in TOOLSETS


def validate_toolset(name: str) -> str:
    """Validate a toolset name and return the normalized name."""
    name = name.lower().strip()
    if name in TOOLSETS:
        return name
    try:
        from VoidCube_cli.plugins import discover_plugins, get_plugin_toolsets
        discover_plugins()
        plugin_keys = {ts_key for ts_key, _, _ in get_plugin_toolsets()}
        if name in plugin_keys:
            return name
    except Exception:
        pass
    raise ValueError(f"Invalid toolset: {name}")


def get_toolset_info(name: str) -> dict:
    """Get toolset information including tools and description."""
    name = validate_toolset(name)
    toolset = get_toolset(name)
    return {
        "name": name,
        "description": toolset.get("description", ""),
        "tools": list(resolve_toolset(name)),
    }


def get_default_toolset() -> str:
    """Get the default toolset name."""
    return "voidcube"


def get_default_tools() -> List[str]:
    """Get tools for the default toolset."""
    return resolve_toolset(get_default_toolset())
