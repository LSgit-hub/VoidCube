from datetime import datetime, timezone

from fastapi.testclient import TestClient

from voidcube.systems.perception import LocalPerceptionLoop, PerceptionRecord, PerceptionRuntime, ScreenFrame
from voidcube.systems.supervisor.config_models import (
    SupervisorConfig, SupervisorExecutionConfig, SupervisorBodyRuntimeConfig,
)
from voidcube.systems.supervisor.supervisor import Supervisor


class _Source:
    def capture(self) -> ScreenFrame:
        return ScreenFrame(b"a" * 128, 32, 1, 1, datetime.now(timezone.utc))


class _Analyzer:
    def analyze(self, _image: bytes, **kwargs: object) -> PerceptionRecord:
        return PerceptionRecord(record_id=str(kwargs["record_id"]), observed_at=kwargs["observed_at"], scene="test", confidence=1)


def test_supervisor_perception_routes_are_unavailable_until_explicitly_injected(tmp_path) -> None:
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
        soul_store_path=str(tmp_path / ".soul-runtime"),
        body_runtime=SupervisorBodyRuntimeConfig(state_root=str(tmp_path / "body-state")),
    )
    supervisor = Supervisor(config)
    assert TestClient(supervisor.app).get("/runtime/perception/scene").status_code == 503

    runtime = PerceptionRuntime(LocalPerceptionLoop(_Source(), _Analyzer()))
    supervisor.configure_perception(runtime)
    client = TestClient(supervisor.app)
    assert client.get("/runtime/perception/status").json()["runtime"]["state"] == "stopped"
    assert client.post("/runtime/perception/start").status_code == 403
    started = client.post("/runtime/perception/start", json={"consent": True})
    assert started.json()["runtime"]["state"] == "running"
    assert started.json()["runtime"]["authorized"] is True
