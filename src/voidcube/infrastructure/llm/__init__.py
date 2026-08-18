"""LLM request transport primitives shared by model-facing adapters."""

from .transport import completion_token_retry_kwargs, validate_llm_response
from .request import ChatRequestConfig, build_chat_completion_kwargs
from .error_classifier import ClassifiedError, FailoverReason, classify_api_error, is_stream_drop_error

__all__ = [
    "ChatRequestConfig",
    "build_chat_completion_kwargs",
    "completion_token_retry_kwargs",
    "validate_llm_response",
    "ClassifiedError",
    "FailoverReason",
    "classify_api_error",
    "is_stream_drop_error",
]
