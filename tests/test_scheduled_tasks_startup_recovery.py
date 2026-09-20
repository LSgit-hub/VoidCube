"""ScheduledTaskStore startup recovery (Stage 5).

A run whose lease expired while the store was not alive (crash window) must be
recovered when a new store instance opens the same DB, so the store never
starts with stale ``running`` claims blocking future dispatches.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from voidcube.systems.supervisor.scheduled_tasks import ScheduledTaskStore
from voidcube.infrastructure.persistence.sqlite_owner import SQLiteOwnerConflict, SQLiteOwnerLease


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_deferred_store_and_unused_close_do_not_touch_storage(tmp_path):
    path = tmp_path / "deferred" / "schedule.db"
    store = ScheduledTaskStore(path, defer_open=True)
    assert not path.parent.exists()
    store.close()
    assert not path.parent.exists()
    assert store.recent_runs() == []
    assert path.exists()
    assert path.with_suffix(".db.owner").exists()
    store.close()
    assert not path.with_suffix(".db.owner").exists()


def test_deferred_owner_conflict_is_reported_on_open_and_can_be_retried(tmp_path):
    path = tmp_path / "schedule.db"
    owner = SQLiteOwnerLease(path, "another-owner")
    store = ScheduledTaskStore(path, defer_open=True)
    try:
        with pytest.raises(SQLiteOwnerConflict):
            store.open()
        assert not path.exists()
    finally:
        owner.close()
    store.open()
    assert store.recent_runs() == []
    store.close()


def test_failed_schema_initialization_releases_owner(tmp_path, monkeypatch):
    path = tmp_path / "schedule.db"
    store = ScheduledTaskStore(path, defer_open=True)
    initialize = store._initialize_schema

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(store, "_initialize_schema", fail)
    with pytest.raises(OSError, match="disk full"):
        store.open()
    assert not path.with_suffix(".db.owner").exists()
    monkeypatch.setattr(store, "_initialize_schema", initialize)
    store.open()
    assert store.recent_runs() == []
    store.close()


def test_concurrent_first_reads_initialize_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    store = ScheduledTaskStore(tmp_path / "schedule.db", defer_open=True)
    initialize = store._initialize_schema
    calls = []

    def initialize_once():
        calls.append(1)
        initialize()

    monkeypatch.setattr(store, "_initialize_schema", initialize_once)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: store.recent_runs(), range(8))) == [[]] * 8
    assert calls == [1]
    store.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _create_and_claim(tmp_path, now: datetime) -> tuple[ScheduledTaskStore, str, str]:
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    store.create(
        {
            "title": "recovery probe",
            "instruction": "do the work",
            "schedule_type": "once",
            "run_at": now.isoformat(),
            "created_by": "api_b",
            "requested_via": "autonomous_worker",
            "worker_role": "research",
        },
        now=now,
    )
    claim = store.claim_due(
        owner_session_id="cli-session",
        now=now,
        lease_seconds=300,
    )
    assert claim is not None
    run_id = claim["run"]["run_id"]
    schedule_id = claim["task"]["schedule_id"]
    store.close()
    return store, run_id, schedule_id


def test_startup_recovery_fails_expired_claimed_run(tmp_path):
    now = _now()
    _, run_id, schedule_id = _create_and_claim(tmp_path, now)

    # Simulate a crash window: the lease expires while no process owns the DB.
    with sqlite3.connect(tmp_path / "scheduled.db") as conn:
        conn.execute(
            "UPDATE scheduled_task_runs SET lease_expires_at = ? WHERE run_id = ?",
            ((now - timedelta(minutes=5)).isoformat(), run_id),
        )

    reopened = ScheduledTaskStore(tmp_path / "scheduled.db")
    try:
        runs = reopened.recent_runs(limit=10)
        recovered = next(run for run in runs if run["run_id"] == run_id)
        assert recovered["status"] == "failed"
        assert "lease expired" in recovered["error"]
        # The task must no longer reference the dead run as active.
        task = reopened.get(schedule_id)
        assert task["active_run_id"] is None
        assert task["last_run_status"] == "failed"
    finally:
        reopened.close()


def test_startup_recovery_is_noop_without_claims(tmp_path):
    now = _now()
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    try:
        store.create(
            {
                "title": "quiet probe",
                "instruction": "wait",
                "schedule_type": "once",
                "run_at": now.isoformat(),
                "created_by": "api_b",
                "requested_via": "companion_delegate",
                "worker_role": "general",
            },
            now=now,
        )
        assert store.recent_runs(limit=10) == []
    finally:
        store.close()


def test_startup_recovery_keeps_live_claims_untouched(tmp_path):
    now = _now()
    _, run_id, _ = _create_and_claim(tmp_path, now)

    # Fresh instance opens while the lease is still live: nothing to recover.
    reopened = ScheduledTaskStore(tmp_path / "scheduled.db")
    try:
        runs = reopened.recent_runs(limit=10)
        live = next(run for run in runs if run["run_id"] == run_id)
        assert live["status"] == "running"
    finally:
        reopened.close()


def test_latest_run_finds_schedule_result_outside_recent_display_window(tmp_path):
    now = _now()
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    try:
        target = store.create(
            {
                "title": "target employee task",
                "instruction": "return the result",
                "schedule_type": "once",
                "run_at": now.isoformat(),
                "created_by": "api_b",
                "requested_via": "autonomous_worker",
                "worker_role": "research",
                "autonomous_task_id": "auto-target",
            },
            now=now,
        )
        target_claim = store.claim_due(owner_session_id="target-session", now=now)
        assert target_claim is not None
        store.finish_run(
            target_claim["run"]["run_id"],
            owner_session_id="target-session",
            success=True,
            result_summary="target result",
            now=now + timedelta(seconds=1),
        )

        for index in range(201):
            current = now + timedelta(minutes=index + 1)
            store.create(
                {
                    "title": f"noise task {index}",
                    "instruction": "finish later",
                    "schedule_type": "once",
                    "run_at": current.isoformat(),
                    "created_by": "api_b",
                    "requested_via": "autonomous_worker",
                    "worker_role": "research",
                    "autonomous_task_id": f"noise-{index}",
                },
                now=current,
            )
            claim = store.claim_due(
                owner_session_id=f"noise-session-{index}",
                now=current,
            )
            assert claim is not None
            store.finish_run(
                claim["run"]["run_id"],
                owner_session_id=f"noise-session-{index}",
                success=True,
                result_summary="noise result",
                now=current + timedelta(seconds=1),
            )

        assert all(
            run["schedule_id"] != target["schedule_id"]
            for run in store.recent_runs(limit=200)
        )
        latest = store.latest_run(target["schedule_id"])
        assert latest is not None
        assert latest["result_summary"] == "target result"
    finally:
        store.close()
