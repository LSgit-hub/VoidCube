from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MEMORY_PROVIDER_PLUGINS = frozenset(
    {"", "mem", "hindsight", "openviking", "holographic", "retaindb", "byterover"}
)

PROVIDER_DEFAULTS = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "provider_profile": "openai",
    },
    "openrouter": {
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "provider_profile": "openai",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "provider_profile": "openai",
    },
    "ollama": {
        "api_key_env": "OLLAMA_API_KEY",
        "base_url": "http://localhost:11434/v1",
        "provider_profile": "legacy-compatible",
    },
}

MEM_MODEL_ROLES = frozenset(
    {
        "default",
        "extraction",
        "summarization",
        "governance_summary",
        "governance_reasoner",
        "embedding",
    }
)


@dataclass(frozen=True, slots=True)
class MemModelConfig:
    provider: str = "openai"
    model: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    provider_profile: str = "openai"
    provider_profile_file: str | None = None
    chat_completions_path: str | None = None
    system_prompt_style: str | None = None
    response_format_style: str | None = None
    response_content_style: str | None = None

    def overlay(self, overrides: dict[str, Any]) -> "MemModelConfig":
        provider = str(overrides.get("provider") or self.provider)
        defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
        provider_changed = provider != self.provider
        return MemModelConfig(
            provider=provider,
            model=_optional_str(overrides.get("model")) or self.model,
            api_key_env=str(
                overrides.get("api_key_env")
                or (defaults["api_key_env"] if provider_changed else self.api_key_env)
            ),
            base_url=_optional_str(overrides.get("base_url"))
            or (str(defaults["base_url"]) if provider_changed else self.base_url),
            provider_profile=str(
                overrides.get("provider_profile")
                or (defaults["provider_profile"] if provider_changed else self.provider_profile)
                or defaults["provider_profile"]
            ),
            provider_profile_file=_optional_str(
                overrides.get("provider_profile_file")
            )
            or self.provider_profile_file,
            chat_completions_path=_optional_str(
                overrides.get("chat_completions_path")
            )
            or self.chat_completions_path,
            system_prompt_style=_optional_str(overrides.get("system_prompt_style"))
            or self.system_prompt_style,
            response_format_style=_optional_str(overrides.get("response_format_style"))
            or self.response_format_style,
            response_content_style=_optional_str(
                overrides.get("response_content_style")
            )
            or self.response_content_style,
        )

    @classmethod
    def from_voidcube_config(cls, config: dict[str, Any]) -> "MemModelConfig":
        memory = config.get("memory", {})
        if not isinstance(memory, dict):
            memory = {}
        llm = memory.get("llm", {})
        if not isinstance(llm, dict):
            llm = {}

        legacy_provider = memory.get("provider")
        legacy_model = memory.get("model")
        provider = str(
            llm.get("provider")
            or _legacy_llm_provider(legacy_provider)
            or "openai"
        )
        defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
        model = llm.get("model") or legacy_model or None
        return cls(
            provider=provider,
            model=str(model) if model else None,
            api_key_env=str(llm.get("api_key_env") or defaults["api_key_env"]),
            base_url=str(llm.get("base_url") or defaults["base_url"]),
            provider_profile=str(
                llm.get("provider_profile") or defaults["provider_profile"]
            ),
            provider_profile_file=llm.get("provider_profile_file") or None,
            chat_completions_path=llm.get("chat_completions_path") or None,
            system_prompt_style=llm.get("system_prompt_style") or None,
            response_format_style=llm.get("response_format_style") or None,
            response_content_style=llm.get("response_content_style") or None,
        )

    def with_cli_overrides(self, args: Any) -> "MemModelConfig":
        return MemModelConfig(
            provider=self.provider,
            model=getattr(args, "model", None) or self.model,
            api_key_env=getattr(args, "api_key_env", None) or self.api_key_env,
            base_url=getattr(args, "base_url", None) or self.base_url,
            provider_profile=getattr(args, "provider_profile", None)
            or self.provider_profile,
            provider_profile_file=getattr(args, "provider_profile_file", None)
            or self.provider_profile_file,
            chat_completions_path=getattr(args, "chat_completions_path", None)
            or self.chat_completions_path,
            system_prompt_style=getattr(args, "system_prompt_style", None)
            or self.system_prompt_style,
            response_format_style=getattr(args, "response_format_style", None)
            or self.response_format_style,
            response_content_style=getattr(args, "response_content_style", None)
            or self.response_content_style,
        )


@dataclass(frozen=True, slots=True)
class MemModelConfigSet:
    default: MemModelConfig
    roles: dict[str, MemModelConfig]

    @classmethod
    def from_voidcube_config(cls, config: dict[str, Any]) -> "MemModelConfigSet":
        default = MemModelConfig.from_voidcube_config(config)
        memory = config.get("memory", {})
        if not isinstance(memory, dict):
            memory = {}
        llm = memory.get("llm", {})
        if not isinstance(llm, dict):
            llm = {}
        raw_roles = llm.get("roles", {})
        roles: dict[str, MemModelConfig] = {}
        if isinstance(raw_roles, dict):
            for role, overrides in raw_roles.items():
                if role not in MEM_MODEL_ROLES or not isinstance(overrides, dict):
                    continue
                roles[role] = default.overlay(overrides)
        return cls(default=default, roles=roles)

    def for_role(self, role: str) -> MemModelConfig:
        return self.roles.get(role, self.default)


def load_voidcube_mem_model_config() -> MemModelConfig:
    return load_voidcube_mem_model_config_set().default


def load_voidcube_mem_model_config_set() -> MemModelConfigSet:
    try:
        from VoidCube_cli.config import load_config

        return MemModelConfigSet.from_voidcube_config(load_config())
    except Exception:
        return MemModelConfigSet(default=MemModelConfig(), roles={})


def _legacy_llm_provider(provider: object) -> str | None:
    if not provider:
        return None
    provider_name = str(provider)
    if provider_name in MEMORY_PROVIDER_PLUGINS:
        return None
    return provider_name


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
