"""Canonical built-in Provider metadata and runtime allowlist."""

from __future__ import annotations


class ProviderConfig(dict):
    """Provider metadata with both mapping and attribute access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{key}'"
            ) from exc

    def __setattr__(self, key, value):
        self[key] = value


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
        "headers": {
            "HTTP-Referer": "https://VoidCube-agent.nousresearch.com",
            "X-OpenRouter-Title": "Voidcube Agent",
            "X-OpenRouter-Categories": "productivity,cli-agent",
        },
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
        "headers": {"User-Agent": "KimiCLI/1.30.0"},
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

# Header templates for runtime providers that are not API-key entries in the
# registry.  Keeping these beside provider metadata avoids endpoint substring
# matching while still allowing the standard request-header resolver to serve
# OAuth-backed providers.
PROVIDER_HEADER_TEMPLATES = {
    "qwen-oauth": {
        "User-Agent": "QwenCode/0.14.1 ({platform}; {machine})",
        "X-DashScope-CacheControl": "enable",
        "X-DashScope-UserAgent": "QwenCode/0.14.1 ({platform}; {machine})",
        "X-DashScope-AuthType": "qwen-oauth",
    },
}

SPECIAL_RUNTIME_PROVIDER_IDS = frozenset({
    "nous",
    "qwen-oauth",
    "copilot-acp",
    "custom",
})
LOCAL_RUNTIME_PROVIDER_IDS = frozenset({"ollama"})
RUNTIME_PROVIDER_IDS = frozenset(
    provider
    for provider, config in PROVIDER_REGISTRY.items()
    if config.get("auth_type") == "api_key"
) | SPECIAL_RUNTIME_PROVIDER_IDS | LOCAL_RUNTIME_PROVIDER_IDS

__all__ = [
    "PROVIDER_HEADER_TEMPLATES",
    "PROVIDER_REGISTRY",
    "LOCAL_RUNTIME_PROVIDER_IDS",
    "RUNTIME_PROVIDER_IDS",
    "SPECIAL_RUNTIME_PROVIDER_IDS",
    "ProviderConfig",
]
