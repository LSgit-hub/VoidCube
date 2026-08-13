from __future__ import annotations

import json
import time

import pytest

from agent.action_journal import ActionJournal
from tools.model_tools import handle_function_call
from tools.registry import ToolRegistry, registry


def test_prepared_action_can_remain_unforwarded_after_local_crash(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    action = journal.prepare(
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "x"},
        effect="idempotent_write",
        call_id="call-prepared",
    )

    assert journal.get(action.action_id)["state"] == "prepared"


def test_non_idempotent_unknown_requires_reconcile(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    action = journal.prepare(
        tool_name="terminal",
        arguments={"command": "create-resource"},
        effect="non_idempotent_write",
        call_id="call-unknown",
    )
    journal.transition(action.action_id, "dispatched")
    journal.transition(action.action_id, "unknown", reason="connection lost")

    record = journal.get(action.action_id)
    assert record["state"] == "unknown"
    assert record["retryability"] == "reconcile_first"
    with pytest.raises(ValueError, match="Illegal action transition"):
        journal.transition(action.action_id, "dispatched")
    journal.begin_reconcile(action.action_id, reason="query external resource")
    journal.reconcile(
        action.action_id,
        state="succeeded",
        evidence={"resource_id": "resource-1"},
    )
    assert journal.get(action.action_id)["state"] == "succeeded"


def test_same_idempotency_key_reuses_action(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    first = journal.prepare(
        tool_name="write_file",
        arguments={"content": "x", "path": "a.txt"},
        effect="idempotent_write",
        task_id="task",
        call_id="call-1",
    )
    replay = journal.prepare(
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "x"},
        effect="idempotent_write",
        task_id="task",
        call_id="call-1",
    )

    assert replay == first


def test_new_protocol_call_id_reuses_stable_task_operation(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    first = journal.prepare(
        tool_name="mem_remember",
        arguments={"title": "fact", "summary": "stable"},
        effect="non_idempotent_write",
        task_id="task-1",
        attempt_id="attempt-1",
        call_id="protocol-call-1",
    )
    replay = journal.prepare(
        tool_name="mem_remember",
        arguments={"summary": "stable", "title": "fact"},
        effect="non_idempotent_write",
        task_id="task-1",
        attempt_id="attempt-1",
        call_id="protocol-call-2",
    )

    assert replay == first
    assert journal.get(first.action_id)["operation_id"]


def test_startup_recovery_moves_stale_dispatched_to_unknown(tmp_path):
    path = tmp_path / "actions.db"
    journal = ActionJournal(path)
    action = journal.prepare(
        tool_name="terminal",
        arguments={"command": "create-resource"},
        effect="non_idempotent_write",
        task_id="task-1",
    )
    assert journal.claim_dispatch(action.action_id)

    recovered = journal.recover_stale_dispatched(stale_after_seconds=0, now=time.time() + 1)

    assert recovered == 1
    assert journal.get(action.action_id)["state"] == "unknown"


def test_claim_dispatch_is_single_winner(tmp_path):
    path = tmp_path / "actions.db"
    journal = ActionJournal(path)
    replay_journal = ActionJournal(path)
    action = journal.prepare(
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "x"},
        effect="idempotent_write",
        call_id="call-claim",
    )
    replay = replay_journal.prepare(
        tool_name="write_file",
        arguments={"content": "x", "path": "a.txt"},
        effect="idempotent_write",
        call_id="call-claim",
    )

    assert replay == action
    assert journal.claim_dispatch(action.action_id)
    assert not replay_journal.claim_dispatch(action.action_id)
    assert journal.get(action.action_id)["state"] == "dispatched"


def test_journal_prepare_failure_blocks_side_effect(monkeypatch):
    invoked = []
    registry.register(
        name="journal_block_test",
        handler=lambda args: invoked.append(args) or "ok",
        effect="non_idempotent_write",
    )

    class _BrokenJournal:
        def prepare(self, **_kwargs):
            raise RuntimeError("journal unavailable")

    monkeypatch.setattr(
        "agent.action_journal.get_action_journal",
        lambda: _BrokenJournal(),
    )
    try:
        result = json.loads(
            handle_function_call(
                "journal_block_test",
                {"value": 1},
                tool_call_id="call-block",
            )
        )
    finally:
        registry.unregister("journal_block_test")

    assert invoked == []
    assert "journal unavailable" in result["error"]


def test_handler_exception_after_dispatch_records_unknown(monkeypatch, tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")

    def raise_after_effect(args):
        del args
        raise RuntimeError("lost response")

    registry.register(
        name="raise_after_effect_test",
        handler=raise_after_effect,
        effect="non_idempotent_write",
    )
    monkeypatch.setattr(
        "agent.action_journal.get_action_journal",
        lambda: journal,
    )
    try:
        result = json.loads(
            handle_function_call(
                "raise_after_effect_test",
                {"resource": "target"},
                task_id="task-unknown",
                tool_call_id="call-unknown-after-dispatch",
            )
        )
    finally:
        registry.unregister("raise_after_effect_test")

    action = journal.find_by_call_id(
        "call-unknown-after-dispatch",
        task_id="task-unknown",
    )
    assert "lost response" in result["error"]
    assert action is not None
    assert action.state == "unknown"


def test_dynamic_memory_write_uses_journal_and_stable_operation(monkeypatch, tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    invoked = []
    monkeypatch.setattr("agent.action_journal.get_action_journal", lambda: journal)

    result = handle_function_call(
        "mem_remember",
        {"title": "fact", "summary": "detail"},
        task_id="task-memory",
        tool_call_id="call-memory",
        dynamic_handler=lambda name, args: invoked.append((name, args)) or '{"success": true}',
        dynamic_effect="non_idempotent_write",
    )

    action = journal.find_by_call_id("call-memory", task_id="task-memory")
    assert json.loads(result)["success"] is True
    assert invoked == [("mem_remember", {"title": "fact", "summary": "detail"})]
    assert action is not None
    assert action.state == "succeeded"


def test_unclassified_tool_defaults_to_non_idempotent_write():
    local = ToolRegistry()
    local.register(name="unknown", handler=lambda: "ok")

    assert local.get_effect("unknown") == "non_idempotent_write"
    assert local.get_effect("missing") == "non_idempotent_write"


def test_inactive_execution_lease_blocks_dispatch(monkeypatch):
    invoked = []
    registry.register(
        name="lease_block_test",
        handler=lambda args: invoked.append(args) or "ok",
        effect="non_idempotent_write",
    )
    try:
        result = json.loads(
            handle_function_call(
                "lease_block_test",
                {},
                tool_call_id="call-stale",
                main_runtime={
                    "execution_lease": {
                        "generation": 2,
                        "attempt_id": "old",
                        "state": "expired",
                    }
                },
            )
        )
    finally:
        registry.unregister("lease_block_test")

    assert invoked == []
    assert "stale_execution_lease" in result["error"]


def test_action_ref_uses_task_scoped_call_id_and_projects_filtered_evidence(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    first = journal.prepare(
        tool_name="write_file",
        arguments={"path": "first.txt", "content": "secret"},
        effect="idempotent_write",
        task_id="task-first",
        call_id="reused-call",
    )
    second = journal.prepare(
        tool_name="write_file",
        arguments={"path": "second.txt", "content": "secret"},
        effect="idempotent_write",
        task_id="task-second",
        call_id="reused-call",
    )
    journal.transition(first.action_id, "dispatched")
    journal.transition(
        first.action_id,
        "succeeded",
        evidence={
            "resource_id": "resource-1",
            "result_hash": "hash-1",
            "access_token": "must-not-leak",
            "result_preview": "also-not-projected",
        },
    )

    ref = journal.find_by_call_id("reused-call", task_id="task-first")
    assert ref.action_id == first.action_id
    assert ref.action_id != second.action_id
    assert ref.state == "succeeded"
    assert ref.target_summary == "first.txt"
    assert len(ref.evidence_refs) == 1

    metadata_only = journal.evidence_projection(first.action_id)
    assert "payload" not in metadata_only[0]
    filtered = journal.evidence_projection(first.action_id, include_payload=True)
    assert filtered[0]["payload"] == {
        "resource_id": "resource-1",
        "result_hash": "hash-1",
    }


def test_action_arguments_redact_credentials_without_changing_idempotency(tmp_path):
    journal = ActionJournal(tmp_path / "actions.db")
    action = journal.prepare(
        tool_name="deploy",
        arguments={
            "resource": "service-1",
            "api_key": "sensitive-key",
            "nested": {"access_token": "sensitive-token", "region": "east"},
        },
        effect="non_idempotent_write",
        call_id="call-redacted",
    )

    stored = json.loads(journal.get(action.action_id)["normalized_arguments"])
    assert stored == {
        "api_key": "[redacted]",
        "nested": {"access_token": "[redacted]", "region": "east"},
        "resource": "service-1",
    }
