#!/usr/bin/env python3
"""
Toolsets Module - Server Management Focused (精简版)
"""

from typing import List, Dict, Any, Set, Optional


TOOLSETS = {
    "web": {
        "description": "Web research, content extraction, site crawling, and Web UI media playback tools",
        "tools": ["web_search", "web_extract", "web_crawl", "media_play", "media_playlist", "media_display", "media_control", "account_status"],
        "includes": []
    },

    "playback": {
        "description": "Display images, documents, web pages, and play audio/video in the VoidCube Web UI",
        "tools": ["media_play", "media_playlist", "media_display", "media_control", "account_status"],
        "includes": [],
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

    "moa": {
        "description": "Collaborative reasoning with multiple frontier models",
        "tools": ["mixture_of_agents"],
        "includes": [],
    },

    "assistant": {
        "description": "Interactive assistance and clarification tools",
        "tools": ["clarify"],
        "includes": [],
    },

    "delegation": {
        "description": "Delegate bounded work to a child agent",
        "tools": ["delegate_task"],
        "includes": [],
    },

    "todo": {
        "description": "Track the current agent's task list",
        "tools": ["todo"],
        "includes": [],
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
        "tools": ["system_info", "cpu_stats", "memory_stats", "disk_usage", "top_processes", "check_dependencies"],
        "includes": []
    },
    
    "network": {
        "description": "Network diagnostics and tools",
        "tools": ["ping", "check_port", "dns_lookup", "curl_check"],
        "includes": []
    },
    
    "logs": {
        "description": "Log analysis and viewing tools (use analyze_log mode='errors' for error extraction)",
        "tools": ["read_log", "analyze_log"],
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
        "includes": ["web", "playback", "browser", "vision", "terminal", "file", "skills", "scheduling", "code_execution", "ops", "media", "assistant", "delegation", "todo"]
    },

    "voidcube-cli": {
        "description": "Core tools for server management (alias for voidcube)",
        "tools": [],
        "includes": ["voidcube"]
    },
    
    "mini": {
        "description": "Minimal toolset for quick tasks",
        "tools": ["web_search", "terminal", "read_file", "write_file"],
        "includes": []
    },
    
    "full": {
        "description": "All available tools",
        "tools": [],
        "includes": ["voidcube", "session_search", "moa"]
    },
    "learn": {
        "description": (
            "Research-only tools for self-learning subagents: web search, "
            "read-only file inspection, and non-mutating browser observation. "
            "No terminal or code execution, file writes, skill mutations, memory writes, "
            "state-changing browser input, or delegation."
        ),
        "tools": [
            "web_search", "web_extract", "web_crawl",
            "read_file", "search_files",
            "browser_navigate", "browser_snapshot", "browser_scroll", "browser_back",
        ],
        "includes": [],
    },

    "media": {
        "description": "Media generation and display tools: image/video generation, editing, and multimedia display board",
        "tools": ["image_generate", "image_edit", "video_generate", "media_display", "media_play", "media_control"],
        "includes": [],
    },

    "vision": {
        "description": "Analyze images through the configured vision model",
        "tools": ["vision_analyze"],
        "includes": [],
    },
}


def create_custom_toolset(
    name: str,
    description: str,
    tools: List[str],
) -> Dict[str, Any]:
    """Register or replace a runtime toolset, such as an MCP server."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        raise ValueError("toolset name is required")
    TOOLSETS[normalized] = {
        "description": str(description or "").strip(),
        "tools": list(dict.fromkeys(str(tool).strip() for tool in tools if str(tool).strip())),
        "includes": [],
    }
    return TOOLSETS[normalized]


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
        from ...extensions.plugins.cli_adapter import discover_plugins, get_plugin_toolsets
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
