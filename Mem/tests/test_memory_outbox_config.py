from __future__ import annotations

from plugins.memory.mem.outbox import load_memory_outbox_settings


def test_memory_outbox_settings_define_isolated_default_queues(tmp_path):
    settings = load_memory_outbox_settings({})

    assert settings.path_for("api_a", home=tmp_path).name == "write-outbox.sqlite3"
    assert settings.path_for("companion", home=tmp_path).name == "companion-write-outbox.sqlite3"
    assert settings.path_for("gateway", home=tmp_path).name == "gateway-write-outbox.sqlite3"
    assert settings.path_for("api_a", home=tmp_path).parent == tmp_path / "runtime" / "memory"


def test_memory_outbox_settings_share_transport_policy_and_allow_path_overrides(tmp_path):
    settings = load_memory_outbox_settings(
        {
            "memory": {
                "outbox": {
                    "paths": {"gateway": "custom/gateway.sqlite3"},
                    "max_attempts": 7,
                    "lease_seconds": 11,
                    "retry_base_seconds": 0.5,
                    "retry_max_seconds": 9,
                    "health_report_interval_seconds": 4,
                    "shutdown_drain_timeout_seconds": 3,
                }
            }
        }
    )

    assert settings.path_for("gateway", home=tmp_path) == tmp_path / "custom" / "gateway.sqlite3"
    assert settings.max_attempts == 7
    assert settings.lease_seconds == 11
    assert settings.retry_base_seconds == 0.5
    assert settings.retry_max_seconds == 9
    assert settings.health_report_interval_seconds == 4
    assert settings.shutdown_drain_timeout_seconds == 3
