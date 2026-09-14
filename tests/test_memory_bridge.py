from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.domain.agent.effect_outcomes import EffectOutcome
from voidcube.infrastructure.memory.memory_bridge import MemoryBridge, create_memory_bridge
from plugins.memory.mem import MemMemoryProvider
from voidcube.application.memory_context import (
    build_memory_context_block,
    infer_sync_tags,
)


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
    assert infer_sync_tags(
        "Review the conversation above and consider saving or updating a skill if appropriate."
    ) == ["evaluation"]


def test_memory_bridge_passes_inferred_tags_to_new_provider():
    received = {}

    def sync_turn(_user, _assistant, *, session_id="", tags=None):
        received.update(session_id=session_id, tags=tags)
        return EffectOutcome(status="queued", details={})

    manager = MemoryBridge(_provider(sync_turn))

    outcome = manager.sync_turn("继续自检记忆系统", "完成", session_id="session-1")

    assert outcome.status == "queued"
    assert received == {"session_id": "session-1", "tags": ["evaluation"]}


def test_memory_bridge_keeps_explicit_evaluation_tag_without_duplicate():
    received = {}

    def sync_turn(_user, _assistant, *, session_id="", tags=None):
        received["tags"] = tags
        return EffectOutcome(status="queued", details={})

    manager = MemoryBridge(_provider(sync_turn))
    manager.sync_turn(
        "继续自检记忆系统",
        "完成",
        tags=["evaluation", "suite=memory"],
    )

    assert received["tags"] == ["evaluation", "suite=memory"]


def test_memory_bridge_reports_durable_provider_queue_receipt():
    manager = MemoryBridge(
        _provider(
            lambda _user, _assistant, *, session_id="", tags=None: EffectOutcome(
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


def test_memory_bridge_converts_provider_exception_to_failed_outcome():
    def fail(_user, _assistant, *, session_id="", tags=None):
        raise OSError("outbox unavailable")

    manager = MemoryBridge(_provider(fail))

    outcome = manager.sync_turn("question", "answer", session_id="session-1")

    assert outcome.status == "failed"
    assert "outbox unavailable" in (outcome.error or "")
    assert outcome.details["provider"] == "mem"


def test_memory_bridge_reports_provider_contract_violation():
    manager = MemoryBridge(_provider(lambda *_args, **_kwargs: None))

    outcome = manager.sync_turn("question", "answer")

    assert outcome.status == "failed"
    assert "must return EffectOutcome" in (outcome.error or "")
    assert outcome.details["provider"] == "mem"


def test_memory_bridge_normalizes_non_string_turn_content():
    received = {}

    def sync_turn(user, assistant, *, session_id="", tags=None):
        received.update(user=user, assistant=assistant, tags=tags)
        return EffectOutcome(status="queued", details={})

    manager = MemoryBridge(_provider(sync_turn))

    outcome = manager.sync_turn(None, {"text": "完成"})

    assert outcome.status == "queued"
    assert received == {"user": "", "assistant": "{'text': '完成'}", "tags": []}


def test_memory_context_zero_normalized_score_is_not_replaced_by_raw_score():
    raw = '{"results":[{"summary":"irrelevant","normalized_score":0,"raw_score":1}]}'
    assert build_memory_context_block(raw, min_score=0.5) == ""


def test_bridge_tool_surface_is_stable_and_unknown_tools_never_dispatch():
    schemas = [{"name": "mem_search", "parameters": {"type": "object"}}]
    calls = []
    provider = SimpleNamespace(
        get_tool_schemas=lambda: schemas,
        handle_tool_call=lambda name, args, **kwargs: calls.append((name, args, kwargs)) or "ok",
    )
    bridge = MemoryBridge(provider)
    schemas[0]["name"] = "changed"
    bridge.get_all_tool_schemas()[0]["name"] = "also-changed"
    assert bridge.get_all_tool_schemas()[0]["name"] == "mem_search"
    assert bridge.has_tool("mem_search")
    assert not bridge.has_tool("changed")
    assert "Unknown memory tool" in bridge.handle_tool_call("changed", {})
    assert calls == []
    assert bridge.handle_tool_call("mem_search", {"query": "decision"}, task_id="t1") == "ok"
    assert calls == [("mem_search", {"query": "decision"}, {"task_id": "t1"})]


@pytest.mark.parametrize("schema_failure", [False, True])
def test_factory_cleans_failed_provider_and_preserves_original_error(monkeypatch, tmp_path, schema_failure):
    calls = []

    def initialize(**settings):
        calls.append("initialize")
        if not schema_failure:
            raise OSError("initialization failed")

    def schemas():
        raise OSError("schema failed")

    def shutdown():
        calls.append("shutdown")
        raise RuntimeError("cleanup failed")

    provider = SimpleNamespace(initialize=initialize, get_tool_schemas=schemas, shutdown=shutdown)
    monkeypatch.setattr("plugins.memory.mem.MemMemoryProvider", lambda: provider)
    monkeypatch.setattr("voidcube.infrastructure.config.runtime_paths.get_VoidCube_home", lambda: tmp_path)
    monkeypatch.setattr("voidcube.infrastructure.config.profiles.get_active_profile_name", lambda: "test")
    with pytest.raises(OSError, match="schema failed" if schema_failure else "initialization failed"):
        create_memory_bridge(session_id="s1")
    assert calls == ["initialize", "shutdown"]


def test_real_bridge_factory_binds_scope_and_reports_durable_session_closure(monkeypatch, tmp_path):
    import json
    from plugins.memory.mem.outbox import MemoryWriteOutbox

    provider = MemMemoryProvider()
    settings = {}
    initialize = provider.initialize

    def capture_initialize(**kwargs):
        settings.update(kwargs)
        initialize(**kwargs)

    monkeypatch.setattr(provider, "initialize", capture_initialize)
    monkeypatch.setattr("plugins.memory.mem.MemMemoryProvider", lambda: provider)
    monkeypatch.setattr("voidcube.infrastructure.config.runtime_paths.get_VoidCube_home", lambda: tmp_path)
    monkeypatch.setattr("voidcube.infrastructure.config.profiles.get_active_profile_name", lambda: "test-profile")
    monkeypatch.setattr("voidcube.infrastructure.config.configuration.load_config", lambda: {"memory": {"mem": {"auto_sync": False}}})
    bridge = create_memory_bridge(session_id="s1", platform="gateway", user_id="user-1")
    try:
        assert settings == {
            "session_id": "s1", "platform": "gateway", "user_id": "user-1",
            "agent_context": "primary", "agent_workspace": "VoidCube",
            "agent_identity": "test-profile", "VoidCube_home": str(tmp_path),
        }
        bridge.bind_session("s2")
        recalled = []
        monkeypatch.setattr(provider, "_request_json", lambda method, path, payload: recalled.append(payload) or {"context": "fact"})
        assert bridge.prefetch("decision") == "fact"
        assert recalled[-1]["current_session_id"] == "s2"
        assert bridge.sync_turn("q", "a").details["reason"] == "auto_sync_disabled"
        assert bridge.on_pre_compress([]).status == "skipped"
        closure = bridge.on_session_end([])
        assert closure.status == "queued"
        assert closure.details["write_id"] == "session-close:s2"
        assert closure.details["durable_outbox"] is True
        # Reopen the real outbox: this receipt must survive an adapter restart.
        reopened = MemoryWriteOutbox(provider._outbox.path)
        pending = reopened.next_due()
        assert pending["session_id"] == "s2"
        assert pending["owner_id"] == "user-1"
        assert pending["operation"] == "close_session"
    finally:
        assert bridge.shutdown().status == "succeeded"
    assert bridge.shutdown().status == "skipped"
