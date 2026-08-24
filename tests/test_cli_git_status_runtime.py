from types import SimpleNamespace

from voidcube.interfaces.cli.git_status_runtime import CliGitStatusPorts, CliGitStatusRuntime


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _runtime(status):
    display = SimpleNamespace(
        runner=SimpleNamespace(
            get_status=lambda: status,
        )
    )
    now = [100.0]
    return CliGitStatusRuntime(
        CliGitStatusPorts(
            git_display_factory=lambda: display,
            clock=lambda: now[0],
            thread_factory=_ImmediateThread,
        )
    )


def test_git_status_runtime_refreshes_and_then_serves_cache():
    runtime = _runtime(
        SimpleNamespace(
            is_repo=True,
            branch="main",
            staged=["a"],
            modified=["b"],
            deleted=["c"],
            untracked=["d"],
            renamed=[],
            conflicts=[],
        )
    )

    first = runtime.build()
    second = runtime.build()
    rendered = "".join(text for _, text in second)

    assert first == second
    assert "Git <main>" in rendered
    assert "改动 4" in rendered
    assert "暂存" not in rendered
    assert "origin,upstream" not in rendered


def test_git_status_runtime_deduplicates_a_file_changed_in_index_and_worktree():
    rendered = "".join(
        text
        for _, text in _runtime(
            SimpleNamespace(
                is_repo=True,
                branch="master",
                staged=["same.py", "staged.py"],
                modified=["same.py", "working.py"],
                deleted=[],
                untracked=["new.py"],
                renamed=["old.py -> new-name.py"],
                conflicts=["same.py"],
            )
        ).build()
    )

    assert "Git <master>" in rendered
    assert "改动 5" in rendered


def test_git_status_runtime_hides_non_repo_and_isolates_reader_failure():
    non_repo = _runtime(SimpleNamespace(is_repo=False)).build()
    assert non_repo == []

    runtime = CliGitStatusRuntime(
        CliGitStatusPorts(
            git_display_factory=lambda: (_ for _ in ()).throw(RuntimeError("git unavailable")),
            clock=lambda: 100.0,
            thread_factory=_ImmediateThread,
        )
    )
    assert runtime.build() == []
