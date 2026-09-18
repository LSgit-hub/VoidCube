from __future__ import annotations

import pytest

from voidcube.systems.perception.local_transport import local_chat_url


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:11434/v1", "http://localhost:11434/api/chat"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/api/chat"),
        ("http://[::1]:11434/v1", "http://[::1]:11434/api/chat"),
    ],
)
def test_local_chat_url_accepts_only_loopback_endpoints(base_url: str, expected: str) -> None:
    assert local_chat_url(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.invalid/v1",
        "http://localhost:11434/v1?redirect=https://example.invalid",
        "http://user:password@localhost:11434/v1",
        "http://localhost:11434/proxy/v1",
    ],
)
def test_local_chat_url_rejects_non_local_or_ambiguous_endpoints(base_url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        local_chat_url(base_url)
