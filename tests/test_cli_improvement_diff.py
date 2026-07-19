from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from VoidCube_cli.cli_handlers import _git_head_commit, _git_improvement_diff


pytestmark = pytest.mark.smoke


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "t@t.t"], path)
    _run(["git", "config", "user.name", "t"], path)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-m", "seed"], path)


def test_git_head_commit_returns_hash(tmp_path):
    _init_repo(tmp_path)
    head = _git_head_commit(str(tmp_path))
    assert head and len(head) >= 7


def test_git_head_commit_blank_path_returns_empty():
    assert _git_head_commit("") == ""


def test_improvement_diff_detects_commit(tmp_path):
    _init_repo(tmp_path)
    baseline = _git_head_commit(str(tmp_path))

    # Agent edits + commits in the worktree.
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "foo.py").write_text("print('improved')\n", encoding="utf-8")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-m", "improve foo"], tmp_path)

    diff = _git_improvement_diff(str(tmp_path), baseline)
    assert diff is not None
    assert diff["commit_hash"] != baseline
    assert "skills/foo.py" in diff["changed_files"]
    assert "foo.py" in diff["diff_summary"]


def test_improvement_diff_none_when_no_commit(tmp_path):
    _init_repo(tmp_path)
    baseline = _git_head_commit(str(tmp_path))
    # No new commit — HEAD unchanged.
    assert _git_improvement_diff(str(tmp_path), baseline) is None


def test_improvement_diff_none_on_missing_inputs(tmp_path):
    _init_repo(tmp_path)
    assert _git_improvement_diff("", "abc") is None
    assert _git_improvement_diff(str(tmp_path), "") is None
