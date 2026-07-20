from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.error_classifier import (
    FailoverReason,
    classify_api_error,
    clean_error_message,
    extract_api_error_context,
    is_stream_drop_error,
    retry_after_seconds,
    summarize_api_error,
)


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


class FakeApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.response = SimpleNamespace(headers=headers or {})


def test_summarize_api_error_reduces_html_to_title_and_request_id():
    error = FakeApiError(
        "<!DOCTYPE html><title>Service unavailable</title>"
        "Cloudflare Ray ID: <strong>ray-123</strong>",
        status_code=503,
    )

    summary = summarize_api_error(error)

    assert summary == "HTTP 503 - Service unavailable - Ray ray-123"
    assert "<!DOCTYPE" not in summary


def test_summarize_api_error_prefers_structured_body_message():
    error = FakeApiError(
        "generic failure",
        status_code=400,
        body={"error": {"message": "invalid request payload"}},
    )

    assert summarize_api_error(error) == "HTTP 400: invalid request payload"


def test_clean_error_message_normalizes_whitespace_and_html():
    assert clean_error_message("alpha\n   beta") == "alpha beta"
    assert clean_error_message("<HTML>large proxy response</HTML>") == (
        "Service temporarily unavailable (HTML error page returned)"
    )


def test_clean_error_message_applies_configured_limit():
    assert clean_error_message("abcdefgh", max_length=5) == "abcde..."


def test_retry_after_seconds_prefers_header_and_caps_delay():
    error = FakeApiError(
        "rate limited",
        body={"error": {"retry_after": 15}},
        headers={"Retry-After": "500"},
    )

    assert retry_after_seconds(error) == 120.0


def test_retry_after_seconds_falls_back_to_payload():
    error = FakeApiError(
        "rate limited",
        body={"error": {"retry_after": "2.5"}},
    )

    assert retry_after_seconds(error) == 2.5


def test_extract_api_error_context_is_attached_to_classification():
    error = FakeApiError(
        "rate limited",
        status_code=429,
        body={
            "error": {
                "code": "quota_window",
                "message": "retry later",
                "retry_after": 30,
            }
        },
        headers={"Retry-After": "25", "x-ratelimit-reset": "999"},
    )

    context = extract_api_error_context(error, now=100.0)
    classified = classify_api_error(error, provider="openrouter", model="test-model")

    assert context == {
        "reason": "quota_window",
        "message": "retry later",
        "reset_at": 125.0,
    }
    assert classified.reason is FailoverReason.rate_limit
    assert classified.status_code == 429
    assert classified.error_context["reason"] == "quota_window"
    assert classified.error_context["message"] == "retry later"
    assert classified.error_context["reset_at"] > 0


@pytest.mark.parametrize(
    ("message", "expected_reset"),
    [
        ('quotaResetDelay: "1500ms"', 101.5),
        ("please retry after 8 seconds", 108.0),
    ],
)
def test_extract_api_error_context_parses_message_delays(message, expected_reset):
    context = extract_api_error_context(FakeApiError(message), now=100.0)

    assert context["reset_at"] == expected_reset


def test_is_stream_drop_error_rejects_http_failures():
    assert is_stream_drop_error(FakeApiError("network connection lost")) is True
    assert is_stream_drop_error(
        FakeApiError("network connection lost", status_code=502)
    ) is False


def test_run_agent_only_references_declared_failover_reasons():
    source = (ROOT / "run_agent.py").read_text(encoding="utf-8")
    referenced = set(re.findall(r"FailoverReason\.(\w+)", source))

    assert referenced <= set(FailoverReason.__members__)
