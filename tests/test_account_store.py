"""账号存储单元测试。"""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

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
    load_accounts,
    missing_required_auth_cookies,
    _decrypt_chrome_cookie,
    _load_aes_key_from_local_state,
)


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
    def test_masks_cookie_values(self) -> None:
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
        # SESSDATA has 14 chars, should mask: abcd****
        assert result["cookies"][0]["value"] == "abcd****"
        # bili_jct has 3 chars, should mask: ****
        assert result["cookies"][1]["value"] == "****"

    def test_hides_raw_cookies(self) -> None:
        account = PlatformAccount(
            platform="bilibili",
            cookies_raw="SESSDATA=secret123; bili_jct=secret456",
        )
        result = account_for_api(account)
        assert "cookies_raw" not in result


class TestPlatformName:
    def test_known_platforms(self) -> None:
        assert platform_name("bilibili") == "B站"
        assert platform_name("netease_music") == "网易云音乐"

    def test_unknown_platform(self) -> None:
        assert platform_name("unknown_xyz") == "unknown_xyz"


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

    def test_delete_removes_account(self, monkeypatch) -> None:
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
            # Delete a2
            remaining = delete_account("a2")
            assert len(remaining) == 1
            assert remaining[0].id == "a1"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


def test_chromium_v10_cookie_uses_aes_master_key() -> None:
    AESGCM = pytest.importorskip(
        "cryptography.hazmat.primitives.ciphers.aead"
    ).AESGCM

    key = AESGCM.generate_key(bit_length=256)
    nonce = b"0123456789ab"
    encrypted = b"v10" + nonce + AESGCM(key).encrypt(nonce, b"cookie-value", None)

    assert _decrypt_chrome_cookie(encrypted, key) == "cookie-value"


def test_local_state_keeps_dpapi_master_key_as_bytes(tmp_path, monkeypatch) -> None:
    key = bytes(range(256))
    state = tmp_path / "Local State"
    state.write_text(
        json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(b"DPAPI" + b"wrapped").decode()}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.supervisor.account_store._dpapi_decrypt",
        lambda value: key if value == b"wrapped" else None,
    )

    assert _load_aes_key_from_local_state(str(state)) == key
