"""Cache and render the compact git status used by the CLI footer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread
from typing import Any


StatusFragment = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CliGitStatusPorts:
    """Git reader, clock and thread operations supplied by the CLI host."""

    git_display_factory: Callable[[], Any]
    clock: Callable[[], float]
    thread_factory: Callable[..., Thread]


class CliGitStatusRuntime:
    """Own cached background git status projection without CLI state access."""

    _CACHE_SECONDS = 60.0
    _BACKGROUND = "bg:#1a1a2e"

    def __init__(self, ports: CliGitStatusPorts) -> None:
        self.ports = ports
        self._cache: list[StatusFragment] | None = None
        self._cache_timestamp = 0.0
        self._refreshing = False

    def build(self) -> list[StatusFragment]:
        now = self.ports.clock()
        if self._cache is not None and now - self._cache_timestamp < self._CACHE_SECONDS:
            return self._cache
        if self._refreshing:
            return self._cache or []

        self._refreshing = True

        def refresh() -> None:
            try:
                git_display = self.ports.git_display_factory()
                status = git_display.runner.get_status()
                fragments = self._render_status(status)
                if fragments is not None:
                    try:
                        code, output, _ = git_display.runner._run(["remote"])
                        if code == 0 and output.strip():
                            fragments.extend(self._render_remotes(output))
                    except Exception:
                        pass
                self._cache = fragments or []
            except Exception:
                self._cache = []
            finally:
                self._cache_timestamp = self.ports.clock()
                self._refreshing = False

        self.ports.thread_factory(
            target=refresh,
            daemon=True,
            name="git-status-refresh",
        ).start()
        return self._cache or []

    @classmethod
    def _render_status(cls, status: Any) -> list[StatusFragment] | None:
        if not status.is_repo:
            return None

        fragments: list[StatusFragment] = [
            (f"{cls._BACKGROUND} #58A6FF", "Git "),
            (f"{cls._BACKGROUND} #9CA3AF", "<"),
            (f"{cls._BACKGROUND} #58A6FF bold", status.branch),
            (f"{cls._BACKGROUND} #9CA3AF", ">"),
        ]
        if status.staged:
            fragments.extend(
                [
                    (f"{cls._BACKGROUND} #9CA3AF", "  暂存 "),
                    (f"{cls._BACKGROUND} #FFFFFF bold", str(len(status.staged))),
                ]
            )

        changes = len(status.modified) + len(status.deleted) + len(status.untracked)
        if changes > 0:
            fragments.extend(
                [
                    (f"{cls._BACKGROUND} #9CA3AF", "  更改 "),
                    (f"{cls._BACKGROUND} #FFFFFF bold", str(changes)),
                ]
            )
        return fragments

    @classmethod
    def _render_remotes(cls, output: str) -> list[StatusFragment]:
        remote_names = ",".join(output.strip().splitlines())
        return [
            (f"{cls._BACKGROUND} #9CA3AF", "  <"),
            (f"{cls._BACKGROUND} #8B949E", remote_names),
            (f"{cls._BACKGROUND} #9CA3AF", ">"),
        ]
