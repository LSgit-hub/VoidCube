import pytest

from voidcube.application.ports import (
    CallbackEventPort, CallbackGovernancePort, CallbackPersistencePort,
    CallbackTaskPort, RuntimePorts,
)
from voidcube.domain.events import MemorySyncFailed, TurnCompleted
from voidcube.domain.agent.effect_outcomes import EffectOutcome


def test_callback_event_port_returns_structured_success():
    seen = []
    outcome = CallbackEventPort(seen.append).emit({"kind": "turn_completed"})
    assert outcome.status == "succeeded"
    assert seen == [{"kind": "turn_completed"}]


def test_callback_event_port_contains_sink_failures():
    def fail(_event):
        raise RuntimeError("sink down")

    outcome = CallbackEventPort(fail).emit(object())
    assert outcome.status == "failed"
    assert "sink down" in (outcome.error or "")


def test_runtime_ports_default_to_optional_capabilities():
    ports = RuntimePorts()
    assert ports.memory is None
    assert ports.events is None


def test_callback_persistence_port_requires_structured_outcome():
    outcome = CallbackPersistencePort(lambda *_args: None).persist([])
    assert outcome.status == "failed"
    assert "must return EffectOutcome" in (outcome.error or "")


def test_domain_events_are_typed_and_immutable():
    event = TurnCompleted(session_id="s1", response_length=12)
    assert event.response_length == 12
    failure = MemorySyncFailed(session_id="s1", error="outbox down")
    assert failure.error == "outbox down"


def test_task_and_governance_callbacks_normalize_outcomes():
    task = CallbackTaskPort(lambda value, **_: EffectOutcome(status="queued", details={"task": value}))
    governance = CallbackGovernancePort(lambda value: EffectOutcome(status="succeeded", details={"decision": value}))
    assert task.submit("t1", session_id="s1").status == "queued"
    assert governance.record_decision("approve").status == "succeeded"


def test_task_and_governance_callback_failures_are_contained():
    task = CallbackTaskPort(lambda *_args, **_kwargs: None)
    governance = CallbackGovernancePort(lambda _value: (_ for _ in ()).throw(RuntimeError("down")))
    assert task.submit("t").status == "failed"
    assert governance.record_decision("approve").status == "failed"
