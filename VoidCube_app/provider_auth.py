"""
认证模块

本，移除复杂认证逻辑。
"""

from typing import Optional, Dict, Any, List
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from VoidCube_app.environment import is_placeholder_secret


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


class ProviderConfig(dict):
    """提供者配置类，支持字典和属性访问"""
    
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")
    
    def __setattr__(self, key, value):
        self[key] = value


def _configured_env_value(name: str) -> str:
    """Read a process override, then the persisted VoidCube environment."""
    name = str(name or "").strip()
    if not name:
        return ""
    process_value = os.getenv(name, "").strip()
    if process_value:
        return process_value
    try:
        from VoidCube_app.config import get_env_value

        return str(get_env_value(name) or "").strip()
    except Exception:
        return ""


# 提供者注册表 (，包含api_key_env_vars)
PROVIDER_REGISTRY = {
    "ollama": ProviderConfig({
        "name": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key_env_vars": [],
        "base_url_env_var": "OLLAMA_BASE_URL",
        "auth_type": "none",
        "inference_base_url": "http://localhost:11434/v1",
    }),
    "lm-studio": ProviderConfig({
        "name": "LM Studio",
        "base_url": "http://localhost:1234/v1",
        "api_key_env_vars": [],
        "base_url_env_var": "LM_STUDIO_BASE_URL",
        "auth_type": "none",
        "inference_base_url": "http://localhost:1234/v1",
    }),
    "openai": ProviderConfig({
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env_vars": ["OPENAI_API_KEY"],
        "base_url_env_var": "OPENAI_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.openai.com/v1",
    }),
    "deepseek": ProviderConfig({
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env_vars": ["DEEPSEEK_API_KEY"],
        "base_url_env_var": "DEEPSEEK_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.deepseek.com/v1",
    }),
    "openrouter": ProviderConfig({
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env_vars": ["OPENROUTER_API_KEY"],
        "base_url_env_var": "OPENROUTER_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://openrouter.ai/api/v1",
    }),
    "zai": ProviderConfig({
        "name": "Z.AI / GLM",
        "base_url": "https://api.zai.com/v1",
        "api_key_env_vars": ["GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"],
        "base_url_env_var": "GLM_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.zai.com/v1",
    }),
    "kimi-coding": ProviderConfig({
        "name": "Kimi / Moonshot",
        "base_url": "https://api.kimi.moonshot.cn/v1",
        "api_key_env_vars": ["KIMI_API_KEY"],
        "base_url_env_var": "KIMI_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.kimi.moonshot.cn/v1",
    }),
    "minimax": ProviderConfig({
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "api_key_env_vars": ["MINIMAX_API_KEY"],
        "base_url_env_var": "MINIMAX_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.minimax.io/v1",
    }),
    "minimax-cn": ProviderConfig({
        "name": "MiniMax (China)",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key_env_vars": ["MINIMAX_CN_API_KEY"],
        "base_url_env_var": "MINIMAX_CN_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.minimaxi.com/v1",
    }),
    "agnes-ai": ProviderConfig({
        "name": "Agnes-AI",
        "base_url": "https://api.agnes-ai.cn/v1",
        "api_key_env_vars": ["AGNES_API_KEY"],
        "base_url_env_var": "AGNES_BASE_URL",
        "auth_type": "api_key",
        "inference_base_url": "https://api.agnes-ai.cn/v1",
    }),
}

SPECIAL_RUNTIME_PROVIDER_IDS = frozenset({
    "nous",
    "qwen-oauth",
    "copilot-acp",
    "custom",
})
RUNTIME_PROVIDER_IDS = frozenset(
    provider
    for provider, config in PROVIDER_REGISTRY.items()
    if config.get("auth_type") == "api_key"
) | SPECIAL_RUNTIME_PROVIDER_IDS

# 认证相关常量
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 3600
KIMI_CODE_BASE_URL = "https://api.kimi.moonshot.cn/v1"
DEFAULT_NOUS_BASE_URL = "https://api.nous.com/v1"
DEFAULT_ZAI_BASE_URL = "https://api.zai.com/v1"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# 认证存储锁
_auth_store_lock = threading.RLock()  # reentrant — callers may nest _save_auth_store inside their own lock

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
    if not api_key:
        return False
    api_key = api_key.strip()
    if is_placeholder_secret(api_key):
        return False
    # 检查是否是有效的 API Key
    # 通常 API Key 至少有一定长度
    if len(api_key) < 10:
        return False
    # 常见的 API Key 前缀
    valid_prefixes = [
        "sk-", "pk-", "api-", "key-", "token-",
        "OPENROUTER-", "DEEPSEEK-",
    ]
    return any(api_key.startswith(prefix) for prefix in valid_prefixes) or len(api_key) >= 32

def resolve_api_key_provider_credentials(provider: str) -> Optional[Dict[str, Any]]:
    """解析 API Key 提供者凭证"""
    if provider not in PROVIDER_REGISTRY:
        return None
    
    pconfig = PROVIDER_REGISTRY[provider]
    api_key_env_vars = pconfig.get("api_key_env_vars", [])
    
    # 查找API密钥
    api_key = None
    for env_var in api_key_env_vars:
        candidate = _configured_env_value(env_var)
        if has_usable_secret(candidate):
            api_key = candidate
            break

    if not api_key:
        store = _load_auth_store()
        provider_state = store.get(provider)
        if isinstance(provider_state, dict):
            for key_name in ("api_key", "access_token"):
                candidate = str(provider_state.get(key_name) or "").strip()
                if has_usable_secret(candidate):
                    api_key = candidate
                    break
    
    base_url_env_var = str(pconfig.get("base_url_env_var") or "").strip()
    base_url = (
        _configured_env_value(base_url_env_var)
        if base_url_env_var
        else ""
    ) or pconfig.get("base_url", "")
    
    return {
        "api_key": api_key or "",
        "base_url": base_url,
    }

def resolve_nous_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Nous 运行时凭证"""
    return None

def get_provider_auth_state(provider: str) -> Dict[str, Any]:
    """获取提供者认证状态"""
    return {"authenticated": False, "provider": provider}

def _get_auth_store_path() -> Path:
    """Return the path to the auth store JSON file."""
    from pathlib import Path as _Path
    try:
        from VoidCube_core.constants import get_VoidCube_home
        home = get_VoidCube_home()
    except Exception:
        home = _Path.home() / ".VoidCube"
    home.mkdir(parents=True, exist_ok=True)
    return home / "auth_store.json"


def _load_auth_store() -> Dict[str, Any]:
    """Load the persistent auth store from disk."""
    import json as _json
    store_path = _get_auth_store_path()
    try:
        if store_path.exists():
            with open(store_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _save_auth_store(store: Dict[str, Any]) -> None:
    """Persist the auth store to disk atomically, with lock protection."""
    import json as _json
    store_path = _get_auth_store_path()
    tmp_path = store_path.with_suffix(".tmp")
    try:
        with _auth_store_lock:
            with open(tmp_path, "w", encoding="utf-8") as f:
                _json.dump(store, f, ensure_ascii=False, indent=2)
            tmp_path.replace(store_path)
    except Exception:
        pass
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

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
    store = _load_auth_store()
    pool = store.get("credential_pool")
    if not isinstance(pool, dict):
        pool = {}
    if provider is None:
        return pool
    entries = pool.get(str(provider or "").strip().lower(), [])
    return entries if isinstance(entries, list) else []


def write_credential_pool(provider: str, entries: Any) -> None:
    """Write a provider's persistent credential pool."""
    with _auth_store_lock:
        store = _load_auth_store()
        pool = store.get("credential_pool")
        if not isinstance(pool, dict):
            pool = {}
        key = str(provider or "").strip().lower()
        pool[key] = entries if isinstance(entries, list) else []
        store["credential_pool"] = pool
        _save_auth_store(store)

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
