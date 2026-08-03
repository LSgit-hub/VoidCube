from VoidCube_cli.cli_middle_status_runtime import (
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
            "mem_usage": {"context_percent": 72},
            "error_count": 2,
        },
        memory={"model": "local/demo.gguf"},
    ).build()

    rendered = "".join(text for _, text in fragments)
    assert "[M]" in rendered
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
    assert "辅助" in rendered
