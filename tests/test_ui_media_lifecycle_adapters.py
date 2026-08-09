from collections import deque
from types import SimpleNamespace

import pytest

from systems.supervisor.ui_media_state_adapters import (
    SupervisorUIMediaStateContext,
    control_media_state,
    enqueue_media_state,
    enqueue_media_playlist_state,
    load_media_state,
    persist_media_state,
)
from systems.supervisor.ui_open_lifecycle_adapters import (
    SupervisorUIOpenLifecycleContext,
    maybe_open_supervisor_ui,
)


def test_media_state_owner_updates_revision_and_current_payload():
    state = {"revision": 4, "current": None}
    queue = deque()
    current = enqueue_media_state(
        context=SupervisorUIMediaStateContext(
            current_revision=state["revision"],
            current_media=state["current"],
            media_queue=queue,
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
    assert current["media_id"]
    assert current["playback"] == "playing"
    assert current["_revision"] == 5


def test_media_state_queue_and_controls_share_one_canonical_state():
    state = {"revision": 0, "current": None}
    queue = deque()

    def context():
        return SupervisorUIMediaStateContext(
            current_revision=state["revision"],
            current_media=state["current"],
            media_queue=queue,
            set_revision=lambda value: state.update(revision=value),
            set_current_media=lambda value: state.update(current=value),
        )

    first = enqueue_media_state(
        context=context(),
        media={"url": "https://example.com/a.mp3", "title": "A"},
    )
    second = enqueue_media_state(
        context=context(),
        media={"url": "https://example.com/b.mp3", "title": "B"},
        queue_mode="enqueue",
    )

    assert state["current"]["media_id"] == first["media_id"]
    assert [item["media_id"] for item in queue] == [second["media_id"]]

    third = enqueue_media_state(
        context=context(),
        media={"url": "https://example.com/c.mp3", "title": "C"},
        queue_mode="enqueue",
    )
    selected = control_media_state(
        context=context(), action="select", media_id=third["media_id"]
    )
    assert selected["media_id"] == third["media_id"]
    assert [item["media_id"] for item in queue] == [second["media_id"]]

    paused = control_media_state(context=context(), action="pause")
    assert paused["playback"] == "paused"
    resumed = control_media_state(context=context(), action="resume")
    assert resumed["playback"] == "playing"

    advanced = control_media_state(
        context=context(), action="ended", media_id=third["media_id"]
    )
    assert advanced["media_id"] == second["media_id"]
    assert not queue

    assert control_media_state(context=context(), action="stop") is None
    assert state["current"] is None


def test_media_playlist_state_persists_current_and_queue(tmp_path):
    state = {"revision": 0, "current": None}
    queue = deque()

    def context():
        return SupervisorUIMediaStateContext(
            current_revision=state["revision"],
            current_media=state["current"],
            media_queue=queue,
            set_revision=lambda value: state.update(revision=value),
            set_current_media=lambda value: state.update(current=value),
        )

    current = enqueue_media_playlist_state(
        context=context(),
        items=[{"url": "https://example.com/a.mp3"}, {"url": "https://example.com/b.mp3"}],
    )
    assert current["url"].endswith("a.mp3")
    assert [item["url"] for item in queue] == ["https://example.com/b.mp3"]
    path = tmp_path / "media.json"
    persist_media_state(path, current=state["current"], queue=queue, revision=state["revision"])
    restored_current, restored_queue, restored_revision = load_media_state(path)
    assert restored_current["url"].endswith("a.mp3")
    assert [item["url"] for item in restored_queue] == ["https://example.com/b.mp3"]
    assert restored_revision == state["revision"]


@pytest.mark.parametrize(
    ("ui_enabled", "auto_open", "test_environment", "desktop_environment"),
    [
        (False, True, False, False),
        (True, False, False, False),
        (True, True, True, False),
        (True, True, False, True),
    ],
)
def test_open_lifecycle_owner_skips_disabled_or_test_schedules(
    monkeypatch,
    ui_enabled,
    auto_open,
    test_environment,
    desktop_environment,
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
    if desktop_environment:
        monkeypatch.setenv("VOIDCUBE_DESKTOP", "1")
    else:
        monkeypatch.delenv("VOIDCUBE_DESKTOP", raising=False)

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
    monkeypatch.delenv("VOIDCUBE_DESKTOP", raising=False)

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
