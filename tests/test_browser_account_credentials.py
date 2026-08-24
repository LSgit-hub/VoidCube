from __future__ import annotations

import pytest

from voidcube.systems.supervisor.account_store import ParsedCookie
from voidcube.extensions.tools.browser import browser_tool


pytestmark = pytest.mark.unit


def test_local_browser_injects_saved_cookies_once(monkeypatch) -> None:
    cookie = ParsedCookie(
        name="SESSDATA",
        value="local-session",
        domain=".bilibili.com",
        http_only=True,
    )
    calls: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: True)
    monkeypatch.setattr(
        "voidcube.systems.supervisor.account_store.cookies_for_url",
        lambda _url: [cookie],
    )
    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda task_id, command, args, **_kwargs: (
            calls.append((task_id, command, args)) or {"success": True}
        ),
    )
    session_info: dict = {}

    browser_tool._inject_saved_account_cookies(
        "task-1", "https://www.bilibili.com/video/BV1", session_info
    )
    browser_tool._inject_saved_account_cookies(
        "task-1", "https://www.bilibili.com/video/BV2", session_info
    )

    assert len(calls) == 1
    assert calls[0][0:2] == ("task-1", "cookies")
    assert calls[0][2] == [
        "set",
        "SESSDATA",
        "local-session",
        "--domain",
        ".bilibili.com",
        "--path",
        "/",
        "--httpOnly",
        "--secure",
    ]


def test_cloud_browser_never_receives_saved_cookies(monkeypatch) -> None:
    monkeypatch.setattr(browser_tool, "_is_local_mode", lambda: False)
    monkeypatch.setattr(
        "voidcube.systems.supervisor.account_store.cookies_for_url",
        lambda _url: pytest.fail("cloud browser must not read local account cookies"),
    )

    browser_tool._inject_saved_account_cookies(
        "task-1", "https://www.bilibili.com/", {}
    )
