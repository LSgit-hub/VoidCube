"""
VoidCube Live Dashboard — task execution visibility and countdowns.

Fetches real-time data from Gateway (:6000) and Supervisor (:6002)
to surface what is happening and when the next execution cycle will fire.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ── Configuration ──────────────────────────────────────────────────────
GATEWAY_URL = "http://127.0.0.1:6000"
SUPERVISOR_URL = "http://127.0.0.1:6002"
REQUEST_TIMEOUT = 5  # seconds per HTTP call

# Scene→human label and color hint (per baseline §8.1 reporter-scene mapping).
# The CLI status bar reads the per-reporter scene and renders a three-
# segment headline.  This map is the *only* translation layer between
# scene names and the user-facing label.  Adding a new scene must
# extend SUPERVISOR_LEGAL_SCENES / AGENT_LEGAL_SCENES / EXECUTOR_LEGAL_SCENES
# first; the label map is purely cosmetic.
SCENE_LABEL: Dict[str, str] = {
    # Supervisor (API-B)
    "idle": "idle",
    "planning": "planning",
    "drive": "drive",
    "memory": "memory",
    "maintenance": "maintenance",
    "dispatch": "dispatch",
    # Agent (API-A)
    "learning": "learning",
    "code_editing": "code_editing",
    "executing": "executing",
    # Executor
    "body_switch": "body_switch",
}


# ── HTTP helpers ───────────────────────────────────────────────────────

def _get_json(url: str, timeout: float = REQUEST_TIMEOUT) -> Optional[Dict[str, Any]]:
    """GET a JSON endpoint.  Returns None on any error."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return None


def _post_json(url: str, payload: Dict[str, Any], timeout: float = REQUEST_TIMEOUT) -> Optional[Dict[str, Any]]:
    """POST JSON to an endpoint.  Returns None on any error."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return None


# ── Data fetchers ──────────────────────────────────────────────────────

def fetch_gateway_services() -> Dict[str, Any]:
    """Return registered services from the gateway."""
    return _get_json(f"{GATEWAY_URL}/admin/services") or {}


def fetch_gateway_activity() -> Dict[str, Any]:
    """Return gateway activity snapshot."""
    return _get_json(f"{GATEWAY_URL}/admin/activity") or {}


def fetch_supervisor_state() -> Dict[str, Any]:
    """Return supervisor UI state (tasks, drive, idle window)."""
    return _get_json(f"{SUPERVISOR_URL}/ui/state") or {}


def fetch_supervisor_tasks(status_filter: str = "") -> Dict[str, Any]:
    """Return task queue from supervisor."""
    url = f"{SUPERVISOR_URL}/self-evolution/tasks"
    if status_filter:
        url += f"?status={status_filter}"
    return _get_json(url) or {}


def fetch_idle_window(task_family: str = "general_self_evolution") -> Dict[str, Any]:
    """Evaluate the idle window for a specific task family."""
    return _post_json(
        f"{SUPERVISOR_URL}/runtime/idle-window/evaluate",
        {"task_family": task_family},
    ) or {}


# ── Time calculations ──────────────────────────────────────────────────

def _fmt_countdown(seconds: Optional[float]) -> str:
    """Format seconds into a human-readable countdown."""
    if seconds is None:
        return "unknown"
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s"
    h, r = divmod(int(seconds), 3600)
    m = r // 60
    return f"{h}h{m:02d}m"


def _sec_until(target_hour: int, target_min: int = 0) -> float:
    """Seconds from now until the next occurrence of target_hour:target_min."""
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
    if target <= now:
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp string, returning a naive UTC datetime."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


# ── Dashboard builder ──────────────────────────────────────────────────

def build_dashboard() -> Dict[str, Any]:
    """Collect all data and compute visibility metrics."""
    now = datetime.now()

    # ── Fetch data ──────────────────────────────────────────────────
    services = fetch_gateway_services()
    activity = fetch_gateway_activity()
    state = fetch_supervisor_state()
    idle = fetch_idle_window("general_self_evolution")
    tasks_data = fetch_supervisor_tasks()

    # ── Services ────────────────────────────────────────────────────
    registered = services.get("services", {})
    svc_list = list(registered.values()) if isinstance(registered, dict) else []

    agents = [s for s in svc_list if s.get("service_type") == "agent"]
    supervisor_info = next((s for s in svc_list if s.get("service_type") == "supervisor"), None)
    memory_info = next((s for s in svc_list if s.get("service_type") == "memory"), None)

    # ── Activity timestamps ─────────────────────────────────────────
    last_user = _parse_iso(activity.get("last_user_request_at"))
    last_agent = _parse_iso(activity.get("last_agent_work_at"))
    last_memory = _parse_iso(activity.get("last_memory_task_at"))
    last_se_plan = _parse_iso(activity.get("last_self_evolution_plan_at"))
    last_se_exec = _parse_iso(activity.get("last_self_evolution_execute_at"))

    # ── Idle seconds ────────────────────────────────────────────────
    def idle_since(dt: Optional[datetime]) -> Optional[float]:
        if dt is None:
            return None
        return max(0.0, (now - dt).total_seconds())

    user_idle_s = idle_since(last_user)
    agent_idle_s = idle_since(last_agent)
    memory_idle_s = idle_since(last_memory)
    se_plan_idle_s = idle_since(last_se_plan)
    se_exec_idle_s = idle_since(last_se_exec)

    # ── Idle window from supervisor ─────────────────────────────────
    checks = idle.get("checks", {})
    idle_secs = idle.get("idle_seconds", {})
    thresholds = idle.get("thresholds", {})
    decisions = idle.get("decisions", {})

    user_threshold = int(thresholds.get("user_idle_seconds", 600))
    memory_threshold = int(thresholds.get("memory_idle_seconds", 600))
    workflow_threshold = int(thresholds.get("workflow_idle_seconds", 600))
    exec_start = int(thresholds.get("execution_window_start_hour", 0))
    exec_end = int(thresholds.get("execution_window_end_hour", 24))

    # ── Countdowns ──────────────────────────────────────────────────
    # When will each idle condition be met?
    countdowns: Dict[str, Any] = {}

    for label, idle_val, thresh in [
        ("user_idle", user_idle_s, user_threshold),
        ("agent_idle", agent_idle_s, workflow_threshold),
        ("memory_idle", memory_idle_s, memory_threshold),
        ("se_plan_idle", se_plan_idle_s, workflow_threshold),
        ("se_exec_idle", se_exec_idle_s, workflow_threshold),
    ]:
        if idle_val is None:
            countdowns[label] = {"remaining_s": None, "display": "no data", "met": True}
        else:
            remaining = max(0.0, thresh - idle_val)
            countdowns[label] = {
                "remaining_s": remaining,
                "display": _fmt_countdown(remaining),
                "met": remaining <= 0,
                "idle_s": idle_val,
                "threshold_s": thresh,
            }

    # Execution window countdown
    now_hour = now.hour
    in_window = exec_start <= now_hour < exec_end
    if in_window:
        window_remaining = _sec_until(exec_end, 0)
    else:
        window_remaining = _sec_until(exec_start, 0)
    countdowns["execution_window"] = {
        "remaining_s": window_remaining if not in_window else max(0.0, window_remaining),
        "display": _fmt_countdown(window_remaining if not in_window else window_remaining),
        "met": in_window,
        "in_window": in_window,
        "window": f"{exec_start:02d}:00–{exec_end:02d}:00",
    }

    # Overall execution eligibility
    all_idle_met = all(
        countdowns[k]["met"]
        for k in ["user_idle", "agent_idle", "memory_idle", "se_plan_idle", "se_exec_idle"]
    )
    can_execute = all_idle_met and in_window
    countdowns["can_execute"] = {"met": can_execute}

    # ── Tasks ───────────────────────────────────────────────────────
    tasks = tasks_data.get("tasks", [])
    approved = [t for t in tasks if t.get("status") == "approved"]
    pending = [t for t in tasks if t.get("status") in ("planned", "deferred", "paused")]

    # ── Next review cycle estimate ──────────────────────────────────
    review_interval = 300  # default 5 min
    next_review_s = max(0.0, review_interval - (se_plan_idle_s or review_interval))
    if se_plan_idle_s is not None and se_plan_idle_s < review_interval:
        next_review_s = review_interval - se_plan_idle_s
    else:
        next_review_s = review_interval

    return {
        "now": now.isoformat(),
        "services": {
            "agents": len(agents),
            "agent_instances": [
                {
                    "name": a.get("service_name", "?"),
                    "healthy": a.get("healthy", False),
                    "address": a.get("address", "?"),
                    "slot_id": a.get("metadata", {}).get("slot_id", "?"),
                }
                for a in agents
            ],
            "supervisor": bool(supervisor_info),
            "memory": bool(memory_info),
        },
        "tasks": {
            "approved": len(approved),
            "pending": len(pending),
            "approved_list": [
                {
                    "title": t.get("title", "?"),
                    "status": t.get("status", "?"),
                    "task_type": t.get("task_type", "?"),
                    "task_id": t.get("task_id", "?")[:8],
                }
                for t in approved[:5]
            ],
            "pending_summary": [
                {
                    "title": t.get("title", "?"),
                    "status": t.get("status", "?"),
                }
                for t in pending[:5]
            ],
        },
        "idle_window": {
            "checks": checks,
            "idle_seconds": idle_secs,
            "thresholds": {
                "user_idle_s": user_threshold,
                "memory_idle_s": memory_threshold,
                "workflow_idle_s": workflow_threshold,
            },
        },
        "countdowns": countdowns,
        "eligibility": {
            "can_execute": can_execute,
            "eligible_for_planning": decisions.get("eligible_for_planning", False),
            "eligible_for_execution": decisions.get("eligible_for_execution", False),
        },
        "next_review_cycle_s": next_review_s,
        "next_review_cycle_display": _fmt_countdown(next_review_s),
        "execution_window": {
            "start_hour": exec_start,
            "end_hour": exec_end,
            "in_window": in_window,
        },
    }


# ── Terminal display ───────────────────────────────────────────────────

# ── Three-segment scene bar (baseline §8.1) ──
# Each reporter (supervisor / agent / executor) declares its own scene;
# the CLI status bar surfaces all three side-by-side.  The legacy single
# "API-B status" field that mixed the supervisor's scene with the agent's
# has been split into per-reporter segments so the user can tell who is
# actually doing the work.

REPORTER_SEGMENT: List[Dict[str, str]] = [
    {"key": "supervisor", "icon": "🧠", "name": "API-B"},
    {"key": "agent",      "icon": "🤖", "name": "API-A"},
    {"key": "executor",   "icon": "⚙️",  "name": "Executor"},
]


def fetch_scenes_aggregated(force_refresh: bool = True) -> Dict[str, Any]:
    """Fetch the gateway's aggregated per-reporter scene view.

    ``force_refresh=True`` triggers a fresh fetch from each registered
    service so the status bar reflects current activity.  Returns an
    empty envelope if the gateway is unreachable.
    """
    if force_refresh:
        # /admin/scenes/refresh is a POST endpoint that forces a re-fetch
        # of every reporter's scene before returning the cached view.
        return _post_json(f"{GATEWAY_URL}/admin/scenes/refresh", {}) or {}
    return _get_json(f"{GATEWAY_URL}/admin/scenes") or {}


def _format_segment_line(seg: Dict[str, str], state: Dict[str, Any]) -> str:
    info = state.get(seg["key"]) or {}
    scene = str(info.get("scene") or "idle")
    label = SCENE_LABEL.get(scene, scene)
    reachable = bool(info.get("reachable"))
    icon = seg["icon"]
    if not reachable:
        return f"{icon} {seg['name']}: ⛔ unreachable"
    task_hint = ""
    if seg["key"] == "agent":
        task_id = info.get("scene_task_id")
        if task_id:
            short = str(task_id)[:8]
            task_hint = f" · task {short}"
        fg_count = max(0, int(info.get("subagent_foreground_count") or 0))
        bg_count = max(0, int(info.get("subagent_background_count") or 0))
        if fg_count or bg_count:
            counts = f"{fg_count}+{bg_count}" if bg_count else str(fg_count)
            task_hint += f" · SA {counts}"
            focus = str(
                info.get("subagent_focus_tool")
                or info.get("subagent_focus_preview")
                or ""
            ).strip()
            if focus:
                task_hint += f" · {focus[:20]}"
    elif seg["key"] == "supervisor":
        title = info.get("title")
        if title:
            task_hint = f" · {str(title)[:24]}"
    return f"{icon} {seg['name']}: {label}{task_hint}"


def print_three_segment_status_bar() -> None:
    """Render the per-reporter scene status bar at the CLI prompt.

    The bar reads the gateway's ``/admin/scenes`` aggregation and prints
    three independent segments — never a fused string.  When the gateway
    or any reporter is unreachable, the segment shows the ⛔ marker
    instead of fabricating a scene.
    """
    payload = fetch_scenes_aggregated(force_refresh=True)
    scenes = payload.get("scenes") or {}
    if not scenes:
        print("  ⛔ Scene status unavailable (gateway offline)")
        return
    print("  ┌─ Scene Status (per-reporter) ─────────────────────────────")
    for seg in REPORTER_SEGMENT:
        print(f"  │  {_format_segment_line(seg, scenes)}")
    print("  └───────────────────────────────────────────────────────────")


def print_dashboard() -> None:
    """Print a rich terminal dashboard with execution visibility."""
    db = build_dashboard()

    svc = db["services"]
    tasks = db["tasks"]
    cds = db["countdowns"]
    elig = db["eligibility"]
    ew = db["execution_window"]

    # ── Header ──────────────────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║          VoidCube Supervisor — Live Status               ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print_three_segment_status_bar()
    print()

    # ── Services ────────────────────────────────────────────────────
    agent_n = svc["agents"]
    sup_ok = "✓" if svc["supervisor"] else "✗"
    mem_ok = "✓" if svc["memory"] else "✗"
    print(f"  ║  Services   Gateway ✓  Super {sup_ok}  Memory {mem_ok}  Agents {agent_n:<3}         ║")

    # ── Task queue ──────────────────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  Tasks      {tasks['approved']} approved  ·  {tasks['pending']} pending                            ║")

    for t in tasks["approved_list"]:
        title = t["title"][:42]
        print(f"  ║  ✓ {title:<42s}   ║")
    for t in tasks["pending_summary"][:3]:
        status_icon = {"planned": "○", "deferred": "⏸", "paused": "⏸"}.get(t["status"], "•")
        title = t["title"][:40]
        print(f"  ║  {status_icon} {title:<42s}   ║")

    # ── Idle window conditions ──────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  Idle Window Conditions                                 ║")

    conds = [
        ("User idle", "user_idle"),
        ("Agent idle", "agent_idle"),
        ("Memory idle", "memory_idle"),
        ("Plan idle", "se_plan_idle"),
        ("Exec idle", "se_exec_idle"),
    ]
    for label, key in conds:
        c = cds.get(key, {})
        icon = "✓" if c.get("met") else "⏳"
        display = c.get("display", "?")
        print(f"  ║    {icon} {label:<14s} {display:>8s}  (need {c.get('threshold_s', '?')}s)                    ║")

    # Execution window
    ew_cd = cds.get("execution_window", {})
    ew_icon = "✓" if ew.get("in_window") else "⏳"
    print(f"  ║    {ew_icon} Exec window  {ew_cd.get('display', '?'):>8s}  ({ew.get('window', '?')})                    ║")

    # ── Eligibility ─────────────────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    can = "✓ CAN EXECUTE" if elig["can_execute"] else "✗ BLOCKED"
    plan_ok = "✓" if elig["eligible_for_planning"] else "✗"
    exec_ok = "✓" if elig["eligible_for_execution"] else "✗"
    print(f"  ║  Status     {can:<40s}   ║")
    print(f"  ║             Plan eligible: {plan_ok}   Execute eligible: {exec_ok}                       ║")

    # ── Next review cycle ───────────────────────────────────────────
    next_rev = db.get("next_review_cycle_display", "?")
    print(f"  ║  Next review cycle in {next_rev:<10s}                               ║")

    # ── Agent instances ─────────────────────────────────────────────
    agents = svc.get("agent_instances", [])
    if agents:
        print(f"  ╠══════════════════════════════════════════════════════════╣")
        print(f"  ║  Agent Instances                                        ║")
        for a in agents:
            healthy = "✓" if a["healthy"] else "✗"
            print(f"  ║    {healthy} {a['name']:<20s} slot={a['slot_id']:<8s} {a['address']}   ║")
    else:
        print(f"  ╠══════════════════════════════════════════════════════════╣")
        print(f"  ║  ⚠ No agent instances registered                        ║")
        print(f"  ║    Agents are launched by the supervisor when a task     ║")
        print(f"  ║    is approved AND the execution window is open AND      ║")
        print(f"  ║    all idle conditions are met.                          ║")

    # ── Footer ──────────────────────────────────────────────────────
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Plain-language summary ──────────────────────────────────────
    if not elig["can_execute"]:
        print("  💡 Why is execution blocked?")
        if not cds.get("execution_window", {}).get("in_window"):
            print(f"     → Execution window is {ew.get('window', '?')}.  Current hour: {datetime.now().hour:02d}:00")
            print(f"     → Window opens in {cds['execution_window']['display']}")
        for label, key in conds:
            c = cds.get(key, {})
            if not c.get("met") and c.get("remaining_s", 1) > 0:
                print(f"     → {label}: {c['display']} remaining (need {c.get('threshold_s', '?')}s total)")
        if svc["agents"] == 0:
            print(f"     → No agent instances are running.  One will be spawned when execution fires.")
        print()


def watch_dashboard(interval: float = 3.0) -> None:
    """Print the dashboard in a loop, refreshing every *interval* seconds.

    Press Ctrl+C to exit watch mode.
    """
    import os
    import sys

    # ANSI clear-screen sequence
    clear = "\033[2J\033[H"

    try:
        while True:
            # Clear and re-print
            sys.stdout.write(clear)
            sys.stdout.flush()

            # Timestamp
            from datetime import datetime
            print(f"  Refreshed at {datetime.now().strftime('%H:%M:%S')}  (Ctrl+C to exit)")
            print()

            try:
                print_dashboard()
            except Exception as exc:
                print(f"  ⚠ Dashboard error: {exc}")
                print()

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Watch stopped.\n")
