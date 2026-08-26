from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.interfaces.cli.entrypoints import management
from voidcube.infrastructure.persistence.session_db import SessionDB


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_session_owner_status_and_context_manager_release_state(tmp_path):
    db_path = tmp_path / "state.db"

    with SessionDB(db_path) as db:
        status = db.owner_status()
        assert status["domain"] == "session"
        assert status["owner"] == "session-owner"
        assert status["owned"] is True
        assert status["pid"]
        assert db.execution_stats()["closed"] is False

    assert not db_path.with_name("state.db.owner").exists()
    assert db.owner_status()["owned"] is False
    assert db.execution_stats()["closed"] is True


def test_closed_session_owner_rejects_new_writes(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.close()

    with pytest.raises(RuntimeError, match="SessionDB is closed"):
        db.create_session("closed-session", source="cli")


def test_sessions_command_releases_owner_on_early_return(monkeypatch, capsys):
    class FakeSessionDB:
        instances = []

        def __init__(self):
            self.closed = False
            self.__class__.instances.append(self)

        def list_sessions_rich(self, **_kwargs):
            return []

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "voidcube.infrastructure.persistence.session_db.SessionDB",
        FakeSessionDB,
    )

    management.cmd_sessions(
        SimpleNamespace(sessions_action="list", source=None, limit=5)
    )

    assert "No sessions found." in capsys.readouterr().out
    assert FakeSessionDB.instances[0].closed is True


def test_sessions_command_releases_owner_when_command_fails(monkeypatch):
    class FailingSessionDB:
        instance = None

        def __init__(self):
            self.__class__.instance = self
            self.closed = False

        def list_sessions_rich(self, **_kwargs):
            raise RuntimeError("query failed")

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "voidcube.infrastructure.persistence.session_db.SessionDB",
        FailingSessionDB,
    )

    with pytest.raises(RuntimeError, match="query failed"):
        management.cmd_sessions(
            SimpleNamespace(sessions_action="list", source=None, limit=5)
        )

    assert FailingSessionDB.instance.closed is True


def test_sessions_browse_releases_owner_before_exec(monkeypatch, capsys):
    events: list[str] = []

    class ExecReplaced(RuntimeError):
        pass

    class FakeSessionDB:
        def list_sessions_rich(self, **_kwargs):
            return [{"id": "session-1", "source": "cli", "preview": ""}]

        def close(self):
            events.append("close")

    def fake_execvp(_file, _args):
        events.append("exec")
        raise ExecReplaced

    monkeypatch.setattr(
        "voidcube.infrastructure.persistence.session_db.SessionDB",
        FakeSessionDB,
    )
    monkeypatch.setattr(management, "_session_browse_picker", lambda _sessions: "session-1")
    monkeypatch.setattr(management.os, "execvp", fake_execvp)
    monkeypatch.setattr("shutil.which", lambda _name: "VoidCube")

    with pytest.raises(ExecReplaced):
        management.cmd_sessions(
            SimpleNamespace(sessions_action="browse", source=None, limit=5)
        )

    assert "Resuming session: session-1" in capsys.readouterr().out
    assert events[:2] == ["close", "exec"]
