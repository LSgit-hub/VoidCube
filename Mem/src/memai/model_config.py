from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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
        "provider_profile": "openai",
    },
}

MEM_MODEL_ROLES = frozenset(
    {
        "default",
        "extraction",
        "summarization",
        "governance_summary",
        "governance_reasoner",
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

        provider = str(llm.get("provider") or "openai")
        defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
        model = llm.get("model") or None
        configured_base_url = str(llm.get("base_url") or "").strip()
        configured_api_key_env = str(llm.get("api_key_env") or "").strip()
        base_url = configured_base_url or str(defaults["base_url"])
        api_key_env = configured_api_key_env or str(defaults["api_key_env"])
        if (
            provider != "openai"
            and configured_api_key_env == PROVIDER_DEFAULTS["openai"]["api_key_env"]
        ):
            api_key_env = str(defaults["api_key_env"])
        # Invalid/stale configs sometimes mirrored memory.llm.* from the
        # main agent provider and pointed Mem back into the local Gateway.
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


@dataclass(frozen=True, slots=True)
class MemLLMResolution:
    """Non-secret outcome of resolving a Mem LLM client."""

    client: Any | None
    model: str
    status: str
    detail: str = ""


def load_voidcube_mem_model_config() -> MemModelConfig:
    return load_voidcube_mem_model_config_set().default


def load_voidcube_mem_model_config_set() -> MemModelConfigSet:
    try:
        from VoidCube_app.config import load_config

        return MemModelConfigSet.from_voidcube_config(load_config())
    except Exception:
        return MemModelConfigSet(default=MemModelConfig(), roles={})


def resolve_mem_llm(role: str = "default") -> MemLLMResolution:
    """Resolve a Mem LLM client and retain an actionable failure reason."""
    import logging

    logger = logging.getLogger("memai.resolver")

    try:
        config_set = load_voidcube_mem_model_config_set()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to load MemModelConfigSet: %s", exc)
        config_set = MemModelConfigSet(default=MemModelConfig(), roles={})

    mem_cfg = config_set.for_role(role) if role else config_set.default
    model = mem_cfg.model or "deepseek-chat"
    base_url = (mem_cfg.base_url or "https://api.deepseek.com/v1").rstrip("/")
    config_source = f"memory.llm.{role or 'default'} (provider={mem_cfg.provider})"

    try:
        from agent.integration_policy import require_active_integration

        require_active_integration(mem_cfg.provider, model, base_url)
    except ImportError:
        pass
    except ValueError as exc:
        detail = str(exc) or "blocked by project integration policy"
        logger.warning(
            "Mem LLM configuration is blocked by project integration policy "
            "(%s, model=%s, base_url=%s): %s",
            config_source,
            model,
            base_url,
            detail,
        )
        return MemLLMResolution(None, model, "policy_blocked", detail)

    api_key = _resolve_mem_api_key(mem_cfg)

    if not api_key and mem_cfg.provider == "ollama":
        api_key = "no-key-required"
    if not api_key:
        detail = f"no usable credential found via {mem_cfg.api_key_env or 'provider store'}"
        logger.warning("Mem LLM credential unavailable (%s): %s", config_source, detail)
        return MemLLMResolution(None, model, "api_key_unavailable", detail)

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
        return MemLLMResolution(client, model, "ready")
    except Exception as exc:  # pragma: no cover - defensive
        detail = type(exc).__name__
        logger.warning(
            "Failed to build Mem LLM client via %s (model=%s): %s",
            config_source,
            model,
            detail,
        )
        return MemLLMResolution(None, model, "client_initialization_failed", detail)


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
    resolution = resolve_mem_llm(role=role)
    return resolution.client, resolution.model


def _resolve_mem_api_key(mem_cfg: MemModelConfig) -> str:
    if mem_cfg.api_key_env:
        try:
            from VoidCube_app.config import get_env_value

            raw_api_key = get_env_value(mem_cfg.api_key_env) or ""
        except Exception:
            import os

            raw_api_key = os.environ.get(mem_cfg.api_key_env, "")
        api_key = _first_usable_secret(raw_api_key)
        if api_key:
            return api_key

    provider = str(mem_cfg.provider or "").strip().lower()
    if not provider:
        return ""

    try:
        from VoidCube_app.provider_auth import resolve_api_key_provider_credentials

        creds = resolve_api_key_provider_credentials(provider) or {}
        api_key = _first_usable_secret(str(creds.get("api_key") or ""))
        if api_key:
            return api_key
    except Exception:
        pass

    try:
        from agent.credential_pool import load_pool

        pool = load_pool(provider)
        entry = pool.select() if pool and pool.has_credentials() else None
        if entry is not None:
            return _first_usable_secret(
                str(getattr(entry, "runtime_api_key", "") or ""),
                str(getattr(entry, "access_token", "") or ""),
            )
    except Exception:
        pass

    return ""


def _first_usable_secret(*values: object) -> str:
    try:
        from VoidCube_app.provider_auth import has_usable_secret
    except Exception:
        def has_usable_secret(value: str) -> bool:  # type: ignore[no-redef]
            return bool(str(value or "").strip())

    for value in values:
        candidate = str(value or "").strip()
        if has_usable_secret(candidate):
            return candidate
    return ""


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
