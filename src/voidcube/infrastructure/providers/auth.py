"""
Provider authentication module

本，移除复杂认证逻辑。
"""

from typing import Optional, Dict, Any, List
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .registry import (
    PROVIDER_REGISTRY,
    RUNTIME_PROVIDER_IDS,
    SPECIAL_RUNTIME_PROVIDER_IDS,
    ProviderConfig,
)
from . import credentials as credential_store

# Transitional private alias for ``agent.credential_pool``; the lock itself
# belongs to the canonical credential store and is not reimplemented here.
_auth_store_lock = credential_store._STORE_LOCK


def normalize_openai_compatible_base_url(value: str) -> str:
    """Normalize an OpenAI-compatible endpoint to its API root."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, "")).rstrip("/")


def _configured_env_value(name: str) -> str:
    """Read a process override, then the persisted VoidCube environment."""
    return credential_store.configured_env_value(name)


# 认证相关常量
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 3600
KIMI_CODE_BASE_URL = "https://api.kimi.moonshot.cn/v1"
DEFAULT_NOUS_BASE_URL = "https://api.nous.com/v1"
DEFAULT_ZAI_BASE_URL = "https://api.zai.com/v1"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

class AuthError(Exception):
    """认证错误"""
    pass

def get_auth_status(provider: str = None) -> Dict[str, Any]:
    """获取认证状态
    
    Args:
        provider: 提供者名称，如不指定则返回通用状态
    """
    if provider:
        # 检查特定 provider 的认证状态
        if provider in PROVIDER_REGISTRY:
            creds = resolve_api_key_provider_credentials(provider) or {}
            if has_usable_secret(str(creds.get("api_key") or "")):
                return {
                    "authenticated": True,
                    "provider": provider,
                    "logged_in": True,
                    "configured": True,
                }
        return {
            "authenticated": False,
            "provider": provider,
            "logged_in": False,
            "configured": False,
        }
    
    # 默认返回通用状态
    return {
        "authenticated": False,
        "provider": "",
        "logged_in": False,
        "configured": False,
    }

def resolve_provider(provider: str = None, explicit_api_key: str = None, explicit_base_url: str = None, **kwargs) -> str:
    """解析提供者名称
    
    Args:
        provider: 提供者名称
        explicit_api_key: 显式API密钥（忽略）
        explicit_base_url: 显式基础URL（忽略）
        **kwargs: 其他参数（忽略）
    
    Returns:
        提供者名称字符串
    """
    if provider and isinstance(provider, str):
        return provider.lower().strip()
    return ""

def resolve_provider_config(provider: str = None, explicit_api_key: str = None, explicit_base_url: str = None, **kwargs) -> Dict[str, Any]:
    """解析提供者配置（完整版）
    
    Args:
        provider: 提供者名称
        explicit_api_key: 显式API密钥
        explicit_base_url: 显式基础URL
        **kwargs: 其他参数（忽略）
    
    Returns:
        提供者配置字典
    """
    provider_name = resolve_provider(provider)
    
    if provider_name in PROVIDER_REGISTRY:
        result = dict(PROVIDER_REGISTRY[provider_name])
        if explicit_api_key:
            result["api_key"] = explicit_api_key
        if explicit_base_url:
            result["base_url"] = explicit_base_url
        return result

    result = {
        "provider": provider_name,
        "base_url": (explicit_base_url or "").strip(),
        "model": "",
    }

    if explicit_api_key:
        result["api_key"] = explicit_api_key

    return result

def has_usable_secret(api_key: str) -> bool:
    """检查是否有可用密钥
    
    Args:
        api_key: API密钥字符串
    """
    return credential_store.has_usable_secret(api_key)

def resolve_api_key_provider_credentials(provider: str) -> Optional[Dict[str, Any]]:
    """解析 API Key 提供者凭证"""
    return credential_store.resolve_api_key_provider_credentials(provider)

def resolve_nous_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Nous 运行时凭证"""
    return None

def get_provider_auth_state(provider: str) -> Dict[str, Any]:
    """获取提供者认证状态"""
    return {"authenticated": False, "provider": provider}

def _get_auth_store_path() -> Path:
    """Return the path to the auth store JSON file."""
    return credential_store.auth_store_path()


# Captured once so the credential store can distinguish the default hook from
# an explicit replacement made by a legacy integration or test.
credential_store._CANONICAL_AUTH_STORE_GETTER_CODE = _get_auth_store_path.__code__


def _load_auth_store() -> Dict[str, Any]:
    """Load the persistent auth store from disk."""
    return credential_store.load_auth_store()


def _save_auth_store(store: Dict[str, Any]) -> None:
    """Persist the auth store to disk atomically, with lock protection."""
    credential_store.save_auth_store(store)

def fetch_nous_models() -> List[Dict[str, Any]]:
    """获取 Nous 模型列表"""
    return []

# 默认值
DEFAULT_NOUS_PORTAL_URL = "https://portal.nousresearch.com"

def get_nous_auth_status() -> Dict[str, Any]:
    """获取 Nous 认证状态"""
    return {"authenticated": False}

def get_qwen_auth_status() -> Dict[str, Any]:
    """获取 Qwen 认证状态"""
    return {"authenticated": False}

def _decode_jwt_claims(token: str) -> Dict[str, Any]:
    """解码 JWT 声明"""
    return {}

def _load_provider_state(provider: str) -> Dict[str, Any]:
    """加载提供者状态"""
    return {}

def _resolve_kimi_base_url() -> str:
    """解析 Kimi 基础 URL"""
    return os.getenv("KIMI_CODE_BASE_URL", KIMI_CODE_BASE_URL)

def _resolve_zai_base_url() -> str:
    """解析 Zai 基础 URL"""
    return os.getenv("ZAI_BASE_URL", "https://api.zai.com/v1")

def _save_provider_state(provider: str, state: Dict[str, Any]) -> None:
    """保存提供者状态"""
    pass

def read_credential_pool(provider: str = None) -> Any:
    """Read the persistent credential pool from the auth store."""
    return credential_store.read_credential_pool(provider)


def write_credential_pool(provider: str, entries: Any) -> None:
    """Write a provider's persistent credential pool."""
    credential_store.write_credential_pool(provider, entries)

def _agent_key_is_usable(key: str = None) -> bool:
    """检查代理密钥是否可用"""
    return key is not None and len(key) > 0

def format_auth_error(error: Exception) -> str:
    """格式化认证错误"""
    return str(error)

def resolve_qwen_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Qwen 运行时凭证"""
    return {}

def resolve_external_process_provider_credentials(provider: str) -> Optional[Dict[str, Any]]:
    """解析外部进程提供者凭证"""
    return {}
