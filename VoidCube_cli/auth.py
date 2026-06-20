"""
认证模块

本，移除复杂认证逻辑。
"""

from typing import Optional, Dict, Any, List
import os
import threading

class ProviderConfig(dict):
    """提供者配置类，支持字典和属性访问"""
    
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")
    
    def __setattr__(self, key, value):
        self[key] = value

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
}

# 认证相关常量
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 300
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 3600
KIMI_CODE_BASE_URL = "https://api.kimi.moonshot.cn/v1"
DEFAULT_CODEX_BASE_URL = "https://api.codex.com/v1"
DEFAULT_NOUS_BASE_URL = "https://api.nous.com/v1"
DEFAULT_ZAI_BASE_URL = "https://api.zai.com/v1"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# 认证存储锁
_auth_store_lock = threading.Lock()

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
            pconfig = PROVIDER_REGISTRY[provider]
            api_key_env_vars = pconfig.get("api_key_env_vars", [])
            for env_var in api_key_env_vars:
                api_key = os.getenv(env_var, "").strip()
                if api_key:
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
        api_key = os.getenv(env_var)
        if api_key:
            break
    
    # 获取base_url
    base_url = pconfig.get("base_url", "")
    
    return {
        "api_key": api_key,
        "base_url": base_url,
    }

def resolve_nous_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Nous 运行时凭证"""
    return None

def resolve_codex_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Codex 运行时凭证"""
    return None

def get_provider_auth_state(provider: str) -> Dict[str, Any]:
    """获取提供者认证状态"""
    return {"authenticated": False, "provider": provider}

def is_provider_explicitly_configured(provider: str) -> bool:
    """检查提供者是否显式配置"""
    return provider in ["ollama", "lm-studio", "local"]

def is_source_suppressed(source: str) -> bool:
    """检查源是否被抑制"""
    return False

def deactivate_provider(provider: str) -> None:
    """停用提供者"""
    pass

def _prompt_model_selection() -> Optional[str]:
    """提示选择模型"""
    return None

def _save_model_choice(model: str) -> None:
    """保存模型选择"""
    pass

def _load_auth_store() -> Dict[str, Any]:
    """加载认证存储"""
    return {}

def fetch_nous_models() -> List[Dict[str, Any]]:
    """获取 Nous 模型列表"""
    return []

# 默认值
DEFAULT_NOUS_PORTAL_URL = "https://portal.nousresearch.com"

def _read_codex_tokens() -> Dict[str, Any]:
    """读取 Codex 令牌"""
    return {}

def _import_codex_cli_tokens() -> Dict[str, Any]:
    """导入 Codex CLI 令牌"""
    return {}

def _save_codex_tokens(tokens: Dict[str, Any]) -> None:
    """保存 Codex 令牌"""
    pass

def get_nous_auth_status() -> Dict[str, Any]:
    """获取 Nous 认证状态"""
    return {"authenticated": False}

def get_codex_auth_status() -> Dict[str, Any]:
    """获取 Codex 认证状态"""
    return {"authenticated": False}

def get_qwen_auth_status() -> Dict[str, Any]:
    """获取 Qwen 认证状态"""
    return {"authenticated": False}

def _codex_access_token_is_expiring(token: str = None) -> bool:
    """检查 Codex 访问令牌是否即将过期"""
    return False

def _decode_jwt_claims(token: str) -> Dict[str, Any]:
    """解码 JWT 声明"""
    return {}

def _write_codex_cli_tokens(tokens: Dict[str, Any]) -> None:
    """写入 Codex CLI 令牌"""
    pass

def _load_provider_state(provider: str) -> Dict[str, Any]:
    """加载提供者状态"""
    return {}

def _resolve_kimi_base_url() -> str:
    """解析 Kimi 基础 URL"""
    return os.getenv("KIMI_CODE_BASE_URL", KIMI_CODE_BASE_URL)

def _resolve_zai_base_url() -> str:
    """解析 Zai 基础 URL"""
    return os.getenv("ZAI_BASE_URL", "https://api.zai.com/v1")

def _save_auth_store(store: Dict[str, Any]) -> None:
    """保存认证存储"""
    pass

def _save_provider_state(provider: str, state: Dict[str, Any]) -> None:
    """保存提供者状态"""
    pass

def read_credential_pool() -> Dict[str, Any]:
    """读取凭证池"""
    return {}

def write_credential_pool(pool: Dict[str, Any]) -> None:
    """写入凭证池"""
    pass

def _agent_key_is_usable(key: str = None) -> bool:
    """检查代理密钥是否可用"""
    return key is not None and len(key) > 0

def format_auth_error(error: Exception) -> str:
    """格式化认证错误"""
    return str(error)

def resolve_qwen_runtime_credentials() -> Optional[Dict[str, Any]]:
    """解析 Qwen 运行时凭证"""
    return None

def resolve_external_process_provider_credentials(provider: str) -> Optional[Dict[str, Any]]:
    """解析外部进程提供者凭证"""
    return None
