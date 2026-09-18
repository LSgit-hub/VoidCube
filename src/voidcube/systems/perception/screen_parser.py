"""Offline screen-parser boundary for OmniParser-like adapters.

The project does not import a third-party parser at module load time.  An
adapter can be supplied by an optional installation or a local subprocess,
while this module owns only normalization and the read-only contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class ScreenParserUnavailable(RuntimeError):
    """Raised when an optional screen parser cannot be used."""


class ScreenParser(Protocol):
    """Parse one image without clicking, typing, or controlling applications."""

    def parse(self, image_bytes: bytes) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class ParsedScreen:
    """Normalized parser output passed as context to a local VL model."""

    elements: tuple[dict[str, Any], ...] = ()
    visible_text: tuple[str, ...] = ()
    confidence: float = 0.0
    backend: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "elements": [dict(item) for item in self.elements],
            "visible_text": list(self.visible_text),
            "confidence": self.confidence,
        }


class OmniParserAdapter:
    """Adapt an injected, offline OmniParser-compatible backend.

    ``backend`` may expose ``parse(image_bytes)`` or be directly callable.  No
    package import, model download, network access, or action API is performed
    here.  Integrators must provide a parser instance that is already local.
    """

    def __init__(self, backend: Any, *, backend_name: str = "omniparser") -> None:
        if backend is None:
            raise ValueError("screen parser backend must not be None")
        if not callable(getattr(backend, "parse", None)) and not callable(backend):
            raise TypeError("screen parser backend must expose parse() or be callable")
        self.backend = backend
        self.backend_name = str(backend_name or "omniparser").strip() or "omniparser"

    def parse(self, image_bytes: bytes) -> ParsedScreen:
        if not image_bytes:
            raise ScreenParserUnavailable("screen parser input is empty")
        try:
            parser = getattr(self.backend, "parse", None)
            raw = parser(image_bytes) if callable(parser) else self.backend(image_bytes)
            return normalize_screen_parser_output(raw, backend=self.backend_name)
        except ScreenParserUnavailable:
            raise
        except Exception as exc:
            raise ScreenParserUnavailable(
                f"{self.backend_name} screen parsing failed: {type(exc).__name__}: {exc}"
            ) from exc


def normalize_screen_parser_output(raw: Any, *, backend: str = "") -> ParsedScreen:
    """Normalize common detector/OCR output shapes without preserving actions."""

    if isinstance(raw, ParsedScreen):
        return raw
    if not isinstance(raw, Mapping):
        raise ScreenParserUnavailable("screen parser output must be a mapping")
    elements_raw = raw.get("elements") or raw.get("ui_elements") or raw.get("parsed_content") or ()
    if isinstance(elements_raw, Mapping):
        elements_raw = list(elements_raw.values())
    elements: list[dict[str, Any]] = []
    if isinstance(elements_raw, (list, tuple)):
        for item in elements_raw:
            if not isinstance(item, Mapping):
                continue
            element = {
                key: item[key]
                for key in ("type", "element_type", "label", "text", "bbox", "confidence")
                if key in item
            }
            if element:
                elements.append(element)
    texts = raw.get("visible_text") or raw.get("texts") or raw.get("ocr_text") or ()
    if isinstance(texts, str):
        texts = (texts,)
    if not isinstance(texts, (list, tuple)):
        texts = ()
    visible_text = tuple(str(item).strip()[:2000] for item in texts if str(item).strip())
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return ParsedScreen(
        elements=tuple(elements),
        visible_text=visible_text,
        confidence=confidence,
        backend=str(backend or raw.get("backend") or "").strip(),
    )


__all__ = [
    "OmniParserAdapter",
    "ParsedScreen",
    "ScreenParser",
    "ScreenParserUnavailable",
    "normalize_screen_parser_output",
]
