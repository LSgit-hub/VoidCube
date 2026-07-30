"""Skills hub command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class SkillRecord:
    name: str
    source: str
    trust_level: str


@dataclass(frozen=True, slots=True)
class SkillSearchResult:
    name: str
    description: str
    source: str
    trust_level: str
    tags: Sequence[str]


@dataclass(frozen=True, slots=True)
class SkillsCommandPorts:
    builtin_skills: Callable[[], Sequence[tuple[str, Sequence[str]]]]
    installed_skills: Callable[[], Sequence[SkillRecord]]
    search: Callable[[str], Sequence[SkillSearchResult]]
    install: Callable[[str], tuple[bool, str, str]]
    uninstall: Callable[[str], tuple[bool, str]]
    refresh_cache: Callable[[], None]
    emit: Callable[[str], None]


def handle_skills_command(request: ParsedCliCommand, *, ports: SkillsCommandPorts) -> None:
    parts = request.arguments.split(maxsplit=1)
    subcommand = parts[0] if parts else "help"
    value = parts[1].strip() if len(parts) > 1 else ""
    if subcommand == "list":
        _render_list(ports)
    elif subcommand == "search":
        _render_search(value, ports)
    elif subcommand == "install":
        _install(value, ports)
    elif subcommand == "uninstall":
        _uninstall(value, ports)
    else:
        _render_help(ports)


def _render_help(ports: SkillsCommandPorts) -> None:
    for line in (
        "", "  技能管理命令 (/skills)", "", "  用法:",
        "    /skills                 — 显示此帮助",
        "    /skills list            — 列出已安装的技能",
        "    /skills search <query>  — 搜索技能",
        "    /skills install <name>  — 安装技能",
        "    /skills uninstall <name> — 卸载技能", "",
    ):
        ports.emit(line)


def _render_list(ports: SkillsCommandPorts) -> None:
    ports.emit("\n  📦 内置技能:")
    builtins = ports.builtin_skills()
    if not builtins:
        ports.emit("    暂无内置技能")
    for category, names in builtins:
        ports.emit(f"    {category}:")
        for name in names:
            ports.emit(f"      - [{name}]")
    ports.emit("\n  🚀 通过技能中心安装的技能:")
    installed = ports.installed_skills()
    if not installed:
        ports.emit("    暂无通过技能中心安装的技能")
    for skill in installed:
        ports.emit(f"    [{skill.name}]")
        ports.emit(f"        来源: {skill.source}")
        ports.emit(f"        信任级别: {skill.trust_level}")
    ports.emit("")


def _render_search(query: str, ports: SkillsCommandPorts) -> None:
    ports.emit(f"\n  搜索技能: '{query}'")
    try:
        results = ports.search(query)
    except Exception as exc:
        ports.emit(f"    ❌ 搜索失败: {exc}")
        return
    if not results:
        ports.emit("    未找到匹配的技能")
        return
    for index, skill in enumerate(results, 1):
        ports.emit(f"    {index}. [{skill.name}]")
        ports.emit(f"        {skill.description}")
        ports.emit(f"        来源: {skill.source}")
        ports.emit(f"        信任级别: {skill.trust_level}")
        if skill.tags:
            ports.emit(f"        标签: {', '.join(skill.tags)}")


def _install(name: str, ports: SkillsCommandPorts) -> None:
    if not name:
        ports.emit("\n  ❌ 请指定要安装的技能名称")
        ports.emit("    用法: /skills install <name>")
        return
    ports.emit(f"\n  正在安装技能: {name}")
    try:
        success, message, installed_name = ports.install(name)
    except Exception as exc:
        ports.emit(f"    ❌ 安装失败: {exc}")
        return
    if success:
        ports.refresh_cache()
        ports.emit(f"    ✅ 技能 '{installed_name or name}' 安装成功")
    else:
        ports.emit(f"    ❌ 安装失败: {message}")


def _uninstall(name: str, ports: SkillsCommandPorts) -> None:
    if not name:
        ports.emit("\n  ❌ 请指定要卸载的技能名称")
        ports.emit("    用法: /skills uninstall <name>")
        return
    ports.emit(f"\n  正在卸载技能: {name}")
    try:
        success, message = ports.uninstall(name)
    except Exception as exc:
        ports.emit(f"    ❌ 卸载失败: {exc}")
        return
    if success:
        ports.refresh_cache()
        ports.emit(f"    ✅ 技能 '{name}' 卸载成功")
    else:
        ports.emit(f"    ❌ 卸载失败: {message}")
