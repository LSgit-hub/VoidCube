"""
Append File Tool — append content to a file without reading it first.

Unlike write_file (which overwrites), this tool appends to the end of
an existing file or creates it if it doesn't exist.  Uses atomic
copy-on-write for safety on files under 100MB, direct append with
fsync for larger files.
"""

from VoidCube_cli.tools import register_tool


TOOL_SCHEMA = {
    "name": "append_file",
    "description": "Append content to the end of a file. Creates the file if it "
                   "doesn't exist.  Use this instead of read_file + write_file "
                   "when you only need to add content — avoids O(n) read overhead "
                   "on large files.  Automatically adds a newline separator when "
                   "the existing file doesn't end with one.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to append to (absolute or relative). "
                               "Parent directories are created if needed.",
            },
            "content": {
                "type": "string",
                "description": "Text content to append to the file.",
            },
            "ensure_newline": {
                "type": "boolean",
                "description": "If true, ensure a newline separates old and new "
                               "content when the file doesn't end with one "
                               "(default: true).",
                "default": True,
            },
        },
        "required": ["path", "content"],
    },
}


def register() -> None:
    """Register the append_file tool schema."""
    register_tool("append_file", TOOL_SCHEMA)
