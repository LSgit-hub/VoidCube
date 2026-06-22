"""VoidCube integrated service launcher.

Implements the Phase 1 multi-process startup sequence:
  1. Internal Gateway  (port 6000)
  2. Supervisor        (port 6002)
  (+ optional Agent subprocess managed by supervisor)

Usage:
  VoidCube serve                 # start all services in background
  VoidCube serve --foreground    # start all services in foreground
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
AGENT_BASE_PORT = 6080

# PID file directory
PID_DIR = Path.home() / ".VoidCube" / "run"


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
}


# ── Helpers ───────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
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


def _build_service_app(name: str, port: int):
    """Build the FastAPI app for a named service (shared by fg/bg paths)."""
    from systems.gateway.internal_gateway import InternalGateway, GatewayConfig
    from systems.supervisor.supervisor import Supervisor, SupervisorConfig
    from systems.memory.memory_service import MemoryService, MemoryServiceConfig

    if name == "gateway":
        return InternalGateway(GatewayConfig(port=port)).app
    elif name == "supervisor":
        return Supervisor(config=SupervisorConfig(port=port)).app
    elif name == "memory":
        return MemoryService(config=MemoryServiceConfig(port=port)).app
    raise ValueError(f"Unknown service: {name}")


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
        print(f"Unknown service: {name}")
        return None

    # Check if already running (PID file)
    existing_pid = _read_pid(svc.pid_file)
    if existing_pid and _pid_alive(existing_pid):
        print(f"  {svc.name:12s} already running (pid {existing_pid})")
        svc.pid = existing_pid
        return None

    # Check if port is occupied by an unknown/stale process
    if _port_listening(svc.port) and not (existing_pid and _pid_alive(existing_pid)):
        print(f"  ⚠ {svc.name:12s} port {svc.port} is occupied by an unknown process")
        print(f"     Use 'netstat -ano | findstr :{svc.port}' to find and kill it")
        return None

    if foreground:
        print(f"Starting {svc.name} on port {svc.port} (foreground)...")
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

    script = f"""
import sys, os
# Redirect Python-level stdio to log file so the child is fully detached
# from the parent console.  uvicorn uses Python logging → sys.stderr, so
# this is sufficient on all platforms.
log_file = open({log_path}, 'a', buffering=1)
sys.stdout = log_file
sys.stderr = log_file

sys.path.insert(0, {json.dumps(str(Path(__file__).resolve().parents[2]))})
os.chdir({json.dumps(str(Path.cwd()))})
import uvicorn
from systems.gateway.internal_gateway import InternalGateway, GatewayConfig
from systems.supervisor.supervisor import Supervisor, SupervisorConfig
from systems.memory.memory_service import MemoryService, MemoryServiceConfig

if {json.dumps(name)} == 'gateway':
    gw = InternalGateway(GatewayConfig(port={svc.port}))
    uvicorn.run(gw.app, host='127.0.0.1', port={svc.port}, log_level='info')
elif {json.dumps(name)} == 'supervisor':
    sv_cfg = SupervisorConfig(port={svc.port})
    sup = Supervisor(config=sv_cfg)
    uvicorn.run(sup.app, host='127.0.0.1', port={svc.port}, log_level='info')
elif {json.dumps(name)} == 'memory':
    mem_cfg = MemoryServiceConfig(port={svc.port})
    mem = MemoryService(config=mem_cfg)
    uvicorn.run(mem.app, host='127.0.0.1', port={svc.port}, log_level='info')
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
    print(f"  {svc.name:12s} started (pid {proc.pid}, port {svc.port})")
    return proc


def stop_service(name: str, silent: bool = False) -> bool:
    """Stop a named service by PID file.

    When ``silent=True``, skip status messages (used by force-stop paths).
    """
    svc = SERVICES.get(name)
    if svc is None:
        if not silent:
            print(f"Unknown service: {name}")
        return False

    pid = _read_pid(svc.pid_file)
    if pid is None or not _pid_alive(pid):
        if not silent:
            print(f"  {svc.name:12s} not running")
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
    except Exception:
        pass

    _delete_pid(svc.pid_file)
    if not silent:
        print(f"  {svc.name:12s} stopped (was pid {pid})")
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
    the rich execution dashboard with task countdowns and agent status.
    """
    # Always show daemon status
    status = status_all()
    print("\n  VoidCube Services")
    print("  " + "─" * 52)
    print(f"  {'Service':12s} {'Port':>6s} {'PID':>8s} {'Status':>10s}")
    print("  " + "─" * 52)
    for name, info in status.items():
        running = "✓ running" if info["healthy"] else ("✗ dead" if info["running"] else "— stopped")
        pid_str = str(info["pid"]) if info["pid"] else "—"
        print(f"  {name:12s} {info['port']:>6d} {pid_str:>8s} {running:>10s}")
    print("  " + "─" * 52)
    print()

    # If full dashboard requested, fetch supervisor/gateway data
    if full:
        try:
            from VoidCube_cli.ops.dashboard import print_dashboard
            print_dashboard()
        except Exception as exc:
            print(f"  ⚠ Dashboard unavailable: {exc}")
            print(f"    Is the supervisor running?  http://127.0.0.1:6002/ui")
            print()


def start_all(foreground: bool = False) -> None:
    """Start all services in order: memory → gateway → supervisor.

    Per architecture baseline §7.2: Mem must be ready first (long-term
    memory is the soul layer), then gateway (the nerve centre), then
    supervisor (which registers with gateway).
    """
    PID_DIR.mkdir(parents=True, exist_ok=True)
    print("\n  Starting VoidCube services...\n")

    # 1. Memory (soul layer — must be ready first)
    start_service("memory", foreground=foreground)
    if not foreground:
        _wait_for_health("memory", SERVICES["memory"].port)

    # 2. Gateway (nerve centre)
    start_service("gateway", foreground=foreground)
    if not foreground:
        _wait_for_health("gateway", GATEWAY_PORT)

    # 3. Supervisor (registers with gateway)
    start_service("supervisor", foreground=foreground)
    if not foreground:
        _wait_for_health("supervisor", SUPERVISOR_PORT)

    if foreground:
        # Foreground: both services are running in daemon threads.
        # Wait for them (join) — the main thread stays alive until interrupted.
        print(f"\n  Both services started. Press Ctrl+C to stop.\n")
        print(f"  Gateway:    http://127.0.0.1:{GATEWAY_PORT}")
        print(f"  Supervisor: http://127.0.0.1:{SUPERVISOR_PORT}/ui")
        print()
        try:
            import threading
            for t in _foreground_threads:
                t.join()
        except KeyboardInterrupt:
            print("\n  Shutting down...")
        return

    print()
    print_status()
    print(f"  Gateway:    http://127.0.0.1:{GATEWAY_PORT}")
    print(f"  Supervisor: http://127.0.0.1:{SUPERVISOR_PORT}/ui")
    print(f"  PID files:  {PID_DIR}")
    print()


def stop_all(force: bool = False) -> None:
    """Stop all services.

    When ``force=True``, skip status messages and terminate immediately
    without prompting (used by /quit and other automated shutdown paths).
    """
    if not force:
        print("\n  Stopping VoidCube services...\n")
    for name in SERVICES:
        stop_service(name, silent=force)
    if not force:
        print()


def _wait_for_health(name: str, port: int, timeout: float = 15.0) -> bool:
    """Wait for a service to become healthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health_check(port):
            return True
        time.sleep(0.3)
    print(f"  ⚠ {name} did not respond within {timeout}s")
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

    # Per architecture baseline §7.2: Mem → Gateway → Supervisor.
    # Memory must be ready before gateway routes memory-bound requests;
    # gateway must be ready before the supervisor registers with it.
    startup_order = ["memory", "gateway", "supervisor"]
    for name in startup_order:
        svc = SERVICES.get(name)
        if svc is None:
            continue

        existing_pid = _read_pid(svc.pid_file)
        if existing_pid and _pid_alive(existing_pid):
            healthy = _health_check(svc.port)
            result[name] = {"running": True, "healthy": healthy, "pid": existing_pid, "started": False}
            if not silent:
                tag = "✓" if healthy else "⚠"
                print(f"  {tag} {svc.name:12s} already running (pid {existing_pid}, port {svc.port})")
                if not healthy:
                    print(f"     health check failed — will not restart (pid exists)")
            svc.pid = existing_pid
            continue

        if not silent:
            print(f"  ▶ {svc.name:12s} starting on port {svc.port}...")
        proc = start_service(name, foreground=False)

        if proc is None:
            # start_service returned None — could be port-occupied by unknown
            # process, or already-running detected late.
            healthy = _health_check(svc.port)
            pid = _read_pid(svc.pid_file)
            result[name] = {"running": healthy, "healthy": healthy, "pid": pid, "started": False}
            if not silent:
                tag = "✓" if healthy else "⚠"
                print(f"     {tag} port {svc.port} reachable (reusing existing service)" if healthy
                      else f"     {tag} could not start or reach {svc.name}")
            continue

        healthy = _wait_for_health(name, svc.port, timeout=30.0)
        new_pid = _read_pid(svc.pid_file)
        result[name] = {"running": new_pid is not None, "healthy": healthy, "pid": new_pid, "started": True}
        if not silent:
            tag = "✓" if healthy else "⚠"
            print(f"     {tag} ready" if healthy else f"     {tag} not responding (may still be starting)")

    return result
