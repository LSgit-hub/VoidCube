"""平台账号存储 — Cookie 解析、验证和持久化管理。

AI Agent 通过已登录平台的 cookie 获取高清播放和会员内容访问权限。
存储文件: ``~/.VoidCube/accounts.json``
"""

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

from VoidCube_core.utils import atomic_json_write

logger = logging.getLogger(__name__)

# ── 平台预设 ────────────────────────────────────────────

PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "bilibili": {
        "name": "B站",
        "domain": ".bilibili.com",
        "verify_url": "https://api.bilibili.com/x/web-interface/nav",
        "verify_method": "GET",
        "verify_ok_status": 0,  # B站 API code=0 表示正常
        "verify_field": "code",
        "required_auth_cookies": ["SESSDATA"],
    },
    "netease_music": {
        "name": "网易云音乐",
        "domain": ".music.163.com",
        "verify_url": "https://music.163.com/api/login/status",
        "verify_method": "GET",
        "verify_ok_status": 200,
        "verify_field": "code",
        "required_auth_cookies": ["MUSIC_U"],
    },
}

SUPPORTED_PLATFORMS = sorted(PLATFORM_PRESETS.keys())


def platform_name(platform: str) -> str:
    """返回平台的中文显示名。"""
    preset = PLATFORM_PRESETS.get(platform)
    return preset["name"] if preset else platform


def _accounts_path() -> Path:
    home = os.getenv("VOIDCUBE_HOME", "")
    if home:
        base = Path(home)
    else:
        base = Path.home() / ".VoidCube"
    return base / "accounts.json"


# ── 数据模型 ────────────────────────────────────────────


class ParsedCookie(BaseModel):
    """解析后的单条 cookie。"""
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = True
    http_only: bool = False


class PlatformAccount(BaseModel):
    """单个平台账号。"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str  # "bilibili" | "netease_music" | ...
    label: str = ""  # 用户自定义标签
    cookies_raw: str = ""  # 原始 cookie 字符串（API 响应中隐藏）
    parsed_cookies: List[ParsedCookie] = Field(default_factory=list)
    added_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_verified: Optional[str] = None
    status: str = "active"  # "active" | "expired" | "error"


class AccountStoreSnapshot(BaseModel):
    """accounts.json 文件的持久化结构。"""
    version: int = 1
    accounts: List[PlatformAccount] = Field(default_factory=list)


# ── Cookie 解析 ─────────────────────────────────────────


def parse_cookie_string(raw: str, platform: str) -> List[ParsedCookie]:
    """将浏览器复制的 cookie 字符串解析为 ParsedCookie 列表。

    ``raw`` 格式: ``"name1=value1; name2=value2; ..."``

    根据平台预设自动设置 domain / secure / http_only。
    """
    preset = PLATFORM_PRESETS.get(platform, {})
    domain = preset.get("domain", "")
    if not raw or not raw.strip():
        return []
    parsed: List[ParsedCookie] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
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
                http_only=(name in ("SESSDATA", "MUSIC_U")),
            )
        )
    return parsed


def missing_required_auth_cookies(
    parsed: List[ParsedCookie], platform: str
) -> List[str]:
    """Return login cookies required for the platform but absent from input."""
    required = PLATFORM_PRESETS.get(platform, {}).get("required_auth_cookies") or []
    present = {cookie.name for cookie in parsed if cookie.value}
    return [str(name) for name in required if str(name) not in present]


def sanitize_account_label(value: str) -> str:
    """Keep credentials out of the user-visible account label."""
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
    """Return active account cookies whose domain and path match ``url``."""
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
    """Build a Cookie request header for a matching saved local account."""
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies_for_url(url))


# ── 存储读写 ───────────────────────────────────────────

_storage_lock = threading.RLock()


def load_account_store() -> AccountStoreSnapshot:
    """从 accounts.json 加载全部账号数据。"""
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
    """原子写入 accounts.json。"""
    with _storage_lock:
        atomic_json_write(
            _accounts_path(),
            snapshot.model_dump(mode="json"),
            indent=2,
            default=str,
        )


def load_accounts() -> List[PlatformAccount]:
    """读取所有平台账号。"""
    return list(load_account_store().accounts)


def save_account(account: PlatformAccount) -> List[PlatformAccount]:
    """添加或更新一个账号。

    如果已存在相同平台和 domain 的账号则替换。
    返回更新后的全部账号列表。
    """
    with _storage_lock:
        store = load_account_store()
        accounts = list(store.accounts)
        # 查找是否已存在同平台账号
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
    """删除指定 ID 的账号。返回剩余账号列表。"""
    with _storage_lock:
        store = load_account_store()
        accounts = [a for a in store.accounts if a.id != account_id]
        store.accounts = accounts
        save_account_store(store)
        return accounts


# ── 验证 ────────────────────────────────────────────────


async def verify_account(account: PlatformAccount) -> Dict[str, Any]:
    """发送 HTTP 请求验证账号 cookie 是否仍然有效。

    返回 ``{"status": "active"|"expired"|"error", "detail": ...}``。
    """
    preset = PLATFORM_PRESETS.get(account.platform)
    if not preset:
        return {"status": "error", "detail": f"未知平台: {account.platform}"}

    verify_url = preset["verify_url"]
    method = preset.get("verify_method", "GET").upper()
    ok_status = preset.get("verify_ok_status", 0)
    field = preset.get("verify_field", "code")

    # 构造 cookie header
    cookie_str = "; ".join(
        f"{c.name}={c.value}" for c in account.parsed_cookies if c.name and c.value
    )
    if not cookie_str:
        return {"status": "error", "detail": "没有可用的 cookie"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "GET":
                resp = await client.get(verify_url, headers={"Cookie": cookie_str})
            else:
                resp = await client.post(verify_url, headers={"Cookie": cookie_str})

        if resp.status_code != 200:
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}

        data = resp.json()
        code = data.get(field)
        if code == ok_status:
            return {"status": "active", "detail": "cookie 有效"}
        else:
            return {"status": "expired", "detail": f"cookie 已过期 (code={code})"}
    except httpx.ConnectError:
        return {"status": "error", "detail": "无法连接验证服务器"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ── 脱敏 ────────────────────────────────────────────────


def account_for_api(account: PlatformAccount) -> Dict[str, Any]:
    """返回适合 UI 的账号摘要，不暴露 Cookie 名称或值。"""
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
