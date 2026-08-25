from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from voidcube.infrastructure.persistence.sqlite_owner import (
    SQLiteOwnerConflict,
    SQLiteOwnerLease,
)


def test_owner_lease_is_reentrant_and_releases_after_final_close(tmp_path):
    db_path = tmp_path / "state.db"
    first = SQLiteOwnerLease(db_path, "session-owner")
    second = SQLiteOwnerLease(db_path, "session-owner")
    assert json.loads((tmp_path / "state.db.owner").read_text())[
        "owner"
    ] == "session-owner"

    first.close()
    assert (tmp_path / "state.db.owner").exists()
    second.close()
    assert not (tmp_path / "state.db.owner").exists()


def test_owner_lease_rejects_live_process(tmp_path):
    db_path = tmp_path / "actions.db"
    lease = SQLiteOwnerLease(db_path, "action-journal-owner")
    try:
        with pytest.raises(SQLiteOwnerConflict):
            SQLiteOwnerLease(db_path, "other-owner")
    finally:
        lease.close()


def test_owner_lease_recovers_dead_process_marker(tmp_path):
    db_path = tmp_path / "scheduled.db"
    marker = tmp_path / "scheduled.db.owner"
    marker.write_text(
        json.dumps({"pid": 99999999, "owner": "stale-owner"}),
        encoding="utf-8",
    )
    lease = SQLiteOwnerLease(db_path, "scheduled-task-owner")
    assert json.loads(marker.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lease.close()


def test_owner_lease_conflict_is_visible_across_processes(tmp_path):
    db_path = tmp_path / "registry.db"
    lease = SQLiteOwnerLease(db_path, "process-registry-owner")
    script = (
        "from voidcube.infrastructure.persistence.sqlite_owner import SQLiteOwnerLease; "
        f"SQLiteOwnerLease(r'{db_path}', 'other-owner')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "Mem/src"])},
        )
    finally:
        lease.close()
    assert result.returncode != 0
    assert "SQLite file is owned" in result.stderr
