"""Attachment command handlers backed by explicit CLI ports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, AbstractSet

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class PasteCommandText:
    termux_unavailable: str
    extraction_failed: str
    no_image: str


@dataclass(frozen=True, slots=True)
class PasteCommandPorts:
    is_termux: Callable[[], bool]
    has_clipboard_image: Callable[[], bool]
    attach_clipboard_image: Callable[[], bool]
    attachment_count: Callable[[], int]
    emit: Callable[[str], None]
    text: PasteCommandText


@dataclass(frozen=True, slots=True)
class ImageCommandText:
    dim_prefix: str
    reset_suffix: str
    tip_prefix: str


@dataclass(frozen=True, slots=True)
class ImageCommandPorts:
    is_termux: Callable[[], bool]
    split_path: Callable[[str], tuple[str, str]]
    resolve_path: Callable[[str], Path | None]
    supported_extensions: AbstractSet[str]
    append_attachment: Callable[[Path], None]
    termux_example_path: Callable[[str], str]
    emit: Callable[[str], None]
    text: ImageCommandText


@dataclass(frozen=True, slots=True)
class AttachmentCommandPorts:
    split_path: Callable[[str], tuple[str, str]]
    resolve_path: Callable[[str], Path | None]
    supported_extensions: AbstractSet[str]
    append_attachment: Callable[[Path], None]
    emit: Callable[[str], None]


def handle_attachment_command(
    request: ParsedCliCommand,
    *,
    ports: AttachmentCommandPorts,
) -> None:
    """Attach one local image, audio, or video for the next user turn."""
    if not request.arguments:
        ports.emit("  Usage: /attach <image|audio|video path>")
        return
    path_token, remainder = ports.split_path(request.arguments)
    attachment_path = ports.resolve_path(path_token)
    if attachment_path is None:
        ports.emit(f"  File not found: {path_token}")
        return
    if attachment_path.suffix.lower() not in ports.supported_extensions:
        ports.emit(
            f"  Not a supported attachment file: {attachment_path.name}"
        )
        return
    ports.append_attachment(attachment_path)
    ports.emit(f"  Attached: {attachment_path.name}")
    if remainder:
        ports.emit(f"  Next prompt: {remainder}")


def handle_paste_command(
    request: ParsedCliCommand,
    *,
    ports: PasteCommandPorts,
) -> None:
    del request
    if ports.is_termux():
        ports.emit(ports.text.termux_unavailable)
        return
    if not ports.has_clipboard_image():
        ports.emit(ports.text.no_image)
        return
    if not ports.attach_clipboard_image():
        ports.emit(ports.text.extraction_failed)
        return
    ports.emit(
        f"  📎 Image #{ports.attachment_count()} attached from clipboard"
    )


def handle_image_command(
    request: ParsedCliCommand,
    *,
    ports: ImageCommandPorts,
) -> None:
    raw_args = request.arguments
    dim = ports.text.dim_prefix
    reset = ports.text.reset_suffix
    if not raw_args:
        hint = (
            ports.termux_example_path("cat.png")
            if ports.is_termux()
            else "/path/to/image.png"
        )
        ports.emit(f"  {dim}Usage: /image <path>  e.g. /image {hint}{reset}")
        return

    path_token, remainder = ports.split_path(raw_args)
    image_path = ports.resolve_path(path_token)
    if image_path is None:
        ports.emit(f"  {dim}(>_<) File not found: {path_token}{reset}")
        return
    if image_path.suffix.lower() not in ports.supported_extensions:
        ports.emit(
            f"  {dim}(._.) Not a supported image file: {image_path.name}{reset}"
        )
        return

    ports.append_attachment(image_path)
    ports.emit(f"  📎 Attached image: {image_path.name}")
    if remainder:
        ports.emit(
            f"  {dim}Now type your prompt (or use --image in single-query mode): "
            f"{remainder}{reset}"
        )
    elif ports.is_termux():
        ports.emit(
            f"  {dim}{ports.text.tip_prefix} type your next message, or run "
            f"VoidCube chat -q --image "
            f"{ports.termux_example_path(image_path.name)} \"What do you see?\""
            f"{reset}"
        )
