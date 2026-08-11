"""账号存储单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from systems.supervisor.account_store import (
    AccountStoreSnapshot,
    ParsedCookie,
    PlatformAccount,
    account_for_api,
    parse_cookie_string,
    platform_name,
    save_account,
    delete_account,
    cookie_header_for_url,
    cookies_for_url,
    load_account_store,
    load_accounts,
    missing_required_auth_cookies,
    sanitize_account_label,
    verify_account,
)
from systems.supervisor.ui_runtime import SupervisorUIRuntime


class TestParseCookieString:
    def test_parses_bilibili_cookies(self) -> None:
        raw = "SESSDATA=abc123; bili_jct=xyz789; DedeUserID=12345"
        parsed = parse_cookie_string(raw, "bilibili")
        assert len(parsed) == 3
        names = {c.name for c in parsed}
        assert names == {"SESSDATA", "bili_jct", "DedeUserID"}
        for c in parsed:
            assert c.domain == ".bilibili.com"
            assert c.path == "/"
            assert c.secure is True

    def test_sessdata_is_http_only(self) -> None:
        raw = "SESSDATA=abc123; bili_jct=xyz789"
        parsed = parse_cookie_string(raw, "bilibili")
        sessdata = next(c for c in parsed if c.name == "SESSDATA")
        assert sessdata.http_only is True
        jwt = next(c for c in parsed if c.name == "bili_jct")
        assert jwt.http_only is False

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_cookie_string("", "bilibili") == []
        assert parse_cookie_string("   ", "bilibili") == []

    def test_invalid_parts_skipped(self) -> None:
        raw = "valid=123; ; novalue; =empty; also_valid=456"
        parsed = parse_cookie_string(raw, "bilibili")
        # Only "valid=123" and "also_valid=456" should parse
        names = {c.name for c in parsed}
        assert names == {"valid", "also_valid"}

    def test_reports_missing_platform_login_cookie(self) -> None:
        parsed = parse_cookie_string("bili_jct=csrf-token; DedeUserID=12345", "bilibili")

        assert missing_required_auth_cookies(parsed, "bilibili") == ["SESSDATA"]

    def test_accepts_platform_login_cookie(self) -> None:
        parsed = parse_cookie_string("SESSDATA=session-token; bili_jct=csrf-token", "bilibili")

        assert missing_required_auth_cookies(parsed, "bilibili") == []


class TestAccountStoreSnapshot:
    def test_default_snapshot_is_empty(self) -> None:
        snapshot = AccountStoreSnapshot()
        assert snapshot.version == 1
        assert snapshot.accounts == []

    def test_roundtrip_json(self) -> None:
        account = PlatformAccount(
            id="test001",
            platform="bilibili",
            label="测试号",
            cookies_raw="SESSDATA=abc",
            parsed_cookies=[ParsedCookie(name="SESSDATA", value="abc", domain=".bilibili.com")],
        )
        snapshot = AccountStoreSnapshot(accounts=[account])
        raw = snapshot.model_dump_json()
        restored = AccountStoreSnapshot.model_validate_json(raw)
        assert len(restored.accounts) == 1
        assert restored.accounts[0].id == "test001"
        assert restored.accounts[0].platform == "bilibili"


class TestAccountForApi:
    def test_returns_cookie_count_without_cookie_details(self) -> None:
        account = PlatformAccount(
            id="test001",
            platform="bilibili",
            label="主号",
            parsed_cookies=[
                ParsedCookie(name="SESSDATA", value="abcdefgh-1234", domain=".bilibili.com"),
                ParsedCookie(name="bili_jct", value="xyz", domain=".bilibili.com"),
            ],
        )
        result = account_for_api(account)
        assert result["id"] == "test001"
        assert result["platform"] == "bilibili"
        assert result["platform_name"] == "B站"
        assert result["label"] == "主号"
        assert result["cookies_count"] == 2
        assert "cookies" not in result

    def test_hides_raw_cookies(self) -> None:
        account = PlatformAccount(
            platform="bilibili",
            cookies_raw="SESSDATA=secret123; bili_jct=secret456",
        )
        result = account_for_api(account)
        assert "cookies_raw" not in result

    def test_hides_cookie_string_accidentally_saved_as_label(self) -> None:
        account = PlatformAccount(
            platform="bilibili",
            label="SESSDATA=secret; bili_jct=csrf-token",
        )

        assert account_for_api(account)["label"] == ""


class TestPlatformName:
    def test_known_platforms(self) -> None:
        assert platform_name("bilibili") == "B站"
        assert platform_name("netease_music") == "网易云音乐"

    def test_unknown_platform(self) -> None:
        assert platform_name("unknown_xyz") == "unknown_xyz"


def test_sanitize_account_label_rejects_credentials() -> None:
    assert sanitize_account_label("我的主号") == "我的主号"
    assert sanitize_account_label("SESSDATA=secret") == ""
    assert sanitize_account_label("first=value; second=value") == ""


def test_loading_store_permanently_cleans_credential_label(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path))
    path = tmp_path / "accounts.json"
    path.write_text(
        AccountStoreSnapshot(
            accounts=[
                PlatformAccount(
                    platform="bilibili",
                    label="SESSDATA=secret; bili_jct=csrf-token",
                )
            ]
        ).model_dump_json(),
        encoding="utf-8",
    )

    assert load_account_store().accounts[0].label == ""
    assert "SESSDATA=secret" not in path.read_text(encoding="utf-8")


def test_cookie_lookup_matches_only_platform_domain(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path))
    save_account(
        PlatformAccount(
            platform="bilibili",
            parsed_cookies=[
                ParsedCookie(name="SESSDATA", value="session-token", domain=".bilibili.com"),
                ParsedCookie(name="bili_jct", value="csrf-token", domain=".bilibili.com"),
            ],
        )
    )

    assert {cookie.name for cookie in cookies_for_url("https://api.bilibili.com/x/web-interface/nav")} == {
        "SESSDATA",
        "bili_jct",
    }
    assert cookie_header_for_url("https://www.bilibili.com/video/BV1") == (
        "SESSDATA=session-token; bili_jct=csrf-token"
    )
    assert cookie_header_for_url("https://evilbilibili.com/") == ""
    assert cookie_header_for_url("https://example.com/?next=bilibili.com") == ""


class TestSaveAndDelete:
    def test_save_creates_account(self, monkeypatch) -> None:
        import tempfile
        import os
        tmp = tempfile.mkdtemp()
        try:
            accounts_file = os.path.join(tmp, "accounts.json")
            monkeypatch.setattr(
                "systems.supervisor.account_store._accounts_path",
                lambda: __import__("pathlib").Path(accounts_file),
            )
            account = PlatformAccount(
                id="test001",
                platform="bilibili",
                label="测试",
                parsed_cookies=[
                    ParsedCookie(name="SESSDATA", value="abc", domain=".bilibili.com"),
                ],
            )
            result = save_account(account)
            assert len(result) == 1
            assert result[0].id == "test001"
            # Verify file was actually written
            assert os.path.exists(accounts_file)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


def _account(platform: str) -> PlatformAccount:
    cookie_name = "SESSDATA" if platform == "bilibili" else "MUSIC_U"
    domain = ".bilibili.com" if platform == "bilibili" else ".music.163.com"
    return PlatformAccount(
        id=f"{platform}-account",
        platform=platform,
        parsed_cookies=[ParsedCookie(name=cookie_name, value="session-token", domain=domain)],
    )


def _mock_verification_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def create_client(**kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr("systems.supervisor.account_store.httpx.AsyncClient", create_client)


@pytest.mark.asyncio
async def test_bilibili_verification_uses_browser_headers_and_accepts_logged_in(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.bilibili.com/x/web-interface/nav"
        assert request.headers["referer"] == "https://www.bilibili.com/"
        assert request.headers["origin"] == "https://www.bilibili.com"
        assert request.headers["user-agent"].startswith("Mozilla/5.0")
        assert request.headers["cookie"] == "SESSDATA=session-token"
        return httpx.Response(200, json={"code": 0, "data": {"isLogin": True}})

    _mock_verification_client(monkeypatch, handler)

    assert await verify_account(_account("bilibili")) == {
        "status": "active",
        "detail": "Cookie 有效",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (httpx.Response(200, json={"code": -101}), "expired"),
        (httpx.Response(412), "error"),
    ],
)
async def test_bilibili_verification_distinguishes_logout_from_antibot(
    monkeypatch, response: httpx.Response, expected_status: str
) -> None:
    _mock_verification_client(monkeypatch, lambda _request: response)

    result = await verify_account(_account("bilibili"))

    assert result["status"] == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"code": 200, "account": {"id": 123}, "profile": {"nickname": "user"}}, "active"),
        ({"code": 200, "account": None, "profile": None}, "expired"),
        ({"code": 404}, "error"),
    ],
)
async def test_netease_verification_uses_account_endpoint_and_requires_login_proof(
    monkeypatch, payload: dict, expected_status: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://music.163.com/api/nuser/account/get"
        assert request.headers["referer"] == "https://music.163.com/"
        return httpx.Response(200, json=payload)

    _mock_verification_client(monkeypatch, handler)

    result = await verify_account(_account("netease_music"))

    assert result["status"] == expected_status


@pytest.mark.asyncio
async def test_inconclusive_verification_preserves_working_account_status(monkeypatch) -> None:
    account = _account("bilibili")
    saved_accounts = []
    monkeypatch.setattr("systems.supervisor.account_store.load_accounts", lambda: [account])
    monkeypatch.setattr(
        "systems.supervisor.account_store.verify_account",
        lambda _account: _async_result(
            {"status": "error", "detail": "平台暂时拒绝验证请求 (HTTP 412)"}
        ),
    )
    monkeypatch.setattr(
        "systems.supervisor.account_store.save_account", saved_accounts.append
    )
    runtime = object.__new__(SupervisorUIRuntime)

    result = await runtime.verify_account_endpoint(
        SimpleNamespace(path_params={"account_id": account.id})
    )

    assert result["verify_result"]["status"] == "error"
    assert result["account"]["status"] == "active"
    assert account.last_verified is None
    assert saved_accounts == []


async def _async_result(value):
    return value

def test_delete_removes_account(monkeypatch) -> None:
    import tempfile
    import os
    tmp = tempfile.mkdtemp()
    try:
        accounts_file = os.path.join(tmp, "accounts.json")
        monkeypatch.setattr(
            "systems.supervisor.account_store._accounts_path",
            lambda: __import__("pathlib").Path(accounts_file),
        )
        a1 = PlatformAccount(
            id="a1",
            platform="bilibili",
            parsed_cookies=[ParsedCookie(name="SESSDATA", value="abc", domain=".bilibili.com")],
        )
        a2 = PlatformAccount(
            id="a2",
            platform="netease_music",
            parsed_cookies=[ParsedCookie(name="MUSIC_U", value="xyz", domain=".music.163.com")],
        )
        save_account(a1)
        save_account(a2)
        remaining = delete_account("a2")
        assert len(remaining) == 1
        assert remaining[0].id == "a1"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
