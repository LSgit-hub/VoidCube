"""Compatibility facade for canonical LLM error classification."""

try:
    from voidcube.infrastructure.llm.error_classifier import *
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.llm.error_classifier import *
