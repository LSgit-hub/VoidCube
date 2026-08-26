"""Skill Registry concurrency gate (Stage 5).

The registry DB uses a per-connection ``SQLiteOwnerLease`` (owner
``skill-registry-owner``): reentrant within the process (refcounted), mutually
exclusive across processes.  These tests pin that gate plus concurrent
refresh behavior.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from voidcube.extensions.skills import registry


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _write_skill(root, name: str) -> object:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} skill\n"
        "---\n\nInstructions.\n",
        encoding="utf-8",
    )
    return path


def test_registry_lease_is_reentrant_across_connections(tmp_path):
    db = tmp_path / "registry.sqlite3"
    first = registry.open_registry(db)
    second = registry.open_registry(db)
    try:
        assert db.with_name("registry.sqlite3.owner").exists()
    finally:
        first.close()
        assert db.with_name("registry.sqlite3.owner").exists()
        second.close()
        assert not db.with_name("registry.sqlite3.owner").exists()


def test_registry_owner_conflict_is_visible_across_processes(tmp_path):
    db = tmp_path / "registry.sqlite3"
    connection = registry.open_registry(db)
    script = (
        "from voidcube.extensions.skills import registry; "
        f"registry.open_registry(r'{db}')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "Mem/src"])},
        )
    finally:
        connection.close()
    assert result.returncode != 0
    assert "SQLite file is owned" in result.stderr


def test_concurrent_refresh_is_consistent(tmp_path):
    root = tmp_path / "skills"
    for name in ("alpha", "beta", "gamma"):
        _write_skill(root, name)
    db = tmp_path / "registry.sqlite3"
    spec = registry.DiscoveryRoot(root, "home", 0)
    # Warm the registry first.  Concurrent first-time schema creation (WAL
    # mode switch + CREATE TABLE) can contend and is not the hot-path
    # contract; production always refreshes an existing registry.
    assert registry.refresh_registry([spec], path=db)["added"] == 3

    barrier = threading.Barrier(4)
    results: list[dict] = []
    errors: list[Exception] = []

    def _refresh() -> None:
        try:
            barrier.wait()
            # Each thread opens its own lease; process-reentrancy allows it.
            results.append(registry.refresh_registry([spec], path=db))
        except BaseException as exc:  # pragma: no cover - failure reporting
            errors.append(exc)

    threads = [threading.Thread(target=_refresh) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"concurrent refresh failed: {errors}"
    assert len(results) == 4
    # Warm hot-path refresh is fully reused, never removes, never re-adds.
    assert all(result == {"added": 0, "reparsed": 0, "reused": 3, "removed": 0, "errors": 0} for result in results)
    connection = registry.open_registry(db)
    try:
        rows = registry.query_skills(connection)
        assert {row["directory_name"] for row in rows} == {"alpha", "beta", "gamma"}
    finally:
        connection.close()
