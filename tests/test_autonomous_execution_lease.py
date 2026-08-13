from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from systems.supervisor.autonomous_chain_store import (
    AutonomousChainStore,
    StaleExecutionLeaseError,
)


def _approved_task(store: AutonomousChainStore):
    task = store.create_task(
        title="lease test",
        task_type="self_learning",
        metadata={"execution_kind": "self_learning"},
    )
    return store.update_status(task.task_id, status="approved", reason="ready")


def test_concurrent_claim_has_one_winner_and_one_generation_increment(tmp_path):
    store = AutonomousChainStore(tmp_path / "chain.json")
    task = _approved_task(store)
    barrier = threading.Barrier(3)
    winners = []
    failures = []

    def claim(owner: str) -> None:
        barrier.wait()
        try:
            winners.append(
                store.claim_execution(task.task_id, owner_session_id=owner)
            )
        except StaleExecutionLeaseError as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=claim, args=("owner-a",)),
        threading.Thread(target=claim, args=("owner-b",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert len(winners) == 1
    assert len(failures) == 1
    persisted = store.get_task(task.task_id)
    assert persisted is not None
    assert persisted.execution_lease.generation == 1
    assert persisted.status == "running"


def test_old_attempt_is_rejected_after_same_session_reclaims(tmp_path):
    store = AutonomousChainStore(tmp_path / "chain.json")
    task = _approved_task(store)
    first = store.claim_execution(task.task_id, owner_session_id="same-session")
    first_generation = first.execution_lease.generation
    first_attempt = str(first.execution_lease.attempt_id)

    store.begin_reconcile(task.task_id, reason="owner expired")
    store.update_status(task.task_id, status="approved", reason="reconciled as not dispatched")
    second = store.claim_execution(task.task_id, owner_session_id="same-session")

    assert second.execution_lease.generation == first_generation + 1
    assert second.execution_lease.attempt_id != first_attempt
    with pytest.raises(StaleExecutionLeaseError, match="stale_execution_lease"):
        store.finalize_execution(
            task.task_id,
            generation=first_generation,
            attempt_id=first_attempt,
            status="completed",
            actor="cli_agent",
            reason="late result",
        )
    assert store.get_task(task.task_id).status == "running"


def test_expired_active_lease_is_rejected(tmp_path):
    store = AutonomousChainStore(tmp_path / "chain.json")
    task = _approved_task(store)
    claimed = store.claim_execution(task.task_id, owner_session_id="owner")
    snapshot = store._load_snapshot()
    snapshot.tasks[0].execution_lease.expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    store._write_snapshot(snapshot)

    with pytest.raises(StaleExecutionLeaseError, match="stale_execution_lease"):
        store.validate_execution_lease(
            task.task_id,
            generation=claimed.execution_lease.generation,
            attempt_id=str(claimed.execution_lease.attempt_id),
            owner_session_id="owner",
        )


def test_two_store_instances_do_not_lose_concurrent_creates(tmp_path):
    path = tmp_path / "chain.json"
    stores = [AutonomousChainStore(path), AutonomousChainStore(path)]
    barrier = threading.Barrier(3)

    def create(store: AutonomousChainStore, title: str) -> None:
        barrier.wait()
        store.create_task(title=title)

    threads = [
        threading.Thread(target=create, args=(stores[0], "first")),
        threading.Thread(target=create, args=(stores[1], "second")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert {task.title for task in AutonomousChainStore(path).list_tasks()} == {
        "first",
        "second",
    }


def test_legacy_snapshot_migrates_to_unclaimed_generation_zero(tmp_path):
    path = tmp_path / "chain.json"
    path.write_text(
        '{"version":1,"tasks":[{"task_id":"legacy","title":"old",'
        '"status":"approved","metadata":{"owner_session_id":"stale"}}]}',
        encoding="utf-8",
    )

    task = AutonomousChainStore(path).get_task("legacy")

    assert task is not None
    assert task.execution_lease.generation == 0
    assert task.execution_lease.attempt_id is None
    assert task.execution_lease.owner_session_id is None
    assert task.execution_lease.state == "released"
    assert '"version": 2' in path.read_text(encoding="utf-8")
