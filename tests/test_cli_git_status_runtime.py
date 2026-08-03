from types import SimpleNamespace

from VoidCube_cli.cli_git_status_runtime import CliGitStatusPorts, CliGitStatusRuntime


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _runtime(status, remotes="origin\nupstream\n"):
    display = SimpleNamespace(
        runner=SimpleNamespace(
            get_status=lambda: status,
            _run=lambda _args: (0, remotes, ""),
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
        )
    )

    first = runtime.build()
    second = runtime.build()
    rendered = "".join(text for _, text in second)

    assert first == second
    assert "Git <main>" in rendered
    assert "暂存 1" in rendered
    assert "更改 3" in rendered
    assert "origin,upstream" in rendered


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
