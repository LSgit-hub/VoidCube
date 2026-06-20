"""
语音模式
"""

from typing import Optional, Dict, Any

def play_beep() -> None:
    """播放提示音"""
    pass

def create_audio_recorder(output_path: str) -> Optional[Any]:
    """创建录音器"""
    return None

def check_voice_requirements() -> Dict[str, Any]:
    """检查语音要求"""
    return {"available": False, "reason": "Voice disabled"}

def transcribe_recording(audio_path: str) -> Optional[str]:
    """转录录音"""
    return None

def play_audio_file(path: str) -> None:
    """播放音频文件"""
    pass

def detect_audio_environment() -> Dict[str, Any]:
    """检测音频环境"""
    return {"available": False}

def stop_playback() -> None:
    """停止播放"""
    pass

def cleanup_temp_recordings() -> None:
    """清理临时录音"""
    pass

def stream_tts_to_speaker(
    text_queue,
    stop_event,
    done_callback,
    display_callback=None
) -> None:
    """
    Stream text to speaker using TTS.
    
    Args:
        text_queue: Queue containing text chunks to speak
        stop_event: Event to signal stopping
        done_callback: Callback when done
        display_callback: Optional callback to display text
    """
    try:
        while not stop_event.is_set():
            try:
                text = text_queue.get(timeout=0.1)
                if display_callback:
                    display_callback(text)
            except:
                pass
    finally:
        if done_callback:
            done_callback()
