from __future__ import annotations

from pathlib import Path

import pytest

from VoidCube_core.runtime_paths import (
    get_legacy_project_runtime_layout,
    get_runtime_layout,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_runtime_layout_is_scoped_under_voidcube_home_without_side_effects(
    tmp_path: Path,
) -> None:
    home = tmp_path / "profile-home"

    layout = get_runtime_layout(home)

    assert layout.runtime_root == home / "runtime"
    assert layout.memory_db == home / "runtime" / "memory" / "memory.db"
    assert layout.supervisor_root == home / "runtime" / "supervisor"
    assert layout.supervisor_governance_log == (
        home / "runtime" / "supervisor" / "mem_governance.jsonl"
    )
    assert layout.body_root == home / "runtime" / "body"
    assert layout.body_slots_root == home / "runtime" / "body" / "slots"
    assert layout.body_registry == home / "runtime" / "body" / "registry.json"
    assert layout.body_active_pointer == home / "runtime" / "body" / "active.json"
    assert layout.session_db == home / "state.db"
    assert home.exists() is False


def test_runtime_layout_honors_voidcube_home_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "custom-home"
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    layout = get_runtime_layout()

    assert layout.home == home
    assert layout.runtime_root == home / "runtime"


def test_legacy_layout_only_describes_known_project_root_sources(
    tmp_path: Path,
) -> None:
    legacy = get_legacy_project_runtime_layout(tmp_path)

    assert legacy.memory_db == tmp_path / "memory.db"
    assert legacy.supervisor_root == tmp_path / ".soul-runtime"
    assert legacy.body_slots_root == tmp_path / ".body-slots"
    assert legacy.body_registry == tmp_path / ".body-registry.json"
    assert legacy.body_active_pointer == tmp_path / ".body-active.json"
    assert legacy.mem_governance_log == tmp_path / "mem_governance.jsonl"
