"""Dependency-light frame change detection for the local perception loop."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class FrameChangeResult:
    """Result of comparing a frame with the previous sampled frame."""

    changed: bool
    changed_ratio: float
    digest: str
    sample_size: int


class FrameChangeDetector:
    """Compare sampled frame bytes without requiring an image dependency.

    Capture adapters may pass raw image bytes or a grayscale/thumbnail buffer.
    Sampling keeps the detector cheap; a later adapter can replace this with
    perceptual hashes or regional image metrics without changing its result
    contract.
    """

    def __init__(self, *, threshold: float = 0.08, sample_bytes: int = 4096) -> None:
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self.sample_bytes = max(64, int(sample_bytes))
        self._previous: bytes | None = None
        self._previous_digest = ""

    def reset(self) -> None:
        self._previous = None
        self._previous_digest = ""

    def observe(self, frame: bytes | bytearray | memoryview) -> FrameChangeResult:
        current = bytes(frame)
        if not current:
            return FrameChangeResult(False, 0.0, sha256(b"").hexdigest(), 0)
        sampled = self._sample(current)
        digest = sha256(sampled).hexdigest()
        if self._previous is None:
            self._previous = sampled
            self._previous_digest = digest
            return FrameChangeResult(True, 1.0, digest, len(sampled))
        size = max(len(sampled), len(self._previous))
        left = sampled.ljust(size, b"\0")
        right = self._previous.ljust(size, b"\0")
        changed = sum(a != b for a, b in zip(left, right)) / size
        self._previous = sampled
        self._previous_digest = digest
        return FrameChangeResult(changed >= self.threshold, changed, digest, len(sampled))

    def _sample(self, frame: bytes) -> bytes:
        if len(frame) <= self.sample_bytes:
            return frame
        step = len(frame) / self.sample_bytes
        return bytes(frame[min(len(frame) - 1, int(index * step))] for index in range(self.sample_bytes))


__all__ = ["FrameChangeDetector", "FrameChangeResult"]
