from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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
        configured_base_url = str(llm.get("base_url") or "").strip()
        configured_api_key_env = str(llm.get("api_key_env") or "").strip()
        base_url = configured_base_url or str(defaults["base_url"])
        api_key_env = configured_api_key_env or str(defaults["api_key_env"])
        if (
            provider != "openai"
            and configured_api_key_env == PROVIDER_DEFAULTS["openai"]["api_key_env"]
        ):
            api_key_env = str(defaults["api_key_env"])
        # Legacy configs sometimes mirrored memory.llm.* from the main
        # agent provider and pointed Mem back into the local Gateway.
        # That causes API-B (Mem/supervisor) calls to re-enter API-A's
        # /v1/chat/completions surface and leak the wrong model string
        # into active-agent execution.  Normalize such loopback URLs back
        # to the provider's direct endpoint.
        if _is_local_gateway_loop_base_url(base_url):
            base_url = str(defaults["base_url"])
            if not configured_api_key_env:
                api_key_env = str(defaults["api_key_env"])
        return cls(
            provider=provider,
            model=str(model) if model else None,
            api_key_env=api_key_env,
            base_url=base_url,
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


def resolve_mem_llm_client(role: str = "default"):
    """Single source of truth for Mem's LLM client construction.

    All Mem-related LLM callers (memory service, supervisor endogenous
    drive, Tier 1→Tier 2 bridge, etc.) must go through this function.
    That way the CLI ``/api [1][2][3]`` command — which writes to
    ``memory.llm.*`` in voidcube config — is the *only* knob that needs
    to be turned to retarget Mem's model, base URL, and key env.

    Args:
        role: Which role-specific config to resolve (see
            ``MEM_MODEL_ROLES``).  Falls back to ``"default"`` when the
            role is not present in the config.

    Returns:
        ``(client, model_name)``.  ``client`` is ``None`` when no API
        key can be resolved (caller must degrade to heuristic /
        mechanical path).  ``model_name`` is always populated so the
        caller can log which model was selected.
    """
    import logging
    import os

    logger = logging.getLogger("memai.resolver")

    mem_cfg: MemModelConfig | None = None
    try:
        config_set = load_voidcube_mem_model_config_set()
        mem_cfg = config_set.for_role(role) if role else config_set.default
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to load MemModelConfigSet: %s", exc)

    if mem_cfg is not None:
        # Always use the config's model and base_url; only the API key
        # may need a fallback when api_key_env is not set.
        model = mem_cfg.model or "deepseek-chat"
        base_url = (mem_cfg.base_url or "https://api.deepseek.com/v1").rstrip("/")
        config_source = f"memory.llm.{role or 'default'} (provider={mem_cfg.provider})"
        if mem_cfg.api_key_env:
            api_key = os.environ.get(mem_cfg.api_key_env, "").strip()
        else:
            api_key = ""
    else:
        # Legacy fallback — keep older env-var driven installs working
        # when the voidcube config has no memory.llm block at all.
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
        base_url = os.environ.get(
            "MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1"
        ).rstrip("/")
        config_source = "env fallback"

    if not api_key and mem_cfg is not None and mem_cfg.provider == "ollama":
        api_key = "no-key-required"
    if not api_key:
        return None, model

    try:
        # Imported lazily so callers that don't need the LLM don't have
        # to pay for the openai/httpx import chain.
        from memai.llm_client import OpenAICompatibleLLMClient

        client = OpenAICompatibleLLMClient(
            model=model, api_key=api_key, base_url=base_url
        )
        logger.debug(
            "Mem LLM client resolved via %s (role=%s, model=%s)",
            config_source, role or "default", model,
        )
        return client, model
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to build Mem LLM client: %s", exc)
        return None, model


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


def _is_local_gateway_loop_base_url(base_url: str) -> bool:
    try:
        parsed = urlparse(str(base_url).strip())
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return False
    return port == 6000
