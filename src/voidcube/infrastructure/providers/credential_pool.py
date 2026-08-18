"""Persistent multi-credential pool for same-provider failover."""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
import os
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .endpoints import OPENROUTER_BASE_URL
from . import auth as auth_mod
from .registry import PROVIDER_REGISTRY
from .auth import (
    DEFAULT_AGENT_KEY_MIN_TTL_SECONDS,
    KIMI_CODE_BASE_URL,
    _auth_store_lock,
    _decode_jwt_claims,
    _load_auth_store,
    _load_provider_state,
    _resolve_kimi_base_url,
    _resolve_zai_base_url,
    _save_auth_store,
    _save_provider_state,
    read_credential_pool,
    write_credential_pool,
)

logger = logging.getLogger(__name__)


def _load_config_safe() -> Optional[dict]:
    """Load config.yaml, returning None on any error."""
    try:
        from ..config.configuration import load_config

        return load_config()
    except (OSError, IOError, ImportError):
        # Config file may not exist or be invalid
        return None


# --- Status and type constants ---

STATUS_OK = "ok"
STATUS_EXHAUSTED = "exhausted"

AUTH_TYPE_OAUTH = "oauth"
AUTH_TYPE_API_KEY = "api_key"

SOURCE_MANUAL = "manual"

STRATEGY_FILL_FIRST = "fill_first"
STRATEGY_ROUND_ROBIN = "round_robin"
STRATEGY_RANDOM = "random"
STRATEGY_LEAST_USED = "least_used"
SUPPORTED_POOL_STRATEGIES = {
    STRATEGY_FILL_FIRST,
    STRATEGY_ROUND_ROBIN,
    STRATEGY_RANDOM,
    STRATEGY_LEAST_USED,
}

# Cooldown before retrying an exhausted credential.
# 429 (rate-limited) and 402 (billing/quota) both cool down after 1 hour.
# Provider-supplied reset_at timestamps override these defaults.
EXHAUSTED_TTL_429_SECONDS = 60 * 60          # 1 hour
EXHAUSTED_TTL_DEFAULT_SECONDS = 60 * 60      # 1 hour

# Pool key prefix for custom OpenAI-compatible endpoints.
# Custom endpoints all share provider='custom' but are keyed by their
# providers.<name> config entry: 'custom:<normalized_name>'.
CUSTOM_POOL_PREFIX = "custom:"


# Fields that are only round-tripped through JSON — never used for logic as attributes.
_EXTRA_KEYS = frozenset({
    "token_type", "scope", "client_id", "portal_base_url", "obtained_at",
    "expires_in", "agent_key_id", "agent_key_expires_in", "agent_key_reused",
    "agent_key_obtained_at", "tls",
})


@dataclass
class PooledCredential:
    provider: str
    id: str
    label: str
    auth_type: str
    priority: int
    source: str
    access_token: str
    refresh_token: Optional[str] = None
    last_status: Optional[str] = None
    last_status_at: Optional[float] = None
    last_error_code: Optional[int] = None
    last_error_reason: Optional[str] = None
    last_error_message: Optional[str] = None
    last_error_reset_at: Optional[float] = None
    base_url: Optional[str] = None
    expires_at: Optional[str] = None
    expires_at_ms: Optional[int] = None
    last_refresh: Optional[str] = None
    inference_base_url: Optional[str] = None
    agent_key: Optional[str] = None
    agent_key_expires_at: Optional[str] = None
    request_count: int = 0
    extra: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}

    def __getattr__(self, name: str):
        if name in _EXTRA_KEYS:
            return self.extra.get(name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute {name!r}")

    @classmethod
    def from_dict(cls, provider: str, payload: Dict[str, Any]) -> "PooledCredential":
        field_names = {f.name for f in fields(cls) if f.name != "provider"}
        data = {k: payload.get(k) for k in field_names if k in payload}
        extra = {k: payload[k] for k in _EXTRA_KEYS if k in payload and payload[k] is not None}
        data["extra"] = extra
        data.setdefault("id", uuid.uuid4().hex[:6])
        data.setdefault("label", payload.get("source", provider))
        data.setdefault("auth_type", AUTH_TYPE_API_KEY)
        data.setdefault("priority", 0)
        data.setdefault("source", SOURCE_MANUAL)
        data.setdefault("access_token", "")
        return cls(provider=provider, **data)

    def to_dict(self) -> Dict[str, Any]:
        _ALWAYS_EMIT = {
            "last_status",
            "last_status_at",
            "last_error_code",
            "last_error_reason",
            "last_error_message",
            "last_error_reset_at",
        }
        result: Dict[str, Any] = {}
        for field_def in fields(self):
            if field_def.name in ("provider", "extra"):
                continue
            value = getattr(self, field_def.name)
            if value is not None or field_def.name in _ALWAYS_EMIT:
                result[field_def.name] = value
        for k, v in self.extra.items():
            if v is not None:
                result[k] = v
        return result

    @property
    def runtime_api_key(self) -> str:
        if self.provider == "nous":
            return str(self.agent_key or self.access_token or "")
        return str(self.access_token or "")

    @property
    def runtime_base_url(self) -> Optional[str]:
        if self.provider == "nous":
            return self.inference_base_url or self.base_url
        return self.base_url


def label_from_token(token: str, fallback: str) -> str:
    claims = _decode_jwt_claims(token)
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _next_priority(entries: List[PooledCredential]) -> int:
    return max((entry.priority for entry in entries), default=-1) + 1


def _is_manual_source(source: str) -> bool:
    normalized = (source or "").strip().lower()
    return normalized == SOURCE_MANUAL or normalized.startswith(f"{SOURCE_MANUAL}:")


def _exhausted_ttl(error_code: Optional[int]) -> int:
    """Return cooldown seconds based on the HTTP status that caused exhaustion."""
    if error_code == 429:
        return EXHAUSTED_TTL_429_SECONDS
    return EXHAUSTED_TTL_DEFAULT_SECONDS


def _parse_absolute_timestamp(value: Any) -> Optional[float]:
    """Best-effort parse for provider reset timestamps.

    Accepts epoch seconds, epoch milliseconds, and ISO-8601 strings.
    Returns seconds since epoch.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            numeric = None
        if numeric is not None:
            return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _extract_retry_delay_seconds(message: str) -> Optional[float]:
    if not message:
        return None
    delay_match = re.search(r"quotaResetDelay[:\s\"]+(\d+(?:\.\d+)?)(ms|s)", message, re.IGNORECASE)
    if delay_match:
        value = float(delay_match.group(1))
        return value / 1000.0 if delay_match.group(2).lower() == "ms" else value
    sec_match = re.search(r"retry\s+(?:after\s+)?(\d+(?:\.\d+)?)\s*(?:sec|secs|seconds|s\b)", message, re.IGNORECASE)
    if sec_match:
        return float(sec_match.group(1))
    return None


def _normalize_error_context(error_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(error_context, dict):
        return {}
    normalized: Dict[str, Any] = {}
    reason = error_context.get("reason")
    if isinstance(reason, str) and reason.strip():
        normalized["reason"] = reason.strip()
    message = error_context.get("message")
    if isinstance(message, str) and message.strip():
        normalized["message"] = message.strip()
    reset_at = (
        error_context.get("reset_at")
        or error_context.get("resets_at")
        or error_context.get("retry_until")
    )
    parsed_reset_at = _parse_absolute_timestamp(reset_at)
    if parsed_reset_at is None and isinstance(message, str):
        retry_delay_seconds = _extract_retry_delay_seconds(message)
        if retry_delay_seconds is not None:
            parsed_reset_at = time.time() + retry_delay_seconds
    if parsed_reset_at is not None:
        normalized["reset_at"] = parsed_reset_at
    return normalized


def _exhausted_until(entry: PooledCredential) -> Optional[float]:
    if entry.last_status != STATUS_EXHAUSTED:
        return None
    reset_at = _parse_absolute_timestamp(getattr(entry, "last_error_reset_at", None))
    if reset_at is not None:
        return reset_at
    if entry.last_status_at:
        return entry.last_status_at + _exhausted_ttl(entry.last_error_code)
    return None


def _normalize_custom_pool_name(name: str) -> str:
    """Normalize a custom provider name for use as a pool key suffix."""
    return name.strip().lower().replace(" ", "-")


def _iter_custom_provider_entries(config: Optional[dict] = None):
    """Yield (normalized_name, entry_dict) for each valid custom provider entry."""
    if config is None:
        config = _load_config_safe()
    if config is None:
        return
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return
    for provider_key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        provider_key = str(provider_key or "").strip()
        if not provider_key or provider_key in PROVIDER_REGISTRY:
            continue
        base_url = str(entry.get("base_url") or entry.get("api") or entry.get("url") or "").strip()
        if not base_url:
            continue
        provider_type = str(entry.get("type") or "").strip().lower()
        if provider_type and provider_type not in {"openai_compatible", "custom"}:
            continue
        name = str(entry.get("label") or entry.get("name") or provider_key).strip()
        normalized_entry = dict(entry)
        normalized_entry["name"] = name or provider_key
        normalized_entry["base_url"] = base_url
        yield _normalize_custom_pool_name(provider_key), normalized_entry


def get_custom_provider_pool_key(base_url: str) -> Optional[str]:
    """Look up providers in config.yaml and return 'custom:<name>' for a matching custom base_url.

    Returns None if no match is found.
    """
    if not base_url:
        return None
    normalized_url = base_url.strip().rstrip("/")
    for norm_name, entry in _iter_custom_provider_entries():
        entry_url = str(entry.get("base_url") or "").strip().rstrip("/")
        if entry_url and entry_url == normalized_url:
            return f"{CUSTOM_POOL_PREFIX}{norm_name}"
    return None


def list_custom_pool_providers() -> List[str]:
    """Return all 'custom:*' pool keys that have entries in auth.json."""
    pool_data = read_credential_pool(None)
    return sorted(
        key for key in pool_data
        if key.startswith(CUSTOM_POOL_PREFIX)
        and isinstance(pool_data.get(key), list)
        and pool_data[key]
    )


def _get_custom_provider_config(pool_key: str) -> Optional[Dict[str, Any]]:
    """Return the custom provider config entry matching a pool key like 'custom:together-ai'."""
    if not pool_key.startswith(CUSTOM_POOL_PREFIX):
        return None
    suffix = pool_key[len(CUSTOM_POOL_PREFIX):]
    for norm_name, entry in _iter_custom_provider_entries():
        if norm_name == suffix:
            return entry
    return None


def get_pool_strategy(provider: str) -> str:
    """Return the configured selection strategy for a provider."""
    config = _load_config_safe()
    if config is None:
        return STRATEGY_FILL_FIRST

    strategies = config.get("credential_pool_strategies")
    if not isinstance(strategies, dict):
        return STRATEGY_FILL_FIRST

    strategy = str(strategies.get(provider, "") or "").strip().lower()
    if strategy in SUPPORTED_POOL_STRATEGIES:
        return strategy
    return STRATEGY_FILL_FIRST


DEFAULT_MAX_CONCURRENT_PER_CREDENTIAL = 1


class CredentialPool:
    def __init__(self, provider: str, entries: List[PooledCredential]):
        self.provider = provider
        self._entries = sorted(entries, key=lambda entry: entry.priority)
        self._current_id: Optional[str] = None
        self._strategy = get_pool_strategy(provider)
        self._lock = threading.Lock()
        self._active_leases: Dict[str, int] = {}
        self._max_concurrent = DEFAULT_MAX_CONCURRENT_PER_CREDENTIAL

    def has_credentials(self) -> bool:
        return bool(self._entries)

    def has_available(self) -> bool:
        """True if at least one entry is not currently in exhaustion cooldown."""
        return bool(self._available_entries())

    def entries(self) -> List[PooledCredential]:
        return list(self._entries)

    def current(self) -> Optional[PooledCredential]:
        if not self._current_id:
            return None
        return next((entry for entry in self._entries if entry.id == self._current_id), None)

    def _replace_entry(self, old: PooledCredential, new: PooledCredential) -> None:
        """Swap an entry in-place by id, preserving sort order."""
        for idx, entry in enumerate(self._entries):
            if entry.id == old.id:
                self._entries[idx] = new
                return

    def _persist(self) -> None:
        write_credential_pool(
            self.provider,
            [entry.to_dict() for entry in self._entries],
        )

    def _mark_exhausted(
        self,
        entry: PooledCredential,
        status_code: Optional[int],
        error_context: Optional[Dict[str, Any]] = None,
    ) -> PooledCredential:
        normalized_error = _normalize_error_context(error_context)
        updated = replace(
            entry,
            last_status=STATUS_EXHAUSTED,
            last_status_at=time.time(),
            last_error_code=status_code,
            last_error_reason=normalized_error.get("reason"),
            last_error_message=normalized_error.get("message"),
            last_error_reset_at=normalized_error.get("reset_at"),
        )
        self._replace_entry(entry, updated)
        self._persist()
        return updated

    def _sync_device_code_entry_to_auth_store(self, entry: PooledCredential) -> None:
        """Write refreshed pool entry tokens back to auth.json providers.

        After a pool-level refresh, the pool entry has fresh tokens but
        auth.json's ``providers.<id>`` still holds the pre-refresh state.
        On the next ``load_pool()``, ``_seed_from_singletons()`` reads that
        stale state and can overwrite the fresh pool entry — potentially
        re-seeding a consumed single-use refresh token.

        Applies to the Nous OAuth singleton stored in auth.json.
        """
        if entry.source != "device_code":
            return
        try:
            with _auth_store_lock:
                auth_store = _load_auth_store()
                if self.provider == "nous":
                    state = _load_provider_state(auth_store, "nous")
                    if state is None:
                        return
                    state["access_token"] = entry.access_token
                    if entry.refresh_token:
                        state["refresh_token"] = entry.refresh_token
                    if entry.expires_at:
                        state["expires_at"] = entry.expires_at
                    if entry.agent_key:
                        state["agent_key"] = entry.agent_key
                    if entry.agent_key_expires_at:
                        state["agent_key_expires_at"] = entry.agent_key_expires_at
                    for extra_key in ("obtained_at", "expires_in", "agent_key_id",
                                      "agent_key_expires_in", "agent_key_reused",
                                      "agent_key_obtained_at"):
                        val = entry.extra.get(extra_key)
                        if val is not None:
                            state[extra_key] = val
                    if entry.inference_base_url:
                        state["inference_base_url"] = entry.inference_base_url
                    _save_provider_state(auth_store, "nous", state)

                else:
                    return

                _save_auth_store(auth_store)
        except Exception as exc:
            logger.debug("Failed to sync %s pool entry back to auth store: %s", self.provider, exc)

    def _refresh_entry(self, entry: PooledCredential, *, force: bool) -> Optional[PooledCredential]:
        if entry.auth_type != AUTH_TYPE_OAUTH or not entry.refresh_token:
            if force:
                self._mark_exhausted(entry, None)
            return None

        try:
            if self.provider == "nous":
                nous_state = {
                    "access_token": entry.access_token,
                    "refresh_token": entry.refresh_token,
                    "client_id": entry.client_id,
                    "portal_base_url": entry.portal_base_url,
                    "inference_base_url": entry.inference_base_url,
                    "token_type": entry.token_type,
                    "scope": entry.scope,
                    "obtained_at": entry.obtained_at,
                    "expires_at": entry.expires_at,
                    "agent_key": entry.agent_key,
                    "agent_key_expires_at": entry.agent_key_expires_at,
                    "tls": entry.tls,
                }
                refreshed = auth_mod.refresh_nous_oauth_from_state(
                    nous_state,
                    min_key_ttl_seconds=DEFAULT_AGENT_KEY_MIN_TTL_SECONDS,
                    force_refresh=force,
                    force_mint=force,
                )
                # Apply returned fields: dataclass fields via replace, extras via dict update
                field_updates = {}
                extra_updates = dict(entry.extra)
                _field_names = {f.name for f in fields(entry)}
                for k, v in refreshed.items():
                    if k in _field_names:
                        field_updates[k] = v
                    elif k in _EXTRA_KEYS:
                        extra_updates[k] = v
                updated = replace(entry, extra=extra_updates, **field_updates)
            else:
                return entry
        except Exception as exc:
            logger.debug("Credential refresh failed for %s/%s: %s", self.provider, entry.id, exc)
            self._mark_exhausted(entry, None)
            return None

        updated = replace(
            updated,
            last_status=STATUS_OK,
            last_status_at=None,
            last_error_code=None,
            last_error_reason=None,
            last_error_message=None,
            last_error_reset_at=None,
        )
        self._replace_entry(entry, updated)
        self._persist()
        # Sync refreshed tokens back to auth.json providers so that
        # _seed_from_singletons() on the next load_pool() sees fresh state
        # instead of re-seeding stale/consumed tokens.
        self._sync_device_code_entry_to_auth_store(updated)
        return updated

    def _entry_needs_refresh(self, entry: PooledCredential) -> bool:
        if entry.auth_type != AUTH_TYPE_OAUTH:
            return False
        if self.provider == "nous":
            # Nous refresh/mint can require network access and should happen when
            # runtime credentials are actually resolved, not merely when the pool
            # is enumerated for listing, migration, or selection.
            return False
        return False

    def select(self) -> Optional[PooledCredential]:
        with self._lock:
            return self._select_unlocked()

    def _available_entries(self, *, clear_expired: bool = False, refresh: bool = False) -> List[PooledCredential]:
        """Return entries not currently in exhaustion cooldown.

        When *clear_expired* is True, entries whose cooldown has elapsed are
        reset to STATUS_OK and persisted.  When *refresh* is True, entries
        that need a token refresh are refreshed (skipped on failure).
        """
        now = time.time()
        cleared_any = False
        available: List[PooledCredential] = []
        for entry in self._entries:
            if entry.last_status == STATUS_EXHAUSTED:
                exhausted_until = _exhausted_until(entry)
                if exhausted_until is not None and now < exhausted_until:
                    continue
                if clear_expired:
                    cleared = replace(
                        entry,
                        last_status=STATUS_OK,
                        last_status_at=None,
                        last_error_code=None,
                        last_error_reason=None,
                        last_error_message=None,
                        last_error_reset_at=None,
                    )
                    self._replace_entry(entry, cleared)
                    entry = cleared
                    cleared_any = True
            if refresh and self._entry_needs_refresh(entry):
                refreshed = self._refresh_entry(entry, force=False)
                if refreshed is None:
                    continue
                entry = refreshed
            available.append(entry)
        if cleared_any:
            self._persist()
        return available

    def _select_unlocked(self) -> Optional[PooledCredential]:
        available = self._available_entries(clear_expired=True, refresh=True)
        if not available:
            self._current_id = None
            logger.info("credential pool: no available entries (all exhausted or empty)")
            return None

        if self._strategy == STRATEGY_RANDOM:
            entry = random.choice(available)
            self._current_id = entry.id
            return entry

        if self._strategy == STRATEGY_LEAST_USED and len(available) > 1:
            entry = min(available, key=lambda e: e.request_count)
            self._current_id = entry.id
            return entry

        if self._strategy == STRATEGY_ROUND_ROBIN and len(available) > 1:
            entry = available[0]
            rotated = [candidate for candidate in self._entries if candidate.id != entry.id]
            rotated.append(replace(entry, priority=len(self._entries) - 1))
            self._entries = [replace(candidate, priority=idx) for idx, candidate in enumerate(rotated)]
            self._persist()
            self._current_id = entry.id
            return self.current() or entry

        entry = available[0]
        self._current_id = entry.id
        return entry

    def peek(self) -> Optional[PooledCredential]:
        current = self.current()
        if current is not None:
            return current
        available = self._available_entries()
        return available[0] if available else None

    def mark_exhausted_and_rotate(
        self,
        *,
        status_code: Optional[int],
        error_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[PooledCredential]:
        with self._lock:
            entry = self.current() or self._select_unlocked()
            if entry is None:
                return None
            _label = entry.label or entry.id[:8]
            logger.info(
                "credential pool: marking %s exhausted (status=%s), rotating",
                _label, status_code,
            )
            self._mark_exhausted(entry, status_code, error_context)
            self._current_id = None
            next_entry = self._select_unlocked()
            if next_entry:
                _next_label = next_entry.label or next_entry.id[:8]
                logger.info("credential pool: rotated to %s", _next_label)
            return next_entry

    def acquire_lease(self, credential_id: Optional[str] = None) -> Optional[str]:
        """Acquire a soft lease on a credential.

        If a specific credential_id is provided, lease that entry directly.
        Otherwise prefer the least-leased available credential, using priority as
        a stable tie-breaker. When every credential is already at the soft cap,
        still return the least-leased one instead of blocking.
        """
        with self._lock:
            if credential_id:
                self._active_leases[credential_id] = self._active_leases.get(credential_id, 0) + 1
                self._current_id = credential_id
                return credential_id

            available = self._available_entries(clear_expired=True, refresh=True)
            if not available:
                return None

            below_cap = [
                entry for entry in available
                if self._active_leases.get(entry.id, 0) < self._max_concurrent
            ]
            candidates = below_cap if below_cap else available
            chosen = min(
                candidates,
                key=lambda entry: (self._active_leases.get(entry.id, 0), entry.priority),
            )
            self._active_leases[chosen.id] = self._active_leases.get(chosen.id, 0) + 1
            self._current_id = chosen.id
            return chosen.id

    def release_lease(self, credential_id: str) -> None:
        """Release a previously acquired credential lease."""
        with self._lock:
            count = self._active_leases.get(credential_id, 0)
            if count <= 1:
                self._active_leases.pop(credential_id, None)
            else:
                self._active_leases[credential_id] = count - 1

    def try_refresh_current(self) -> Optional[PooledCredential]:
        with self._lock:
            return self._try_refresh_current_unlocked()

    def _try_refresh_current_unlocked(self) -> Optional[PooledCredential]:
        entry = self.current()
        if entry is None:
            return None
        refreshed = self._refresh_entry(entry, force=True)
        if refreshed is not None:
            self._current_id = refreshed.id
        return refreshed

    def reset_statuses(self) -> int:
        count = 0
        new_entries = []
        for entry in self._entries:
            if entry.last_status or entry.last_status_at or entry.last_error_code:
                new_entries.append(
                    replace(
                        entry,
                        last_status=None,
                        last_status_at=None,
                        last_error_code=None,
                        last_error_reason=None,
                        last_error_message=None,
                        last_error_reset_at=None,
                    )
                )
                count += 1
            else:
                new_entries.append(entry)
        if count:
            self._entries = new_entries
            self._persist()
        return count

    def remove_index(self, index: int) -> Optional[PooledCredential]:
        if index < 1 or index > len(self._entries):
            return None
        removed = self._entries.pop(index - 1)
        self._entries = [
            replace(entry, priority=new_priority)
            for new_priority, entry in enumerate(self._entries)
        ]
        self._persist()
        if self._current_id == removed.id:
            self._current_id = None
        return removed

    def resolve_target(self, target: Any) -> Tuple[Optional[int], Optional[PooledCredential], Optional[str]]:
        raw = str(target or "").strip()
        if not raw:
            return None, None, "No credential target provided."

        for idx, entry in enumerate(self._entries, start=1):
            if entry.id == raw:
                return idx, entry, None

        label_matches = [
            (idx, entry)
            for idx, entry in enumerate(self._entries, start=1)
            if entry.label.strip().lower() == raw.lower()
        ]
        if len(label_matches) == 1:
            return label_matches[0][0], label_matches[0][1], None
        if len(label_matches) > 1:
            return None, None, f'Ambiguous credential label "{raw}". Use the numeric index or entry id instead.'
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(self._entries):
                return index, self._entries[index - 1], None
            return None, None, f"No credential #{index}."
        return None, None, f'No credential matching "{raw}".'

    def add_entry(self, entry: PooledCredential) -> PooledCredential:
        entry = replace(entry, priority=_next_priority(self._entries))
        self._entries.append(entry)
        self._persist()
        return entry


def _upsert_entry(entries: List[PooledCredential], provider: str, source: str, payload: Dict[str, Any]) -> bool:
    existing_idx = None
    for idx, entry in enumerate(entries):
        if entry.source == source:
            existing_idx = idx
            break

    if existing_idx is None:
        payload.setdefault("id", uuid.uuid4().hex[:6])
        payload.setdefault("priority", _next_priority(entries))
        payload.setdefault("label", payload.get("label") or source)
        entries.append(PooledCredential.from_dict(provider, payload))
        return True

    existing = entries[existing_idx]
    field_updates = {}
    extra_updates = {}
    _field_names = {f.name for f in fields(existing)}
    for key, value in payload.items():
        if key in {"id", "priority"} or value is None:
            continue
        if key == "label" and existing.label:
            continue
        if key in _field_names:
            if getattr(existing, key) != value:
                field_updates[key] = value
        elif key in _EXTRA_KEYS:
            if existing.extra.get(key) != value:
                extra_updates[key] = value
    if field_updates or extra_updates:
        if extra_updates:
            field_updates["extra"] = {**existing.extra, **extra_updates}
        entries[existing_idx] = replace(existing, **field_updates)
        return True
    return False


def _normalize_pool_priorities(provider: str, entries: List[PooledCredential]) -> bool:
    return False


def _seed_from_singletons(provider: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    changed = False
    active_sources: Set[str] = set()
    auth_store = _load_auth_store()

    if provider == "nous":
        state = _load_provider_state(auth_store, "nous")
        if state:
            active_sources.add("device_code")
            changed |= _upsert_entry(
                entries,
                provider,
                "device_code",
                {
                    "source": "device_code",
                    "auth_type": AUTH_TYPE_OAUTH,
                    "access_token": state.get("access_token", ""),
                    "refresh_token": state.get("refresh_token"),
                    "expires_at": state.get("expires_at"),
                    "token_type": state.get("token_type"),
                    "scope": state.get("scope"),
                    "client_id": state.get("client_id"),
                    "portal_base_url": state.get("portal_base_url"),
                    "inference_base_url": state.get("inference_base_url"),
                    "agent_key": state.get("agent_key"),
                    "agent_key_expires_at": state.get("agent_key_expires_at"),
                    "tls": state.get("tls") if isinstance(state.get("tls"), dict) else None,
                    "label": label_from_token(state.get("access_token", ""), "device_code"),
                },
            )

    return changed, active_sources


def _seed_from_env(provider: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    changed = False
    active_sources: Set[str] = set()
    if provider == "openrouter":
        token = os.getenv("OPENROUTER_API_KEY", "").strip()
        if token:
            source = "env:OPENROUTER_API_KEY"
            active_sources.add(source)
            changed |= _upsert_entry(
                entries,
                provider,
                source,
                {
                    "source": source,
                    "auth_type": AUTH_TYPE_API_KEY,
                    "access_token": token,
                    "base_url": OPENROUTER_BASE_URL,
                    "label": "OPENROUTER_API_KEY",
                },
            )
        return changed, active_sources

    pconfig = PROVIDER_REGISTRY.get(provider)
    if not pconfig or pconfig.auth_type != AUTH_TYPE_API_KEY:
        return changed, active_sources

    env_url = ""
    if pconfig.base_url_env_var:
        env_url = os.getenv(pconfig.base_url_env_var, "").strip().rstrip("/")

    env_vars = list(pconfig.api_key_env_vars)

    for env_var in env_vars:
        token = os.getenv(env_var, "").strip()
        if not token:
            continue
        source = f"env:{env_var}"
        active_sources.add(source)
        auth_type = AUTH_TYPE_API_KEY
        base_url = env_url or pconfig.inference_base_url
        if provider == "kimi-coding":
            base_url = _resolve_kimi_base_url(token, pconfig.inference_base_url, env_url)
        elif provider == "zai":
            base_url = _resolve_zai_base_url(token, pconfig.inference_base_url, env_url)
        changed |= _upsert_entry(
            entries,
            provider,
            source,
            {
                "source": source,
                "auth_type": auth_type,
                "access_token": token,
                "base_url": base_url,
                "label": env_var,
            },
        )
    return changed, active_sources


def _prune_stale_seeded_entries(entries: List[PooledCredential], active_sources: Set[str]) -> bool:
    retained = [
        entry
        for entry in entries
        if _is_manual_source(entry.source)
        or entry.source in active_sources
        or not entry.source.startswith("env:")
    ]
    if len(retained) == len(entries):
        return False
    entries[:] = retained
    return True


def _seed_custom_pool(pool_key: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    """Seed a custom endpoint pool from providers config and active model config."""
    changed = False
    active_sources: Set[str] = set()

    # Seed from the custom provider config entry's api_key field.
    cp_config = _get_custom_provider_config(pool_key)
    if cp_config:
        api_key = str(cp_config.get("api_key") or "").strip()
        base_url = str(cp_config.get("base_url") or "").strip().rstrip("/")
        name = str(cp_config.get("name") or "").strip()
        if api_key:
            source = f"config:{name}"
            active_sources.add(source)
            changed |= _upsert_entry(
                entries,
                pool_key,
                source,
                {
                    "source": source,
                    "auth_type": AUTH_TYPE_API_KEY,
                    "access_token": api_key,
                    "base_url": base_url,
                    "label": name or source,
                },
            )

    # Seed from the active custom provider config when it matches this pool.
    try:
        config = _load_config_safe()
        if config:
            from ..config.configuration import get_active_model_config, get_active_provider_key

            active_provider = str(get_active_provider_key(config) or "").strip().lower()
            model_cfg = get_active_model_config(config)
            model_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
            model_api_key = ""
            for k in ("api_key", "api"):
                v = model_cfg.get(k)
                if isinstance(v, str) and v.strip():
                    model_api_key = v.strip()
                    break
            if active_provider and active_provider not in PROVIDER_REGISTRY and model_base_url and model_api_key:
                matched_key = get_custom_provider_pool_key(model_base_url)
                if matched_key == pool_key:
                    source = "model_config"
                    active_sources.add(source)
                    changed |= _upsert_entry(
                        entries,
                        pool_key,
                        source,
                        {
                            "source": source,
                            "auth_type": AUTH_TYPE_API_KEY,
                            "access_token": model_api_key,
                            "base_url": model_base_url,
                            "label": "model_config",
                        },
                    )
    except Exception:
        pass

    return changed, active_sources


def load_pool(provider: str) -> CredentialPool:
    provider = (provider or "").strip().lower()
    raw_entries = read_credential_pool(provider)
    entries = [PooledCredential.from_dict(provider, payload) for payload in raw_entries]

    if provider.startswith(CUSTOM_POOL_PREFIX):
        # Custom endpoint pool — seed from providers config and active model config.
        custom_changed, custom_sources = _seed_custom_pool(provider, entries)
        changed = custom_changed
        changed |= _prune_stale_seeded_entries(entries, custom_sources)
    else:
        singleton_changed, singleton_sources = _seed_from_singletons(provider, entries)
        env_changed, env_sources = _seed_from_env(provider, entries)
        changed = singleton_changed or env_changed
        changed |= _prune_stale_seeded_entries(entries, singleton_sources | env_sources)
        changed |= _normalize_pool_priorities(provider, entries)

    if changed:
        write_credential_pool(
            provider,
            [entry.to_dict() for entry in sorted(entries, key=lambda item: item.priority)],
        )
    return CredentialPool(provider, entries)
