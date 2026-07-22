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
    from VoidCube_cli.env_loader import load_VoidCube_dotenv

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
    # ── Embedded surfaces (not standalone processes) ──
    # Autonomous-chain display and execution tracking live inside the main CLI.
    # Body/agent execution is triggered through the API-A pull path and tracked
    # by the embedded minimal CLI panel, not by a separate terminal process.
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


def _port_listening(port: int, timeout: float = 0.5) -> bool:
    """Quick TCP connect check — returns True if something is listening."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        sock.close()
        return True
    except Exception:
        return False


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


def _service_python_path_entries() -> list[str]:
    """Return repo-local import roots required by service subprocesses."""
    repo_root = Path(__file__).resolve().parents[2]
    entries = [str(repo_root)]
    mem_src = repo_root / "Mem" / "src"
    if mem_src.exists():
        entries.append(str(mem_src))
    return entries


def _run_service_in_thread(name: str, port: int) -> None:
    """Run a uvicorn server in a foreground thread."""
    import asyncio
    import uvicorn

    app = _build_service_app(name, port)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        loop.run_until_complete(server.serve())
    except (KeyboardInterrupt, SystemExit):
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

    venv_python = sys.executable  # use the same Python that's running the CLI
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
from VoidCube_cli.env_loader import load_VoidCube_dotenv
load_VoidCube_dotenv(project_env={project_env_path}, force_reload=True)
import uvicorn
from VoidCube_cli.ops.serve import _build_service_app
app = _build_service_app({json.dumps(name)}, {svc.port})
uvicorn.run(app, host='127.0.0.1', port={svc.port}, log_level='info')
"""

    proc = subprocess.Popen(
        [venv_python, "-c", script],
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
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    except (Exception, KeyboardInterrupt):
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
       supervisor and embedded executor surfaces with Gateway

    Body/agent subprocesses are not part of the default startup path.
    They should only be started by an explicit body-runtime operation.
    """
    PID_DIR.mkdir(parents=True, exist_ok=True)
    _safe_print("\n  Starting VoidCube services...\n")

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
        _safe_print(f"\n  Core services started. Press Ctrl+C to stop.\n")
        _safe_print(f"  Gateway:    http://127.0.0.1:{GATEWAY_PORT}")
        _safe_print(f"  Supervisor: http://127.0.0.1:{SUPERVISOR_PORT}/ui")
        _safe_print()
        try:
            import threading
            for t in _foreground_threads:
                t.join()
        except KeyboardInterrupt:
            _safe_print("\n  Shutting down...")
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
        time.sleep(0.3)
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
        time.sleep(0.3)
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

    # Default stable path: Gateway → Mem → Supervisor.
    # The live CLI session is the canonical API-A runtime; body/agent
    # subprocesses are only started explicitly for body-runtime workflows.
    startup_order = ["gateway", "memory", "supervisor"]
    for name in startup_order:
        svc = SERVICES.get(name)
        if svc is None:
            continue

        existing_pid = _read_pid(svc.pid_file)
        if existing_pid and _pid_alive(existing_pid):
            healthy = _health_check(svc.port)
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
                    result[name] = {"running": True, "healthy": True, "pid": existing_pid, "started": False}
                    if required_gateway_types:
                        result[name]["registered"] = True
                    if not silent:
                        _safe_print(f"  ✓ {svc.name:12s} already running (pid {existing_pid}, port {svc.port})")
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
