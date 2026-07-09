from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any, Dict

from VoidCube_cli.autonomous_observation import format_supervisor_status_snapshot
from VoidCube_cli.autonomous_events import sync_autonomous_supervisor_event


def initialize_autonomous_status_caches(host: Any) -> None:
    """Initialize autonomous observation caches on the CLI host."""
    host._supervisor_state_cache = {}
    host._supervisor_state_ts = 0.0
    host._supervisor_state_refreshing = False
    host._supervisor_url = ""
    host._autonomous_gateway_status_cache = {}
    host._autonomous_gateway_status_ts = 0.0
    host._autonomous_gateway_status_refreshing = False
    host._autonomous_gateway_execute_cache = {}
    host._autonomous_gateway_execute_ts = 0.0
    host._autonomous_gateway_execute_refreshing = False


def get_supervisor_url(host: Any) -> str:
    """Resolve supervisor UI state endpoint from config or defaults."""
    cached = str(getattr(host, "_supervisor_url", "") or "").strip()
    if cached:
        return cached
    try:
        from VoidCube_cli.config import load_config

        cfg = load_config()
        sc = cfg.get("supervisor", {}) if isinstance(cfg, dict) else {}
        resolved = f"http://{sc.get('host', '127.0.0.1')}:{sc.get('port', 6002)}/ui/state"
    except Exception:
        resolved = "http://127.0.0.1:6002/ui/state"
    host._supervisor_url = resolved
    return resolved


def fetch_supervisor_status(host: Any) -> Dict[str, Any]:
    """Return cached supervisor state without blocking."""
    override = getattr(host, "_fetch_supervisor_status", None)
    if callable(override):
        try:
            return dict(override() or {})
        except Exception:
            return {}
    return getattr(host, "_supervisor_state_cache", None) or {}


def fetch_autonomous_gateway_status(host: Any) -> Dict[str, Any]:
    """Return cached gateway body status for API-A autonomous execution visibility."""
    return getattr(host, "_autonomous_gateway_status_cache", None) or {}


def fetch_cached_gateway_autonomous_execute(host: Any) -> Dict[str, Any]:
    """Return cached autonomous-chain execution activity for CLI-side observation."""
    return getattr(host, "_autonomous_gateway_execute_cache", None) or {}


def is_supervisor_status_refreshing(host: Any) -> bool:
    return bool(getattr(host, "_supervisor_state_refreshing", False))


def is_gateway_autonomous_execute_refreshing(host: Any) -> bool:
    return bool(getattr(host, "_autonomous_gateway_execute_refreshing", False))


def supervisor_activity_snapshot(host: Any) -> Dict[str, Any]:
    status = fetch_supervisor_status(host)
    scene = str(status.get("scene") or "idle").strip() or "idle"
    return {
        "scene": scene,
        "is_active": scene != "idle",
        "mem_usage": dict(status.get("mem_usage") or {}),
    }


def refresh_supervisor_status(host: Any) -> None:
    """Fetch supervisor state in a background thread."""
    now = time.time()
    if (now - getattr(host, "_supervisor_state_ts", 0.0)) < 5.0:
        return
    if getattr(host, "_supervisor_state_refreshing", False):
        return
    host._supervisor_state_refreshing = True

    def _do_fetch() -> None:
        try:
            req = urllib.request.Request(get_supervisor_url(host))
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                host._supervisor_state_cache = data
                sync_autonomous_supervisor_event(host, data)
        except Exception:
            pass
        finally:
            host._supervisor_state_ts = time.time()
            host._supervisor_state_refreshing = False

    threading.Thread(target=_do_fetch, daemon=True, name="supervisor-status").start()


def refresh_autonomous_gateway_status(host: Any) -> None:
    """Fetch gateway body status in a background thread."""
    now = time.time()
    if (now - getattr(host, "_autonomous_gateway_status_ts", 0.0)) < 5.0:
        return
    if getattr(host, "_autonomous_gateway_status_refreshing", False):
        return
    host._autonomous_gateway_status_refreshing = True

    def _do_fetch() -> None:
        try:
            req = urllib.request.Request("http://127.0.0.1:6000/admin/body/status")
            with urllib.request.urlopen(req, timeout=2) as resp:
                host._autonomous_gateway_status_cache = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        finally:
            host._autonomous_gateway_status_ts = time.time()
            host._autonomous_gateway_status_refreshing = False

    threading.Thread(target=_do_fetch, daemon=True, name="auto-gateway-status").start()


def _fetch_gateway_autonomous_execute_snapshot_now(host: Any) -> Dict[str, Any]:
    gateway_base = "http://127.0.0.1:6000"
    try:
        from VoidCube_cli.config import load_config

        cfg = load_config()
        gateway_cfg = cfg.get("gateway", {})
        gateway_base = f"http://{gateway_cfg.get('host', '127.0.0.1')}:{gateway_cfg.get('port', 6000)}"
    except Exception:
        pass

    try:
        activity = json.loads(urllib.request.urlopen(f"{gateway_base}/admin/activity", timeout=5).read())
    except Exception:
        return {}

    recent = dict(activity.get("recent_metadata") or {})
    execute = dict(recent.get("autonomous_chain_execute") or {})
    if not execute:
        return {}
    return {
        "last_autonomous_chain_execute_at": activity.get("last_autonomous_chain_execute_at"),
        "autonomous_chain_execute_count": dict(activity.get("counts") or {}).get("autonomous_chain_execute_count", 0),
        "autonomous_chain_execute": execute,
    }


def refresh_gateway_autonomous_execute_snapshot(host: Any) -> None:
    """Fetch gateway autonomous-chain execution activity in a background thread."""
    now = time.time()
    if (now - getattr(host, "_autonomous_gateway_execute_ts", 0.0)) < 5.0:
        return
    if getattr(host, "_autonomous_gateway_execute_refreshing", False):
        return
    host._autonomous_gateway_execute_refreshing = True

    def _do_fetch() -> None:
        try:
            host._autonomous_gateway_execute_cache = _fetch_gateway_autonomous_execute_snapshot_now(host)
        except Exception:
            pass
        finally:
            host._autonomous_gateway_execute_ts = time.time()
            host._autonomous_gateway_execute_refreshing = False

    threading.Thread(target=_do_fetch, daemon=True, name="auto-gateway-autonomous-execute").start()


def refresh_autonomous_observation_surfaces(
    host: Any,
    *,
    refresh_gateway_cli_presence: Any,
) -> None:
    """Refresh cached autonomous observation surfaces while the CLI is idle."""
    refresh_supervisor_status(host)
    refresh_autonomous_gateway_status(host)
    refresh_gateway_autonomous_execute_snapshot(host)
    refresh_gateway_cli_presence()


def fetch_supervisor_status_snapshot(host: Any) -> Dict[str, Any]:
    override = getattr(host, "_fetch_supervisor_status_snapshot", None)
    if callable(override):
        try:
            return dict(override() or {})
        except Exception:
            return {}
    try:
        from VoidCube_cli.config import load_config

        cfg = load_config()
        sv_cfg = cfg.get("supervisor", {})
        supervisor_url = f"http://{sv_cfg.get('host', '127.0.0.1')}:{sv_cfg.get('port', 6002)}"
    except Exception:
        supervisor_url = "http://127.0.0.1:6002"

    try:
        return json.loads(urllib.request.urlopen(f"{supervisor_url}/ui/state", timeout=5).read())
    except Exception:
        return {}


def fetch_gateway_autonomous_execute_snapshot(host: Any) -> Dict[str, Any]:
    override = getattr(host, "_fetch_gateway_autonomous_execute_snapshot", None)
    if callable(override):
        try:
            return dict(override() or {})
        except Exception:
            return {}
    return _fetch_gateway_autonomous_execute_snapshot_now(host)


def format_gateway_autonomous_execute_snapshot(state: Dict[str, Any]) -> list[str]:
    lines: list[str] = []
    execute = dict(state.get("autonomous_chain_execute") or {})
    task_identity = dict(execute.get("task_identity") or {})
    summary = str(task_identity.get("summary") or "").strip()
    task_id = str(task_identity.get("task_id") or execute.get("task_id") or "").strip()
    source_service = str(execute.get("source_service") or "unknown").strip()
    count = int(state.get("autonomous_chain_execute_count") or 0)
    last_at = str(state.get("last_autonomous_chain_execute_at") or "").strip()

    if summary:
        lines.append(f"最近链路项: {summary}")
    elif task_id:
        lines.append(f"最近链路项: {task_id}")
    else:
        lines.append("最近链路项: 已记录自主执行回报")

    details: list[str] = [f"来源={source_service}", f"次数={count}"]
    if task_id:
        details.append(f"task_id={task_id}")
    if last_at:
        details.append(f"最近时间={last_at}")
    lines.append("详情: " + ", ".join(details))
    return lines


def autonomous_observation_summary_sections(
    host: Any,
) -> list[str]:
    lines: list[str] = []
    refresh_supervisor_status(host)
    refresh_gateway_autonomous_execute_snapshot(host)

    supervisor_status = fetch_supervisor_status(host)
    if supervisor_status:
        lines.extend(["", "监督者快照:"])
        lines.extend(format_supervisor_status_snapshot(supervisor_status))
    elif is_supervisor_status_refreshing(host):
        lines.extend(["", "监督者快照:", "后台刷新中，稍后会回到当前自主闭环快照。"])

    execute_activity = fetch_cached_gateway_autonomous_execute(host)
    if execute_activity:
        lines.extend(["", "最近自主执行回报:"])
        lines.extend(format_gateway_autonomous_execute_snapshot(execute_activity))
    elif is_gateway_autonomous_execute_refreshing(host):
        lines.extend(["", "最近自主执行回报:", "后台刷新中，稍后会回到最近自主执行回报。"])
    return lines


def preview_supervisor_status_lines(
    host: Any,
    *,
    limit: int = 4,
) -> list[str]:
    refresh_supervisor_status(host)
    supervisor_status = fetch_supervisor_status(host)
    if not supervisor_status:
        return []
    lines = format_supervisor_status_snapshot(supervisor_status)
    if limit <= 0:
        return list(lines)
    return list(lines[:limit])
