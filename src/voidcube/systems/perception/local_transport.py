"""Loopback-only Ollama transport shared by screen and text analysis."""

from ipaddress import ip_address
from urllib.parse import urlsplit


def local_chat_url(base_url: str) -> str:
    url = urlsplit(base_url.strip())
    host = url.hostname or ""
    try:
        local = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        local = False
    if (not local or url.scheme not in {"http", "https"}
            or url.username or url.password or url.query or url.fragment
            or url.path.rstrip("/") not in {"", "/v1"}):
        raise ValueError("perception requires a loopback Ollama endpoint")
    return f"{url.scheme}://{url.netloc}/api/chat"


def complete_local(payload: dict, *, timeout: float) -> str:
    import httpx
    from ...infrastructure.providers.runtime import resolve_runtime_provider

    runtime = resolve_runtime_provider(requested="ollama")
    endpoint = local_chat_url(str(runtime.get("base_url") or ""))
    # Environment proxies and HTTP redirects must never move frames off-host.
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict) or body.get("done_reason") == "length":
        raise ValueError("incomplete Ollama response")
    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama returned empty content")
    return content
