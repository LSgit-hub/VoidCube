from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.effect_outcomes import EffectOutcome
from agent.memory_manager import MemoryManager


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _provider(sync_turn):
    return SimpleNamespace(
        name="mem",
        get_tool_schemas=lambda: [],
        sync_turn=sync_turn,
    )


def test_memory_manager_reports_durable_provider_queue_receipt():
    manager = MemoryManager()
    manager.add_provider(
        _provider(
            lambda _user, _assistant, session_id="": EffectOutcome(
                status="queued",
                details={"write_id": "write-1", "durable_outbox": True},
            )
        )
    )

    outcome = manager.sync_turn("question", "answer", session_id="session-1")

    assert outcome.status == "queued"
    assert outcome.details == {
        "provider": "mem",
        "write_id": "write-1",
        "durable_outbox": True,
    }


def test_memory_manager_converts_provider_exception_to_failed_outcome():
    def fail(_user, _assistant, session_id=""):
        raise OSError("outbox unavailable")

    manager = MemoryManager()
    manager.add_provider(_provider(fail))

    outcome = manager.sync_turn("question", "answer", session_id="session-1")

    assert outcome.status == "failed"
    assert "outbox unavailable" in (outcome.error or "")
    assert outcome.details["provider"] == "mem"


def test_memory_manager_reports_provider_contract_violation():
    manager = MemoryManager()
    manager.add_provider(_provider(lambda *_args, **_kwargs: None))

    outcome = manager.sync_turn("question", "answer")

    assert outcome.status == "failed"
    assert "must return EffectOutcome" in (outcome.error or "")
    assert outcome.details["provider"] == "mem"


def test_memory_manager_reports_missing_provider_as_skipped():
    outcome = MemoryManager().sync_turn("question", "answer")

    assert outcome.status == "skipped"
    assert outcome.details["reason"] == "no_provider"
