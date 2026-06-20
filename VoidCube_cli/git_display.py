"""
Git Display — 终端中的实时代码变更可视化组件。

在自愈/自进化编程过程中提供:
  - 实时分支状态 (当前分支 / ahead / behind)
  - 变更摘要 (modified + staged + untracked 计数)
  - 提交历史摘要 (最近 N 次提交)
  - 彩色 diff 预览
  - 变更风险提示 (大改动 / 多文件)

使用方式:
  - /git 命令查看完整状态
  - 嵌入 banner 显示精简状态行
  - GitStatusWidget 可嵌入 curses UI
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── ANSI color helpers ─────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def _c(text: str, code: str) -> str:
    """Wrap text in ANSI color, return plain if colors unavailable."""
    return f"{code}{text}{_RST}"


# ── Data structures ────────────────────────────────────────────────────

@dataclass
class GitChange:
    """Single file change record."""
    path: str
    status: str          # M=modified, A=added, D=deleted, R=renamed, ?=untracked
    staged: bool = False
    additions: int = 0
    deletions: int = 0


@dataclass
class GitStatus:
    """Full git status snapshot."""
    branch: str = ""
    ahead: int = 0          # commits ahead of remote
    behind: int = 0         # commits behind remote
    modified: List[str] = field(default_factory=list)    # unstaged modified
    staged: List[str] = field(default_factory=list)      # staged
    untracked: List[str] = field(default_factory=list)   # untracked
    deleted: List[str] = field(default_factory=list)
    renamed: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    total_changes: int = 0
    is_repo: bool = False


@dataclass
class GitLogEntry:
    """Single commit log entry."""
    hash: str = ""
    author: str = ""
    date: str = ""
    message: str = ""
    refs: str = ""  # branch/tag pointers


# ── Git command runner ─────────────────────────────────────────────────

class GitRunner:
    """Thin wrapper around git CLI with timeout and error handling."""

    def __init__(self, cwd: Optional[Path] = None):
        self.cwd = cwd or Path.cwd()

    def _run(self, args: List[str], timeout: int = 10) -> Tuple[int, str, str]:
        """Run a git command. Returns (returncode, stdout, stderr)."""
        try:
            r = subprocess.run(
                ["git"] + args,
                capture_output=True, text=True,
                cwd=str(self.cwd), timeout=timeout,
            )
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except FileNotFoundError:
            return -2, "", "git not found"
        except Exception as e:
            return -3, "", str(e)

    def is_repo(self) -> bool:
        """Check if cwd is inside a git repository."""
        code, _, _ = self._run(["rev-parse", "--git-dir"])
        return code == 0

    def get_status(self) -> GitStatus:
        """Get full git status snapshot."""
        status = GitStatus()

        if not self.is_repo():
            return status
        status.is_repo = True

        # Branch name
        code, branch, _ = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        if code == 0:
            status.branch = branch

        # Remote tracking
        code, remote, _ = self._run(
            ["rev-list", "--count", "--left-right",
             f"HEAD...@{'{u}'}", "--"], timeout=5,
        )
        if code == 0 and remote:
            parts = remote.split('\t')
            if len(parts) == 2:
                try:
                    status.behind = int(parts[0])
                    status.ahead = int(parts[1])
                except ValueError:
                    pass

        # Porcelain status
        code, out, _ = self._run(
            ["status", "--porcelain", "-u"], timeout=10,
        )
        if code != 0:
            return status

        for line in out.splitlines():
            if not line or len(line) < 3:
                continue
            xy = line[:2]
            path = line[3:].strip()

            # Staged changes (index)
            if xy[0] == 'M':
                status.staged.append(path)
            elif xy[0] == 'A':
                status.staged.append(path)
            elif xy[0] == 'D':
                status.staged.append(path)
            elif xy[0] == 'R':
                status.renamed.append(path)

            # Working tree changes
            if xy[1] == 'M':
                status.modified.append(path)
            elif xy[1] == 'D':
                status.deleted.append(path)
            elif xy[0] == '?' and xy[1] == '?':
                status.untracked.append(path)

            # Conflicts
            if 'U' in xy or xy in ('AA', 'DD', 'AU', 'UA', 'DU', 'UD'):
                status.conflicts.append(path)

        status.total_changes = (
            len(status.modified) + len(status.staged) +
            len(status.untracked) + len(status.deleted)
        )
        return status

    def get_log(self, limit: int = 10) -> List[GitLogEntry]:
        """Get recent commit history."""
        code, out, _ = self._run(
            ["log", f"-{limit}", "--oneline", "--decorate", "--date=relative",
             "--format=%h|%an|%ar|%s|%D"],
            timeout=10,
        )
        if code != 0 or not out:
            return []

        entries = []
        for line in out.splitlines():
            parts = line.split('|', 4)
            if len(parts) >= 4:
                entries.append(GitLogEntry(
                    hash=parts[0].strip(),
                    author=parts[1].strip(),
                    date=parts[2].strip(),
                    message=parts[3].strip(),
                    refs=parts[4].strip() if len(parts) > 4 else "",
                ))
        return entries

    def get_diff_summary(self, staged: bool = False) -> Dict[str, int]:
        """Get additions/deletions summary. Returns {'files': N, '+': N, '-': N}."""
        args = ["diff", "--stat"]
        if staged:
            args.insert(1, "--staged")
        code, out, _ = self._run(args, timeout=15)
        if code != 0 or not out:
            return {"files": 0, "+": 0, "-": 0}

        # Parse the last line: "N files changed, X insertions(+), Y deletions(-)"
        result = {"files": 0, "+": 0, "-": 0}
        last_line = out.splitlines()[-1] if out.splitlines() else ""
        files_m = re.search(r'(\d+)\s+files?\s+changed', last_line)
        plus_m = re.search(r'(\d+)\s+insertions?\(\+\)', last_line)
        minus_m = re.search(r'(\d+)\s+deletions?\(-\)', last_line)
        if files_m:
            result["files"] = int(files_m.group(1))
        if plus_m:
            result["+"] = int(plus_m.group(1))
        if minus_m:
            result["-"] = int(minus_m.group(1))
        return result

    def get_diff_text(self, staged: bool = False, max_lines: int = 200) -> str:
        """Get unified diff text, truncated to max_lines."""
        args = ["diff", "--unified=3"]
        if staged:
            args.insert(1, "--staged")
        code, out, _ = self._run(args, timeout=15)
        if code != 0:
            return ""
        lines = out.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]
        return '\n'.join(lines)

    def get_latest_tag(self) -> Optional[str]:
        """Get the most recent tag, if any."""
        code, out, _ = self._run(
            ["describe", "--tags", "--abbrev=0"], timeout=5,
        )
        return out if code == 0 and out else None


# ── Display formatters ──────────────────────────────────────────────────

class GitDisplay:
    """Format git status for terminal display."""

    def __init__(self, cwd: Optional[Path] = None):
        self.runner = GitRunner(cwd)

    # ── Status line (compact, fits in status bar) ─────────────────────

    def status_line(self) -> str:
        """One-line status for the CLI status bar.

        Examples:
          [main ↑2 ✓]                    — clean, 2 ahead
          [main M:3 S:1 ?:2]            — 3 modified, 1 staged, 2 untracked
          [main ✗ conflicts]             — merge conflict
          (no repo)                      — not a git repo
        """
        s = self.runner.get_status()
        if not s.is_repo:
            return ""

        parts = [_c(s.branch, _CYAN)]

        # Remote status
        remote = ""
        if s.ahead:
            remote += f"↑{s.ahead}"
        if s.behind:
            remote += f"↓{s.behind}"
        if remote:
            parts.append(_c(remote, _YELLOW))

        # Change summary
        changes = []
        if s.staged:
            changes.append(f"S:{len(s.staged)}")
        if s.modified:
            changes.append(f"M:{len(s.modified)}")
        if s.untracked:
            changes.append(f"?:{len(s.untracked)}")
        if s.deleted:
            changes.append(f"D:{len(s.deleted)}")
        if s.conflicts:
            changes.append(_c("✗", _RED))

        if changes:
            parts.append(" ".join(changes))
        elif s.is_repo:
            parts.append(_c("✓", _GREEN))

        return "[" + " ".join(parts) + "]"

    # ── Full status panel ─────────────────────────────────────────────

    def full_status(self, show_diff: bool = True,
                    log_entries: int = 5) -> str:
        """Rich multi-line status panel. Suitable for /git command output."""
        lines = []
        s = self.runner.get_status()

        if not s.is_repo:
            return _c("  (not a git repository)", _DIM)

        # ── Header: branch + remote ──
        header = f"  Branch: {_c(s.branch, _CYAN + _BOLD)}"
        if s.ahead or s.behind:
            header += "  "
            if s.ahead:
                header += _c(f"↑{s.ahead} ahead", _YELLOW)
            if s.behind:
                header += _c(f" ↓{s.behind} behind", _RED)
        lines.append(header)

        # ── Change summary ──
        if s.total_changes > 0:
            change_parts = []
            if s.staged:
                change_parts.append(
                    _c(f"{len(s.staged)} staged", _GREEN))
            if s.modified:
                change_parts.append(
                    _c(f"{len(s.modified)} modified", _YELLOW))
            if s.untracked:
                change_parts.append(
                    f"{len(s.untracked)} untracked")
            if s.deleted:
                change_parts.append(
                    _c(f"{len(s.deleted)} deleted", _RED))
            if s.conflicts:
                change_parts.append(
                    _c("CONFLICTS", _RED + _BOLD))
            lines.append("  Changes: " + ", ".join(change_parts))
        else:
            lines.append(_c("  Working tree clean ✓", _GREEN))

        # ── Diff summary (if changed) ──
        if s.total_changes > 0 and show_diff:
            summary = self.runner.get_diff_summary(staged=False)
            if summary["files"] > 0:
                plus = summary["+"]
                minus = summary["-"]
                lines.append(
                    f"  Diff: {summary['files']} files, "
                    f"{_c(f'+{plus}', _GREEN)}, "
                    f"{_c(f'-{minus}', _RED)}"
                )

        # ── Recent commits ──
        log = self.runner.get_log(limit=log_entries)
        if log:
            lines.append("")
            lines.append("  Recent commits:")
            for entry in log[:log_entries]:
                refs_str = f" ({entry.refs})" if entry.refs else ""
                lines.append(
                    f"    {_c(entry.hash, _YELLOW)} "
                    f"{entry.message[:60]}{_c(refs_str, _CYAN)}  "
                    f"{_c(entry.date, _DIM)}"
                )

        # ── Latest tag ──
        tag = self.runner.get_latest_tag()
        if tag:
            lines.append(f"  Latest tag: {_c(tag, _CYAN)}")

        return '\n'.join(lines)

    # ── Diff preview ──────────────────────────────────────────────────

    def diff_preview(self, staged: bool = False, max_lines: int = 200) -> str:
        """Colorized diff preview."""
        s = self.runner.get_status()
        if not s.is_repo:
            return _c("  (not a git repository)", _DIM)
        if s.total_changes == 0:
            return _c("  No changes to show.", _DIM)

        text = self.runner.get_diff_text(staged=staged, max_lines=max_lines)
        if not text:
            return _c("  (empty diff)", _DIM)

        # Colorize diff output
        colored_lines = []
        for line in text.splitlines():
            if line.startswith('+') and not line.startswith('+++'):
                colored_lines.append(_c(line, _GREEN))
            elif line.startswith('-') and not line.startswith('---'):
                colored_lines.append(_c(line, _RED))
            elif line.startswith('@@'):
                colored_lines.append(_c(line, _CYAN))
            elif line.startswith('diff ') or line.startswith('index '):
                colored_lines.append(_c(line, _DIM))
            elif line.startswith('---') or line.startswith('+++'):
                colored_lines.append(_c(line, _BOLD))
            else:
                colored_lines.append(line)
        return '\n'.join(colored_lines)

    # ── Risk assessment ───────────────────────────────────────────────

    def risk_level(self) -> Tuple[str, str]:
        """Assess risk level of current changes.

        Returns (level, description).
        Levels: "safe", "caution", "warning", "danger"
        """
        s = self.runner.get_status()
        if not s.is_repo:
            return "safe", "not a repo"

        total = s.total_changes
        summary = self.runner.get_diff_summary()
        changed_lines = summary["+"] + summary["-"]
        files = summary["files"]

        if s.conflicts:
            return "danger", "merge conflicts present"
        if total == 0:
            return "safe", "clean working tree"
        if changed_lines > 500:
            return "danger", f"{changed_lines} lines changed across {files} files"
        if changed_lines > 200:
            return "warning", f"{changed_lines} lines changed across {files} files"
        if files > 10:
            return "caution", f"{files} files changed ({changed_lines} lines)"
        if total > 5:
            return "caution", f"{total} files with changes"
        return "safe", f"{total} files, {changed_lines} lines — small change"


# ── Convenience functions ──────────────────────────────────────────────

def get_git_display(cwd: Optional[Path] = None) -> GitDisplay:
    """Get a GitDisplay for the given directory."""
    return GitDisplay(cwd)


def git_status_line(cwd: Optional[Path] = None) -> str:
    """One-line git status for status bar."""
    return GitDisplay(cwd).status_line()


def git_full_status(cwd: Optional[Path] = None) -> str:
    """Full git status panel."""
    return GitDisplay(cwd).full_status()


def git_risk_assessment(cwd: Optional[Path] = None) -> Tuple[str, str]:
    """Quick risk assessment of current changes."""
    return GitDisplay(cwd).risk_level()
