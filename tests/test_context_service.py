from types import SimpleNamespace

import pytest

from voidcube.application.ports import CallbackEventPort
from voidcube.domain.agent.effect_outcomes import EffectOutcome
from voidcube.domain.events import ContextCompressed
from voidcube.runtime.agent.context_service import ContextBindings, ContextService


def _service(*, compress=None, memory=None, continuation=None, events=None):
    calls = []
    seen = []
    session = {"id": "original"}

    def compress_default(messages, **kwargs):
        calls.append(("compress", kwargs))
        return [{"role": "user", "content": "summary"}]

    def continue_session(prompt):
        calls.append(("continue", prompt))
        session["id"] = "continuation"
        return EffectOutcome(status="succeeded")

    engine = SimpleNamespace(
        compress=compress or compress_default,
        last_prompt_tokens=8000, last_completion_tokens=2000,
        last_total_tokens=10000, compression_count=1, threshold_tokens=10000,
    )
    bindings = ContextBindings(
        session_id=lambda: session["id"],
        system_prompt=lambda original, rebuild: calls.append(("prompt", rebuild)) or "system",
        continue_session=continuation or continue_session,
        todo_snapshot=lambda: "todo",
        reset_pressure=lambda key: calls.append(("pressure", key)),
        reset_file_reads=lambda key: calls.append(("files", key)),
        warn=lambda message: calls.append(("warning", message)),
    )
    service = ContextService(
        engine=engine, bindings=bindings, memory=memory,
        events=events or CallbackEventPort(seen.append),
    )
    return service, engine, calls, seen


def test_compaction_preserves_memory_before_discard_and_reports_effects():
    original = [{"role": "user", "content": "important fact"}, {"role": "assistant", "content": "answer"}]
    memorized = []
    memory = SimpleNamespace(on_pre_compress=lambda messages: memorized.extend(messages))

    def compress(messages, **kwargs):
        assert memorized == original
        assert kwargs == {"current_tokens": 12000, "focus_topic": "pending work"}
        return [{"role": "user", "content": "summary"}]

    service, engine, calls, events = _service(compress=compress, memory=memory)
    messages, prompt = service.compress(original, None, approx_tokens=12000, task_id="task", focus_topic="pending work")

    assert messages[-1]["content"] == "todo"
    assert prompt == "system"
    assert engine.last_prompt_tokens == engine.last_total_tokens < 10000
    assert engine.last_completion_tokens == 0
    assert ("pressure", "continuation") in calls
    assert ("files", "task") in calls
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ContextCompressed)
    assert event.session_id == "continuation"
    assert event.details["previous_session_id"] == "original"
    assert event.details["memory"]["status"] == "succeeded"
    assert event.details["continuation"]["status"] == "succeeded"


@pytest.mark.parametrize("copy_history", [False, True])
def test_noop_does_not_rotate_session_duplicate_todos_or_emit_compression(copy_history):
    service, engine, calls, events = _service(
        compress=lambda messages, **kwargs: list(messages) if copy_history else messages,
    )
    original = [{"role": "user", "content": "short history"}]
    compressed, _ = service.compress(original, None)
    assert compressed is original
    assert calls == [("prompt", False)]
    assert events == []
    assert engine.last_total_tokens == 10000


def test_engine_can_compact_in_place():
    def compress(messages, **kwargs):
        messages[:] = [{"role": "user", "content": "summary"}]
        return messages

    service, _, _, events = _service(compress=compress)
    compressed, _ = service.compress([{"role": "user", "content": "old"}], None)
    assert len(compressed) == 2
    assert len(events) == 1


@pytest.mark.parametrize("invalid_result", [False, True])
def test_memory_and_persistence_failure_remain_visible_without_losing_compaction(invalid_result):
    def fail(*args):
        raise RuntimeError("unavailable")

    service, _, _, events = _service(
        memory=SimpleNamespace(on_pre_compress=fail),
        continuation=(lambda prompt: None) if invalid_result else fail,
    )
    compressed, _ = service.compress([{"role": "user", "content": "old"}], None)
    assert compressed[0]["content"] == "summary"
    event = events[0]
    assert event.session_id == "original"
    assert event.details["memory"]["status"] == "failed"
    assert event.details["continuation"]["status"] == "failed"


def test_event_port_exception_does_not_destroy_compacted_history():
    def fail(event):
        raise RuntimeError("subscriber down")

    service, _, _, _ = _service(events=SimpleNamespace(emit=fail))
    compressed, _ = service.compress([{"role": "user", "content": "old"}], None)
    assert compressed[0]["content"] == "summary"
