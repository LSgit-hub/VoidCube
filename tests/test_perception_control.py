from datetime import datetime, timezone
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voidcube.systems.perception import LocalPerceptionLoop, PerceptionRecord, PerceptionRuntime, ScreenFrame
from voidcube.systems.supervisor.perception_control import PerceptionControl
from voidcube.systems.supervisor.perception_routes import mount_perception_routes


def test_switch_creates_lazily_samples_and_reports_draining_without_blocking():
    entered, release, closed = Event(), Event(), Event()
    creations = []
    class Source:
        def capture(self):
            return ScreenFrame(b'a' * 4, 1, 1, 1, datetime.now(timezone.utc))
        def close(self):
            closed.set()
    class Analyzer:
        def analyze(self, image, **kwargs):
            entered.set()
            assert release.wait(5)
            return PerceptionRecord(record_id=kwargs['record_id'], observed_at=kwargs['observed_at'])
    def factory():
        creations.append(True)
        return PerceptionRuntime(LocalPerceptionLoop(Source(), Analyzer()))
    control = PerceptionControl(factory=factory)
    auto = [False]
    app = FastAPI()
    mount_perception_routes(app, get_service=lambda: control.service, control=control, is_auto=lambda: auto[0])
    with TestClient(app) as client:
        assert client.get('/runtime/perception/status').json()['runtime']['enabled'] is False
        assert creations == []
        assert client.post('/runtime/perception/start', json={}).status_code == 403
        assert creations == []
        try:
            assert client.post('/runtime/perception/start', json={'consent': True}).status_code == 200
            assert entered.wait(3)
            assert client.get('/runtime/perception/status').json()['runtime']['enabled'] is True
            assert client.post('/runtime/perception/stop').json()['runtime']['state'] == 'stopping'
            assert client.get('/runtime/perception/status').json()['runtime']['enabled'] is False
            assert client.post('/runtime/perception/start', json={'consent': True}).status_code == 503
        finally:
            release.set()
            control.drain(timeout_seconds=5)
        assert closed.is_set()
        assert client.get('/runtime/perception/status').json()['runtime']['state'] == 'stopped'
        assert len(creations) == 1
        auto[0] = True
        assert client.post('/runtime/perception/start', json={'consent': True}).status_code == 409
        assert client.get('/runtime/perception/scene').status_code == 409
        assert client.get('/runtime/perception/status').json()['allowed'] is False


def test_initialization_failure_does_not_claim_enabled():
    def fail():
        raise RuntimeError('unavailable')
    control = PerceptionControl(factory=fail)
    app = FastAPI()
    mount_perception_routes(app, get_service=lambda: control.service, control=control)
    with TestClient(app) as client:
        assert client.post('/runtime/perception/start', json={'consent': True}).status_code == 503
        assert client.get('/runtime/perception/status').json()['runtime']['enabled'] is False
        assert client.post('/runtime/perception/resume').status_code == 403
