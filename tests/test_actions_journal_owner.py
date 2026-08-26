"""Action Journal concurrency gate: two in-process instances writing the same
DB under contention must retry (BEGIN IMMEDIATE + jitter) instead of raising
``database is locked``, and keep all writes durable and consistent.

The owner lease is process-reentrant, so two ActionJournal instances on the
same path are legal inside one process; this is exactly the contention case
the application-level jitter retry is designed for.
"""

from __future__ import annotations

import threading
import uuid

import pytest

from voidcube.infrastructure.persistence.action_journal import ActionJournal


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _worker(journal: ActionJournal, barrier: threading.Barrier, results: list, tag: str) -> None:
    try:
        barrier.wait()
        for i in range(25):
            prepared = journal.prepare(
                tool_name="concurrent_probe",
                arguments={"worker": tag, "index": i, "payload": "x" * 64},
                effect="idempotent_write",
                task_id=f"{tag}-{i}",
                owner_session_id=f"session-{tag}",
            )
            claimed = journal.claim_dispatch(
                prepared.action_id,
                reason="contention_probe",
                owner_session_id=f"session-{tag}",
            )
            if claimed:
                journal.record_outcome(prepared.action_id, "succeeded", reason="ok")
            results.append(prepared.action_id)
    except BaseException as exc:  # pragma: no cover - failure reporting
        results.append(f"ERROR:{type(exc).__name__}:{exc}")


def test_concurrent_instances_survive_write_contention(tmp_path):
    db_path = tmp_path / "actions.db"
    first = ActionJournal(db_path)
    second = ActionJournal(db_path)

    try:
        results: list = []
        barrier = threading.Barrier(4)
        threads = [
            threading.Thread(target=_worker, args=(first, barrier, results, f"a{n}"))
            for n in range(2)
        ] + [
            threading.Thread(target=_worker, args=(second, barrier, results, f"b{n}"))
            for n in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        errors = [item for item in results if isinstance(item, str) and item.startswith("ERROR:")]
        assert not errors, f"concurrent writes failed: {errors}"
        assert len([item for item in results if isinstance(item, str)]) == 100

        stats = first.execution_stats()
        assert stats["closed"] is False
        assert stats["write_count"] >= 100
        # All prepared actions must be durable and terminal.
        unknown = first.list_unknown()
        assert unknown == [], f"expected no unknown actions, got {len(unknown)}"
        for item in results[:10]:
            if isinstance(item, str) and not item.startswith("ERROR:"):
                record = first.get(item)
                assert record is not None
                assert record["state"] in {"prepared", "dispatched", "succeeded"}
    finally:
        first.close()
        second.close()


def test_execution_stats_expose_retries_and_closed_state(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    assert journal.execution_stats() == {
        "closed": False,
        "write_count": 0,
        "write_busy_retries": 0,
        "write_failures": 0,
    }
    journal.prepare(
        tool_name="probe",
        arguments={"a": 1},
        effect="read_only",
        task_id="stats-probe",
    )
    stats = journal.execution_stats()
    assert stats["write_count"] >= 1
    assert stats["write_busy_retries"] >= 0
    assert stats["write_failures"] == 0
    journal.close()
    assert journal.execution_stats()["closed"] is True


def test_closed_journal_rejects_new_writes(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    journal.close()
    with pytest.raises(RuntimeError, match="ActionJournal is closed"):
        journal.prepare(
            tool_name="probe",
            arguments={"a": 1},
            effect="read_only",
            task_id="closed-probe",
        )


def test_parallel_prepare_same_idempotency_key_yields_single_action(tmp_path):
    """Concurrent prepare of the same operation must coalesce onto one row."""
    journal = ActionJournal(tmp_path / "actions.db")
    try:
        barrier = threading.Barrier(4)
        keys: list[str] = []
        errors: list[Exception] = []

        def _hammer() -> None:
            try:
                barrier.wait()
                for _ in range(5):
                    prepared = journal.prepare(
                        tool_name="coalesce_probe",
                        arguments={"seed": "same-operation"},
                        effect="idempotent_write",
                        task_id="coalesce",
                        operation_id="stable-operation-id",
                    )
                    keys.append(prepared.action_id)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert not errors
        assert keys, "no prepares returned"
        # All callers must have been routed to the same coalesced action.
        assert len(set(keys)) == 1
        record = journal.get(keys[0])
        assert record is not None
        assert record["idempotency_key"] == journal.prepare(
            tool_name="coalesce_probe",
            arguments={"seed": "same-operation"},
            effect="idempotent_write",
            task_id="coalesce",
            operation_id="stable-operation-id",
        ).idempotency_key
    finally:
        journal.close()


def test_contention_gate_keeps_no_orphan_marker_after_close(tmp_path):
    db_path = tmp_path / "actions.db"
    journal = ActionJournal(db_path)
    assert db_path.with_name("actions.db.owner").exists()
    journal.close()
    assert not db_path.with_name("actions.db.owner").exists()
