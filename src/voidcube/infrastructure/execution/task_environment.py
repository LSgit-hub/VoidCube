"""Task environment adapter used by autonomous execution hosts."""

from __future__ import annotations

from .terminal_tool import (
    prepare_task_git_worktree,
    release_task_environment,
)

__all__ = ["prepare_task_git_worktree", "release_task_environment"]
