"""
HTTP Request Tool — make HTTP requests from the agent.

Provides a unified interface for GET, POST, PUT, DELETE, PATCH requests.
Use this instead of curl/wget in terminal for cleaner output and better
error handling.
"""

from VoidCube_cli.tools import register_tool


TOOL_SCHEMA = {
    "name": "http_request",
    "description": "Make HTTP requests to REST APIs. Supports GET, POST, PUT, DELETE, "
                   "PATCH. Returns status code, headers, and response body. "
                   "Use this instead of curl in terminal for clean, structured output.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to request (including https://)",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method (default: GET)",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "HTTP headers as key-value pairs. "
                               "Content-Type defaults to application/json.",
                "additionalProperties": {"type": "string"},
            },
            "body": {
                "type": "string",
                "description": "Request body. For JSON APIs, pass a JSON string. "
                               "For form data, use URL-encoded string.",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default: 30, max: 120)",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
            },
            "auth": {
                "type": "object",
                "description": "Authentication settings",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["bearer", "basic"],
                        "description": "Auth type: bearer token or basic auth",
                    },
                    "token": {
                        "type": "string",
                        "description": "Bearer token or base64-encoded user:pass for basic",
                    },
                },
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Whether to follow HTTP redirects (default: true)",
                "default": True,
            },
        },
        "required": ["url"],
    },
}


def register() -> None:
    """Register the HTTP request tool schema."""
    register_tool("http_request", TOOL_SCHEMA)
