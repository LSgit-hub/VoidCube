from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from voidcube.domain.agent.tool_scheduler import (
    _extract_parallel_scope_path,
    paths_overlap,
    should_parallelize_tool_batch,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _tool_call(name: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=json.dumps({"path": path}))
    )


def _scope_alias(tmp_path, monkeypatch) -> tuple[Path, Path]:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        alias.mkdir()
        original_resolve = Path.resolve

        def resolve_alias(path: Path, *, strict: bool = False) -> Path:
            resolved = original_resolve(path, strict=strict)
            try:
                relative = path.relative_to(alias)
            except ValueError:
                return resolved
            return original_resolve(target / relative, strict=strict)

        monkeypatch.setattr(Path, "resolve", resolve_alias)
    return target, alias


def test_symlink_aliases_resolve_to_the_same_parallel_scope(tmp_path, monkeypatch):
    target, alias = _scope_alias(tmp_path, monkeypatch)
    target_path = _extract_parallel_scope_path(
        "read_file", {"path": str(target / "result.json")}
    )
    alias_path = _extract_parallel_scope_path(
        "read_file", {"path": str(alias / "result.json")}
    )

    assert target_path == alias_path
    assert paths_overlap(target_path, alias_path)


def test_symlink_aliases_force_sequential_execution(tmp_path, monkeypatch):
    target, alias = _scope_alias(tmp_path, monkeypatch)
    calls = [
        _tool_call("read_file", str(target / "a.txt")),
        _tool_call("read_file", str(alias / "a.txt")),
    ]

    assert should_parallelize_tool_batch(calls) is False
