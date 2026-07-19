#!/usr/bin/env python3
"""
VoidCube CLI Command Handlers

Contains process notification formatting and CLI git worktree management.
"""

import os
import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

def _format_process_notification(evt: dict) -> Optional[str]:
    """Format a process notification event into a [SYSTEM: ...] message.

    Handles both completion events (notify_on_complete) and watch pattern
    match events from the unified completion_queue.
    """
    evt_type = evt.get("type", "completion")
    _sid = evt.get("session_id", "unknown")
    _cmd = evt.get("command", "unknown")

    try:
        from VoidCube_cli.i18n import t
    except Exception:
        t = lambda key, default=None, **kwargs: default or key

    if evt_type == "watch_disabled":
        return f"[SYSTEM: {evt.get('message', '')}]"

    if evt_type == "watch_match":
        _pat = evt.get("pattern", "?")
        _out = evt.get("output", "")
        _sup = evt.get("suppressed", 0)
        text = (
            f"[SYSTEM: {t('process.watch_match', default=f'后台进程 {_sid} 匹配到监视模式')}"
            f" \"{_pat}\"。\n"
            f"{t('process.command', default='命令')}: {_cmd}\n"
            f"{t('process.matched_output', default='匹配的输出')}:\n{_out}"
        )
        if _sup:
            text += f"\n{t('process.suppressed_count', count=_sup, default=f'{_sup} 条较早的匹配被频率限制所抑制')}"
        text += "]"
        return text

    _exit = evt.get("exit_code", "?")
    _out = evt.get("output", "")
    return (
        f"[SYSTEM: {t('process.completed', default=f'后台进程 {_sid} 已完成')}"
        f" ({t('process.exit_code', default='退出码')} {_exit})。\n"
        f"{t('process.command', default='命令')}: {_cmd}\n"
        f"{t('process.output', default='输出')}:\n{_out}]"
    )


def _git_repo_root() -> Optional[str]:
    """Return the git repo root for CWD, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _git_head_commit(worktree_path: str) -> str:
    """Return the current HEAD commit hash of a worktree, or an empty string."""
    if not worktree_path:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", worktree_path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _git_improvement_diff(
    worktree_path: str,
    baseline_head: str,
) -> Optional[Dict[str, Any]]:
    """Return the committed changes since a captured worktree baseline."""
    if not worktree_path or not baseline_head:
        return None
    try:
        head_now = _git_head_commit(worktree_path)
        if not head_now or head_now == baseline_head:
            return None
        names = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--name-only", f"{baseline_head}..{head_now}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        changed_files = [
            line.strip()
            for line in (names.stdout or "").splitlines()
            if line.strip()
        ]
        stat = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--stat", f"{baseline_head}..{head_now}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "commit_hash": head_now,
            "changed_files": changed_files,
            "diff_summary": (stat.stdout or "").strip()[:4000],
        }
    except Exception:
        return None


def _path_is_within_root(path: Path, root: Path) -> bool:
    """Return True when a resolved path stays within the expected root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _setup_worktree(repo_root: str = None) -> Optional[Dict[str, str]]:
    """Create an isolated git worktree for this CLI session.

    Returns a dict with worktree metadata on success, None on failure.
    The dict contains: path, branch, repo_root.
    """
    from VoidCube_cli.i18n import t

    repo_root = repo_root or _git_repo_root()
    if not repo_root:
        print(f"\033[31m✗ {t('prompts.worktree_requires_git')}\033[0m")
        print(f"  {t('prompts.worktree_cd_first')}")
        return None

    short_id = uuid.uuid4().hex[:8]
    wt_name = f"VoidCube-{short_id}"
    branch_name = f"VoidCube/{wt_name}"

    worktrees_dir = Path(repo_root) / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    wt_path = worktrees_dir / wt_name

    gitignore = Path(repo_root) / ".gitignore"
    _ignore_entry = ".worktrees/"
    try:
        existing = gitignore.read_text() if gitignore.exists() else ""
        if _ignore_entry not in existing.splitlines():
            with open(gitignore, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"{_ignore_entry}\n")
    except Exception as e:
        logger.debug("Could not update .gitignore: %s", e)

    try:
        result = subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch_name, "HEAD"],
            capture_output=True, text=True, timeout=30, cwd=repo_root,
        )
        if result.returncode != 0:
            print(f"\033[31m✗ {t('prompts.worktree_failed_to_create', error=result.stderr.strip())}\033[0m")
            return None
    except Exception as e:
        print(f"\033[31m✗ {t('prompts.worktree_failed_to_create', error=str(e))}\033[0m")
        return None

    include_file = Path(repo_root) / ".worktreeinclude"
    if include_file.exists():
        try:
            repo_root_resolved = Path(repo_root).resolve()
            wt_path_resolved = wt_path.resolve()
            for line in include_file.read_text().splitlines():
                entry = line.strip()
                if not entry or entry.startswith("#"):
                    continue
                src = Path(repo_root) / entry
                dst = wt_path / entry
                try:
                    src_resolved = src.resolve(strict=False)
                    dst_resolved = dst.resolve(strict=False)
                except (OSError, ValueError):
                    logger.debug("Skipping invalid .worktreeinclude entry: %s", entry)
                    continue
                if not _path_is_within_root(src_resolved, repo_root_resolved):
                    logger.warning("Skipping .worktreeinclude entry outside repo root: %s", entry)
                    continue
                if not _path_is_within_root(dst_resolved, wt_path_resolved):
                    logger.warning("Skipping .worktreeinclude entry that escapes worktree: %s", entry)
                    continue
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
                elif src.is_dir():
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.symlink(str(src_resolved), str(dst))
        except Exception as e:
            logger.debug("Error copying .worktreeinclude entries: %s", e)

    info = {
        "path": str(wt_path),
        "branch": branch_name,
        "repo_root": repo_root,
    }

    print(f"\033[32m✓ {t('prompts.worktree_created')}\033[0m {wt_path}")
    print(f"  {t('prompts.worktree_branch', branch=branch_name)}")

    return info


def _cleanup_worktree(info: Dict[str, str] = None) -> None:
    """Remove a worktree and its branch on exit.

    Preserves the worktree only if it has unpushed commits (real work
    that hasn't been pushed to any remote).  Uncommitted changes alone
    (untracked files, test artifacts) are not enough to keep it.
    """
    if not info:
        return

    from VoidCube_cli.i18n import t

    wt_path = info["path"]
    branch = info["branch"]
    repo_root = info["repo_root"]

    if not Path(wt_path).exists():
        return

    has_unpushed = False
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
            capture_output=True, text=True, timeout=10, cwd=wt_path,
        )
        has_unpushed = bool(result.stdout.strip())
    except Exception:
        has_unpushed = True

    if has_unpushed:
        print(f"\n\033[33m⚠ {t('prompts.worktree_has_unpushed', path=wt_path)}\033[0m")
        print(f"  {t('prompts.worktree_cleanup_manual', path=wt_path)}")
        return

    try:
        subprocess.run(
            ["git", "worktree", "remove", wt_path, "--force"],
            capture_output=True, text=True, timeout=15, cwd=repo_root,
        )
    except Exception as e:
        logger.debug("Failed to remove worktree: %s", e)

    try:
        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
    except Exception as e:
        logger.debug("Failed to delete branch %s: %s", branch, e)

    print(f"\033[32m✓ {t('prompts.worktree_cleaned_up', path=wt_path)}\033[0m")


def _prune_stale_worktrees(repo_root: str, max_age_hours: int = 24) -> None:
    """Remove stale worktrees and orphaned branches on startup.

    Age-based tiers:
    - Under max_age_hours (24h): skip — session may still be active.
    - 24h–72h: remove if no unpushed commits.
    - Over 72h: force remove regardless.

    Also prunes orphaned ``VoidCube/*`` and ``pr-*`` local branches.
    """
    worktrees_dir = Path(repo_root) / ".worktrees"
    if not worktrees_dir.exists():
        _prune_orphaned_branches(repo_root)
        return

    now = time.time()
    soft_cutoff = now - (max_age_hours * 3600)
    hard_cutoff = now - (max_age_hours * 3 * 3600)

    for entry in worktrees_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("VoidCube-"):
            continue

        try:
            mtime = entry.stat().st_mtime
            if mtime > soft_cutoff:
                continue
        except Exception:
            continue

        force = mtime <= hard_cutoff

        if not force:
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
                    capture_output=True, text=True, timeout=5, cwd=str(entry),
                )
                if result.stdout.strip():
                    continue
            except Exception:
                continue

        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, timeout=5, cwd=str(entry),
            )
            branch = branch_result.stdout.strip()

            subprocess.run(
                ["git", "worktree", "remove", str(entry), "--force"],
                capture_output=True, text=True, timeout=15, cwd=repo_root,
            )
            if branch:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    capture_output=True, text=True, timeout=10, cwd=repo_root,
                )
            logger.debug("Pruned stale worktree: %s (force=%s)", entry.name, force)
        except Exception as e:
            logger.debug("Failed to prune worktree %s: %s", entry.name, e)

    _prune_orphaned_branches(repo_root)


def _prune_orphaned_branches(repo_root: str) -> None:
    """Delete local ``VoidCube/VoidCube-*`` and ``pr-*`` branches with no worktree."""
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        if result.returncode != 0:
            return
        all_branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
    except Exception:
        return

    active_branches: set = set()
    try:
        wt_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=repo_root,
        )
        for line in wt_result.stdout.split("\n"):
            if line.startswith("branch refs/heads/"):
                active_branches.add(line.split("branch refs/heads/", 1)[-1].strip())
    except Exception:
        return

    try:
        head_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=repo_root,
        )
        current = head_result.stdout.strip()
        if current:
            active_branches.add(current)
    except Exception:
        pass
    active_branches.add("main")

    orphaned = [
        b for b in all_branches
        if b not in active_branches
        and (b.startswith("VoidCube/VoidCube-") or b.startswith("pr-"))
    ]

    if not orphaned:
        return

    for i in range(0, len(orphaned), 50):
        batch = orphaned[i:i + 50]
        try:
            subprocess.run(
                ["git", "branch", "-D"] + batch,
                capture_output=True, text=True, timeout=30, cwd=repo_root,
            )
        except Exception as e:
            logger.debug("Failed to prune orphaned branches: %s", e)

    logger.debug("Pruned %d orphaned branches", len(orphaned))
