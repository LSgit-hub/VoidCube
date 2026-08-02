from types import SimpleNamespace

import pytest

from systems.supervisor.ui_media_state_adapters import (
    SupervisorUIMediaStateContext,
    enqueue_media_state,
)
from systems.supervisor.ui_open_lifecycle_adapters import (
    SupervisorUIOpenLifecycleContext,
    maybe_open_supervisor_ui,
)


def test_media_state_owner_updates_revision_and_current_payload():
    state = {"revision": 4, "current": None}
    current = enqueue_media_state(
        context=SupervisorUIMediaStateContext(
            current_revision=state["revision"],
            set_revision=lambda value: state.update(revision=value),
            set_current_media=lambda value: state.update(current=value),
        ),
        media={"url": "https://example.com/a.mp3"},
    )

    assert state["revision"] == 5
    assert state["current"] == current
    assert current["title"] == "https://example.com/a.mp3"
    assert current["type"] == "auto"
    assert current["auto_play"] is True
    assert current["_revision"] == 5


@pytest.mark.parametrize(
    ("ui_enabled", "auto_open", "test_environment"),
    [(False, True, False), (True, False, False), (True, True, True)],
)
def test_open_lifecycle_owner_skips_disabled_or_test_schedules(
    monkeypatch,
    ui_enabled,
    auto_open,
    test_environment,
):
    import systems.supervisor.ui_open_lifecycle_adapters as owner

    captured = []
    monkeypatch.setattr(
        owner,
        "threading",
        SimpleNamespace(
            Timer=lambda delay, callback: captured.append((delay, callback))
            or SimpleNamespace(daemon=False, start=lambda: None)
        ),
    )
    monkeypatch.setattr(
        owner,
        "webbrowser",
        SimpleNamespace(open=lambda url: captured.append(url)),
    )
    if test_environment:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    maybe_open_supervisor_ui(
        context=SupervisorUIOpenLifecycleContext(
            ui_enabled=ui_enabled,
            auto_open=auto_open,
            url="http://localhost/ui",
            delay_seconds=-1,
        )
    )

    assert captured == []


def test_open_lifecycle_owner_schedules_daemon_and_opens_url(monkeypatch):
    import systems.supervisor.ui_open_lifecycle_adapters as owner

    captured = {}

    class _Timer:
        daemon = False

        def __init__(self, delay, callback):
            captured.update(delay=delay, callback=callback, timer=self)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(owner.threading, "Timer", _Timer)
    monkeypatch.setattr(
        owner.webbrowser,
        "open",
        lambda url: captured.update(opened=url),
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    maybe_open_supervisor_ui(
        context=SupervisorUIOpenLifecycleContext(
            ui_enabled=True,
            auto_open=True,
            url="http://localhost/ui",
            delay_seconds=-1,
        )
    )

    assert captured["delay"] == 0.0
    assert captured["started"] is True
    assert captured["timer"].daemon is True
    captured["callback"]()
    assert captured["opened"] == "http://localhost/ui"
