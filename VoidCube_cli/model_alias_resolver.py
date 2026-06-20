"""
模型别名解析 — 统一别名解析、Provider 感知解析、可用性预检。

Replaces MODEL_ALIASES from model_switch.py with a cleaner interface.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from VoidCube_core.constants import get_VoidCube_home

logger = logging.getLogger(__name__)


@dataclass
class ModelIdentity:
    vendor: str
    family: str


@dataclass
class ModelResolution:
    model_id: str
    provider_id: str
    base_url: str = ""
    resolved_via: str = ""
    is_available: bool = True
    conflict_providers: List[str] = field(default_factory=list)

    @property
    def full_id(self) -> str:
        if self.provider_id and self.model_id:
            return f"{self.provider_id}/{self.model_id}"
        return self.model_id


@dataclass
class ModelAliasEntry:
    alias: str
    identity: ModelIdentity
    preferred_provider: str = ""


_BUILTIN_ALIASES: Dict[str, ModelIdentity] = {
    "sonnet": ModelIdentity("anthropic", "claude-sonnet"),
    "opus": ModelIdentity("anthropic", "claude-opus"),
    "haiku": ModelIdentity("anthropic", "claude-haiku"),
    "claude": ModelIdentity("anthropic", "claude"),
    "gpt5": ModelIdentity("openai", "gpt-5"),
    "gpt": ModelIdentity("openai", "gpt"),
    "codex": ModelIdentity("openai", "codex"),
    "o3": ModelIdentity("openai", "o3"),
    "o4": ModelIdentity("openai", "o4"),
    "gemini": ModelIdentity("google", "gemini"),
    "deepseek": ModelIdentity("deepseek", "deepseek-chat"),
    "grok": ModelIdentity("x-ai", "grok"),
    "llama": ModelIdentity("meta-llama", "llama"),
    "qwen": ModelIdentity("qwen", "qwen"),
    "minimax": ModelIdentity("minimax", "minimax"),
    "nemotron": ModelIdentity("nvidia", "nemotron"),
    "kimi": ModelIdentity("moonshotai", "kimi"),
    "glm": ModelIdentity("z-ai", "glm"),
    "step": ModelIdentity("stepfun", "step"),
    "mimo": ModelIdentity("xiaomi", "mimo"),
    "trinity": ModelIdentity("arcee-ai", "trinity"),
}

_VENDOR_TO_PROVIDER: Dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "deepseek": "deepseek",
    "x-ai": "xai",
    "meta-llama": "openrouter",
    "qwen": "alibaba",
    "minimax": "minimax",
    "nvidia": "openrouter",
    "moonshotai": "kimi-coding",
    "z-ai": "zai",
    "stepfun": "openrouter",
    "xiaomi": "xiaomi",
    "arcee-ai": "openrouter",
}

_RECENT_MODELS_MAX = 5
_RECENT_MODELS_FILE = "recent_models.json"


class ModelAliasResolver:
    _instance: Optional["ModelAliasResolver"] = None
    _aliases: Dict[str, ModelIdentity]
    _recent: List[str]

    def __init__(self) -> None:
        self._aliases = dict(_BUILTIN_ALIASES)
        self._recent: List[str] = []

    @classmethod
    def get_instance(cls) -> "ModelAliasResolver":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load_recent()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def resolve(self, model_input: str, preferred_provider: str = "") -> ModelResolution:
        alias_match = self._aliases.get(model_input.lower())
        if alias_match:
            provider_id = preferred_provider or _VENDOR_TO_PROVIDER.get(alias_match.vendor, alias_match.vendor)
            model_id = alias_match.family
            return ModelResolution(
                model_id=model_id,
                provider_id=provider_id,
                resolved_via="alias",
                is_available=self.check_availability(provider_id),
            )
        if "/" in model_input:
            parts = model_input.split("/", 1)
            provider_id = parts[0]
            model_id = parts[1]
            return ModelResolution(
                model_id=model_id,
                provider_id=provider_id,
                resolved_via="explicit",
                is_available=self.check_availability(provider_id),
            )
        provider_id = preferred_provider or self._detect_provider_for_model(model_input)
        if provider_id:
            return ModelResolution(
                model_id=model_input,
                provider_id=provider_id,
                resolved_via="detection",
                is_available=self.check_availability(provider_id),
            )
        return ModelResolution(
            model_id=model_input,
            provider_id="",
            resolved_via="unknown",
            is_available=False,
        )

    def _detect_provider_for_model(self, model_id: str) -> str:
        from VoidCube_cli.models import _PROVIDER_MODELS
        for provider_id, models in _PROVIDER_MODELS.items():
            for m in models:
                if isinstance(m, str) and m == model_id:
                    return provider_id
                if isinstance(m, dict) and m.get("id") == model_id:
                    return provider_id
        lower = model_id.lower()
        if "claude" in lower:
            return "anthropic"
        if "gpt" in lower or "o3" in lower or "o4" in lower:
            return "openai"
        if "gemini" in lower:
            return "gemini"
        if "deepseek" in lower:
            return "deepseek"
        if "glm" in lower:
            return "zai"
        if "kimi" in lower:
            return "kimi-coding"
        return ""

    def list_models_for_provider(self, provider_id: str) -> List[ModelResolution]:
        try:
            from VoidCube_cli.models import _PROVIDER_MODELS
            models = _PROVIDER_MODELS.get(provider_id, [])
            result = []
            for m in models:
                if isinstance(m, str):
                    result.append(ModelResolution(
                        model_id=m, provider_id=provider_id,
                        is_available=self.check_availability(provider_id),
                    ))
                elif isinstance(m, dict):
                    mid = m.get("id", "")
                    if mid:
                        result.append(ModelResolution(
                            model_id=mid, provider_id=provider_id,
                            is_available=self.check_availability(provider_id),
                        ))
            return result
        except Exception as e:
            logger.debug(f"Failed to list models for {provider_id}: {e}")
            return []

    def check_availability(self, provider_id: str) -> bool:
        from VoidCube_cli.credential_manager import CredentialManager
        cm = CredentialManager.get_instance()
        status = cm.get_provider_status(provider_id)
        return status == "authenticated"

    def add_recent(self, model_full_id: str) -> None:
        if model_full_id in self._recent:
            self._recent.remove(model_full_id)
        self._recent.insert(0, model_full_id)
        self._recent = self._recent[:_RECENT_MODELS_MAX]
        self._save_recent()

    def get_recent(self) -> List[str]:
        return list(self._recent)

    def _load_recent(self) -> None:
        path = get_VoidCube_home() / _RECENT_MODELS_FILE
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._recent = data[:_RECENT_MODELS_MAX]
            except Exception:
                self._recent = []

    def _save_recent(self) -> None:
        path = get_VoidCube_home() / _RECENT_MODELS_FILE
        try:
            path.write_text(json.dumps(self._recent, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to save recent models: {e}")

    def add_alias(self, alias: str, identity: ModelIdentity) -> None:
        self._aliases[alias.lower()] = identity

    def load_aliases_from_config(self, aliases: Dict[str, Any]) -> None:
        for alias, value in aliases.items():
            if isinstance(value, dict):
                vendor = value.get("vendor", "")
                family = value.get("family", "")
                if vendor and family:
                    self._aliases[alias.lower()] = ModelIdentity(vendor=vendor, family=family)
