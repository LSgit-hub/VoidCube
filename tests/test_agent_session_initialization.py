from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from agent.session_initialization import (
    AgentSessionInitializationPorts,
    AgentSessionInitializationRuntime,
)


class _SessionDB:
    def __init__(self):
        self.created = []

    def create_session(self, **kwargs):
        self.created.append(kwargs)


class _Persistence:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _ports(**overrides):
    values = {
        "requested_session_id": None,
        "platform": "cli",
        "model_reader": lambda: "model",
        "base_url_reader": lambda: "https://api.example/v1",
        "system_prompt_reader": lambda: None,
        "tools_reader": lambda: [],
        "user_message_override_reader": lambda: (None, None),
        "session_db": _SessionDB(),
        "parent_session_id": "parent",
        "max_iterations": 90,
        "reasoning_config": {"effort": "medium"},
        "max_tokens": 512,
        "persist_session": True,
        "verbose_logging": False,
        "checkpoints_enabled": True,
        "checkpoint_max_snapshots": 7,
        "home_provider": lambda: Path("C:/voidcube-home"),
        "clock": lambda: datetime(2026, 8, 5, 12, 34, 56),
        "session_uuid_factory": lambda: "abcdef1234567890",
        "checkpoint_factory": lambda **kwargs: SimpleNamespace(kwargs=kwargs),
        "persistence_factory": _Persistence,
    }
    values.update(overrides)
    return AgentSessionInitializationPorts(**values)


def test_session_runtime_creates_identity_db_registration_and_persistence():
    ports = _ports()

    result = AgentSessionInitializationRuntime(ports).initialize()

    assert result.session_id == "20260805_123456_abcdef"
    assert result.session_start == datetime(2026, 8, 5, 12, 34, 56)
    assert result.logs_dir == Path("C:/voidcube-home/sessions")
    assert result.checkpoint_manager.kwargs == {
        "enabled": True,
        "max_snapshots": 7,
    }
    assert result.session_persistence.kwargs["session_id"]() == result.session_id
    assert result.session_persistence.kwargs["model"]() == "model"

    created = ports.session_db.created
    assert created == [
        {
            "session_id": result.session_id,
            "source": "cli",
            "model": "model",
            "model_config": {
                "max_iterations": 90,
                "reasoning_config": {"effort": "medium"},
                "max_tokens": 512,
            },
            "user_id": None,
            "parent_session_id": "parent",
        }
    ]


def test_requested_session_id_is_preserved():
    result = AgentSessionInitializationRuntime(
        _ports(requested_session_id="existing-session")
    ).initialize()

    assert result.session_id == "existing-session"


def test_session_registration_failure_does_not_disable_persistence():
    class _FailingDB(_SessionDB):
        def create_session(self, **_kwargs):
            raise RuntimeError("database is locked")

    db = _FailingDB()
    result = AgentSessionInitializationRuntime(_ports(session_db=db)).initialize()

    assert result.session_persistence.kwargs["session_db"] is db
