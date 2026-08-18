"""Terminal voice recording and transcription coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .voice_runtime_state import CliVoiceRuntimeState
from ..voice.session_runtime import VoiceSessionRuntime


@dataclass(frozen=True)
class VoiceRecordingPorts:
    """Terminal callbacks required by the canonical voice session adapter."""

    state: CliVoiceRuntimeState
    should_exit: Callable[[], bool]
    invalidate: Callable[[], None]
    emit: Callable[[str], None]
    enqueue_input: Callable[[str], None]
    clear_attached_images: Callable[[], None]
    voice: VoiceSessionRuntime | None = None


def start_terminal_voice_recording(ports: VoiceRecordingPorts) -> None:
    """Capture and transcribe one utterance through the canonical voice owner."""
    if ports.should_exit():
        return

    voice = ports.voice
    if voice is None:
        raise RuntimeError("Canonical voice session is not configured")
    requirements = voice.status().get("voice", {})
    if not requirements.get("capture_available"):
        raise RuntimeError(
            "Voice capture requires sounddevice and numpy with an available input device."
        )
    if not requirements.get("stt_configured"):
        raise RuntimeError(
            "Voice transcription requires a configured STT provider."
        )

    state = ports.state
    with state.lock:
        if state.recording:
            return
        state.recording = True
    ports.emit("\n录音中……")
    ports.invalidate()
    try:
        result = voice.transcribe_once()
    except Exception as error:
        with state.lock:
            state.recording = False
        ports.invalidate()
        _finish_voice_result(ports, {"status": "error", "reason": str(error)})
        return
    finally:
        with state.lock:
            state.recording = False
            state.processing = False
        ports.invalidate()

    _finish_voice_result(ports, result)


def stop_terminal_voice_recording(ports: VoiceRecordingPorts) -> None:
    """Interrupt the active canonical capture without owning its event loop."""
    state = ports.state
    with state.lock:
        if not state.recording:
            return
        state.continuous = False
    with state.lock:
        state.recording = False
    if ports.voice is not None:
        ports.voice.interrupt()
    ports.emit("\n录音已取消。")
    ports.invalidate()


def _finish_voice_result(ports: VoiceRecordingPorts, result: dict[str, object]) -> None:
    status = str(result.get("status") or "error")
    transcript = str(result.get("transcript") or "").strip()
    if status == "complete" and transcript:
        ports.state.no_speech_count = 0
        ports.clear_attached_images()
        ports.enqueue_input(transcript)
        ports.invalidate()
        return
    if status == "empty":
        ports.emit("未检测到语音。")
    elif status == "interrupted":
        return
    elif status == "rejected":
        ports.emit("语音样本未通过所有者声音验证。")
    else:
        ports.emit(f"\n语音转写失败：{result.get('reason', '未知错误')}")

    state = ports.state
    state.no_speech_count += 1
    if state.no_speech_count >= 3:
        state.continuous = False
        state.no_speech_count = 0
        ports.emit("连续 3 次未检测到语音，已停止连续模式。")
        state.stop_continuous = True
    ports.invalidate()
