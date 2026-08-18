"""Local attachment parsing and display helpers for the VoidCube CLI."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional


_IMAGE_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
        ".tiff", ".tif", ".svg", ".ico",
    }
)


def _split_path_input(raw: str) -> tuple[str, str]:
    r"""Split a leading file path token from trailing free-form text."""
    raw = str(raw or "").strip()
    if not raw:
        return "", ""

    if raw[0] in {'"', "'"}:
        quote = raw[0]
        pos = 1
        while pos < len(raw):
            char = raw[pos]
            if char == "\\" and pos + 1 < len(raw):
                pos += 2
                continue
            if char == quote:
                return raw[1:pos], raw[pos + 1 :].strip()
            pos += 1
        return raw[1:], ""

    pos = 0
    while pos < len(raw):
        char = raw[pos]
        if char == "\\" and pos + 1 < len(raw) and raw[pos + 1] == " ":
            pos += 2
        elif char == " ":
            break
        else:
            pos += 1
    return raw[:pos].replace("\\ ", " "), raw[pos:].strip()


def _resolve_attachment_path(raw_path: str) -> Optional[Path]:
    """Resolve an existing local attachment path from the terminal cwd."""
    token = str(raw_path or "").strip()
    if not token:
        return None
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        token = token[1:-1].strip()
    if not token:
        return None

    path = Path(os.path.expandvars(os.path.expanduser(token)))
    if not path.is_absolute():
        path = Path(os.getenv("TERMINAL_CWD", os.getcwd())) / path
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path
    return resolved if resolved.is_file() else None


def _starts_like_path(value: str) -> bool:
    candidate = value[1:] if value[:1] in {'"', "'"} else value
    return (
        candidate.startswith(("/", "~", "./", "../", "\\\\"))
        or re.match(r"^[A-Za-z]:[\\/]", candidate) is not None
    )


def _detect_file_drop(user_input: str) -> Optional[dict[str, Any]]:
    """Return attachment metadata when input starts with a real local path."""
    if not isinstance(user_input, str):
        return None
    stripped = user_input.strip()
    if not stripped or not _starts_like_path(stripped):
        return None

    first_token, remainder = _split_path_input(stripped)
    drop_path = _resolve_attachment_path(first_token)
    if drop_path is None:
        return None
    return {
        "path": drop_path,
        "is_image": drop_path.suffix.lower() in _IMAGE_EXTENSIONS,
        "remainder": remainder,
    }


def _collect_query_images(
    query: str | None,
    image_arg: str | None = None,
) -> tuple[str, list[Path]]:
    """Collect and deduplicate local images for a single-query CLI flow."""
    message = query or ""
    images: list[Path] = []

    dropped = _detect_file_drop(message) if isinstance(message, str) else None
    if dropped and dropped["is_image"]:
        images.append(dropped["path"])
        message = dropped["remainder"] or (
            f"[User attached image: {dropped['path'].name}]"
        )

    if image_arg:
        explicit_path = _resolve_attachment_path(image_arg)
        if explicit_path is None:
            raise ValueError(f"Image file not found: {image_arg}")
        if explicit_path.suffix.lower() not in _IMAGE_EXTENSIONS:
            raise ValueError(f"Not a supported image file: {explicit_path}")
        images.append(explicit_path)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for image in images:
        normalized = image.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(image)
    return message, deduped


def _format_image_attachment_badges(
    attached_images: list[Path],
    image_counter: int,
    width: int | None = None,
) -> str:
    """Format the attached-image badge row for the interactive CLI."""
    if not attached_images:
        return ""
    terminal_width = width or shutil.get_terminal_size((80, 24)).columns

    def truncate(name: str, limit: int) -> str:
        return name if len(name) <= limit else name[: max(1, limit - 3)] + "..."

    if terminal_width < 52:
        if len(attached_images) == 1:
            return f"[📎 {truncate(attached_images[0].name, 20)}]"
        return f"[📎 {len(attached_images)} images attached]"
    if terminal_width < 80:
        if len(attached_images) == 1:
            return f"[📎 {truncate(attached_images[0].name, 32)}]"
        return (
            f"[📎 {truncate(attached_images[0].name, 20)}] "
            f"[+{len(attached_images) - 1}]"
        )

    base = image_counter - len(attached_images) + 1
    return " ".join(
        f"[📎 Image #{base + index}]" for index in range(len(attached_images))
    )


def _should_auto_attach_clipboard_image_on_paste(pasted_text: str) -> bool:
    """Auto-attach clipboard images only for image-only paste gestures."""
    return not pasted_text.strip()


def _termux_example_image_path(filename: str = "cat.png") -> str:
    """Return a POSIX example image path for Termux."""
    return f"~/storage/shared/Pictures/{filename}"
