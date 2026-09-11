import pytest

from voidcube.application.ports import CallbackEventPort, CallbackPersistencePort, RuntimePorts


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
