"""Provider-error projection for scheduled execution writebacks."""

from __future__ import annotations

import re
import time
from typing import Any, Dict

from .error_classifier import FailoverReason, classify_api_error


def scheduled_rate_limit_metadata(error: str) -> Dict[str, Any]:
    message = str(error or "").strip()
    if not message:
        return {
            "rate_limited": False,
            "retry_after_seconds": None,
            "error_code": None,
        }
    classified = classify_api_error(RuntimeError(message))
    rate_limited = (
        classified.reason is FailoverReason.rate_limit
        or bool(re.search(r"\b429\b", message))
    )
    retry_after: float | None = None
    reset_at = classified.error_context.get("reset_at")
    if rate_limited and reset_at not in (None, ""):
        try:
            retry_after = max(0.0, float(reset_at) - time.time())
        except (TypeError, ValueError):
            retry_after = None
    return {
        "rate_limited": rate_limited,
        "retry_after_seconds": retry_after,
        "error_code": 429 if rate_limited else None,
    }


__all__ = ["scheduled_rate_limit_metadata"]
