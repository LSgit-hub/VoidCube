"""
VoidCube Live Dashboard — supervisor execution visibility and activity signals.

Fetches real-time data from Gateway (:6000) and Supervisor (:6002)
to surface what is happening and how the supervisor currently observes
the autonomous chain.
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
    "idle": "静置",
    "planning": "治理安排",
    "drive": "内生判断",
    "memory": "记忆整理",
    "maintenance": "连续性维护",
    "handoff": "执行交接",
    # Agent (API-A)
    "learning": "自主学习",
    "code_editing": "替身改进",
    "executing": "执行中",
    # Executor
    "body_switch": "身体切换",
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
    """Return supervisor UI state."""
    return _get_json(f"{SUPERVISOR_URL}/ui/state") or {}


def _build_autonomous_chain_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    counts = dict(observation.get("counts") or {})
    board = dict(observation.get("board") or {})
    chain = dict(observation.get("chain") or {})
    loop = dict(observation.get("loop") or {})
    segment_label_fallback = {
        "api_b_backlog": "治理在途",
        "api_a_ready": "待拉取窗口",
        "api_b_candidates": "候选形成",
        "mem_recent": "写回回流",
    }
    loop_stages = []
    for stage in list(loop.get("stages") or []):
        if not isinstance(stage, dict):
            continue
        focus_task = dict(stage.get("focus_task") or {})
        loop_stages.append(
            {
                "key": str(stage.get("key") or "").strip(),
                "owner": str(stage.get("owner") or "").strip(),
                "label": str(stage.get("label") or "阶段"),
                "title": str(focus_task.get("title") or stage.get("label") or "阶段"),
                "status": str(
                    focus_task.get("display_status")
                    or stage.get("status_label")
                    or stage.get("summary")
                    or stage.get("status")
                    or "等待中"
                ),
            }
        )
    chain_segments = []
    for section in list(chain.get("segments") or []):
        if not isinstance(section, dict):
            continue
        items = [
            dict(item)
            for item in list(section.get("items") or [])
            if isinstance(item, dict)
        ]
        head_item = items[0] if items else {}
        chain_segments.append(
            {
                "key": str(section.get("key") or "").strip(),
                "label": str(
                    section.get("label")
                    or section.get("owner")
                    or segment_label_fallback.get(str(section.get("key") or "").strip(), "?")
                ),
                "owner": str(section.get("owner") or "").strip(),
                "stage_label": str(section.get("stage_label") or "").strip(),
                "count": len(items),
                "title": str(
                    head_item.get("title")
                    or section.get("empty_text")
                    or section.get("summary")
                    or "暂无可见链路项"
                ),
                "status": str(
                    head_item.get("display_status")
                    or head_item.get("status")
                    or ("空" if not items else "?")
                ),
            }
        )

    if not loop_stages and not chain_segments:
        return {}

    return {
        "api_b_backlog": int(counts.get("api_b_backlog") or 0),
        "api_a_ready": int(counts.get("api_a_ready") or 0),
        "candidates": int(counts.get("candidates") or 0),
        "writebacks": int(counts.get("writebacks") or 0),
        "loop_stages": loop_stages[:4],
        "segments": chain_segments[:4],
        "headline": str(board.get("headline") or "自主链路闭环观测"),
        "segments_headline": str(chain.get("headline") or "自主链路分段观察"),
    }


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


def _human_dashboard_display(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return "?"
    return {
        "continuous": "持续运行",
        "no data": "暂无数据",
        "unknown": "未知",
        "now": "现在",
    }.get(text, text)


def _human_policy_scope(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return "?"
    return {
        "soft_signal_only": "仅软感知用户链路",
    }.get(text, text)


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
    observation = dict(state.get("autonomous_observation") or {})
    runtime = dict(observation.get("runtime") or {})
    guards = dict(runtime.get("activity_guards") or {})
    decisions = dict(runtime.get("eligibility") or guards.get("decisions") or {})
    thresholds = dict(guards.get("thresholds") or {})
    chain_snapshot = _build_autonomous_chain_snapshot(state)

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
    last_chain_plan = _parse_iso(activity.get("last_autonomous_chain_plan_at"))
    last_chain_exec = _parse_iso(activity.get("last_autonomous_chain_execute_at"))

    # ── Idle seconds ────────────────────────────────────────────────
    def idle_since(dt: Optional[datetime]) -> Optional[float]:
        if dt is None:
            return None
        return max(0.0, (now - dt).total_seconds())

    user_idle_s = idle_since(last_user)
    agent_idle_s = idle_since(last_agent)
    memory_idle_s = idle_since(last_memory)
    chain_plan_idle_s = idle_since(last_chain_plan)
    chain_exec_idle_s = idle_since(last_chain_exec)

    # ── Activity guard read model from supervisor state ─────────────
    user_threshold = int(thresholds.get("user_idle_seconds", 600))
    memory_threshold = int(thresholds.get("memory_idle_seconds", 600))
    workflow_threshold = int(thresholds.get("workflow_idle_seconds", 600))
    # ── Countdowns ──────────────────────────────────────────────────
    # When will each activity signal next cross its configured threshold?
    countdowns: Dict[str, Any] = {}

    for label, idle_val, thresh in [
        ("user_chain_quiet", user_idle_s, user_threshold),
        ("agent_idle", agent_idle_s, workflow_threshold),
        ("memory_idle", memory_idle_s, memory_threshold),
        ("autonomous_chain_plan_idle", chain_plan_idle_s, workflow_threshold),
        ("autonomous_chain_execute_idle", chain_exec_idle_s, workflow_threshold),
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

    countdowns["autonomous_chain"] = {
        "remaining_s": 0.0,
        "display": "continuous",
        "met": True,
        "scope": "soft_signal_only",
        "summary": "API-B 24x7 self-governance; user chain is soft signal only",
    }

    # Overall execution eligibility follows the supervisor's current decision,
    # not a locally reconstructed execution-window gate.
    can_execute = bool(decisions.get("eligible_for_execution", False))
    countdowns["can_execute"] = {"met": can_execute}

    # ── Autonomous chain board ──────────────────────────────────────
    chain_view: Dict[str, Any]
    if chain_snapshot:
        chain_view = {
            "mode": "autonomous_chain_board",
            "headline": chain_snapshot.get("headline", "自主链路闭环观测"),
            "api_b_backlog": chain_snapshot.get("api_b_backlog", 0),
            "api_a_ready": chain_snapshot.get("api_a_ready", 0),
            "candidates": chain_snapshot.get("candidates", 0),
            "writebacks": chain_snapshot.get("writebacks", 0),
            "loop_stages": list(chain_snapshot.get("loop_stages") or []),
            "segments": list(chain_snapshot.get("segments") or []),
            "segments_headline": chain_snapshot.get("segments_headline", "自主链路分段观察"),
        }
    else:
        chain_view = {
            "mode": "observation_unavailable",
            "headline": "自主链路观测暂不可用",
            "summary": "监督者尚未提供自主链路读模型",
            "api_b_backlog": 0,
            "api_a_ready": 0,
            "candidates": 0,
            "writebacks": 0,
            "loop_stages": [],
            "segments": [],
            "segments_headline": "自主链路分段观察",
        }

    # ── Next review cycle estimate ──────────────────────────────────
    review_interval = 300  # default 5 min
    next_review_s = max(0.0, review_interval - (chain_plan_idle_s or review_interval))
    if chain_plan_idle_s is not None and chain_plan_idle_s < review_interval:
        next_review_s = review_interval - chain_plan_idle_s
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
        "chain": chain_view,
        "countdowns": countdowns,
        "eligibility": {
            "can_execute": can_execute,
            "eligible_for_planning": decisions.get("eligible_for_planning", False),
            "eligible_for_execution": decisions.get("eligible_for_execution", False),
        },
        "next_review_cycle_s": next_review_s,
        "next_review_cycle_display": _fmt_countdown(next_review_s),
        "autonomous_chain_policy": {
            "label": "continuous",
            "scope": "soft_signal_only",
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
    # The minimal ops dashboard observes the autonomous chain: for the API-A
    # segment read the supervisor_task lane so its view is never overwritten by
    # the main CLI's user-chat subagents. Fall back to the top-level slot when
    # an older gateway doesn't expose lanes yet.
    if seg["key"] == "agent":
        lane = ((info.get("lanes") or {}).get("supervisor_task")) if isinstance(info, dict) else None
        if isinstance(lane, dict) and lane:
            info = lane
    scene = str(info.get("scene") or "idle")
    label = SCENE_LABEL.get(scene, scene)
    reachable = bool(info.get("reachable"))
    icon = seg["icon"]
    if not reachable:
        return f"{icon} {seg['name']}: ⛔ 不可达"
    task_hint = ""
    if seg["key"] == "agent":
        task_id = info.get("scene_task_id")
        if task_id:
            short = str(task_id)[:8]
            task_hint = f" · 链路项 {short}"
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
        print("  ⛔ 场景状态暂不可用（网关离线）")
        return
    print("  ┌─ 分域场景状态（按报告者）─────────────────────────────")
    for seg in REPORTER_SEGMENT:
        print(f"  │  {_format_segment_line(seg, scenes)}")
    print("  └───────────────────────────────────────────────────────────")


def print_dashboard() -> None:
    """Print a rich terminal dashboard with execution visibility."""
    db = build_dashboard()

    svc = db["services"]
    chain = db["chain"]
    cds = db["countdowns"]
    elig = db["eligibility"]
    policy = db["autonomous_chain_policy"]

    # ── Header ──────────────────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║            VoidCube Supervisor · 实时观测                ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print_three_segment_status_bar()
    print()

    # ── Services ────────────────────────────────────────────────────
    agent_n = svc["agents"]
    sup_ok = "✓" if svc["supervisor"] else "✗"
    mem_ok = "✓" if svc["memory"] else "✗"
    print(f"  ║  服务状态   Gateway ✓  Super {sup_ok}  Memory {mem_ok}  Agents {agent_n:<3}         ║")

    # ── Autonomous chain board ─────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    if chain.get("mode") == "autonomous_chain_board":
        print(
            f"  ║  {chain.get('headline', '自主链路闭环观测')[:46]:<46s}          ║"
        )
        print(
            f"  ║  {chain.get('segments_headline', '自主链路分段观察')[:46]:<46s}          ║"
        )
        print(
            f"  ║  候选 {chain.get('candidates', 0)}  ·  治理 {chain.get('api_b_backlog', 0)}  ·  "
            f"待拉取 {chain.get('api_a_ready', 0)}  ·  写回 {chain.get('writebacks', 0)}              ║"
        )
        for item in list(chain.get("loop_stages") or [])[:4]:
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:12]
            print(f"  ║  ↻ {title:<28s} {status:<12s}                          ║")
        for item in list(chain.get("segments") or [])[:4]:
            group = str(item.get("label") or item.get("owner") or "?")[:12]
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:10]
            print(f"  ║  • {group:<12s} {title:<28s} {status:<10s}              ║")
    else:
        print(
            f"  ║  {chain.get('headline', '自主链路观测暂不可用')[:46]:<46s}          ║"
        )
        print(
            f"  ║  {chain.get('summary', '等待 Supervisor 观测板数据')[:54]:<54s}  ║"
        )

    # ── Runtime activity signals ────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  运行时活动信号                                         ║")

    conds = [
        ("用户链路安静", "user_chain_quiet"),
        ("API-A 空闲", "agent_idle"),
        ("记忆空闲", "memory_idle"),
        ("规划空闲", "autonomous_chain_plan_idle"),
        ("执行空闲", "autonomous_chain_execute_idle"),
    ]
    for label, key in conds:
        c = cds.get(key, {})
        icon = "✓" if c.get("met") else "⏳"
        display = _human_dashboard_display(c.get("display", "?"))
        print(f"  ║    {icon} {label:<14s} {display:>8s}  (阈值 {c.get('threshold_s', '?')}s)                    ║")

    # Continuous autonomous-chain banner
    policy_cd = cds.get("autonomous_chain", {})
    print(
        f"  ║    ✓ 自主链路   {_human_dashboard_display(policy_cd.get('display', '?')):>8s}  "
        f"({_human_policy_scope(policy.get('scope', '?'))})              ║"
    )

    # ── Eligibility ─────────────────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    can = "✓ 允许执行" if elig["can_execute"] else "✗ 暂缓执行"
    plan_ok = "✓" if elig["eligible_for_planning"] else "✗"
    exec_ok = "✓" if elig["eligible_for_execution"] else "✗"
    print(f"  ║  当前状态   {can:<40s}   ║")
    print(f"  ║             可规划: {plan_ok}   可执行: {exec_ok}                                  ║")

    # ── Next review cycle ───────────────────────────────────────────
    next_rev = db.get("next_review_cycle_display", "?")
    print(f"  ║  下一轮复核 {next_rev:<14s}                                   ║")

    # ── Agent instances ─────────────────────────────────────────────
    agents = svc.get("agent_instances", [])
    if agents:
        print(f"  ╠══════════════════════════════════════════════════════════╣")
        print(f"  ║  API-A 实例                                              ║")
        for a in agents:
            healthy = "✓" if a["healthy"] else "✗"
            print(f"  ║    {healthy} {a['name']:<20s} slot={a['slot_id']:<8s} {a['address']}   ║")
    else:
        print(f"  ╠══════════════════════════════════════════════════════════╣")
        print(f"  ║  ⚠ 尚无 API-A 实例注册                                  ║")
        print(f"  ║    监督者放行链路项后，API-A 自主执行器才会出现。         ║")
        print(f"  ║    下方活动信号仅作为观测上下文。                         ║")

    # ── Footer ──────────────────────────────────────────────────────
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Plain-language summary ──────────────────────────────────────
    if not elig["can_execute"]:
        print("  💡 为什么当前仍暂缓执行？")
        print("     → 监督者尚未放行执行。")
        for label, key in conds:
            c = cds.get(key, {})
            if not c.get("met") and c.get("remaining_s", 1) > 0:
                print(f"     → {label}: 还需 {c['display']}（参考阈值 {c.get('threshold_s', '?')}s）")
        if svc["agents"] == 0:
            print(f"     → 当前没有 API-A 实例在运行；监督者放行后才会拉起执行器。")
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
            print(f"  刷新时间 {datetime.now().strftime('%H:%M:%S')}  （Ctrl+C 退出）")
            print()

            try:
                print_dashboard()
            except Exception as exc:
                print(f"  ⚠ Dashboard 错误: {exc}")
                print()

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  监视已停止。\n")
