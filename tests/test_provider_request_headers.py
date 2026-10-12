"""Config-driven custom request headers for OpenAI-compatible providers."""

from __future__ import annotations

import pytest

from voidcube.infrastructure.providers.request_headers import (
    SESSION_ID_PLACEHOLDER,
    builtin_headers_for_base_url,
    normalize_header_template,
    provider_header_template,
    resolve_header_template,
    resolve_provider_headers,
)


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_header_cache():
    """Keep the module-level template cache from leaking across tests.

    The cache is keyed by provider and invalidated by config mtime, so a test
    that populates it (directly or through another module's request path)
    would otherwise satisfy a later lookup and bypass its monkeypatched reader.
    """
    from voidcube.infrastructure.providers import request_headers

    request_headers._USER_TEMPLATE_CACHE.clear()
    yield
    request_headers._USER_TEMPLATE_CACHE.clear()


def test_normalize_header_template_drops_reserved_and_blank_names():
    template = normalize_header_template(
        {
            "x-opencode-session": "{session_id}",
            "Authorization": "Bearer override",
            "Content-Type": "application/xml",
            "  ": "ignored",
            "User-Agent": "my-agent/1.0",
        }
    )

    assert template == {
        "x-opencode-session": "{session_id}",
        "User-Agent": "my-agent/1.0",
    }


def test_normalize_header_template_rejects_non_mapping():
    assert normalize_header_template(None) == {}
    assert normalize_header_template("x-opencode-session: 1") == {}
    assert normalize_header_template(["a"]) == {}


def test_resolve_header_template_expands_session_placeholder():
    resolved = resolve_header_template(
        {"x-opencode-session": SESSION_ID_PLACEHOLDER, "User-Agent": "my-agent/1.0"},
        session_id="20261011_150139_0cdc79",
    )

    assert resolved == {
        "x-opencode-session": "20261011_150139_0cdc79",
        "User-Agent": "my-agent/1.0",
    }


def test_resolve_header_template_drops_unresolved_and_empty_values():
    resolved = resolve_header_template(
        {
            "x-opencode-session": SESSION_ID_PLACEHOLDER,
            "X-Empty": "",
            "X-Static": "keep",
        },
        session_id="",
    )

    assert resolved == {"X-Static": "keep"}


def test_builtin_headers_come_from_the_registry():
    openrouter = builtin_headers_for_base_url("https://openrouter.ai/api/v1")
    kimi = builtin_headers_for_base_url("https://api.kimi.moonshot.cn/v1")

    assert openrouter["X-OpenRouter-Title"] == "Voidcube Agent"
    assert kimi == {"User-Agent": "KimiCLI/1.30.0"}


def test_builtin_headers_are_empty_for_unknown_endpoints():
    assert builtin_headers_for_base_url("https://api.deepseek.com/v1") == {}
    assert builtin_headers_for_base_url("") == {}


def test_unknown_endpoint_has_no_implicit_headers():
    assert builtin_headers_for_base_url("https://opencode.ai/zen/go/v1") == {}


def test_grok_endpoint_has_no_implicit_headers():
    assert builtin_headers_for_base_url("https://api.x.ai/v1") == {}


def test_qwen_headers_are_selected_by_provider_key(monkeypatch):
    headers = provider_header_template("qwen-oauth")

    assert headers["X-DashScope-AuthType"] == "qwen-oauth"
    assert "{platform}" in headers["User-Agent"]


def test_provider_header_template_prefers_registry_over_user_config(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.request_headers._read_user_header_template",
        lambda key: {"X-User": "should-not-win"},
    )

    assert provider_header_template("openrouter")["X-OpenRouter-Title"] == "Voidcube Agent"


def test_user_config_supplies_headers_for_a_custom_provider(monkeypatch):
    """Read the configured template directly, bypassing the mtime cache.

    Going through ``provider_header_template`` would make this test depend on
    whatever the shared module cache happens to hold, which is exactly the
    cross-test coupling that made it order-dependent.
    """
    from voidcube.infrastructure.providers import request_headers

    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config",
        lambda: {"providers": {"step5": {"headers": {"x-opencode-session": "{session_id}"}}}},
    )

    assert request_headers._read_user_header_template("step5") == {
        "x-opencode-session": SESSION_ID_PLACEHOLDER
    }


def test_resolver_applies_configured_headers_to_model_requests(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.request_headers._read_user_header_template",
        lambda key: {"X-Tenant": "tenant-a", "x-opencode-session": "{session_id}"},
    )
    headers = resolve_provider_headers(
        "step5", base_url="https://provider.example/v1", session_id="s-1"
    )
    assert headers == {"X-Tenant": "tenant-a", "x-opencode-session": "s-1"}


def test_unknown_endpoint_does_not_get_headers_from_url():
    assert builtin_headers_for_base_url("https://opencode.ai/zen/go/v1") == {}
    assert builtin_headers_for_base_url("https://api.x.ai/v1") == {}


def test_user_config_without_headers_yields_no_template(monkeypatch):
    from voidcube.infrastructure.providers import request_headers

    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config",
        lambda: {"providers": {"step5": {"base_url": "https://opencode.ai/zen/go/v1"}}},
    )

    assert request_headers._read_user_header_template("step5") == {}


def test_user_config_lookup_survives_a_broken_config(monkeypatch):
    from voidcube.infrastructure.providers import request_headers

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config", boom
    )

    assert request_headers._read_user_header_template("step5") == {}


def test_provider_header_template_returns_empty_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(
        "voidcube.infrastructure.providers.request_headers._read_user_header_template",
        lambda key: {},
    )

    assert provider_header_template("does-not-exist") == {}
    assert provider_header_template("") == {}


def test_user_template_cache_avoids_repeated_config_reads(monkeypatch):
    from voidcube.infrastructure.providers import request_headers

    calls = []

    def reader(key):
        calls.append(key)
        return {"X-Custom": "value"}

    monkeypatch.setattr(request_headers, "_read_user_header_template", reader)
    monkeypatch.setattr(
        "voidcube.infrastructure.config.runtime_paths.get_config_path",
        lambda: _FakePath(),
    )

    first = request_headers._cached_user_header_template("step5")
    second = request_headers._cached_user_header_template("step5")

    assert first == second == {"X-Custom": "value"}
    assert calls == ["step5"]


def test_user_template_cache_invalidates_when_config_changes(monkeypatch):
    from voidcube.infrastructure.providers import request_headers

    state = {"mtime": 1}

    monkeypatch.setattr(
        request_headers,
        "_read_user_header_template",
        lambda key: {"X-Custom": f"value-{state['mtime']}"},
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.config.runtime_paths.get_config_path",
        lambda: _FakePath(state["mtime"]),
    )

    assert request_headers._cached_user_header_template("step5") == {"X-Custom": "value-1"}

    state["mtime"] = 2
    assert request_headers._cached_user_header_template("step5") == {"X-Custom": "value-2"}


def test_user_template_cache_is_bypassed_while_config_is_unreadable(monkeypatch):
    """A config created later in the same process must still be picked up."""
    from voidcube.infrastructure.providers import request_headers

    state = {"readable": False, "value": "before"}

    def reader(key):
        return {"X-Custom": state["value"]} if state["readable"] else {}

    monkeypatch.setattr(request_headers, "_read_user_header_template", reader)
    monkeypatch.setattr(
        "voidcube.infrastructure.config.runtime_paths.get_config_path",
        lambda: (_ for _ in ()).throw(OSError("no config yet")),
    )

    assert request_headers._cached_user_header_template("step5") == {}

    state["readable"] = True
    state["value"] = "after"
    assert request_headers._cached_user_header_template("step5") == {"X-Custom": "after"}


class _FakePath:
    def __init__(self, mtime: int = 1):
        self._mtime = mtime

    def stat(self):
        return _FakeStat(self._mtime)


class _FakeStat:
    def __init__(self, mtime: int):
        self.st_mtime_ns = mtime
