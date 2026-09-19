from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock, Mock

from voidcube.systems.perception import LocalPerceptionLoop, PerceptionRecord, PerceptionRuntime, ScreenFrame
from voidcube.systems.supervisor.config_models import (
    SupervisorConfig, SupervisorExecutionConfig, SupervisorBodyRuntimeConfig,
)
from voidcube.systems.supervisor.supervisor import Supervisor
from voidcube.systems.supervisor.service_runtime import StellarMode


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
    supervisor._perception_control.close()


@pytest.mark.asyncio
async def test_entering_auto_drains_perception_before_mode_changes(tmp_path):
    supervisor = Supervisor(SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
        soul_store_path=str(tmp_path / '.soul-runtime'),
        body_runtime=SupervisorBodyRuntimeConfig(state_root=str(tmp_path / 'body-state')),
    ))
    modes = []
    supervisor._perception_control.drain = lambda: modes.append(supervisor._service_runtime.stellar_mode.value)
    supervisor._voice_manager = Mock(interrupt=Mock(), stop_continuous=AsyncMock())
    supervisor._stop_daily_companion_worker = AsyncMock()
    supervisor._notify_gateway_autonomous_chain_gate = AsyncMock()
    supervisor._start_autonomous_chain_workers = AsyncMock()
    await supervisor._start_autonomous_chain_gate()
    assert modes == ['daily_companion']
    assert supervisor._service_runtime.stellar_mode.value == 'auto_evolution'
    client = TestClient(supervisor.app)
    assert client.post('/runtime/perception/start', json={'consent': True}).status_code == 409
    supervisor._scheduled_task_store.close()


def test_companion_perception_context_is_excluded_in_auto(tmp_path):
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
        soul_store_path=str(tmp_path / '.soul-runtime'),
        body_runtime=SupervisorBodyRuntimeConfig(state_root=str(tmp_path / 'body-state')),
    )
    supervisor = Supervisor(config)
    supervisor._service_runtime.stellar_mode = StellarMode.AUTO_EVOLUTION
    payload = supervisor._companion_perception_context('我在看什么')
    assert payload['status'] == 'excluded_in_auto'
    assert payload['timeline_segments'] == []
    supervisor._scheduled_task_store.close()
