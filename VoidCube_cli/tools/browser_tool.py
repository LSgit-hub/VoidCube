"""
Browser Automation Tool — control a headless browser from the agent.

Supports navigation, clicking, typing, screenshot, page extraction,
and JavaScript evaluation via a headless Chromium instance.
"""

from VoidCube_cli.tools import register_tool


TOOL_SCHEMA = {
    "name": "browser",
    "description": "Control a headless browser instance for web automation. "
                   "Navigate pages, click elements, fill forms, take screenshots, "
                   "extract page content, and evaluate JavaScript. "
                   "Use when web_extract is insufficient (e.g., login-required "
                   "pages, dynamic SPA content, interactive workflows).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "navigate",
                    "click",
                    "type",
                    "screenshot",
                    "extract",
                    "evaluate",
                    "scroll",
                    "wait",
                    "back",
                    "forward",
                    "refresh",
                ],
                "description": "Browser action to perform:\n"
                               "- navigate: go to a URL\n"
                               "- click: click an element by selector\n"
                               "- type: type text into an input field\n"
                               "- screenshot: capture the current page as PNG\n"
                               "- extract: get page content as markdown/text\n"
                               "- evaluate: run JavaScript in the page\n"
                               "- scroll: scroll the page\n"
                               "- wait: wait for an element or timeout\n"
                               "- back/forward/refresh: browser navigation",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (required for 'navigate' action)",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for the target element "
                               "(required for 'click', 'type', 'wait' actions)",
            },
            "text": {
                "type": "string",
                "description": "Text to type (required for 'type' action)",
            },
            "script": {
                "type": "string",
                "description": "JavaScript code to evaluate "
                               "(required for 'evaluate' action)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
            },
            "full_page": {
                "type": "boolean",
                "description": "For screenshot: capture full scrollable page "
                               "(default: false — viewport only)",
                "default": False,
            },
            "wait_for": {
                "type": "string",
                "description": "Wait condition for 'wait' action: "
                               "a CSS selector or 'networkidle'",
            },
            "extract_format": {
                "type": "string",
                "enum": ["markdown", "text", "html"],
                "description": "Output format for 'extract' action (default: markdown)",
                "default": "markdown",
            },
        },
        "required": ["action"],
    },
}


def register() -> None:
    """Register the browser tool schema."""
    register_tool("browser", TOOL_SCHEMA)
