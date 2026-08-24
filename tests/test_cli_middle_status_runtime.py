from voidcube.interfaces.cli.middle_status_runtime import (
    CliMiddleStatusPorts,
    CliMiddleStatusRuntime,
)


def _runtime(*, supervisor=None, memory=None, ascii_mode=True, subagent=None):
    return CliMiddleStatusRuntime(
        CliMiddleStatusPorts(
            supervisor_snapshot=lambda: supervisor or {"scene": "idle"},
            memory_llm=lambda: memory or {"provider": "mem"},
            ascii_mode=lambda: ascii_mode,
            subagent_snapshot=lambda: subagent or {"active": False},
        )
    )


def test_middle_status_renders_memory_scene_context_and_errors():
    fragments = _runtime(
        supervisor={
            "scene": "planning",
            "is_active": False,
            "mem_usage": {"last_request_usage_percent": 72, "request_count": 1},
            "error_count": 2,
        },
        memory={"model": "local/demo.gguf"},
    ).build()

    rendered = "".join(text for _, text in fragments)
    assert "demo" in rendered
    assert "72%" in rendered
    assert "(?)规划" in rendered
    assert "!2" in rendered


def test_middle_status_renders_active_subagent_summary():
    fragments = _runtime(
        subagent={"active": True, "counts_label": "2+1", "compact_preview": "read_file"},
    ).build()

    rendered = "".join(text for _, text in fragments)
    assert "[SA]" in rendered
    assert "2+1" in rendered
    assert "read_file" in rendered


def test_middle_status_isolates_failed_ports():
    runtime = _runtime()
    runtime.ports = CliMiddleStatusPorts(
        supervisor_snapshot=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        memory_llm=lambda: (_ for _ in ()).throw(RuntimeError("no config")),
        ascii_mode=lambda: True,
        subagent_snapshot=lambda: {"active": False},
    )

    rendered = "".join(text for _, text in runtime.build())
    assert "(-)" in rendered


def test_middle_status_projects_scheduler_snapshot_without_reading_host_state():
    snapshot = type(
        "Snapshot",
        (),
        {
            "active": type("Active", (), {"lane": type("Lane", (), {"value": "user_chat"})(), "state": type("State", (), {"value": "running"})()})(),
            "queued": (object(), object()),
        },
    )()
    runtime = CliMiddleStatusRuntime(
        CliMiddleStatusPorts(
            supervisor_snapshot=lambda: {"scene": "idle"},
            memory_llm=lambda: {"provider": "mem"},
            ascii_mode=lambda: True,
            subagent_snapshot=lambda: {"active": False},
            scheduler_snapshot=lambda: snapshot,
        )
    )

    rendered = "".join(text for _, text in runtime.build())
    assert "*" in rendered
    assert "用户" not in rendered
    assert "running" not in rendered
    assert "+2" in rendered


def test_middle_status_compacts_cancelling_autonomous_request():
    snapshot = type(
        "Snapshot",
        (),
        {
            "active": type(
                "Active",
                (),
                {
                    "lane": type("Lane", (), {"value": "supervisor_task"})(),
                    "state": type("State", (), {"value": "cancelling"})(),
                    "request_id": "auto-123456789",
                },
            )(),
            "queued": (),
            "blocked_reason": "auto-q",
        },
    )()
    runtime = CliMiddleStatusRuntime(
        CliMiddleStatusPorts(
            supervisor_snapshot=lambda: {"scene": "idle"},
            memory_llm=lambda: {"provider": "mem"},
            ascii_mode=lambda: True,
            subagent_snapshot=lambda: {"active": False},
            scheduler_snapshot=lambda: snapshot,
        )
    )

    rendered = "".join(text for _, text in runtime.build())

    assert "o" in rendered
    assert "自主" not in rendered
    assert "cancelling" not in rendered
    assert "23456789" not in rendered


def test_middle_status_uses_compact_unicode_user_and_model_status():
    snapshot = type(
        "Snapshot",
        (),
        {
            "active": type(
                "Active",
                (),
                {
                    "lane": type("Lane", (), {"value": "user_chat"})(),
                    "state": type("State", (), {"value": "running"})(),
                },
            )(),
            "queued": (),
        },
    )()
    runtime = CliMiddleStatusRuntime(
        CliMiddleStatusPorts(
            supervisor_snapshot=lambda: {"scene": "idle"},
            memory_llm=lambda: {"model": "deepseek-v4-flash"},
            ascii_mode=lambda: False,
            subagent_snapshot=lambda: {"active": False},
            scheduler_snapshot=lambda: snapshot,
        )
    )

    rendered = "".join(text for _, text in runtime.build())

    assert rendered.startswith("deepseek-v4-flash --")
    assert "●" in rendered
    assert "B✓" not in rendered
    assert "用户" not in rendered
    assert "running" not in rendered
