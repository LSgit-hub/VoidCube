"""Platform account storage, cookie parsing, verification, and persistence."""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ...infrastructure.persistence.file_store import atomic_json_write

logger = logging.getLogger(__name__)

PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "bilibili": {
        "name": "B站",
        "domain": ".bilibili.com",
        "verify_url": "https://api.bilibili.com/x/web-interface/nav",
        "referer": "https://www.bilibili.com/",
        "origin": "https://www.bilibili.com",
        "required_auth_cookies": ["SESSDATA"],
    },
    "netease_music": {
        "name": "网易云音乐",
        "domain": ".music.163.com",
        "verify_url": "https://music.163.com/api/nuser/account/get",
        "referer": "https://music.163.com/",
        "required_auth_cookies": ["MUSIC_U"],
    },
}

SUPPORTED_PLATFORMS = sorted(PLATFORM_PRESETS.keys())


def platform_name(platform: str) -> str:
    """Return the display name for a supported platform."""
    preset = PLATFORM_PRESETS.get(platform)
    return preset["name"] if preset else platform


def _accounts_path() -> Path:
    home = os.getenv("VOIDCUBE_HOME", "")
    base = Path(home) if home else Path.home() / ".VoidCube"
    return base / "accounts.json"


class ParsedCookie(BaseModel):
    """A parsed browser cookie."""

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = True
    http_only: bool = False


class PlatformAccount(BaseModel):
    """A persisted account for one platform."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str
    label: str = ""
    cookies_raw: str = ""
    parsed_cookies: List[ParsedCookie] = Field(default_factory=list)
    added_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_verified: Optional[str] = None
    status: str = "active"


class AccountStoreSnapshot(BaseModel):
    """The persisted ``accounts.json`` structure."""

    version: int = 1
    accounts: List[PlatformAccount] = Field(default_factory=list)


def parse_cookie_string(raw: str, platform: str) -> List[ParsedCookie]:
    """Parse a browser-copied cookie header into structured cookies."""
    preset = PLATFORM_PRESETS.get(platform, {})
    domain = preset.get("domain", "")
    if not raw or not raw.strip():
        return []
    parsed: List[ParsedCookie] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        parsed.append(
            ParsedCookie(
                name=name,
                value=value,
                domain=domain,
                path="/",
                secure=True,
                http_only=name in ("SESSDATA", "MUSIC_U"),
            )
        )
    return parsed


def missing_required_auth_cookies(
    parsed: List[ParsedCookie], platform: str
) -> List[str]:
    """Return required login cookies that are absent from the input."""
    required = PLATFORM_PRESETS.get(platform, {}).get("required_auth_cookies") or []
    present = {cookie.name for cookie in parsed if cookie.value}
    return [str(name) for name in required if str(name) not in present]


def sanitize_account_label(value: str) -> str:
    """Keep credentials out of a user-visible account label."""
    label = str(value or "").strip()
    known_cookie = re.search(
        r"(?:^|;\s*)(?:SESSDATA|bili_jct|DedeUserID|MUSIC_U)=",
        label,
        flags=re.IGNORECASE,
    )
    cookie_pairs = re.findall(r"(?:^|;\s*)[A-Za-z0-9_.-]+=[^;]+", label)
    if known_cookie or len(cookie_pairs) >= 2:
        return ""
    return label[:40]


def cookies_for_url(url: str) -> List[ParsedCookie]:
    """Return active account cookies matching a URL's domain and path."""
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").casefold().rstrip(".")
    request_path = parsed_url.path or "/"
    if parsed_url.scheme not in {"http", "https"} or not hostname:
        return []

    matched: List[ParsedCookie] = []
    for account in load_accounts():
        if account.status != "active":
            continue
        for cookie in account.parsed_cookies:
            domain = cookie.domain.casefold().lstrip(".").rstrip(".")
            domain_matches = hostname == domain or hostname.endswith(f".{domain}")
            path_matches = request_path.startswith(cookie.path or "/")
            if domain and domain_matches and path_matches and cookie.name and cookie.value:
                matched.append(cookie)
    return matched


def cookie_header_for_url(url: str) -> str:
    """Build a Cookie header for matching saved account cookies."""
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies_for_url(url))


_storage_lock = threading.RLock()


def load_account_store() -> AccountStoreSnapshot:
    """Load all persisted account data."""
    with _storage_lock:
        path = _accounts_path()
        if not path.exists():
            return AccountStoreSnapshot()
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return AccountStoreSnapshot()
            snapshot = AccountStoreSnapshot.model_validate_json(raw)
            labels_changed = False
            for account in snapshot.accounts:
                clean_label = sanitize_account_label(account.label)
                if clean_label != account.label:
                    account.label = clean_label
                    labels_changed = True
            if labels_changed:
                atomic_json_write(
                    path,
                    snapshot.model_dump(mode="json"),
                    indent=2,
                    default=str,
                )
            return snapshot
        except Exception:
            logger.warning("Failed to read accounts.json, returning empty store", exc_info=True)
            return AccountStoreSnapshot()


def save_account_store(snapshot: AccountStoreSnapshot) -> None:
    """Atomically write ``accounts.json``."""
    with _storage_lock:
        atomic_json_write(
            _accounts_path(),
            snapshot.model_dump(mode="json"),
            indent=2,
            default=str,
        )


def load_accounts() -> List[PlatformAccount]:
    """Load all platform accounts."""
    return list(load_account_store().accounts)


def save_account(account: PlatformAccount) -> List[PlatformAccount]:
    """Add or replace an account and return the complete account list."""
    with _storage_lock:
        store = load_account_store()
        accounts = list(store.accounts)
        existing_index: Optional[int] = None
        for i, existing in enumerate(accounts):
            if existing.platform == account.platform:
                existing_index = i
                break
        if existing_index is not None:
            accounts[existing_index] = account
        else:
            accounts.append(account)
        store.accounts = accounts
        save_account_store(store)
        return accounts


def delete_account(account_id: str) -> List[PlatformAccount]:
    """Delete an account by ID and return the remaining accounts."""
    with _storage_lock:
        store = load_account_store()
        accounts = [a for a in store.accounts if a.id != account_id]
        store.accounts = accounts
        save_account_store(store)
        return accounts


async def verify_account(account: PlatformAccount) -> Dict[str, Any]:
    """Verify account cookies against the platform endpoint."""
    preset = PLATFORM_PRESETS.get(account.platform)
    if not preset:
        return {"status": "error", "detail": f"未知平台: {account.platform}"}

    verify_url = preset["verify_url"]
    cookie_str = "; ".join(
        f"{c.name}={c.value}" for c in account.parsed_cookies if c.name and c.value
    )
    if not cookie_str:
        return {"status": "error", "detail": "没有可用的 cookie"}

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie_str,
        "Referer": preset["referer"],
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
    }
    if preset.get("origin"):
        headers["Origin"] = preset["origin"]

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(verify_url, headers=headers)

        if resp.status_code != 200:
            return {
                "status": "error",
                "detail": (
                    f"平台暂时拒绝验证请求 (HTTP {resp.status_code})，"
                    "无法确认 Cookie 是否失效"
                ),
            }

        try:
            data = resp.json()
        except ValueError:
            return {"status": "error", "detail": "平台返回了无法识别的验证结果"}
        if not isinstance(data, dict):
            return {"status": "error", "detail": "平台返回了无法识别的验证结果"}

        code = data.get("code")
        if account.platform == "bilibili":
            bili_data = data.get("data")
            logged_in = isinstance(bili_data, dict) and bili_data.get("isLogin") is True
            logged_out = isinstance(bili_data, dict) and bili_data.get("isLogin") is False
            if code == 0 and logged_in:
                return {"status": "active", "detail": "Cookie 有效"}
            if code == -101 or (code == 0 and logged_out):
                return {"status": "expired", "detail": "B站返回未登录，Cookie 已失效"}
        elif account.platform == "netease_music":
            if code == 200 and (data.get("account") or data.get("profile")):
                return {"status": "active", "detail": "Cookie 有效"}
            if code in {200, 301} and not data.get("account") and not data.get("profile"):
                return {"status": "expired", "detail": "网易云音乐返回未登录，Cookie 已失效"}

        return {
            "status": "error",
            "detail": f"平台返回未知验证结果 (code={code})，无法确认 Cookie 是否失效",
        }
    except httpx.TimeoutException:
        return {"status": "error", "detail": "验证请求超时，无法确认 Cookie 是否失效"}
    except httpx.RequestError:
        return {"status": "error", "detail": "无法连接验证服务器，未改变当前登录状态"}


def account_for_api(account: PlatformAccount) -> Dict[str, Any]:
    """Return a UI-safe account summary without cookie names or values."""
    return {
        "id": account.id,
        "platform": account.platform,
        "platform_name": platform_name(account.platform),
        "label": sanitize_account_label(account.label),
        "cookies_count": len(account.parsed_cookies),
        "added_at": account.added_at,
        "last_verified": account.last_verified,
        "status": account.status,
    }
