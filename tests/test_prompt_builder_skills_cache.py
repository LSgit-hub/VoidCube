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


def test_long_description_keeps_when_to_use_clause(monkeypatch, tmp_path):
    """回归：>60 字符的描述必须保留结尾的"何时使用"触发条件。

    旧实现把描述硬截为 60 字符，导致大多数技能的触发子句从索引里消失，
    相关技能无法被自动发现。
    """
    root = tmp_path / "skills"
    description = (
        "审查 VoidCube 的代码、技能、插件和 Pull Request，重点检查行为回归、"
        "所有权边界、安全性、测试证据、运行时兼容性和无效兼容分支。"
        "需要做代码审查或变更复核时使用。"
    )
    assert len(description) > 60
    _write_skill(root, "long-skill", description)
    registry_path = tmp_path / ".skills_registry.sqlite3"
    monkeypatch.setattr(prompt_builder, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(prompt_builder, "get_disabled_skill_names", lambda: set())
    monkeypatch.setattr(prompt_builder, "_skills_registry_path", lambda: registry_path)
    prompt_builder.clear_skills_system_prompt_cache()

    result = prompt_builder.build_skills_system_prompt()

    assert "需要做代码审查或变更复核时使用。" in result


def test_index_mode_keeps_mandatory_envelope_and_trigger_clause(monkeypatch, tmp_path):
    """回归：运行时切到 mode="index" 后，强制包裹与触发子句都不能丢。

    紧凑模式此前只输出清单行（丢掉加载规则与 <available_skills> 围栏），
    直接切换会静默削弱技能加载行为；描述侧也必须保留\"何时使用\"触发子句，
    否则等于把技能发现失败换了个开关重新打开。
    """
    root = tmp_path / "skills"
    description = (
        "审查 VoidCube 的代码、技能、插件和 Pull Request，重点检查行为回归、所有权边界、安全性、"
        "测试证据、运行时兼容性和无效兼容分支，覆盖 Python 服务、CLI 命令、插件 web 资源、"
        "技能文档、测试夹具与打包契约等所有变更面，并在发现回归时给出最小复现步骤与已验证的修法，"
        "必要时补充回归测试。需要做代码审查或变更复核时使用。"
    )
    assert len(description) > prompt_builder._INDEX_LINE_BUDGET, "前提：描述长于索引行预算"
    _write_skill(root, "long-skill", description)
    registry_path = tmp_path / ".skills_registry.sqlite3"
    monkeypatch.setattr(prompt_builder, "get_all_skills_dirs", lambda: [root])
    monkeypatch.setattr(prompt_builder, "get_disabled_skill_names", lambda: set())
    monkeypatch.setattr(prompt_builder, "_skills_registry_path", lambda: registry_path)
    prompt_builder.clear_skills_system_prompt_cache()

    compact = prompt_builder.build_skills_system_prompt(mode="index")
    full = prompt_builder.build_skills_system_prompt(mode="full")

    for marker in (
        "## Skills (mandatory)",
        "MUST load it with skill_view",
        "<available_skills>",
        "</available_skills>",
        "Only proceed without loading a skill",
    ):
        assert marker in compact, marker
    assert "需要做代码审查或变更复核时使用。" in compact
    assert len(compact) < len(full)


def test_index_line_does_not_cut_inside_technical_token():
    """回归：紧凑索引不得在 llama.cpp / *.run 之类技术词的英文点号处切断。"""
    line = prompt_builder._build_skills_index_line(
        "llama-cpp", "使用 llama.cpp 加载并运行 GGUF 模型。第二句应被省略。"
    )

    assert "llama.cpp" in line
    assert "第二句" not in line


def test_index_line_keeps_trailing_trigger_clause_for_long_cjk_description():
    """回归：中文长单句描述把『何时使用』写在末尾，紧凑索引不能把触发条件砍掉。"""
    desc = (
        "审计和整理 VoidCube 项目的长期记录、决策说明与维护文档，"
        "并判断记录是否仍有未来价值、是否已被当前代码或文档取代、以及是否应归档或删除时使用。"
    )
    line = prompt_builder._build_skills_index_line("archive-agent-notes", desc)

    assert "时使用" in line
    assert line.rstrip().endswith("时使用。")
    assert len(line) <= prompt_builder._INDEX_LINE_BUDGET


def test_index_line_stays_bounded_without_trigger_clause():
    """无触发子句时只保留首句头部，且行长度有界（不引入无意义尾段）。"""
    desc = (
        "在 VoidCube 中寻找可验证的简化机会，删除重复状态、失效兼容逻辑、过度抽象，"
        "以及与当前行为脱节的文档，同时保持现有边界与测试证据不被削弱。"
    )
    assert len(desc) > prompt_builder._INDEX_HEAD_BUDGET, "前提：描述长于头部预算"

    line = prompt_builder._build_skills_index_line("find-simplifications", desc)

    assert line.rstrip().endswith("...")
    assert len(line) <= prompt_builder._INDEX_LINE_BUDGET
