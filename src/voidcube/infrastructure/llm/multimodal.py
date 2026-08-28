"""Capability-driven local attachment helpers for chat requests."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


INPUT_MODALITIES = frozenset({"image", "audio", "video"})
SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)
SUPPORTED_AUDIO_MIME_TYPES = frozenset({"audio/mpeg", "audio/wav"})
SUPPORTED_VIDEO_MIME_TYPES = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/webm",
    }
)
MAX_INLINE_ATTACHMENT_REQUEST_BYTES = 44 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 15

_EXTENSION_MIME_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
_LEGACY_IMAGE_MIME_TYPES = {
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
_MIME_MODALITIES = {
    **{mime_type: "image" for mime_type in SUPPORTED_IMAGE_MIME_TYPES},
    **{mime_type: "image" for mime_type in _LEGACY_IMAGE_MIME_TYPES.values()},
    **{mime_type: "audio" for mime_type in SUPPORTED_AUDIO_MIME_TYPES},
    **{mime_type: "video" for mime_type in SUPPORTED_VIDEO_MIME_TYPES},
}
_AUDIO_REQUEST_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}

# Only document-confirmed capabilities belong here. These are used as defaults
# in the configuration prompt; runtime routing requires explicit confirmation
# persisted under the selected Provider/model.
_KNOWN_MODEL_INPUT_CAPABILITIES = {
    "deepseek-v4-flash-vision-exp": frozenset({"image"}),
}


def configured_model_capabilities(
    provider: str,
    model: str,
) -> Mapping[str, Any] | None:
    """Read optional per-model capability declarations from VoidCube config."""
    try:
        from ..config.configuration import get_configured_providers, load_config

        entry = get_configured_providers(load_config()).get(
            str(provider or "").strip()
        )
        if not isinstance(entry, Mapping):
            return None
        capability_map = entry.get("model_capabilities")
        if not isinstance(capability_map, Mapping):
            return None
        candidate = capability_map.get(str(model or "").strip())
        return candidate if isinstance(candidate, Mapping) else None
    except Exception:
        return None


def native_input_modalities(
    provider: str,
    model: str,
    *,
    configured_capabilities: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return native input modalities explicitly confirmed for one model."""
    modalities: set[str] = set()
    if isinstance(configured_capabilities, Mapping):
        for modality in INPUT_MODALITIES:
            explicit = configured_capabilities.get(f"{modality}_input")
            if explicit is None:
                continue
            if bool(explicit):
                modalities.add(modality)
            else:
                modalities.discard(modality)
    return frozenset(modalities)


def suggested_native_input_modalities(provider: str, model: str) -> frozenset[str]:
    """Return known capabilities for configuration prompt defaults only."""
    del provider
    normalized_model = str(model or "").strip().lower()
    return _KNOWN_MODEL_INPUT_CAPABILITIES.get(normalized_model, frozenset())


def supports_native_input(
    provider: str,
    model: str,
    modality: str,
    *,
    configured_capabilities: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether one modality can be sent natively to the model."""
    normalized_modality = str(modality or "").strip().lower()
    return normalized_modality in native_input_modalities(
        provider,
        model,
        configured_capabilities=configured_capabilities,
    )


def supports_native_image_input(
    provider: str,
    model: str,
    *,
    configured_capabilities: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a selected model can receive OpenAI image blocks."""
    return supports_native_input(
        provider,
        model,
        "image",
        configured_capabilities=configured_capabilities,
    )


def native_attachment_supported(attachment: Mapping[str, Any]) -> bool:
    """Return whether attachment metadata can be encoded by native blocks."""
    modality = attachment_modality(attachment)
    mime_type = str(attachment.get("mime_type") or "").strip().lower()
    if not mime_type:
        path = Path(str(attachment.get("path") or "")).expanduser()
        try:
            mime_type = _detect_attachment_mime_type(path, path.read_bytes())
        except OSError:
            return False
    if modality == "image":
        return mime_type in SUPPORTED_IMAGE_MIME_TYPES
    if modality == "audio":
        return mime_type in SUPPORTED_AUDIO_MIME_TYPES
    if modality == "video":
        return mime_type in SUPPORTED_VIDEO_MIME_TYPES
    return False


def attachment_modality(attachment: Mapping[str, Any]) -> str:
    """Return the normalized input modality represented by attachment metadata."""
    kind = str(attachment.get("kind") or "").strip().lower()
    if kind.startswith("local_"):
        candidate = kind.removeprefix("local_")
        if candidate in INPUT_MODALITIES:
            return candidate
    mime_type = str(attachment.get("mime_type") or "").strip().lower()
    return _MIME_MODALITIES.get(mime_type, "")


def attachment_modality_from_path(path: str | Path) -> str:
    """Infer the supported attachment modality from an existing local file."""
    resolved = Path(path).expanduser()
    try:
        data = resolved.read_bytes()
    except OSError:
        return ""
    return _MIME_MODALITIES.get(_detect_attachment_mime_type(resolved, data), "")


def attachments_from_paths(paths: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    """Create safe, content-addressed local references for supported media."""
    attachments: list[dict[str, Any]] = []
    for value in paths:
        path = Path(value).expanduser()
        try:
            resolved = path.resolve(strict=True)
            data = resolved.read_bytes()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Unable to read attachment: {path}") from exc
        mime_type = _detect_attachment_mime_type(resolved, data)
        modality = _MIME_MODALITIES.get(mime_type, "")
        if not modality:
            raise ValueError(
                f"Unsupported attachment {resolved.name}; images must be "
                "JPEG, PNG, GIF, or WebP, and audio/video must be MP3, WAV, "
                "MP4, MOV, or WebM."
            )
        attachments.append(
            {
                "kind": f"local_{modality}",
                "path": str(resolved),
                "mime_type": mime_type,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return tuple(attachments)


def image_attachments_from_paths(paths: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    """Create image-only metadata for the legacy image entry point."""
    attachments = attachments_from_paths(paths)
    non_images = [
        str(attachment.get("path") or "")
        for attachment in attachments
        if attachment_modality(attachment) != "image"
    ]
    if non_images:
        raise ValueError(
            "Native image input accepts JPEG, PNG, GIF, or WebP attachments only."
        )
    return tuple({**attachment, "detail": "auto"} for attachment in attachments)


def build_user_content_with_attachments(
    text: str,
    attachments: Sequence[Mapping[str, Any]],
    *,
    native_modalities: Iterable[str],
) -> list[dict[str, Any]]:
    """Build API-only multimodal content blocks from persisted local references.

    ``input_audio`` and ``video_url`` are emitted only when the selected model
    explicitly declares the matching input capability. ``video_url`` is an
    OpenAI-compatible extension, not a universal protocol.
    """
    enabled = {
        str(modality or "").strip().lower().removesuffix("_input")
        for modality in native_modalities
    }
    content: list[dict[str, Any]] = [{"type": "text", "text": str(text or "")}]
    encoded_bytes = 0
    image_count = 0
    for attachment in attachments:
        modality = attachment_modality(attachment)
        if modality not in enabled or not native_attachment_supported(attachment):
            continue
        path = Path(str(attachment.get("path") or "")).expanduser()
        try:
            data = path.read_bytes()
        except OSError:
            content.append(
                {
                    "type": "text",
                    "text": (
                        "[Previously attached "
                        f"{modality or 'file'} is no longer available: "
                        f"{path.name or path}]"
                    ),
                }
            )
            continue
        mime_type = _detect_attachment_mime_type(path, data)
        if _MIME_MODALITIES.get(mime_type) != modality:
            raise ValueError(
                f"Attached file {path.name or path} is no longer a supported "
                f"{modality} attachment."
            )
        if modality == "image":
            image_count += 1
            if image_count > MAX_IMAGES_PER_REQUEST:
                raise ValueError(
                    f"A native image request can include at most "
                    f"{MAX_IMAGES_PER_REQUEST} images."
                )
        encoded_bytes += 4 * ((len(data) + 2) // 3)
        if encoded_bytes > MAX_INLINE_ATTACHMENT_REQUEST_BYTES:
            raise ValueError(
                "Attached media exceeds the inline request budget. "
                "Use fewer or smaller attachments; Files API support is "
                "required for larger reusable uploads."
            )
        encoded = base64.b64encode(data).decode("ascii")
        if modality == "image":
            detail = str(attachment.get("detail") or "auto").strip().lower()
            if detail not in {"low", "high", "original", "auto"}:
                detail = "auto"
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                        "detail": detail,
                    },
                }
            )
        elif modality == "audio":
            audio_format = _AUDIO_REQUEST_FORMATS.get(mime_type)
            if not audio_format:
                raise ValueError(
                    f"Attached audio {path.name or path} must be MP3 or WAV "
                    "for native OpenAI-compatible audio input."
                )
            content.append(
                {
                    "type": "input_audio",
                    "input_audio": {"data": encoded, "format": audio_format},
                }
            )
        elif modality == "video":
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
    return content


def build_user_content_with_images(
    text: str,
    attachments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build API-only image blocks for callers retained at the image boundary."""
    return build_user_content_with_attachments(
        text,
        attachments,
        native_modalities={"image"},
    )


def _detect_attachment_mime_type(path: Path, data: bytes) -> str:
    image_mime_type = _detect_image_mime_type(data)
    if image_mime_type:
        return image_mime_type
    suffix = path.suffix.lower()
    extension_mime_type = _EXTENSION_MIME_TYPES.get(
        suffix,
        _LEGACY_IMAGE_MIME_TYPES.get(suffix, ""),
    )
    if extension_mime_type.startswith("image/"):
        return ""
    return extension_mime_type


def _detect_image_mime_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


__all__ = [
    "INPUT_MODALITIES",
    "MAX_IMAGES_PER_REQUEST",
    "MAX_INLINE_ATTACHMENT_REQUEST_BYTES",
    "SUPPORTED_AUDIO_MIME_TYPES",
    "SUPPORTED_IMAGE_MIME_TYPES",
    "SUPPORTED_VIDEO_MIME_TYPES",
    "attachment_modality",
    "attachment_modality_from_path",
    "attachments_from_paths",
    "build_user_content_with_attachments",
    "build_user_content_with_images",
    "configured_model_capabilities",
    "image_attachments_from_paths",
    "native_input_modalities",
    "native_attachment_supported",
    "suggested_native_input_modalities",
    "supports_native_image_input",
    "supports_native_input",
]
