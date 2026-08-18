"""Skill metadata and content discovery contracts."""

from .catalog import (
    get_all_skills_dirs,
    get_external_skills_dirs,
    get_repo_skills_dir,
    iter_skill_index_files,
    parse_frontmatter,
)
from .sync import sync_skills
from .models import SkillBundle, SkillMeta
from .guard import Finding, ScanResult, scan_skill, should_allow_install
from .commands import (
    build_plan_path,
    build_preloaded_skills_prompt,
    build_skill_invocation_message,
    get_skill_commands,
    resolve_skill_command_key,
    scan_skill_commands,
)

__all__ = [
    "get_all_skills_dirs",
    "get_external_skills_dirs",
    "get_repo_skills_dir",
    "iter_skill_index_files",
    "parse_frontmatter",
    "sync_skills",
    "SkillBundle",
    "SkillMeta",
    "Finding",
    "ScanResult",
    "scan_skill",
    "should_allow_install",
    "build_plan_path",
    "build_preloaded_skills_prompt",
    "build_skill_invocation_message",
    "get_skill_commands",
    "resolve_skill_command_key",
    "scan_skill_commands",
]
