from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import wave


class AudioUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class AudioFrame:
    samples: Any
    rms: float
    peak: float
    level: float
    captured_at: float
    overflowed: bool = False


class AudioInputStream:
    """Persistent microphone stream that bridges PortAudio into asyncio."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_samples: int = 512,
        queue_frames: int = 96,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_samples = int(frame_samples)
        self.queue_frames = max(8, int(queue_frames))
        self._queue: asyncio.Queue[AudioFrame] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: Any = None

    @staticmethod
    def available() -> bool:
        return AudioRecorder.available()

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if self._stream is not None:
            return
        if not self.available():
            raise AudioUnavailableError(
                "Voice capture requires the optional sounddevice and numpy packages"
            )
        import numpy as np
        import sounddevice as sd

        self._loop = loop or asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self.queue_frames)

        def callback(indata, frames, timing, status) -> None:
            del frames, timing
            mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
            absolute = np.abs(mono)
            peak = float(absolute.max()) if mono.size else 0.0
            rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
            dbfs = 20.0 * math.log10(max(rms, 1e-6))
            level = max(0.0, min(1.0, (dbfs + 60.0) / 55.0))
            frame = AudioFrame(
                samples=mono,
                rms=rms,
                peak=peak,
                level=level,
                captured_at=time.monotonic(),
                overflowed=bool(status),
            )
            active_loop = self._loop
            if active_loop is not None and not active_loop.is_closed():
                active_loop.call_soon_threadsafe(self._enqueue, frame)

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=self.frame_samples,
            callback=callback,
        )
        stream.start()
        self._stream = stream

    def _enqueue(self, frame: AudioFrame) -> None:
        queue = self._queue
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(frame)

    async def read(self) -> AudioFrame:
        queue = self._queue
        if queue is None:
            raise AudioUnavailableError("Microphone stream is not running")
        return await queue.get()

    def flush(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        self.flush()
        self._queue = None
        self._loop = None


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

    @staticmethod
    def write_float_waveform(
        output_path: str | Path,
        samples: Any,
        *,
        sample_rate: int,
    ) -> Path:
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        pcm = np.asarray(
            np.clip(audio, -1.0, 1.0) * 32767.0,
            dtype=np.int16,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
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

    async def play_wake_cue(self) -> None:
        """Play a short local tone without opening a TTS request."""
        if not self._sounddevice_available():
            return

        def play_tone() -> None:
            import numpy as np
            import sounddevice as sd

            sample_rate = 24000
            duration = 0.12
            count = int(sample_rate * duration)
            timeline = np.arange(count, dtype=np.float32) / sample_rate
            envelope = np.minimum(1.0, np.minimum(timeline / 0.02, (duration - timeline) / 0.03))
            tone = (0.12 * np.sin(2.0 * np.pi * 880.0 * timeline) * envelope).astype(
                np.float32
            )
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
            ) as stream:
                stream.write(tone.reshape(-1, 1))

        await asyncio.to_thread(play_tone)
