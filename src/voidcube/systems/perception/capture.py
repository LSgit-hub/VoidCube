"""Optional local screen capture adapter.

The dependency is intentionally lazy: importing VoidCube must remain possible
on machines that have not enabled screen perception.  This adapter only reads
pixels and has no input or application-control capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class ScreenCaptureUnavailable(RuntimeError):
    """Raised when the optional ``mss`` backend is not installed or usable."""


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    """A single in-memory screen frame."""

    pixels: bytes
    width: int
    height: int
    monitor: int
    captured_at: datetime

    def to_png(self) -> bytes:
        """Encode raw BGRA pixels as RGB PNG; reject corrupt frame buffers."""
        from io import BytesIO
        from PIL import Image

        if self.width <= 0 or self.height <= 0 or len(self.pixels) != self.width * self.height * 4:
            raise ValueError("invalid BGRA frame dimensions or byte length")
        image = Image.frombytes("RGB", (self.width, self.height), self.pixels, "raw", "BGRX")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class MssScreenCapture:
    """Read a monitor or ROI through the optional ``mss`` package."""

    def __init__(
        self,
        *,
        monitor: int = 1,
        roi: dict[str, int] | None = None,
    ) -> None:
        if int(monitor) < 0:
            raise ValueError("monitor must be non-negative")
        self.monitor = int(monitor)
        self.roi = self._normalize_roi(roi)
        self._mss: Any | None = None

    def __enter__(self) -> "MssScreenCapture":
        self._open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _open(self) -> Any:
        if self._mss is not None:
            return self._mss
        try:
            import mss
        except ImportError as exc:
            raise ScreenCaptureUnavailable(
                "screen perception requires the optional 'mss' package"
            ) from exc
        try:
            self._mss = mss.mss()
        except Exception as exc:  # pragma: no cover - host-specific capture errors
            raise ScreenCaptureUnavailable(f"mss screen capture unavailable: {exc}") from exc
        return self._mss

    def capture(self) -> ScreenFrame:
        backend = self._open()
        monitors = list(getattr(backend, "monitors", []) or [])
        if self.monitor >= len(monitors):
            raise ScreenCaptureUnavailable(
                f"monitor {self.monitor} is unavailable; detected {max(0, len(monitors) - 1)} monitor(s)"
            )
        area = dict(self.roi or monitors[self.monitor])
        try:
            shot = backend.grab(area)
            pixels = bytes(shot.raw)
            width = int(shot.width)
            height = int(shot.height)
        except Exception as exc:  # pragma: no cover - host-specific capture errors
            raise ScreenCaptureUnavailable(f"screen capture failed: {exc}") from exc
        return ScreenFrame(
            pixels=pixels,
            width=width,
            height=height,
            monitor=self.monitor,
            captured_at=datetime.now(timezone.utc),
        )

    def close(self) -> None:
        backend, self._mss = self._mss, None
        if backend is not None:
            close = getattr(backend, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _normalize_roi(roi: dict[str, int] | None) -> dict[str, int] | None:
        if roi is None:
            return None
        required = {"left", "top", "width", "height"}
        if set(roi) != required:
            raise ValueError("roi must contain exactly left, top, width, and height")
        normalized = {key: int(value) for key, value in roi.items()}
        if normalized["width"] <= 0 or normalized["height"] <= 0:
            raise ValueError("roi width and height must be positive")
        return normalized


__all__ = ["MssScreenCapture", "ScreenCaptureUnavailable", "ScreenFrame"]
