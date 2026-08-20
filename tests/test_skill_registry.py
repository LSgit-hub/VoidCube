from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from voidcube.extensions.skills import registry
import voidcube.extensions.skills.catalog as skill_catalog
import voidcube.extensions.skills.tool as skills_tool
import voidcube.extensions.skills.manager as skills_manager
import voidcube.extensions.skills.hub as skills_hub


pytestmark = pytest.mark.unit


def _write_skill(root, name: str, description: str) -> object:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\nInstructions.\n",
        encoding="utf-8",
    )
    return path


def test_registry_schema_and_upsert_are_idempotent(tmp_path):
    root = tmp_path / "skills"
    skill_file = _write_skill(root, "demo", "Demo skill")
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)

    assert registry.refresh_registry([spec], path=db) == {
        "added": 1,
        "reparsed": 0,
        "reused": 0,
        "removed": 0,
        "errors": 0,
    }
    assert registry.refresh_registry([spec], path=db)["reused"] == 1

    connection = registry.open_registry(db)
    try:
        rows = registry.query_skills(connection)
        assert len(rows) == 1
        assert rows[0]["file_path"] == str(skill_file.resolve())
        assert rows[0]["source"] == "home"
    finally:
        connection.close()


def test_touch_reuses_hash_but_same_size_content_change_reparses(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    skill_file = _write_skill(root, "demo", "Alpha")
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)
    registry.refresh_registry([spec], path=db)

    original = registry._record_from_file
    parse_calls = []

    def tracked(*args, **kwargs):
        parse_calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, "_record_from_file", tracked)
    current = skill_file.stat()
    os.utime(skill_file, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))
    result = registry.refresh_registry([spec], path=db)
    assert result["reused"] == 1
    assert result["reparsed"] == 0
    assert parse_calls == []

    skill_file.write_text(
        "---\nname: demo\ndescription: Bravo\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    result = registry.refresh_registry([spec], path=db)
    assert result["reparsed"] == 1
    assert len(parse_calls) == 1
    connection = registry.open_registry(db)
    try:
        assert registry.query_skills(connection)[0]["description"] == "Bravo"
    finally:
        connection.close()


def test_refresh_removes_deleted_files_and_keeps_lifecycle_metadata(tmp_path):
    root = tmp_path / "skills"
    skill_file = _write_skill(root, "demo", "Demo skill")
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)
    registry.refresh_registry([spec], path=db)

    connection = registry.open_registry(db)
    try:
        registry.set_lifecycle_metadata(connection, skill_file, deprecated=True, supersedes="replacement")
    finally:
        connection.close()

    skill_file.write_text(
        "---\nname: demo\ndescription: Updated skill\n---\n\nInstructions.\n",
        encoding="utf-8",
    )
    registry.refresh_registry([spec], path=db)
    connection = registry.open_registry(db)
    try:
        row = registry.query_skills(connection)[0]
        assert row["deprecated"] == 1
        assert row["supersedes"] == "replacement"
    finally:
        connection.close()

    skill_file.unlink()
    assert registry.refresh_registry([spec], path=db)["removed"] == 1
    connection = registry.open_registry(db)
    try:
        assert registry.query_skills(connection) == []
    finally:
        connection.close()


def test_external_roots_can_have_same_skill_name(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_skill(first, "same", "First")
    _write_skill(second, "same", "Second")
    db = tmp_path / "registry.sqlite3"

    result = registry.refresh_registry(
        [
            registry.DiscoveryRoot(first, "external", 2),
            registry.DiscoveryRoot(second, "external", 3),
        ],
        path=db,
    )
    assert result["added"] == 2
    connection = registry.open_registry(db)
    try:
        rows = registry.query_skills(connection, name="same")
        assert [row["description"] for row in rows] == ["First", "Second"]
    finally:
        connection.close()


def test_skills_list_uses_registry_metadata(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "listed", "Listed description")
    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(skill_catalog, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(skill_catalog, "get_disabled_skill_names", lambda: set())

    payload = json.loads(skills_tool.skills_list())

    assert payload["success"] is True
    assert payload["skills"] == [
        {"name": "listed", "description": "Listed description", "category": None}
    ]


def test_skills_list_falls_back_when_registry_fails(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "fallback", "Fallback description")
    monkeypatch.setattr(skill_catalog, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(
        skills_tool.skills_registry,
        "refresh_and_query",
        lambda _paths: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    payload = json.loads(skills_tool.skills_list())

    assert payload["success"] is True
    assert payload["skills"] == [
        {"name": "fallback", "description": "Fallback description", "category": None}
    ]


def test_skills_list_fallback_preserves_nested_category_path(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    _write_skill(root / "operations" / "release", "deploy", "Deploy description")
    monkeypatch.setattr(skill_catalog, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(
        skills_tool.skills_registry,
        "refresh_and_query",
        lambda _paths: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    payload = json.loads(skills_tool.skills_list())

    assert payload["success"] is True
    assert payload["skills"] == [
        {
            "name": "deploy",
            "description": "Deploy description",
            "category": "operations/release",
        }
    ]


def test_refresh_and_query_does_not_expose_stale_missing_files(tmp_path):
    root = tmp_path / "skills"
    skill_file = _write_skill(root, "stale", "Stale")
    db = tmp_path / "registry.sqlite3"
    registry.refresh_and_query([root], path=db)
    skill_file.unlink()

    assert registry.refresh_and_query([root], path=db) == []


def test_skill_manage_refreshes_index_after_create(monkeypatch, tmp_path):
    monkeypatch.setattr(skills_manager, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skills_manager, "_security_scan_skill", lambda _path: None)
    content = "---\nname: managed\ndescription: Managed\n---\n\nInstructions.\n"

    payload = json.loads(skills_manager.skill_manage("create", "managed", content=content))

    assert payload["success"] is True
    connection = registry.open_registry(tmp_path / ".skills_registry.sqlite3")
    try:
        assert registry.query_skills(connection, name="managed")[0]["description"] == "Managed"
    finally:
        connection.close()


def test_skill_manage_updates_lifecycle_metadata(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "managed", "Managed")
    monkeypatch.setattr(skills_manager, "SKILLS_DIR", skills_root)
    monkeypatch.setattr(skill_catalog, "get_all_skills_dirs", lambda: [skills_root])

    payload = json.loads(
        skills_manager.skill_manage(
            "lifecycle",
            "managed",
            deprecated=True,
            supersedes="replacement",
        )
    )

    assert payload["success"] is True
    connection = registry.open_registry(tmp_path / ".skills_registry.sqlite3")
    try:
        row = registry.query_skills(connection, name="managed")[0]
        assert row["deprecated"] == 1
        assert row["supersedes"] == "replacement"
    finally:
        connection.close()

    cleared = json.loads(
        skills_manager.skill_manage(
            "lifecycle",
            "managed",
            deprecated=False,
            clear_supersedes=True,
        )
    )
    assert cleared["success"] is True
    connection = registry.open_registry(tmp_path / ".skills_registry.sqlite3")
    try:
        row = registry.query_skills(connection, name="managed")[0]
        assert row["deprecated"] == 0
        assert row["supersedes"] is None
    finally:
        connection.close()


def test_hub_refresh_hook_is_best_effort(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(skills_hub, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(
        skills_hub,
        "refresh_catalog_index",
        lambda **kwargs: calls.append(kwargs),
        raising=False,
    )
    # The hook imports the function from registry, so patch that boundary.
    monkeypatch.setattr(registry, "refresh_catalog_index", lambda **kwargs: calls.append(kwargs))

    skills_hub._refresh_skill_registry()

    assert calls


def test_hot_refresh_skips_reading_file_content(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(root, "demo", "Demo skill")
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)
    registry.refresh_registry([spec], path=db)

    # 快速路径（mtime+size 预检）生效后，热刷新绝不该触碰文件内容。
    # 若有人回退到无条件 read_bytes，这里直接抛错暴露回归——机器无关的强守卫。
    def boom(*_args, **_kwargs):
        raise RuntimeError("read_bytes was called on hot refresh")

    monkeypatch.setattr(Path, "read_bytes", boom)
    result = registry.refresh_registry([spec], path=db)

    assert result == {"added": 0, "reparsed": 0, "reused": 1, "removed": 0, "errors": 0}


def test_hot_refresh_is_not_pathologically_slow(tmp_path):
    root = tmp_path / "skills"
    for index in range(30):
        _write_skill(root, f"skill-{index:02d}", f"Skill {index}")
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)
    registry.refresh_registry([spec], path=db)  # 冷：全部入库
    registry.refresh_registry([spec], path=db)  # 预热（消除缓存/懒加载噪声）

    start = time.perf_counter()
    registry.refresh_registry([spec], path=db)
    elapsed = time.perf_counter() - start

    # 宽松阈值：30 文件本地实测 ~5ms。阈值只防秒级退化
    # （如误回退到全量重读/重解析），不追求精确值以免跨机器抖动误报。
    assert elapsed < 0.1, f"hot refresh took {elapsed * 1000:.1f} ms"
