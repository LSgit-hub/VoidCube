from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

pytestmark = [pytest.mark.smoke, pytest.mark.unit]

ACTIVE_DOCS = {
    "API配置双槽与模型调用点.md",
    "CLI展示与gateway双槽设计.md",
    "README.md",
    "voidcube架构基线.md",
    "内生驱动核心设计.md",
    "全链路问题清单.md",
    "开发与验证.md",
    "项目架构与逻辑架构.md",
    "项目文件架构说明.md",
}

REMOVED_DOCS = {
    "endogenous-drive-body-upgrade-gap.md",
    "mem-llm-first-redesign.md",
    "mem-two-tier-renovation-plan.md",
    "全链条迁移日志.md",
}

STALE_AUTO_SEMANTICS = (
    "autonomous_chain_start_on_boot",
    "默认随服务启动自主链路",
    "自主链路保持常驻运行",
    "不是 Supervisor 自主链路主启停开关",
    "只接入主 CLI 内的本地观测/执行面",
)

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _mainline_markdown_files() -> list[Path]:
    return [
        ROOT / "README.md",
        ROOT / "systems" / "architecture.md",
        *sorted(DOCS.glob("*.md")),
    ]


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, flags=re.IGNORECASE):
        return None
    target = unquote(target.split("#", 1)[0]).strip()
    if not target:
        return None
    return (source.parent / target).resolve()


def test_docs_directory_contains_only_active_documents():
    actual = {path.name for path in DOCS.glob("*.md")}

    assert actual == ACTIVE_DOCS
    assert actual.isdisjoint(REMOVED_DOCS)


def test_mainline_markdown_links_resolve():
    broken: list[str] = []
    for source in _mainline_markdown_files():
        content = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(content):
            target = _local_link_target(source, raw_target)
            if target is not None and not target.exists():
                broken.append(
                    f"{source.relative_to(ROOT).as_posix()} -> {raw_target}"
                )

    assert broken == []


def test_auto_gate_semantics_are_consistent_in_mainline_docs():
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in _mainline_markdown_files()
    )
    baseline = (DOCS / "voidcube架构基线.md").read_text(encoding="utf-8")

    assert "自主链路门控初始为关闭" in baseline
    assert "当前 `/auto` 是临时启用开关" in baseline
    assert "`/auto-q` 是对应的临时停用开关" in baseline
    assert all(fragment not in combined for fragment in STALE_AUTO_SEMANTICS)
    assert all(name not in combined for name in REMOVED_DOCS)


def test_mainline_architecture_has_precise_collaboration_and_state_ownership():
    architecture = (DOCS / "项目架构与逻辑架构.md").read_text(encoding="utf-8")
    baseline = (DOCS / "voidcube架构基线.md").read_text(encoding="utf-8")
    systems_entry = (ROOT / "systems" / "architecture.md").read_text(encoding="utf-8")

    assert all(f"M{index} " not in architecture for index in range(6))
    assert "AIAgent -> mem Provider -> Gateway /api/mem/* -> Memory Service -> MemAI" in architecture
    assert "AutonomousChainStore" in architecture
    assert "GovernanceEventRepository" in architecture
    assert "任务状态变化必须先追加治理事件" in architecture
    assert "Supervisor -> VoidCubeExecutionFacade -> Adapter" in architecture
    assert "ToolExecutionCoordinator" in architecture
    assert "并行结果按模型原始顺序写回" in architecture
    assert "Gateway 是跨进程调用 Executor 的唯一标准入口" in baseline
    assert "Supervisor 进程内部通过" in baseline
    assert "VoidCubeExecutionFacade" in baseline
    assert "Agent 保持无状态" not in systems_entry
    assert "不拥有长期身份、治理裁决或跨会话事实真相" in systems_entry
