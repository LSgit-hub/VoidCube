"""SqliteScheduledWritebackOutbox real-DB tests (Stage 5).

Replaces pure-mock coverage with real SQLite verification of every outbox
method, plus the owner-lease conflict visibility across processes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from voidcube.infrastructure.persistence.scheduled_writeback import (
    SqliteScheduledWritebackOutbox,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_enqueue_and_next_due_roundtrip(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1", "status": "succeeded"})
        due = outbox.next_due()
        assert due is not None
        assert due["_outbox_run_id"] == "run-1"
        assert due["_outbox_attempts"] == 0
        assert due["task_id"] == "t1"
        # next_due is a peek: the row stays due until explicitly resolved.
        assert outbox.next_due()["_outbox_run_id"] == "run-1"
        outbox.mark_delivered("run-1")
        assert outbox.next_due() is None
    finally:
        outbox.close()


def test_enqueue_replaces_previous_payload_for_same_run(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1", "attempt": 1})
        outbox.enqueue("run-1", {"task_id": "t1", "attempt": 2})
        due = outbox.next_due()
        assert due is not None
        assert due["attempt"] == 2
    finally:
        outbox.close()


def test_mark_delivered_removes_row(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1"})
        assert outbox.pending_count() == 1
        outbox.mark_delivered("run-1")
        assert outbox.pending_count() == 0
        assert outbox.next_due() is None
    finally:
        outbox.close()


def test_mark_failed_schedules_retry_with_backoff(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1"})
        outbox.mark_failed("run-1", attempts=1, error="boom")
        # Row is still live but not due immediately: backoff applied.
        assert outbox.pending_count() == 1
        assert outbox.next_due() is None
        assert outbox.next_due() is None
    finally:
        outbox.close()


def test_mark_dead_excludes_from_next_due_and_count(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1"})
        outbox.mark_dead("run-1", attempts=12, error="permanent")
        assert outbox.pending_count() == 0
        assert outbox.next_due() is None
    finally:
        outbox.close()


def test_pending_count_counts_only_live_rows(tmp_path):
    outbox = SqliteScheduledWritebackOutbox(tmp_path / "writeback.db")
    try:
        outbox.enqueue("run-1", {"task_id": "t1"})
        outbox.enqueue("run-2", {"task_id": "t2"})
        outbox.mark_delivered("run-1")
        assert outbox.pending_count() == 1
    finally:
        outbox.close()


def test_close_releases_owner_marker(tmp_path):
    db_path = tmp_path / "writeback.db"
    outbox = SqliteScheduledWritebackOutbox(db_path)
    assert db_path.with_name("writeback.db.owner").exists()
    outbox.close()
    assert not db_path.with_name("writeback.db.owner").exists()


def test_owner_lease_is_reentrant_for_second_instance_in_same_process(tmp_path):
    """The owner lease is process-reentrant (refcounted), so a second outbox
    instance in the same process is legal; cross-process exclusion is covered
    by test_owner_conflict_is_visible_across_processes."""
    db_path = tmp_path / "writeback.db"
    first = SqliteScheduledWritebackOutbox(db_path)
    second = SqliteScheduledWritebackOutbox(db_path)
    try:
        first.enqueue("run-1", {"task_id": "t1"})
        assert second.next_due()["_outbox_run_id"] == "run-1"
    finally:
        first.close()
        assert db_path.with_name("writeback.db.owner").exists()
        second.close()
        assert not db_path.with_name("writeback.db.owner").exists()


def test_owner_conflict_is_visible_across_processes(tmp_path):
    db_path = tmp_path / "writeback.db"
    outbox = SqliteScheduledWritebackOutbox(db_path)
    script = (
        "from voidcube.infrastructure.persistence.scheduled_writeback import "
        "SqliteScheduledWritebackOutbox; "
        f"SqliteScheduledWritebackOutbox(r'{db_path}')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "Mem/src"])},
        )
    finally:
        outbox.close()
    assert result.returncode != 0
    assert "SQLite file is owned" in result.stderr
