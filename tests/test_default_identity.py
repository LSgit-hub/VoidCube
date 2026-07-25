from __future__ import annotations

import pytest

from agent.prompt_builder import (
    DEFAULT_AGENT_IDENTITY,
    ensure_persistent_identity_guidance,
    has_canonical_memory_tools,
)
from VoidCube_cli.default_soul import (
    DEFAULT_IDENTITY_PROMPT,
    DEFAULT_SOUL_MD,
    PERSISTENT_IDENTITY_GUIDANCE,
)


pytestmark = pytest.mark.unit


def test_default_identity_distinguishes_persistent_self_from_runtime_carrier():
    assert DEFAULT_AGENT_IDENTITY == DEFAULT_IDENTITY_PROMPT
    assert DEFAULT_SOUL_MD.startswith(DEFAULT_IDENTITY_PROMPT)
    assert "persistent identity is 星子" in DEFAULT_IDENTITY_PROMPT
    assert "model, provider, and Agent runtime are replaceable carriers" in (
        DEFAULT_IDENTITY_PROMPT
    )
    assert "never introduce a carrier's vendor identity as your own" in (
        DEFAULT_IDENTITY_PROMPT
    )
    assert "no prior memory was ever saved" in PERSISTENT_IDENTITY_GUIDANCE


def test_legacy_custom_soul_is_augmented_in_memory_without_duplication():
    legacy = "You are Voidcube Agent, an intelligent AI assistant."

    augmented = ensure_persistent_identity_guidance(legacy)

    assert augmented.startswith(legacy)
    assert augmented.endswith(PERSISTENT_IDENTITY_GUIDANCE)
    assert ensure_persistent_identity_guidance(augmented) == augmented


def test_memory_guidance_uses_only_the_canonical_mem_tool_surface():
    assert has_canonical_memory_tools({"mem_search", "mem_remember"}) is True
    assert has_canonical_memory_tools({"memory"}) is False
