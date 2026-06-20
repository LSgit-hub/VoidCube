"""
Vision Analysis Tool — analyze images using multimodal LLM capabilities.

Supports reading image files or URLs and generating descriptions,
answering questions about visual content, OCR text extraction,
and visual comparison.
"""

from VoidCube_cli.tools import register_tool


TOOL_SCHEMA = {
    "name": "vision_analyze",
    "description": "Analyze images using vision-capable models. "
                   "Describe image content, answer questions about images, "
                   "extract text via OCR, compare multiple images, "
                   "or analyze screenshots. Supports local files and remote URLs. "
                   "Use instead of read_file for image files (.png, .jpg, .gif, "
                   ".webp, .bmp, .svg).",
    "parameters": {
        "type": "object",
        "properties": {
            "images": {
                "type": "array",
                "description": "List of images to analyze. Each entry is either "
                               "a local file path or a URL.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "File path (e.g., /tmp/screenshot.png) "
                                           "or URL (e.g., https://example.com/img.jpg)",
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional label for this image "
                                           "(e.g., 'before', 'after')",
                        },
                    },
                    "required": ["source"],
                },
                "minItems": 1,
                "maxItems": 10,
            },
            "prompt": {
                "type": "string",
                "description": "What to analyze or ask about the image(s). "
                               "Be specific: 'Describe this UI' or "
                               "'What error message is shown?' or "
                               "'Extract all visible text'",
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high", "auto"],
                "description": "Analysis detail level: "
                               "'low' for quick classification, "
                               "'high' for detailed analysis/OCR, "
                               "'auto' for adaptive (default: auto)",
                "default": "auto",
            },
            "output_format": {
                "type": "string",
                "enum": ["markdown", "text", "json"],
                "description": "Output format for the analysis result "
                               "(default: markdown)",
                "default": "markdown",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Maximum tokens for the vision response "
                               "(default: 1024, max: 4096)",
                "default": 1024,
                "minimum": 50,
                "maximum": 4096,
            },
        },
        "required": ["images", "prompt"],
    },
}


def register() -> None:
    """Register the vision analysis tool schema."""
    register_tool("vision_analyze", TOOL_SCHEMA)
