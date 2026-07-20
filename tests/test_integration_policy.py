from __future__ import annotations

from pathlib import Path

import pytest

from agent.integration_policy import RETIRED_INTEGRATION_MARKERS
from agent.auxiliary_client import _build_call_kwargs
from memai.model_config import (
    MemModelConfig,
    MemModelConfigSet,
    resolve_mem_llm_client,
)
from tools.skills_guard import scan_skill, should_allow_install
from VoidCube_cli import models as model_catalog


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke

_TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SURFACE_ROOTS = (
    "agent",
    "docs",
    "Mem/src",
    "plugins",
    "scripts",
    "skills",
    "systems",
    "tests",
    "tools",
    "VoidCube_cli",
    "VoidCube_core",
)
_RUNTIME_SURFACE_ROOTS = (
    ".body-slots",
    ".soul-runtime",
    "state",
)
_ROOT_FILES = (
    ".body-active.json",
    ".body-registry.json",
    ".env.example",
    "cli.py",
    "config.yaml",
    "pyproject.toml",
    "README.md",
    "run_agent.py",
    "voidcube.py",
)


def _runtime_text_files() -> list[Path]:
    files = [ROOT / name for name in _ROOT_FILES if (ROOT / name).is_file()]
    for relative_root in (*_SURFACE_ROOTS, *_RUNTIME_SURFACE_ROOTS):
        directory = ROOT / relative_root
        if not directory.is_dir():
            continue
        files.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.name != "AGENTS.md"
            and path.suffix.casefold() in _TEXT_SUFFIXES
        )
    return sorted(set(files))


@pytest.mark.unit
def test_runtime_and_loadable_skills_have_no_retired_integration_markers():
    violations: list[str] = []
    for path in _runtime_text_files():
        relative = path.relative_to(ROOT).as_posix()
        normalized_path = relative.casefold()
        content = path.read_text(encoding="utf-8", errors="ignore").casefold()
        if any(
            marker in normalized_path or marker in content
            for marker in RETIRED_INTEGRATION_MARKERS
        ):
            violations.append(relative)

    assert violations == []


@pytest.mark.unit
def test_workspace_specific_retired_paths_are_absent():
    retired_config_dir = ROOT / ("." + "".join(("clau", "de")))
    assert not retired_config_dir.exists()
    assert not (ROOT / "skills" / "index-cache").exists()

    root_name_violations = sorted(
        path.name
        for path in ROOT.iterdir()
        if any(marker in path.name.casefold() for marker in RETIRED_INTEGRATION_MARKERS)
    )
    assert root_name_violations == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "marker",
    RETIRED_INTEGRATION_MARKERS,
    ids=[f"retired-{index}" for index, _ in enumerate(RETIRED_INTEGRATION_MARKERS)],
)
def test_skill_guard_never_allows_retired_integration_even_with_force(
    tmp_path: Path,
    marker: str,
):
    skill_dir = tmp_path / "policy-probe"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: policy-probe\n---\nprovider: {marker}\n",
        encoding="utf-8",
    )

    result = scan_skill(skill_dir, source="official")
    allowed, reason = should_allow_install(result, force=True)

    assert allowed is False
    assert "non-overridable" in reason


@pytest.mark.unit
def test_auxiliary_request_rejects_retired_model_before_transport():
    marker = RETIRED_INTEGRATION_MARKERS[1]

    with pytest.raises(ValueError, match="retired by project policy"):
        _build_call_kwargs(
            "custom",
            f"vendor/{marker}-model",
            [{"role": "user", "content": "hello"}],
        )


@pytest.mark.unit
def test_auxiliary_request_reuses_shared_tools_tokens_and_extra_body():
    memory_tool = {"type": "function", "function": {"name": "memory"}}

    kwargs = _build_call_kwargs(
        "custom",
        "gpt-5",
        [{"role": "user", "content": "flush"}],
        temperature=0.3,
        max_tokens=5120,
        tools=[memory_tool],
        timeout=45.0,
        extra_body={"tags": ["purpose=memory"]},
        base_url="https://api.openai.com/v1",
    )

    assert kwargs["max_completion_tokens"] == 5120
    assert kwargs["temperature"] == 0.3
    assert kwargs["timeout"] == 45.0
    assert kwargs["extra_body"] == {"tags": ["purpose=memory"]}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["function"]["name"] == "memory"
    assert "reasoning" not in kwargs["extra_body"]


@pytest.mark.unit
def test_provider_catalog_hides_retired_model_ids(monkeypatch):
    marker = RETIRED_INTEGRATION_MARKERS[2]
    monkeypatch.setattr(
        model_catalog,
        "_provider_model_ids_unfiltered",
        lambda _provider, force_refresh=False: [
            "safe/model",
            f"vendor/{marker}-model",
        ],
    )

    assert model_catalog.provider_model_ids("test") == ["safe/model"]


@pytest.mark.unit
def test_mem_resolver_blocks_retired_model_before_credentials(monkeypatch):
    marker = RETIRED_INTEGRATION_MARKERS[0]
    blocked = MemModelConfig(
        provider="openai",
        model=f"vendor/{marker}-model",
        base_url="https://api.example/v1",
    )
    monkeypatch.setattr(
        "memai.model_config.load_voidcube_mem_model_config_set",
        lambda: MemModelConfigSet(default=blocked, roles={}),
    )

    client, model = resolve_mem_llm_client()

    assert client is None
    assert model == blocked.model
