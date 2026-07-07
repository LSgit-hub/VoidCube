"""
VoidCube Live Dashboard — supervisor autonomous-chain visibility and API-B observation input.

Fetches the minimal shared state needed to surface what is happening and how
the supervisor currently observes the autonomous chain.
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
    "handoff": "自主交接",
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


def fetch_supervisor_state() -> Dict[str, Any]:
    """Return supervisor UI state."""
    return _get_json(f"{SUPERVISOR_URL}/ui/state") or {}


def _project_chain_stage(stage: Dict[str, Any]) -> Dict[str, Any]:
    focus_task = dict(stage.get("focus_task") or {})
    label = str(stage.get("label") or "").strip() or "阶段"
    return {
        "key": str(stage.get("key") or "").strip(),
        "owner": str(stage.get("owner") or "").strip(),
        "label": label,
        "title": str(
            focus_task.get("title")
            or stage.get("title")
            or label
        ).strip() or label,
        "status": str(
            stage.get("status_label")
            or focus_task.get("display_status")
            or focus_task.get("status_label")
            or stage.get("summary")
            or stage.get("status")
            or "等待中"
        ).strip() or "等待中",
    }


def _project_chain_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        dict(item)
        for item in list(segment.get("items") or [])
        if isinstance(item, dict)
    ]
    head_item = items[0] if items else {}
    item_status = str(
        head_item.get("display_status")
        or head_item.get("status_label")
        or head_item.get("status")
        or ""
    ).strip()
    empty_status = "空" if not items else "等待中"
    return {
        "key": str(segment.get("key") or "").strip(),
        "label": str(segment.get("label") or "").strip() or "未命名分段",
        "owner": str(segment.get("owner") or "").strip(),
        "stage_label": str(segment.get("stage_label") or "").strip(),
        "count": max(0, int(segment.get("count") or len(items))),
        "title": str(
            head_item.get("title")
            or segment.get("focus_title")
            or segment.get("summary")
            or segment.get("empty_text")
            or "暂无可见链路项"
        ).strip() or "暂无可见链路项",
        "status": item_status or empty_status,
    }


def _build_autonomous_chain_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    counts = dict(observation.get("counts") or {})
    board = dict(observation.get("board") or {})
    chain = dict(observation.get("chain") or {})
    loop = dict(observation.get("loop") or {})
    primary_focus = dict(board.get("primary_focus") or {})
    loop_stages = [
        _project_chain_stage(stage)
        for stage in list(loop.get("stages") or [])
        if isinstance(stage, dict)
    ]
    chain_segments = [
        _project_chain_segment(section)
        for section in list(chain.get("segments") or [])
        if isinstance(section, dict)
    ]

    if not any(
        [
            str(board.get("headline") or "").strip(),
            str(board.get("hero_summary") or "").strip(),
            str(board.get("summary") or "").strip(),
            bool(primary_focus),
            bool(loop_stages),
            bool(chain_segments),
        ]
    ):
        return {}

    return {
        "api_b_backlog": int(counts.get("api_b_backlog") or 0),
        "api_a_running": int(counts.get("api_a_running") or 0),
        "api_a_ready": int(counts.get("api_a_ready") or 0),
        "candidates": int(counts.get("candidates") or 0),
        "writebacks": int(counts.get("writebacks") or 0),
        "loop_stages": loop_stages[:4],
        "segments": chain_segments[:4],
        "headline": str(board.get("headline") or "").strip(),
        "summary": str(board.get("summary") or chain.get("summary") or "").strip(),
        "hero_summary": str(board.get("hero_summary") or "").strip(),
        "primary_focus": {
            "title": str(primary_focus.get("title") or "当前没有显著闭环焦点").strip()
            or "当前没有显著闭环焦点",
            "status": str(primary_focus.get("status") or "等待中").strip() or "等待中",
            "summary": str(primary_focus.get("summary") or "").strip(),
        },
        "hero_pills": [
            dict(item)
            for item in list(board.get("hero_pills") or [])
            if isinstance(item, dict)
        ][:4],
        "observation_notes": [
            dict(item)
            for item in list(board.get("observation_notes") or [])
            if isinstance(item, dict)
        ][:4],
        "segments_headline": str(chain.get("headline") or "").strip(),
    }


def _human_snapshot_source(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    return {
        "live": "实时快照",
        "cached": "缓存快照",
        "default": "默认快照",
    }.get(text, str(value or "").strip() or "默认快照")


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


def _supervisor_recent_autonomous_activity(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = dict(state.get("autonomous_observation") or {})
    board = dict(observation.get("board") or {})
    recent = dict(board.get("recent_activity") or {})
    if not recent:
        return {}
    normalized = dict(recent)
    normalized["phase_label"] = str(recent.get("phase_label") or "最近自主动作").strip() or "最近自主动作"
    normalized["title"] = str(recent.get("title") or "最近暂无自主链路动作").strip() or "最近暂无自主链路动作"
    normalized["summary"] = str(recent.get("summary") or "").strip()
    normalized["source_label"] = str(recent.get("source_label") or "API-B").strip() or "API-B"
    display_at = str(recent.get("display_at") or "").strip()
    if not display_at:
        recorded_at = _parse_iso(recent.get("recorded_at"))
        if recorded_at is not None:
            display_at = recorded_at.strftime("%H:%M:%S")
    normalized["display_at"] = display_at or "暂无数据"
    return normalized


# ── Dashboard builder ──────────────────────────────────────────────────

def build_dashboard() -> Dict[str, Any]:
    """Collect all data and compute visibility metrics."""
    # ── Fetch data ──────────────────────────────────────────────────
    services = fetch_gateway_services()
    state = fetch_supervisor_state()
    observation = dict(state.get("autonomous_observation") or {})
    runtime = dict(observation.get("runtime") or {})
    user_signal = dict(runtime.get("user_chain_signal") or {})
    snapshot_source = str(runtime.get("snapshot_source") or "default")
    chain_snapshot = _build_autonomous_chain_snapshot(state)
    recent_activity = _supervisor_recent_autonomous_activity(state)
    user_threshold = int(user_signal.get("quiet_after_seconds") or 600)
    observation_input = {
        "headline": "API-B 判断输入",
        "user_chain_quiet": bool(user_signal.get("is_quiet", True)),
        "user_chain_state": (
            "安静软信号" if bool(user_signal.get("is_quiet", True)) else "活跃软信号"
        ),
        "active_sessions": int(user_signal.get("active_sessions") or 0),
        "quiet_after_seconds": user_threshold,
        "snapshot_source": snapshot_source,
        "scope": str(user_signal.get("scope") or "soft_signal_only").strip() or "soft_signal_only",
        "summary": "用户链路只作为 API-B 判断让路参考，不展示聊天内容。",
    }

    # ── Services ────────────────────────────────────────────────────
    registered = services.get("services", {})
    svc_list = list(registered.values()) if isinstance(registered, dict) else []

    agents = [s for s in svc_list if s.get("service_type") == "agent"]
    supervisor_info = next((s for s in svc_list if s.get("service_type") == "supervisor"), None)
    memory_info = next((s for s in svc_list if s.get("service_type") == "memory"), None)

    # ── Autonomous chain board ──────────────────────────────────────
    chain_view: Dict[str, Any]
    if chain_snapshot:
        chain_view = {
            "mode": "autonomous_chain_board",
            "headline": chain_snapshot.get("headline") or "API-B 主视角自主闭环总览",
            "hero_summary": chain_snapshot.get("hero_summary")
            or "Web 小屋与最小 dashboard 只消费 Supervisor 投影出的 API-B 主视角自主闭环读模型。",
            "summary": chain_snapshot.get("summary")
            or "API-B 主视角下的判断、治理、执行回报、Mem 回流与再读取闭环。",
            "primary_focus": dict(chain_snapshot.get("primary_focus") or {}),
            "hero_pills": list(chain_snapshot.get("hero_pills") or []),
            "observation_notes": list(chain_snapshot.get("observation_notes") or []),
            "api_b_backlog": chain_snapshot.get("api_b_backlog", 0),
            "api_a_running": chain_snapshot.get("api_a_running", 0),
            "api_a_ready": chain_snapshot.get("api_a_ready", 0),
            "candidates": chain_snapshot.get("candidates", 0),
            "writebacks": chain_snapshot.get("writebacks", 0),
            "loop_stages": list(chain_snapshot.get("loop_stages") or []),
            "segments": list(chain_snapshot.get("segments") or []),
            "segments_headline": chain_snapshot.get("segments_headline") or "自主闭环分段观察",
        }
    else:
        chain_view = {
            "mode": "observation_unavailable",
            "headline": "自主链路观测暂不可用",
            "hero_summary": "监督者尚未提供 API-B 主视角的自主闭环总览投影。",
            "summary": "监督者尚未提供 API-B 主视角的自主链路读模型。",
            "primary_focus": {},
            "hero_pills": [],
            "observation_notes": [],
            "api_b_backlog": 0,
            "api_a_running": 0,
            "api_a_ready": 0,
            "candidates": 0,
            "writebacks": 0,
            "loop_stages": [],
            "segments": [],
            "segments_headline": "自主闭环分段观察",
        }

    return {
        "now": datetime.now().isoformat(),
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
        "recent_activity": recent_activity,
        "observation_input": observation_input,
    }


# ── Terminal display ───────────────────────────────────────────────────

# ── Three-segment scene bar (baseline §8.1) ──
# Each reporter (supervisor / agent / executor) declares its own scene;
# the CLI status bar simply shows the three reporters side-by-side so the
# user can distinguish API-B governance, API-A execution, and executor
# activity without re-mixing them into one coarse status.

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
    # segment read only the supervisor_task lane so user-chat activity never
    # overwrites the autonomous-chain observation view.
    if seg["key"] == "agent":
        lane = ((info.get("lanes") or {}).get("supervisor_task")) if isinstance(info, dict) else None
        info = lane if isinstance(lane, dict) else info
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
    """Print a rich terminal dashboard with autonomous-chain visibility."""
    db = build_dashboard()

    svc = db["services"]
    chain = db["chain"]
    recent_activity = db.get("recent_activity") or {}
    observation_input = db.get("observation_input") or {}

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
            f"  ║  {chain.get('headline', 'API-B 主视角自主闭环总览')[:46]:<46s}          ║"
        )
        print(
            f"  ║  {chain.get('segments_headline', '自主闭环分段观察')[:46]:<46s}          ║"
        )
        print(
            f"  ║  候选形成 {chain.get('candidates', 0)}  ·  治理在途 {chain.get('api_b_backlog', 0)}  ·  "
            f"执行中 {chain.get('api_a_running', 0)}  ·  待认领 {chain.get('api_a_ready', 0)}  ·  回流 {chain.get('writebacks', 0)}   ║"
        )
        print(
            f"  ║  {chain.get('hero_summary', chain.get('summary', 'API-B 主视角下的闭环观测'))[:54]:<54s}  ║"
        )
        focus = dict(chain.get("primary_focus") or {})
        if focus:
            focus_line = f"当前焦点 {focus.get('status', '等待中')} · {focus.get('title', '暂无')}"
            print(f"  ║  {focus_line[:54]:<54s}  ║")
        for item in list(chain.get("loop_stages") or [])[:4]:
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:12]
            print(f"  ║  ↻ {title:<28s} {status:<12s}                          ║")
        for item in list(chain.get("segments") or [])[:4]:
            group = str(item.get("label") or item.get("owner") or "?")[:12]
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:10]
            print(f"  ║  • {group:<12s} {title:<28s} {status:<10s}              ║")
        hero_pills = [
            str(item.get("text") or "").strip()
            for item in list(chain.get("hero_pills") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if hero_pills:
            print(f"  ║  {hero_pills[0][:54]:<54s}  ║")
    else:
        print(
            f"  ║  {chain.get('headline', '自主链路观测暂不可用')[:46]:<46s}          ║"
        )
        print(
            f"  ║  {chain.get('summary', '监督者尚未提供 API-B 主视角的自主链路读模型。')[:54]:<54s}  ║"
        )
    if recent_activity:
        phase = str(recent_activity.get("phase_label") or "最近动作")[:10]
        title = str(recent_activity.get("title") or "最近暂无自主闭环动作")[:30]
        display_at = str(recent_activity.get("display_at") or "?")[:8]
        summary = str(recent_activity.get("summary") or "")[:44]
        print(f"  ║  最近自主动作 {phase:<10s} {title:<30s} {display_at:>8s}    ║")
        print(f"  ║  {summary:<54s}  ║")

    # ── API-B observation input ─────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    user_state = str(observation_input.get("user_chain_state") or "安静软信号")
    active_sessions = int(observation_input.get("active_sessions") or 0)
    quiet_after = observation_input.get("quiet_after_seconds", "?")
    snapshot = _human_snapshot_source(observation_input.get("snapshot_source"))
    scope = str(observation_input.get("scope") or "soft_signal_only").strip()
    scope_label = "仅软感知用户链路" if scope == "soft_signal_only" else scope
    print(f"  ║  API-B 判断输入                                         ║")
    print(f"  ║    用户链路 {user_state:<12s} 会话 {active_sessions:<3d} 阈值 {quiet_after!s:<6s}      ║")
    print(f"  ║    快照 {snapshot:<16s} 边界 {scope_label:<18s} ║")
    print(f"  ║    {str(observation_input.get('summary') or '用户链路只作为 API-B 判断让路参考。')[:50]:<50s}    ║")

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
        print(f"  ║    dashboard 只读 API-B 投影，不本地再管理队列。          ║")

    # ── Footer ──────────────────────────────────────────────────────
    print(f"  ╚══════════════════════════════════════════════════════════╝")
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
