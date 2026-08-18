from __future__ import annotations

from src.voidcube.extensions.skills import sync as skill_sync


def test_skill_sync_copies_then_preserves_user_modified_skill(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    source = bundled / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\n---\nsource\n", encoding="utf-8")
    monkeypatch.setattr(skill_sync, "_bundled_dir", lambda: bundled)
    monkeypatch.setattr(skill_sync, "_paths", lambda: (user, user / ".bundled_manifest"))

    first = skill_sync.sync_skills(quiet=True)
    assert first["copied"] == ["demo"]
    destination = user / "demo"
    (destination / "SKILL.md").write_text("---\nname: demo\n---\ncustom\n", encoding="utf-8")
    (source / "SKILL.md").write_text("---\nname: demo\n---\nupdated\n", encoding="utf-8")

    second = skill_sync.sync_skills(quiet=True)
    assert second["user_modified"] == ["demo"]
    assert "custom" in (destination / "SKILL.md").read_text(encoding="utf-8")
