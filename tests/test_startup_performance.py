from __future__ import annotations

import socket

from VoidCube_cli.ops import serve


def test_cold_service_start_launches_independent_services_in_parallel(
    monkeypatch,
    tmp_path,
):
    """Keep Memory and Supervisor off the sequential cold-start critical path."""
    calls: list[tuple[str, str]] = []
    pids: dict[str, int] = {}

    monkeypatch.setattr(serve, "PID_DIR", tmp_path)
    monkeypatch.setattr(
        serve,
        "_sync_canonical_mem_binding_before_start",
        lambda: None,
    )

    def read_pid(path: str) -> int | None:
        return pids.get(path.rsplit("\\", 1)[-1].removesuffix(".pid"))

    monkeypatch.setattr(serve, "_read_pid", read_pid)
    monkeypatch.setattr(serve, "_pid_alive", lambda pid: pid in pids.values())
    monkeypatch.setattr(serve, "_health_check", lambda port: True)
    monkeypatch.setattr(serve, "_gateway_has_service_type", lambda service_type: True)
    monkeypatch.setattr(serve, "_safe_print", lambda *args, **kwargs: None)

    def start_service(name: str, foreground: bool = False):
        del foreground
        calls.append(("start", name))
        pid = len(pids) + 100
        pids[name] = pid
        return object()

    monkeypatch.setattr(serve, "start_service", start_service)

    result = serve.ensure_running(silent=True)

    assert [name for action, name in calls if action == "start"] == [
        "gateway",
        "supervisor",
        "memory",
    ]
    assert result["supervisor"]["started"] is True
    assert result["memory"]["started"] is True


def test_port_probe_uses_short_cold_start_timeout(monkeypatch):
    captured: dict[str, float] = {}

    class FakeSocket:
        def settimeout(self, timeout: float) -> None:
            captured["timeout"] = timeout

        def connect(self, address) -> None:
            del address
            raise OSError("closed")

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FakeSocket())

    assert serve._port_listening(6000) is False
    assert captured["timeout"] == 0.1
