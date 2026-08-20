from __future__ import annotations

import pytest

import voidcube.runtime.agent.prompt_builder as prompt_builder


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
    registry_path = tmp_path / "registry.sqlite3"

    monkeypatch.setattr(
        prompt_builder,
        "get_all_skills_dirs",
        lambda: [local_root, bundled_root],
    )
    monkeypatch.setattr(
        prompt_builder, "get_disabled_skill_names", lambda: set()
    )
    monkeypatch.setattr(
        prompt_builder, "_skills_registry_path", lambda: registry_path
    )
    prompt_builder.clear_skills_system_prompt_cache()

    first = prompt_builder.build_skills_system_prompt()

    assert "local-skill: Local description" in first
    assert "Shadowed description" not in first
    assert "bundled-skill: Bundled description" in first
    assert registry_path.exists()

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


def test_registry_is_primary_metadata_source(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "registry-skill", "Indexed description")
    registry_path = tmp_path / "registry.sqlite3"
    registry_path = tmp_path / ".skills_registry.sqlite3"
    monkeypatch.setattr(prompt_builder, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(prompt_builder, "get_disabled_skill_names", lambda: set())
    monkeypatch.setattr(prompt_builder, "_skills_registry_path", lambda: registry_path)
    prompt_builder.clear_skills_system_prompt_cache()

    result = prompt_builder.build_skills_system_prompt()

    assert "registry-skill: Indexed description" in result
    assert registry_path.exists()


def test_root_skill_category_matches_registry_when_fallback_is_used(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "root-skill", "Root description")
    registry_path = tmp_path / "registry.sqlite3"
    monkeypatch.setattr(prompt_builder, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(prompt_builder, "get_disabled_skill_names", lambda: set())
    monkeypatch.setattr(prompt_builder, "_skills_registry_path", lambda: registry_path)
    prompt_builder.clear_skills_system_prompt_cache()

    indexed = prompt_builder.build_skills_system_prompt()

    monkeypatch.setattr(
        prompt_builder.skills_registry,
        "refresh_and_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    prompt_builder.clear_skills_system_prompt_cache()
    fallback = prompt_builder.build_skills_system_prompt()

    assert "  general:\n    - root-skill: Root description" in indexed
    assert fallback == indexed
