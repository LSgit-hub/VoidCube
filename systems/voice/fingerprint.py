from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import wave


class FingerprintStore:
    """Match local-owner speech using a derived template, without user accounts."""

    def __init__(self, path: str | Path, *, threshold: float = 0.86) -> None:
        self.path = Path(path)
        self.threshold = threshold

    def record_owner_template(self, audio_path: str | Path) -> dict[str, float | str]:
        template = extract_voice_template(audio_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"version": 1, "template": template}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"status": "owner_voice_template_recorded", "path": str(self.path)}

    def verify(self, audio_path: str | Path) -> dict[str, float | bool | str]:
        if not self.path.is_file():
            return {
                "owner_voice_matched": False,
                "reason": "owner_voice_template_missing",
                "similarity": 0.0,
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            expected = [float(item) for item in payload["template"]]
            actual = extract_voice_template(audio_path)
            similarity = cosine_similarity(expected, actual)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return {
                "owner_voice_matched": False,
                "reason": f"owner_voice_template_read_failed:{type(exc).__name__}",
                "similarity": 0.0,
            }
        return {
            "owner_voice_matched": similarity >= self.threshold,
            "reason": "matched" if similarity >= self.threshold else "owner_voice_mismatch",
            "similarity": round(similarity, 6),
            "threshold": self.threshold,
        }


def extract_voice_template(audio_path: str | Path) -> list[float]:
    with wave.open(str(audio_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frame_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise ValueError("Only 16-bit PCM WAV is supported for fingerprinting")
    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    if channels > 1:
        samples = tuple(
            sum(samples[index : index + channels]) / channels
            for index in range(0, len(samples), channels)
        )
    if not samples:
        raise ValueError("Audio recording is empty")
    frame_size = max(80, int(frame_rate * 0.02))
    vectors: list[list[float]] = []
    for offset in range(0, len(samples) - frame_size + 1, frame_size):
        frame = [float(value) / 32768.0 for value in samples[offset : offset + frame_size]]
        energy = math.sqrt(sum(value * value for value in frame) / len(frame))
        zcr = sum(
            1 for left, right in zip(frame, frame[1:]) if (left >= 0) != (right >= 0)
        ) / max(1, len(frame) - 1)
        band_features = []
        for band in range(8):
            start = int(band * len(frame) / 16)
            end = max(start + 1, int((band + 1) * len(frame) / 16))
            band_features.append(
                math.sqrt(sum(value * value for value in frame[start:end]) / (end - start))
            )
        vectors.append([energy, zcr, *band_features])
    if not vectors:
        vectors = [[0.0, 0.0, *([0.0] * 8)]]
    dimensions = len(vectors[0])
    template = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimensions)]
    norm = math.sqrt(sum(value * value for value in template))
    if norm <= 1e-9:
        raise ValueError("Audio signal has no measurable voice energy")
    return [round(value / norm, 8) for value in template]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return max(
        0.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm),
        ),
    )
