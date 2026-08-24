from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.domain.agent.effect_outcomes import EffectOutcome
from voidcube.application.memory_manager import MemoryManager, infer_sync_tags


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _provider(sync_turn):
    return SimpleNamespace(
        name="mem",
        get_tool_schemas=lambda: [],
        sync_turn=sync_turn,
    )


def test_infer_sync_tags_is_conservative():
    assert infer_sync_tags("请自检记忆系统") == ["evaluation"]
    assert infer_sync_tags("请评估记忆系统", ["suite=memory"]) == [
        "suite=memory",
        "evaluation",
    ]
    assert infer_sync_tags("请记住我喜欢绿茶") == []
    assert infer_sync_tags("请诊断网络连接") == []


def test_memory_manager_passes_inferred_tags_to_new_provider():
    received = {}

    def sync_turn(_user, _assistant, *, session_id="", tags=None):
        received.update(session_id=session_id, tags=tags)
        return EffectOutcome(status="queued", details={})

    manager = MemoryManager()
    manager.add_provider(_provider(sync_turn))

    outcome = manager.sync_turn("继续自检记忆系统", "完成", session_id="session-1")

    assert outcome.status == "queued"
    assert received == {"session_id": "session-1", "tags": ["evaluation"]}


def test_memory_manager_keeps_explicit_evaluation_tag_without_duplicate():
    received = {}

    def sync_turn(_user, _assistant, *, session_id="", tags=None):
        received["tags"] = tags
        return EffectOutcome(status="queued", details={})

    manager = MemoryManager()
    manager.add_provider(_provider(sync_turn))
    manager.sync_turn(
        "继续自检记忆系统",
        "完成",
        tags=["evaluation", "suite=memory"],
    )

    assert received["tags"] == ["evaluation", "suite=memory"]


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
