from __future__ import annotations

import json

import pytest

import agent.prompt_builder as prompt_builder


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _write_skill(root, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n",
        encoding="utf-8",
    )


def test_skills_snapshot_covers_all_roots_and_invalidates_on_change(
    monkeypatch, tmp_path
):
    local_root = tmp_path / "local"
    bundled_root = tmp_path / "bundled"
    _write_skill(local_root, "local-skill", "Local description")
    _write_skill(bundled_root, "bundled-skill", "Bundled description")
    _write_skill(bundled_root, "local-skill", "Shadowed description")
    snapshot_path = tmp_path / "snapshot.json"

    monkeypatch.setattr(
        prompt_builder,
        "get_all_skills_dirs",
        lambda: [local_root, bundled_root],
    )
    monkeypatch.setattr(
        prompt_builder, "get_disabled_skill_names", lambda: set()
    )
    monkeypatch.setattr(
        prompt_builder, "_skills_prompt_snapshot_path", lambda: snapshot_path
    )
    prompt_builder.clear_skills_system_prompt_cache()

    first = prompt_builder.build_skills_system_prompt()

    assert "local-skill: Local description" in first
    assert "Shadowed description" not in first
    assert "bundled-skill: Bundled description" in first
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["version"] == prompt_builder._SKILLS_SNAPSHOT_VERSION
    assert len(snapshot["manifest"]) == 2

    prompt_builder.clear_skills_system_prompt_cache()
    original_parse = prompt_builder._parse_skill_file
    monkeypatch.setattr(
        prompt_builder,
        "_parse_skill_file",
        lambda _path: pytest.fail("valid snapshot reparsed a skill file"),
    )

    assert prompt_builder.build_skills_system_prompt() == first

    _write_skill(bundled_root, "new-skill", "New bundled description")
    prompt_builder.clear_skills_system_prompt_cache()
    monkeypatch.setattr(
        prompt_builder,
        "_parse_skill_file",
        original_parse,
    )

    refreshed = prompt_builder.build_skills_system_prompt()

    assert "new-skill: New bundled description" in refreshed
