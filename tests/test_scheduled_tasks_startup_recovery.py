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


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


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
