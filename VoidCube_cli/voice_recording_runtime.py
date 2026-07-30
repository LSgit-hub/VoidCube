"""Terminal voice recording and transcription coordination."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import Any

from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


@dataclass(frozen=True)
class VoiceRecordingPorts:
    """Terminal callbacks required by the synchronous voice transport."""

    state: CliVoiceRuntimeState
    should_exit: Callable[[], bool]
    is_termux_environment: Callable[[], bool]
    invalidate: Callable[[], None]
    emit: Callable[[str], None]
    enqueue_input: Callable[[str], None]
    clear_attached_images: Callable[[], None]
    start_recording: Callable[[], None]
    thread_factory: Callable[..., Thread]
    sleep: Callable[[float], None]


def start_terminal_voice_recording(ports: VoiceRecordingPorts) -> None:
    """Start a microphone capture while preserving the established CLI flow."""
    if ports.should_exit():
        return

    from tools.voice_mode import create_audio_recorder, check_voice_requirements

    requirements = check_voice_requirements()
    if not requirements["audio_available"]:
        if ports.is_termux_environment():
            details = requirements.get("details", "")
            if "Termux:API Android app is not installed" in details:
                raise RuntimeError(
                    "Termux:API command package detected, but the Android app is missing.\n"
                    "Install/update the Termux:API Android app, then retry /voice on.\n"
                    "Fallback: pkg install python-numpy portaudio && python -m pip install sounddevice"
                )
            raise RuntimeError(
                "Voice mode requires either Termux:API microphone access or Python audio libraries.\n"
                "Option 1: pkg install termux-api and install the Termux:API Android app\n"
                "Option 2: pkg install python-numpy portaudio && python -m pip install sounddevice"
            )
        raise RuntimeError(
            "Voice mode requires sounddevice and numpy.\n"
            "Install with: pip install sounddevice numpy\n"
            "Or: pip install VoidCube-agent[voice]"
        )
    if not requirements.get("stt_available", requirements.get("stt_key_set")):
        raise RuntimeError(
            "Voice mode requires an STT provider for transcription.\n"
            "Option 1: pip install faster-whisper  (free, local)\n"
            "Option 2: Set GROQ_API_KEY (free tier)\n"
            "Option 3: Set VOICE_TOOLS_OPENAI_KEY (paid)"
        )

    state = ports.state
    with state.lock:
        if state.recording:
            return
        state.recording = True

    voice_config: dict[str, Any] = {}
    try:
        from VoidCube_app.config import load_config
        voice_config = load_config().get("voice", {})
    except Exception:
        pass

    if state.recorder is None:
        state.recorder = create_audio_recorder()
    state.recorder._silence_threshold = voice_config.get("silence_threshold", 200)
    state.recorder._silence_duration = voice_config.get("silence_duration", 3.0)

    def stop_after_silence() -> None:
        with state.lock:
            if not state.recording:
                return
        ports.emit("\nSilence detected, auto-stopping...")
        ports.invalidate()
        stop_terminal_voice_recording(ports)

    try:
        from tools.voice_mode import play_beep
        play_beep(frequency=880, count=1)
    except Exception:
        pass

    try:
        state.recorder.start(on_silence_stop=stop_after_silence)
    except Exception:
        with state.lock:
            state.recording = False
        raise

    if getattr(state.recorder, "supports_silence_autostop", True):
        hint = "auto-stops on silence | Ctrl+B to stop & exit continuous"
    elif ports.is_termux_environment():
        hint = "Termux:API capture | Ctrl+B to stop"
    else:
        hint = "Ctrl+B to stop"
    ports.emit(f"\nRecording... ({hint})")

    def refresh_audio_level() -> None:
        while True:
            with state.lock:
                still_recording = state.recording
            if not still_recording:
                return
            ports.invalidate()
            ports.sleep(0.15)

    ports.thread_factory(target=refresh_audio_level, daemon=True).start()


def stop_terminal_voice_recording(ports: VoiceRecordingPorts) -> None:
    """Stop capture, transcribe it, and queue an input without owning the CLI."""
    state = ports.state
    with state.lock:
        if not state.recording:
            return
        state.recording = False
        state.processing = True

    submitted = False
    recording_path = None
    try:
        if state.recorder is None:
            return
        recording_path = state.recorder.stop()

        try:
            from tools.voice_mode import play_beep
            play_beep(frequency=660, count=2)
        except Exception:
            pass

        if recording_path is None:
            ports.emit("No speech detected.")
            return

        ports.invalidate()
        ports.emit("Transcribing...")

        model = None
        try:
            from VoidCube_app.config import load_config
            model = load_config().get("stt", {}).get("model")
        except Exception:
            pass

        from tools.voice_mode import transcribe_recording
        result = transcribe_recording(recording_path, model=model)
        if result.get("success") and result.get("transcript", "").strip():
            ports.clear_attached_images()
            ports.invalidate()
            ports.enqueue_input(result["transcript"].strip())
            submitted = True
        elif result.get("success"):
            ports.emit("No speech detected.")
        else:
            ports.emit(f"\nTranscription failed: {result.get('error', 'Unknown error')}")
    except Exception as error:
        ports.emit(f"\nVoice processing error: {error}")
    finally:
        with state.lock:
            state.processing = False
        ports.invalidate()
        try:
            if recording_path and os.path.isfile(recording_path):
                os.unlink(recording_path)
        except Exception:
            pass

        if not submitted:
            state.no_speech_count += 1
            if state.no_speech_count >= 3:
                state.continuous = False
                state.no_speech_count = 0
                ports.emit("No speech detected 3 times, continuous mode stopped.")
                state.stop_continuous = True
        else:
            state.no_speech_count = 0

    if state.stop_continuous:
        state.stop_continuous = False
        return

    if not submitted and state.continuous and not state.recording:
        def restart_recording() -> None:
            try:
                ports.start_recording()
                ports.invalidate()
            except Exception as error:
                ports.emit(f"Voice auto-restart failed: {error}")

        ports.thread_factory(target=restart_recording, daemon=True).start()
