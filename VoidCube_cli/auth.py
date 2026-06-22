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

def _get_auth_store_path() -> Path:
    """Return the path to the auth store JSON file."""
    from pathlib import Path as _Path
    try:
        from VoidCube_cli.config import get_VoidCube_home
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


# ── CLI command handlers ────────────────────────────────────────────────


def login_command(args) -> None:
    """Interactive provider login.

    Handles ``VoidCube login`` and ``VoidCube login --provider <name>``.
    Walks the user through OAuth device flow (nous, openai-codex) or
    API key entry for other providers.

    Args:
        args: argparse namespace with optional ``provider``, ``portal_url``,
              ``inference_url``, ``client_id``, ``scope``, ``no_browser``,
              ``timeout``, ``ca_bundle``, ``insecure`` attributes.
    """
    provider = getattr(args, "provider", None)

    # Determine target provider
    if not provider:
        try:
            from VoidCube_cli.config import get_active_provider_key, load_config
            config = load_config()
            provider = get_active_provider_key(config)
        except Exception:
            provider = None

    if not provider:
        print()
        print("No provider specified and no active provider configured.")
        print()
        print("Usage:")
        print("  VoidCube login --provider nous        Login with Nous Research")
        print("  VoidCube login --provider openai-codex Login with OpenAI Codex")
        print()
        print("First configure a provider:  VoidCube api")
        print("Or set an API key directly:  VoidCube config set providers.<name>.api_key <key>")
        return

    provider = provider.lower().strip()

    if provider in ("nous",):
        _login_nous(args)
    elif provider in ("openai-codex", "codex"):
        _login_codex(args)
    else:
        # Generic API-key provider
        _login_api_key(provider, args)


def _login_nous(args) -> None:
    """OAuth device flow login for Nous Research."""
    import time
    import urllib.request
    import urllib.error
    import json
    import webbrowser

    portal_url = getattr(args, "portal_url", None) or os.getenv(
        "NOUS_PORTAL_URL", DEFAULT_NOUS_PORTAL_URL
    )
    client_id = getattr(args, "client_id", None) or "VoidCube-cli"
    scope = getattr(args, "scope", None) or "openid profile email"
    no_browser = getattr(args, "no_browser", False)
    timeout = getattr(args, "timeout", 15.0)

    print()
    print("> Login with Nous Research")
    print()

    # Step 1: Device authorization request
    device_url = f"{portal_url}/oauth2/device/code"
    data = json.dumps({
        "client_id": client_id,
        "scope": scope,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            device_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            device_resp = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if hasattr(exc, "read") else ""
        print(f"  ✗ Device authorization failed: HTTP {exc.code}")
        if body:
            print(f"    {body[:500]}")
        return
    except Exception as exc:
        print(f"  ✗ Cannot reach Nous portal: {exc}")
        print(f"    Check your network and portal URL: {portal_url}")
        return

    verification_uri = device_resp.get("verification_uri_complete") or device_resp.get("verification_uri", "")
    user_code = device_resp.get("user_code", "")
    device_code = device_resp.get("device_code", "")
    interval = device_resp.get("interval", 5)
    expires_in = device_resp.get("expires_in", 600)

    if not user_code:
        print("  ✗ No user_code in device authorization response")
        return

    print(f"  Verification code: {user_code}")
    if verification_uri:
        print(f"  Open: {verification_uri}")

    if not no_browser and verification_uri:
        print()
        print("  Opening browser...")
        try:
            webbrowser.open(verification_uri)
        except Exception:
            print("  (could not open browser — open the URL above manually)")

    print()
    print(f"  Waiting for authorization (expires in {expires_in}s)...")

    # Step 2: Poll for token
    token_url = f"{portal_url}/oauth2/token"
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        try:
            token_data = json.dumps({
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": client_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                token_url,
                data=token_data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                token_resp = json.loads(resp.read().decode())

            if "access_token" in token_resp:
                # Store the credential
                _store_provider_credential("nous", token_resp)
                print()
                print("  ✓ Login successful!")
                print()
                print("  Run 'VoidCube model' to select a Nous model.")
                return
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()
                err = json.loads(body).get("error", "")
            except Exception:
                err = ""
            if err == "authorization_pending":
                continue  # User hasn't approved yet — keep polling
            elif err == "slow_down":
                interval += 2
                continue
            elif err == "expired_token":
                print()
                print("  ✗ Verification code expired. Run 'VoidCube login --provider nous' to retry.")
                return
            else:
                print(f"  ✗ Token request failed: HTTP {exc.code} — {body[:300]}")
                return
        except Exception as exc:
            print(f"  ✗ Token request error: {exc}")
            continue

    print()
    print("  ✗ Timed out waiting for authorization.")


def _login_codex(args) -> None:
    """OAuth login for OpenAI Codex."""
    print()
    print("> Login with OpenAI Codex")
    print()
    print("  OpenAI Codex uses the Copilot ACP protocol.")
    print("  Run 'VoidCube api' to configure the copilot-acp provider,")
    print("  then authenticate via your editor's Copilot integration.")
    print()
    print("  Or set an API key directly:")
    print("    VoidCube config set providers.openai-codex.api_key sk-...")


def _login_api_key(provider: str, args) -> None:
    """Interactive API key entry for generic providers."""
    import getpass

    print()
    print(f"> Login with {provider.title()}")
    print()

    # Show what env var to set
    if provider in PROVIDER_REGISTRY:
        env_vars = PROVIDER_REGISTRY[provider].get("api_key_env_vars", [])
        if env_vars:
            print(f"  Set environment variable: {env_vars[0]}")
            print(f"  Or add to ~/.VoidCube/.env")
    else:
        env_var = f"{provider.upper().replace('-', '_')}_API_KEY"
        print(f"  Set environment variable: {env_var}")

    print()
    try:
        api_key = getpass.getpass("  API Key (input hidden): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return

    if not api_key:
        print("  No key entered. Cancelled.")
        return

    # Save to .env
    try:
        from VoidCube_cli.config import get_env_path, save_env_value
        env_file = get_env_path()
        env_key = PROVIDER_REGISTRY.get(provider, {}).get("api_key_env_vars", [None])[0]
        if not env_key:
            env_key = f"{provider.upper().replace('-', '_')}_API_KEY"
        save_env_value(env_key, api_key)
        print(f"  ✓ API key saved to {env_file}")
        _store_provider_credential(provider, {"api_key": api_key})
    except Exception as exc:
        print(f"  ✗ Failed to save: {exc}")
        print(f"  Manually add to ~/.VoidCube/.env:")
        print(f"  {env_key}={api_key}")


def _store_provider_credential(provider: str, credential: dict) -> None:
    """Store a provider credential in the auth store (thread-safe)."""
    try:
        with _auth_store_lock:
            store = _load_auth_store()
            store[provider] = credential
            # Inline the write inside the lock so load+save is atomic
            import json as _json
            store_path = _get_auth_store_path()
            tmp_path = store_path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    _json.dump(store, f, ensure_ascii=False, indent=2)
                tmp_path.replace(store_path)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
    except Exception:
        pass  # Best-effort


def logout_command(args) -> None:
    """Clear provider authentication.

    Handles ``VoidCube logout`` and ``VoidCube logout --provider <name>``.
    Removes stored credentials, clears env vars from .env, and resets
    provider config.

    Args:
        args: argparse namespace with optional ``provider`` attribute.
    """
    provider = getattr(args, "provider", None)

    if not provider:
        try:
            from VoidCube_cli.config import get_active_provider_key, load_config
            config = load_config()
            provider = get_active_provider_key(config)
        except Exception:
            provider = None

    if not provider:
        print()
        print("No provider specified and no active provider configured.")
        print()
        print("Usage:")
        print("  VoidCube logout --provider nous")
        print("  VoidCube logout --provider openai-codex")
        return

    provider = provider.lower().strip()
    provider_name = PROVIDER_REGISTRY.get(provider, {}).get("name", provider)

    print()
    print(f"> Logout from {provider_name} ({provider})")
    print()

    # Remove from auth store
    try:
        store = _load_auth_store()
        if provider in store:
            del store[provider]
            _save_auth_store(store)
            print("  ✓ Cleared stored credentials")
        else:
            print("  (no stored credentials found)")
    except Exception:
        pass

    # Remove API key env var(s) from .env
    env_keys = PROVIDER_REGISTRY.get(provider, {}).get("api_key_env_vars", [])
    if not env_keys:
        env_keys = [f"{provider.upper().replace('-', '_')}_API_KEY"]

    try:
        from VoidCube_cli.config import save_env_value
        for key in env_keys:
            save_env_value(key, "")
        if env_keys:
            print(f"  ✓ Cleared {', '.join(env_keys)} from .env")
    except Exception:
        pass

    # Clear provider from config if it's the active provider
    try:
        from VoidCube_cli.config import load_config, save_config, get_active_provider_key
        config = load_config()
        if get_active_provider_key(config) == provider:
            config["active_provider"] = ""
            save_config(config)
            print("  ✓ Reset active provider")
    except Exception:
        pass

    print()
    print("  Logged out. Run 'VoidCube api' to reconfigure.")
