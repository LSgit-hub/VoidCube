"""VoidCube integrated service launcher.

Implements the default stable startup sequence:
  1. Internal Gateway  (port 6000)
  2. Memory            (port 6001)
  3. Supervisor        (port 6002)
Usage:
  VoidCube serve start           # start all services in background
  VoidCube serve start --foreground
                               # start all services in foreground
  VoidCube serve stop            # stop all running services
  VoidCube serve status          # show service status
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Default ports ──────────────────────────────────────────────────────
GATEWAY_PORT = 6000
SUPERVISOR_PORT = 6002
# PID file directory
PID_DIR = Path.home() / ".VoidCube" / "run"

try:
    from VoidCube_app.environment import load_VoidCube_dotenv

    load_VoidCube_dotenv(project_env=Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass


def _safe_console_text(text: str) -> str:
    """Normalize Unicode-rich status text for legacy Windows consoles."""
    if sys.platform != "win32":
        return text
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in encoding:
        return text
    replacements = {
        "⚠": "!",
        "✓": "OK",
        "✗": "X",
        "—": "-",
        "▶": ">",
        "⚡": "*",
        "─": "-",
    }
    normalized = text
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    return normalized


def _safe_print(text: str = "") -> None:
    print(_safe_console_text(text))


@dataclass
class ServiceInfo:
    name: str
    port: int
    module: str
    pid_file: str
    log_file: str
    process: Optional[subprocess.Popen] = None
    pid: Optional[int] = None

    @property
    def is_running(self) -> bool:
        if self.pid is None:
            return False
        return _pid_alive(self.pid)

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


SERVICES: Dict[str, ServiceInfo] = {
    "gateway": ServiceInfo(
        name="gateway",
        port=GATEWAY_PORT,
        module="systems.gateway.internal_gateway:InternalGateway",
        pid_file=str(PID_DIR / "gateway.pid"),
        log_file=str(PID_DIR / "gateway.log"),
    ),
    "supervisor": ServiceInfo(
        name="supervisor",
        port=SUPERVISOR_PORT,
        module="systems.supervisor.supervisor:Supervisor",
        pid_file=str(PID_DIR / "supervisor.pid"),
        log_file=str(PID_DIR / "supervisor.log"),
    ),
    "memory": ServiceInfo(
        name="memory",
        port=6001,
        module="systems.memory.memory_service:MemoryService",
        pid_file=str(PID_DIR / "memory.pid"),
        log_file=str(PID_DIR / "memory.log"),
    ),
    # Autonomous-chain display and execution tracking live in the main CLI's
    # narrow execution owner, not in a standalone service process.
}


# ── Helpers ───────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5
            )
            return f'"{pid}"' in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, subprocess.CalledProcessError, TimeoutError):
        return False


def _read_pid(path: str) -> Optional[int]:
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def _write_pid(path: str, pid: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(str(pid))


def _delete_pid(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _port_listening(port: int, timeout: float = 0.1) -> bool:
    """Quick TCP connect check — returns True if something is listening.

    This probe runs before every cold service start.  A closed localhost port
    can take the full socket timeout to fail on Windows, so keep the timeout
    short and leave the longer readiness wait to ``_wait_for_health``.
    """
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        sock.close()
        return True
    except Exception:
        return False


def _port_owner_pid(port: int) -> Optional[int]:
    """Return the owning process for a listening port when the OS exposes it."""
    try:
        import psutil

        for connection in psutil.net_connections(kind="tcp"):
            address = connection.laddr
            if (
                connection.status == "LISTEN"
                and address
                and int(getattr(address, "port", 0)) == int(port)
                and connection.pid
            ):
                return int(connection.pid)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for raw_line in result.stdout.splitlines():
                columns = raw_line.split()
                if len(columns) < 5 or columns[0].upper() != "TCP":
                    continue
                local_address, state, pid_text = columns[1], columns[3], columns[4]
                if state.upper() != "LISTENING":
                    continue
                if local_address.rsplit(":", 1)[-1] != str(int(port)):
                    continue
                return int(pid_text)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def _process_is_service(pid: int, name: str) -> bool:
    """Recognize a VoidCube service process so a lost PID file can be adopted."""
    try:
        import psutil

        command_line = " ".join(psutil.Process(pid).cmdline()).lower()
    except Exception:
        return False
    markers = {
        "gateway": ("internal_gateway", "gateway"),
        "memory": ("memory_service",),
        "supervisor": ("systems.supervisor.supervisor", "supervisor"),
    }.get(name, ())
    return "voidcube" in command_line and any(marker in command_line for marker in markers)


def _health_endpoint_is_service(port: int, name: str) -> bool:
    """Verify an occupied port from the service's own health identity."""
    try:
        from urllib.request import urlopen

        with urlopen(f"http://127.0.0.1:{port}/", timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if name == "gateway":
        return payload.get("gateway_id") == "voidcube-internal-gateway"
    expected_service = {
        "memory": "memory-service",
        "supervisor": "supervisor",
    }.get(name)
    return bool(expected_service and payload.get("service") == expected_service)


def _health_check(port: int, timeout: float = 2.0) -> bool:
    """Check if a service is responding on its health endpoint.

    Uses a raw-socket connect + minimal HTTP handshake to avoid
    urllib hangs on Windows when the server accepts the TCP
    connection but never sends the response.
    """
    import socket

    sock = None
    deadline = time.time() + timeout
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))

        # Send a minimal HTTP request
        request = (
            f"GET / HTTP/1.0\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode())

        # Read just enough to see the status line
        sock.settimeout(max(0.5, timeout - (time.time() - deadline + 0.5)))
        response = b""
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n" in response:
                    break
            except socket.timeout:
                break

        return response.startswith(b"HTTP/1.") and b"200" in response.split(b"\r\n")[0]
    except Exception:
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


# ── Service management ────────────────────────────────────────────────

# Track foreground threads for clean shutdown
_foreground_threads: list = []


def _build_service_config(name: str, port: int, system_config: Any | None = None) -> Any:
    """Build the runtime config object used by a named service."""
    from systems.gateway.internal_gateway import GatewayConfig
    from systems.supervisor.supervisor import SupervisorConfig
    from systems.memory.config import MemoryServiceConfig
    from systems.config import get_config

    system_config = system_config or get_config()

    if name == "gateway":
        return GatewayConfig(
            host=system_config.gateway.host,
            port=port,
            auth_token=system_config.gateway.auth_token,
            log_level=system_config.gateway.log_level,
        )
    elif name == "supervisor":
        if hasattr(system_config.supervisor, "model_copy"):
            supervisor_config: SupervisorConfig = system_config.supervisor.model_copy(deep=True)
        else:
            supervisor_config = system_config.supervisor
        supervisor_config.port = port
        return supervisor_config
    elif name == "memory":
        memory_config = system_config.memory.model_copy(deep=True)
        memory_config.port = port
        return memory_config
    raise ValueError(f"Unknown service: {name}")


def _build_service_app(name: str, port: int):
    """Build the FastAPI app for a named service (shared by fg/bg paths)."""
    from systems.gateway.internal_gateway import InternalGateway
    from systems.supervisor.supervisor import Supervisor
    from systems.memory.memory_service import MemoryService

    service_config = _build_service_config(name, port)

    if name == "gateway":
        return InternalGateway(service_config).app
    elif name == "supervisor":
        return Supervisor(config=service_config).app
    elif name == "memory":
        return MemoryService(config=service_config).app
    raise ValueError(f"Unknown service: {name}")


def _sync_canonical_mem_binding_before_start() -> Dict[str, str]:
    """Bind memai to the shared repository source, independent of Body slots."""
    import sysconfig

    from systems.config import get_config
    from systems.mem_source_binding import sync_canonical_mem_binding
    from VoidCube_core.runtime_paths import get_runtime_layout

    config = get_config()
    source_root = Path(config.supervisor.execution.git_repo_path).resolve()
    result = sync_canonical_mem_binding(
        source_root=source_root,
        site_packages=sysconfig.get_paths()["purelib"],
        audit_path=get_runtime_layout().memory_root / "mem-source-binding.json",
    )
    return result.to_dict()


def _service_python_path_entries() -> list[str]:
    """Return repo-local import roots required by service subprocesses."""
    repo_root = Path(__file__).resolve().parents[2]
    return [str(repo_root), str(repo_root / "Mem" / "src")]


def _service_python_executable(
    repo_root: Path | None = None,
    *,
    current_executable: str | None = None,
    platform: str | None = None,
) -> str:
    """Use the repository virtual environment for service subprocesses.

    The CLI may itself be launched by a system Python that lacks optional
    runtime dependencies such as microphone capture.  Repository services
    must therefore use the project's canonical environment when it exists.
    Installed distributions without a repository ``.venv`` keep using the
    interpreter that launched the CLI.
    """
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    active_platform = platform or sys.platform
    relative_path = (
        Path("Scripts") / "python.exe"
        if active_platform == "win32"
        else Path("bin") / "python"
    )
    project_python = root / ".venv" / relative_path
    if project_python.is_file():
        return str(project_python.resolve())
    return str(Path(current_executable or sys.executable).resolve())


def _running_with_service_python() -> bool:
    expected = os.path.normcase(_service_python_executable())
    current = os.path.normcase(str(Path(sys.executable).resolve()))
    return current == expected


def _restart_foreground_with_service_python() -> None:
    service_python = _service_python_executable()
    _safe_print(
        "  Restarting foreground services with the project Python: "
        f"{service_python}"
    )
    os.execv(
        service_python,
        [
            service_python,
            "-m",
            "VoidCube_cli.main",
            "serve",
            "start",
            "--foreground",
        ],
    )


def _verify_canonical_mem_import_source() -> Dict[str, str]:
    """Fail startup when Python resolves memai outside the shared source."""
    from systems.config import get_config
    from systems.mem_source_binding import validate_canonical_mem_source

    config = get_config()
    source_path = validate_canonical_mem_source(
        config.supervisor.execution.git_repo_path
    )
    expected = source_path / "memai" / "model_config.py"

    from memai import model_config

    actual = Path(model_config.__file__).resolve()
    if actual != expected:
        raise RuntimeError(
            "memai import source does not match the canonical shared binding: "
            f"expected {expected}, loaded {actual}"
        )
    return {"expected": str(expected), "loaded": str(actual)}


def _run_service_in_thread(name: str, port: int) -> None:
    """Run a uvicorn server in a foreground thread."""
    import asyncio
    import uvicorn

    _sync_canonical_mem_binding_before_start()
    _verify_canonical_mem_import_source()
    app = _build_service_app(name, port)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        loop.run_until_complete(server.serve())
    except SystemExit:
        pass
    finally:
        loop.close()


def start_service(name: str, foreground: bool = False) -> Optional[subprocess.Popen]:
    """Start a named service. Returns the Popen object if background, None if foreground."""
    svc = SERVICES.get(name)
    if svc is None:
        _safe_print(f"Unknown service: {name}")
        return None

    # Check if already running (PID file)
    existing_pid = _read_pid(svc.pid_file)
    if existing_pid and _pid_alive(existing_pid):
        _safe_print(f"  {svc.name:12s} already running (pid {existing_pid})")
        svc.pid = existing_pid
        return None

    # Check if port is occupied by an unknown/stale process
    if _port_listening(svc.port) and not (existing_pid and _pid_alive(existing_pid)):
        owner_pid = _port_owner_pid(svc.port)
        if owner_pid and (
            _process_is_service(owner_pid, svc.name)
            or _health_endpoint_is_service(svc.port, svc.name)
        ):
            svc.pid = owner_pid
            _write_pid(svc.pid_file, owner_pid)
            _safe_print(
                f"  {svc.name:12s} reused existing VoidCube process "
                f"(pid {owner_pid}, port {svc.port})"
            )
            return None
        _safe_print(f"  ⚠ {svc.name:12s} port {svc.port} is occupied by an unknown process")
        _safe_print(f"     Use 'netstat -ano | findstr :{svc.port}' to find and kill it")
        return None

    if foreground:
        _safe_print(f"Starting {svc.name} on port {svc.port} (foreground)...")
        import threading

        t = threading.Thread(
            target=_run_service_in_thread,
            args=(svc.name, svc.port),
            daemon=True,
            name=f"voidcube-{svc.name}",
        )
        t.start()
        _foreground_threads.append(t)
        svc.process = None
        return None

    # Background mode: launch via subprocess.
    # Redirect stdout/stderr inside the child to the log file so the
    # subprocess survives independently of the parent lifecycle.
    log_dir = Path(svc.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    service_python = _service_python_executable()
    log_path = json.dumps(str(Path(svc.log_file).resolve()))
    path_entries = json.dumps(_service_python_path_entries())
    project_env_path = json.dumps(str(Path(__file__).resolve().parents[2] / ".env"))

    script = f"""
import sys, os
log_file = open({log_path}, 'a', buffering=1)
sys.stdout = log_file
sys.stderr = log_file
for path_entry in reversed({path_entries}):
    if path_entry not in sys.path:
        sys.path.insert(0, path_entry)
os.chdir({json.dumps(str(Path.cwd()))})
from VoidCube_app.environment import load_VoidCube_dotenv
load_VoidCube_dotenv(project_env={project_env_path}, force_reload=True)
import uvicorn
from VoidCube_cli.ops.serve import _build_service_app, _verify_canonical_mem_import_source
_verify_canonical_mem_import_source()
app = _build_service_app({json.dumps(name)}, {svc.port})
uvicorn.run(app, host='127.0.0.1', port={svc.port}, log_level='info')
"""

    proc = subprocess.Popen(
        [service_python, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    svc.process = proc
    svc.pid = proc.pid
    _write_pid(svc.pid_file, proc.pid)
    _safe_print(f"  {svc.name:12s} started (pid {proc.pid}, port {svc.port})")
    return proc


def stop_service(name: str, silent: bool = False) -> bool:
    """Stop a named service by PID file.

    When ``silent=True``, skip status messages (used by force-stop paths).
    """
    svc = SERVICES.get(name)
    if svc is None:
        if not silent:
            _safe_print(f"Unknown service: {name}")
        return False

    pid = _read_pid(svc.pid_file)
    if pid is None or not _pid_alive(pid):
        if not silent:
            _safe_print(f"  {svc.name:12s} not running")
        _delete_pid(svc.pid_file)
        return True

    try:
        if sys.platform == "win32":
            # venv 的 python.exe 是启动器，实际监听端口的是其子进程。
            # 必须结束完整进程树，否则旧服务会继续占用端口并加载旧代码。
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except Exception:
        pass

    _delete_pid(svc.pid_file)
    if not silent:
        _safe_print(f"  {svc.name:12s} stopped (was pid {pid})")
    return True


def status_all() -> Dict[str, Any]:
    """Return status of all services."""
    result: Dict[str, Any] = {}
    for name, svc in SERVICES.items():
        pid = _read_pid(svc.pid_file)
        alive = pid is not None and _pid_alive(pid)
        healthy = _health_check(svc.port) if alive else False
        result[name] = {
            "name": name,
            "port": svc.port,
            "pid": pid,
            "running": alive,
            "healthy": healthy,
            "pid_file": svc.pid_file,
            "log_file": svc.log_file,
        }
    return result


def print_status(full: bool = False) -> None:
    """Print a formatted status table.

    When ``full=True``, also fetches supervisor/gateway data and displays
    the rich execution dashboard with autonomous-chain observation and agent status.
    """
    # Always show daemon status
    status = status_all()
    _safe_print("\n  VoidCube Services")
    _safe_print("  " + "─" * 52)
    _safe_print(f"  {'Service':12s} {'Port':>6s} {'PID':>8s} {'Status':>10s}")
    _safe_print("  " + "─" * 52)
    for name, info in status.items():
        running = "✓ running" if info["healthy"] else ("✗ dead" if info["running"] else "— stopped")
        pid_str = str(info["pid"]) if info["pid"] else "—"
        _safe_print(f"  {name:12s} {info['port']:>6d} {pid_str:>8s} {running:>10s}")
    _safe_print("  " + "─" * 52)
    _safe_print()

    # If full dashboard requested, fetch supervisor/gateway data
    if full:
        try:
            from VoidCube_cli.ops.dashboard import print_dashboard
            print_dashboard()
        except Exception as exc:
            _safe_print(f"  ⚠ Dashboard unavailable: {exc}")
            _safe_print(f"    Is the supervisor running?  http://127.0.0.1:6002/ui")
            _safe_print()


def start_all(foreground: bool = False) -> None:
    """Start default stable services.

    1. Gateway (nerve centre) — routes all traffic, accepts registrations
    2. Mem (soul layer, API-B) — registers with Gateway during startup
    3. Supervisor (Mem's governance identity, API-B) — registers both the
       supervisor and executor surfaces with Gateway

    Body/agent subprocesses are not part of the default startup path.
    They should only be started by an explicit body-runtime operation.
    """
    if foreground and not _running_with_service_python():
        _restart_foreground_with_service_python()
        return

    PID_DIR.mkdir(parents=True, exist_ok=True)
    _safe_print("\n  Starting VoidCube services...\n")
    _sync_canonical_mem_binding_before_start()

    # 1. Gateway (nerve centre — routes all internal traffic)
    start_service("gateway", foreground=foreground)
    if not foreground:
        _wait_for_health("gateway", GATEWAY_PORT)

    # 2. Memory registers with Gateway during app startup.
    start_service("memory", foreground=foreground)
    if not foreground:
        _wait_for_health("memory", SERVICES["memory"].port)
        if not _wait_for_gateway_service_type("memory"):
            stop_service("memory", silent=True)
            start_service("memory", foreground=foreground)
            _wait_for_health("memory", SERVICES["memory"].port)
            _wait_for_gateway_service_type("memory")

    # 3. Supervisor (Mem's governance identity)
    start_service("supervisor", foreground=foreground)
    if not foreground:
        _wait_for_health("supervisor", SUPERVISOR_PORT)
        supervisor_registered = _wait_for_gateway_service_type("supervisor")
        executor_registered = _wait_for_gateway_service_type("executor")
        if not (supervisor_registered and executor_registered):
            stop_service("supervisor", silent=True)
            start_service("supervisor", foreground=foreground)
            _wait_for_health("supervisor", SUPERVISOR_PORT)
            _wait_for_gateway_service_type("supervisor")
            _wait_for_gateway_service_type("executor")

    if foreground:
        # Foreground: default stable services are running in daemon threads.
        # Wait for them (join) — the main thread stays alive until interrupted.
        _safe_print("\n  Core services started. Use `voidcube serve stop` from another terminal to stop.\n")
        _safe_print(f"  Gateway:    http://127.0.0.1:{GATEWAY_PORT}")
        _safe_print(f"  Supervisor: http://127.0.0.1:{SUPERVISOR_PORT}/ui")
        _safe_print()
        import threading
        for thread in _foreground_threads:
            thread.join()
        return

    _safe_print()
    print_status()
    _safe_print(f"  Gateway:    http://127.0.0.1:{GATEWAY_PORT}")
    _safe_print(f"  Supervisor: http://127.0.0.1:{SUPERVISOR_PORT}/ui")
    _safe_print(f"  PID files:  {PID_DIR}")
    _safe_print()


def stop_all(force: bool = False) -> None:
    """Stop all services.

    When ``force=True``, skip status messages and terminate immediately
    without prompting (used by /quit and other automated shutdown paths).
    """
    if not force:
        _safe_print("\n  Stopping VoidCube services...\n")
    for name in SERVICES:
        stop_service(name, silent=force)
    if not force:
        _safe_print()


def _wait_for_health(name: str, port: int, timeout: float = 15.0) -> bool:
    """Wait for a service to become healthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health_check(port):
            return True
        time.sleep(0.1)
    _safe_print(f"  ⚠ {name} did not respond within {timeout}s")
    return False


def _gateway_has_service_type(service_type: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{GATEWAY_PORT}/admin/services", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    services = payload.get("services", []) if isinstance(payload, dict) else []
    if isinstance(services, dict):
        iterable = services.values()
    elif isinstance(services, list):
        iterable = services
    else:
        iterable = []
    for service in iterable:
        if isinstance(service, dict) and service.get("service_type") == service_type:
            return True
    return False


def _required_gateway_service_types(service_name: str) -> tuple[str, ...]:
    return {
        "memory": ("memory",),
        "supervisor": ("supervisor", "executor"),
    }.get(service_name, ())


def _wait_for_gateway_service_type(service_type: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _gateway_has_service_type(service_type):
            return True
        time.sleep(0.1)
    _safe_print(f"  ⚠ gateway did not list service_type={service_type} within {timeout}s")
    return False


def ensure_running(silent: bool = True) -> Dict[str, Any]:
    """Idempotent: ensure all daemon services are running.

    Checks each service. Starts any that aren't already alive.
    Returns a status dict keyed by service name.

    Called automatically by ``voidcube`` (interactive mode).
    Skipped for ``voidcube -q`` (single-query fast path).
    """
    PID_DIR.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {}
    prestarted_services: set[str] = set()
    _sync_canonical_mem_binding_before_start()

    # Default stable path: Gateway → Mem → Supervisor.
    # The live CLI session is the canonical API-A runtime; body/agent
    # subprocesses are only started explicitly for body-runtime workflows.
    startup_order = ["gateway", "memory", "supervisor"]
    for name in startup_order:
        svc = SERVICES.get(name)
        if svc is None:
            continue

        existing_pid = _read_pid(svc.pid_file)

        # Memory and Supervisor both register against the already-healthy
        # Gateway and do not depend on each other.  Starting a cold Supervisor
        # while Memory is booting removes one full service startup from the
        # interactive CLI's critical path.  The normal loop below still owns
        # Supervisor's health and registration checks, including retries.
        if name == "memory" and not (existing_pid and _pid_alive(existing_pid)):
            supervisor = SERVICES.get("supervisor")
            supervisor_pid = _read_pid(supervisor.pid_file) if supervisor else None
            if supervisor and not (supervisor_pid and _pid_alive(supervisor_pid)):
                if not silent:
                    _safe_print(
                        f"  ▶ {supervisor.name:12s} starting on port {supervisor.port}..."
                    )
                supervisor_process = start_service("supervisor", foreground=False)
                if supervisor_process is not None:
                    prestarted_services.add("supervisor")

        if existing_pid and _pid_alive(existing_pid):
            healthy = (
                _wait_for_health(name, svc.port, timeout=30.0)
                if name in prestarted_services
                else _health_check(svc.port)
            )
            if healthy:
                required_gateway_types = _required_gateway_service_types(name)
                missing_gateway_types = [
                    service_type
                    for service_type in required_gateway_types
                    if not _gateway_has_service_type(service_type)
                ]
                if missing_gateway_types:
                    if not silent:
                        missing = ", ".join(missing_gateway_types)
                        _safe_print(
                            f"  ⚠ {svc.name:12s} healthy but gateway lacks "
                            f"{missing} registration — restarting..."
                        )
                    stop_service(name, silent=True)
                else:
                    result[name] = {
                        "running": True,
                        "healthy": True,
                        "pid": existing_pid,
                        "started": name in prestarted_services,
                    }
                    if required_gateway_types:
                        result[name]["registered"] = True
                    if not silent:
                        label = (
                            "started in parallel"
                            if name in prestarted_services
                            else "already running"
                        )
                        _safe_print(
                            f"  ✓ {svc.name:12s} {label} "
                            f"(pid {existing_pid}, port {svc.port})"
                        )
                    svc.pid = existing_pid
                    continue

            elif not silent:
                _safe_print(f"  ⚠ {svc.name:12s} unhealthy (pid {existing_pid}, port {svc.port}) — restarting...")
            if not healthy:
                stop_service(name, silent=True)

        if not silent:
            _safe_print(f"  ▶ {svc.name:12s} starting on port {svc.port}...")
        proc = start_service(name, foreground=False)

        if proc is None:
            # start_service returned None — could be port-occupied by unknown
            # process, or already-running detected late.
            healthy = _health_check(svc.port)
            pid = _read_pid(svc.pid_file)
            result[name] = {"running": healthy, "healthy": healthy, "pid": pid, "started": False}
            if not silent:
                tag = "✓" if healthy else "⚠"
                _safe_print(f"     {tag} port {svc.port} reachable (reusing existing service)" if healthy
                            else f"     {tag} could not start or reach {svc.name}")
            continue

        healthy = _wait_for_health(name, svc.port, timeout=30.0)
        new_pid = _read_pid(svc.pid_file)
        result[name] = {"running": new_pid is not None, "healthy": healthy, "pid": new_pid, "started": True}
        if not silent:
            tag = "✓" if healthy else "⚠"
            _safe_print(f"     {tag} ready" if healthy else f"     {tag} not responding (may still be starting)")
        required_gateway_types = _required_gateway_service_types(name)
        if required_gateway_types and healthy:
            registration_results = [
                _wait_for_gateway_service_type(service_type, timeout=20.0)
                for service_type in required_gateway_types
            ]
            registered = all(registration_results)
            result[name]["registered"] = registered
            if not silent:
                tag = "✓" if registered else "⚠"
                _safe_print(
                    f"     {tag} registered with gateway"
                    if registered
                    else "     ⚠ not fully registered with gateway"
                )

    return result
