from datetime import datetime

from voidcube.interfaces.cli.status_snapshot_runtime import (
    CliStatusSnapshotPorts,
    CliStatusSnapshotRuntime,
)


def _runtime(*, active_model=None, configured_model="configured", usage=None, context=None):
    return CliStatusSnapshotRuntime(
        CliStatusSnapshotPorts(
            configured_model=lambda: configured_model,
            active_model=lambda: active_model,
            session_start=lambda: datetime(2026, 8, 3, 12, 0, 0),
            now=lambda: datetime(2026, 8, 3, 12, 1, 30),
            agent_usage=lambda: usage or {},
            context_usage=lambda: context or {},
            subagent_snapshot=lambda: {"active": False},
            format_duration=lambda seconds: f"{seconds:.0f}s",
        )
    )


def test_snapshot_prefers_active_model_and_projects_usage():
    snapshot = _runtime(
        active_model="provider/active-model",
        usage={"session_input_tokens": 11, "session_total_tokens": 19},
        context={"context_tokens": 750, "context_length": 1000, "compressions": 2},
    ).snapshot()

    assert snapshot["model_name"] == "provider/active-model"
    assert snapshot["model_short"] == "active-model"
    assert snapshot["duration"] == "90s"
    assert snapshot["session_input_tokens"] == 11
    assert snapshot["session_total_tokens"] == 19
    assert snapshot["context_percent"] == 75
    assert snapshot["compressions"] == 2


def test_snapshot_normalizes_local_model_name_and_defaults_missing_data():
    snapshot = _runtime(active_model="C:/models/very-long-model-name.gguf").snapshot()

    assert snapshot["model_short"] == "very-long-model-name"
    assert snapshot["context_percent"] is None
    assert snapshot["session_api_calls"] == 0
