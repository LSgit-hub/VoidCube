"""Compatibility facade for the canonical voice system package."""

try:
    from voidcube.systems.voice import VoiceConfig, VoiceSessionManager
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.voice import VoiceConfig, VoiceSessionManager

__all__ = ["VoiceConfig", "VoiceSessionManager"]
