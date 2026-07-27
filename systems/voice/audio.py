from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
import wave


class AudioUnavailableError(RuntimeError):
    pass


class AudioRecorder:
    def __init__(self, *, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels

    @staticmethod
    def available() -> bool:
        try:
            import sounddevice as sd
            import numpy  # noqa: F401
        except ImportError:
            return False
        try:
            device = sd.query_devices(kind="input")
        except Exception:
            return False
        return int(device.get("max_input_channels") or 0) > 0

    async def record(
        self,
        output_path: str | Path,
        *,
        duration_seconds: float,
        stop_event: asyncio.Event,
    ) -> Path:
        if not self.available():
            raise AudioUnavailableError(
                "Voice capture requires the optional sounddevice and numpy packages"
            )
        import numpy as np
        import sounddevice as sd

        frame_count = max(1, int(self.sample_rate * max(0.1, duration_seconds)))
        recording = sd.rec(
            frame_count,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
        )
        elapsed = 0.0
        while elapsed < duration_seconds and not stop_event.is_set():
            step = min(0.05, duration_seconds - elapsed)
            await asyncio.sleep(max(step, 0.0))
            elapsed += step
        sd.stop()
        used_frames = max(1, min(len(recording), int(elapsed * self.sample_rate)))
        pcm = np.asarray(recording[:used_frames], dtype=np.int16)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm.tobytes())
        return path


class AudioPlayer:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._sounddevice_playing = False

    @staticmethod
    def _sounddevice_available() -> bool:
        try:
            import sounddevice as sd
            import soundfile  # noqa: F401
        except ImportError:
            return False
        try:
            device = sd.query_devices(kind="output")
        except Exception:
            return False
        return int(device.get("max_output_channels") or 0) > 0

    @classmethod
    def available(cls) -> bool:
        return cls._sounddevice_available() or bool(shutil.which("ffplay"))

    async def play(self, path: str | Path, *, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        if self._sounddevice_available():
            import sounddevice as sd
            import soundfile as sf

            audio, sample_rate = sf.read(
                str(path),
                dtype="float32",
                always_2d=True,
            )
            sd.play(audio, samplerate=sample_rate, blocking=False)
            self._sounddevice_playing = True
            try:
                while bool(getattr(sd.get_stream(), "active", False)):
                    if stop_event.is_set():
                        sd.stop()
                        break
                    await asyncio.sleep(0.05)
            finally:
                self._sounddevice_playing = False
            return

        executable = shutil.which("ffplay")
        if not executable:
            raise AudioUnavailableError(
                "Audio playback requires sounddevice + soundfile or ffplay on PATH"
            )
        self._process = subprocess.Popen(
            [executable, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            ),
        )
        try:
            while self._process.poll() is None:
                if stop_event.is_set():
                    self.stop()
                    break
                await asyncio.sleep(0.05)
        finally:
            self._process = None

    def stop(self) -> None:
        if self._sounddevice_playing:
            try:
                import sounddevice as sd

                sd.stop()
            except ImportError:
                pass
            self._sounddevice_playing = False
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
