"""
VoidCube Live Dashboard — API-B observation and 员工代理 execution visibility.

Fetches the minimal shared state needed to surface what is happening in the
autonomous chain and what API-B is currently observing.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..autonomous.observation import supervisor_employee_execution_hint
from ....infrastructure.gateway.executor import default_gateway_url
from ....infrastructure.gateway.presence import gateway_auth_headers


# ── Configuration ──────────────────────────────────────────────────────
SUPERVISOR_URL = "http://127.0.0.1:6002"


def _gateway_url() -> str:
    return default_gateway_url().rstrip("/")


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
    "planning": "判断安排",
    "drive": "内生判断",
    "memory": "记忆整理",
    "maintenance": "连续性维护",
    "handoff": "自主交接",
    # Agent (员工代理)
    "learning": "自主学习",
    "code_editing": "替身改进",
    "executing": "执行中",
    # Executor
    "body_switch": "身体切换",
}


# ── HTTP helpers ───────────────────────────────────────────────────────

def _get_json(
    url: str,
    timeout: float = REQUEST_TIMEOUT,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """GET a JSON endpoint.  Returns None on any error."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        pass
    return None


# ── Data fetchers ──────────────────────────────────────────────────────

def fetch_supervisor_state() -> Dict[str, Any]:
    """Return supervisor UI state."""
    return _get_json(f"{SUPERVISOR_URL}/ui/state") or {}


def fetch_gateway_scenes() -> Dict[str, Any]:
    """Return gateway scene projections."""
    return _get_json(
        f"{_gateway_url()}/admin/scenes",
        headers=gateway_auth_headers(),
    ) or {}


def fetch_gateway_status() -> Dict[str, Any]:
    """Return gateway health / executor snapshot."""
    return _get_json(f"{_gateway_url()}/") or {}


def _project_stage_card(
    stage_card: Dict[str, Any],
    rail_entry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rail = dict(rail_entry or {})
    label = str(
        rail.get("label")
        or stage_card.get("observation_stage_label")
        or stage_card.get("title")
        or "阶段"
    ).strip() or "阶段"
    return {
        "key": str(stage_card.get("stage_key") or rail.get("key") or "").strip(),
        "source_label": str(
            stage_card.get("source_label") or rail.get("source_label") or ""
        ).strip(),
        "label": label,
        "title": label,
        "status": str(
            stage_card.get("display_status")
            or rail.get("state")
            or stage_card.get("summary")
            or stage_card.get("status")
            or "等待中"
        ).strip() or "等待中",
    }


def _project_rail_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": str(entry.get("key") or "").strip(),
        "label": str(entry.get("label") or "阶段").strip() or "阶段",
        "source_label": str(entry.get("source_label") or "").strip(),
        "status": str(entry.get("status") or "idle").strip().lower() or "idle",
        "state": str(entry.get("state") or "等待中").strip() or "等待中",
        "note": str(entry.get("note") or "").strip(),
        "focus": bool(entry.get("focus")),
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
        "source_label": str(segment.get("source_label") or "").strip(),
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
    rail_entries = [
        dict(entry)
        for entry in list(loop.get("rail_entries") or [])
        if isinstance(entry, dict)
    ]
    rail_by_key = {
        str(entry.get("key") or "").strip(): entry
        for entry in rail_entries
        if str(entry.get("key") or "").strip()
    }
    stage_cards = [
        dict(stage)
        for stage in list(loop.get("stage_cards") or [])
        if isinstance(stage, dict)
    ]
    stage_cards = [
        _project_stage_card(stage, rail_by_key.get(str(stage.get("stage_key") or "").strip()))
        for stage in stage_cards
    ]
    rail_projection = [
        _project_rail_entry(entry)
        for entry in rail_entries
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
            bool(stage_cards),
            bool(chain_segments),
        ]
    ):
        return {}

    return {
        "api_b_judgement": int(counts.get("api_b_judgement") or 0),
        "employee_running": int(counts.get("employee_running") or 0),
        "employee_dispatch": int(counts.get("employee_dispatch") or 0),
        "candidates": int(counts.get("candidates") or 0),
        "writebacks": int(counts.get("writebacks") or 0),
        "stage_cards": stage_cards[:4],
        "rail_entries": rail_projection[:4],
        "segments": chain_segments[:4],
        "headline": str(board.get("headline") or "").strip(),
        "summary": str(board.get("summary") or chain.get("summary") or "").strip(),
        "hero_summary": str(board.get("hero_summary") or "").strip(),
        "primary_focus": {
            "title": str(primary_focus.get("title") or "当前没有明显焦点").strip()
            or "当前没有明显焦点",
            "status": str(primary_focus.get("status") or "等待中").strip() or "等待中",
            "summary": str(primary_focus.get("summary") or "").strip(),
        },
        "segments_headline": str(chain.get("headline") or "").strip(),
    }


def _human_snapshot_source(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    return {
        "live": "实时快照",
        "cached": "缓存快照",
        "default": "默认快照",
    }.get(text, str(value or "").strip() or "默认快照")


def _human_execution_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {
        "self_learning": "自主学习",
        "body_improvement": "替身改进",
        "body_upgrade": "替身改进",
    }.get(text, str(value or "").strip() or "自主链路项")


def _session_tail(value: Any) -> str:
    text = str(value or "").strip()
    return text[-8:] if text else "—"


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


def _build_employee_observation(
    supervisor_state: Dict[str, Any],
    gateway_scenes: Dict[str, Any],
    gateway_status: Dict[str, Any],
) -> Dict[str, Any]:
    hint = supervisor_employee_execution_hint(supervisor_state)
    scenes = dict(gateway_scenes.get("scenes") or {})
    agent_scene = dict(scenes.get("agent") or {})
    lanes = agent_scene.get("lanes") if isinstance(agent_scene.get("lanes"), dict) else {}
    supervisor_lane = dict(lanes.get("supervisor_task") or {})

    active_executor = dict(gateway_status.get("active_cli_executor") or {})
    if str(active_executor.get("agent_lane") or "").strip().lower() != "supervisor_task":
        active_executor = {}

    focus_task = dict(hint.get("focus_task") or {})
    execution_scene = str(
        supervisor_lane.get("scene")
        or active_executor.get("scene")
        or "idle"
    ).strip().lower() or "idle"
    session_id = str(
        supervisor_lane.get("session_id")
        or active_executor.get("session_id")
        or ""
    ).strip()
    task_id = str(
        supervisor_lane.get("scene_task_id")
        or active_executor.get("scene_task_id")
        or focus_task.get("task_id")
        or ""
    ).strip()
    task_title = str(focus_task.get("title") or "").strip()
    execution_kind = str(
        focus_task.get("execution_kind")
        or focus_task.get("task_family")
        or focus_task.get("task_type")
        or supervisor_lane.get("execution_kind")
        or ""
    ).strip().lower()
    fg_count = max(
        0,
        int(
            supervisor_lane.get("subagent_foreground_count")
            or active_executor.get("subagent_foreground_count")
            or 0
        ),
    )
    bg_count = max(
        0,
        int(
            supervisor_lane.get("subagent_background_count")
            or active_executor.get("subagent_background_count")
            or 0
        ),
    )
    focus_tool = str(
        supervisor_lane.get("subagent_focus_tool")
        or active_executor.get("subagent_focus_tool")
        or ""
    ).strip()
    focus_preview = str(
        supervisor_lane.get("subagent_focus_preview")
        or active_executor.get("subagent_focus_preview")
        or ""
    ).strip()
    has_signal = any(
        [
            bool(supervisor_lane),
            bool(active_executor),
            bool(task_id),
            str(hint.get("status_label") or "").strip() != "API-B 判断中",
            str(hint.get("chain_reason") or "").strip(),
            str(hint.get("activity_text") or "").strip(),
        ]
    )

    if active_executor:
        idle_seconds = max(0, int(active_executor.get("idle_seconds") or 0))
        lease_status = str(active_executor.get("lease_status") or "healthy").strip().lower()
        if lease_status == "stale" or bool(active_executor.get("is_stale")):
            lease_summary = (
                f"执行面: 会话 {_session_tail(session_id)} 的 员工代理执行已陈旧"
                f"（静默 {idle_seconds}s）"
            )
        else:
            lease_summary = (
                f"执行面: 会话 {_session_tail(session_id)} 正在进行 员工代理执行"
                f"（静默 {idle_seconds}s）"
            )
    elif session_id and execution_scene != "idle":
        lease_status = "observed"
        lease_summary = (
            f"执行面: 已观测到会话 {_session_tail(session_id)} 正在 员工代理执行中活动"
        )
    else:
        lease_status = "idle"
        lease_summary = "执行面: 当前没有可见的 员工代理执行会话"

    return {
        "mode": "employee_execution_observer" if has_signal else "unavailable",
        "headline": "员工代理执行观察面",
        "summary": "这里只看 员工代理 的自主执行位，不展示用户链路。",
        "current_scene": execution_scene,
        "current_scene_label": SCENE_LABEL.get(execution_scene, execution_scene or "idle"),
        "status_label": str(hint.get("status_label") or "API-B 判断中").strip() or "API-B 判断中",
        "chain_reason": str(hint.get("chain_reason") or "").strip(),
        "activity_text": str(hint.get("activity_text") or "").strip(),
        "task_id": task_id,
        "task_title": task_title,
        "task_kind_label": _human_execution_kind(execution_kind),
        "session_id": session_id,
        "lease_status": lease_status,
        "presence_summary": lease_summary,
        "subagent_foreground_count": fg_count,
        "subagent_background_count": bg_count,
        "subagent_focus_tool": focus_tool,
        "subagent_focus_preview": focus_preview,
    }


def _build_supervisor_status_summary(
    state: Dict[str, Any],
    *,
    snapshot_source: str,
) -> Dict[str, Any]:
    scene = str(state.get("scene") or "idle").strip().lower() or "idle"
    observation = dict(state.get("autonomous_observation") or {})
    mode = dict(observation.get("mode") or {})
    read_model_version = observation.get("read_model_version")
    version_text = (
        str(int(read_model_version))
        if isinstance(read_model_version, (int, float))
        else str(read_model_version or "").strip()
    )
    return {
        "supervisor_online": bool(state),
        "scene": scene,
        "scene_label": SCENE_LABEL.get(scene, scene or "idle"),
        "title": str(state.get("title") or "自主链路观测").strip() or "自主链路观测",
        "snapshot_source": snapshot_source,
        "scope": str(mode.get("scope") or "api_b_autonomous_chain_only").strip()
        or "api_b_autonomous_chain_only",
        "status_text": str(mode.get("status_text") or "只读观测 API-B 与自主链路").strip()
        or "只读观测 API-B 与自主链路",
        "read_model_version": version_text,
    }


# ── Dashboard builder ──────────────────────────────────────────────────

def build_dashboard() -> Dict[str, Any]:
    """Collect all data and compute visibility metrics."""
    # ── Fetch data ──────────────────────────────────────────────────
    state = fetch_supervisor_state()
    gateway_scenes = fetch_gateway_scenes()
    gateway_status = fetch_gateway_status()
    observation = dict(state.get("autonomous_observation") or {})
    runtime = dict(observation.get("runtime") or {})
    user_signal = dict(runtime.get("user_chain_signal") or {})
    snapshot_source = str(runtime.get("snapshot_source") or "default")
    supervisor_status = _build_supervisor_status_summary(
        state,
        snapshot_source=snapshot_source,
    )
    chain_snapshot = _build_autonomous_chain_snapshot(state)
    recent_activity = _supervisor_recent_autonomous_activity(state)
    employee_observation = _build_employee_observation(state, gateway_scenes, gateway_status)
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

    # ── Autonomous chain board ──────────────────────────────────────
    chain_view: Dict[str, Any]
    if chain_snapshot:
        chain_view = {
            "mode": "autonomous_chain_board",
            "headline": chain_snapshot.get("headline") or "API-B 主视角自主闭环总览",
            "hero_summary": chain_snapshot.get("hero_summary")
            or "这里只看 Supervisor 给出的 API-B 自主闭环状态。",
            "summary": chain_snapshot.get("summary")
            or "API-B 主视角下的判断、执行回报、Mem 回流与再读取闭环。",
            "primary_focus": dict(chain_snapshot.get("primary_focus") or {}),
            "api_b_judgement": chain_snapshot.get("api_b_judgement", 0),
            "employee_running": chain_snapshot.get("employee_running", 0),
            "employee_dispatch": chain_snapshot.get("employee_dispatch", 0),
            "candidates": chain_snapshot.get("candidates", 0),
            "writebacks": chain_snapshot.get("writebacks", 0),
            "stage_cards": list(chain_snapshot.get("stage_cards") or []),
            "rail_entries": list(chain_snapshot.get("rail_entries") or []),
            "segments": list(chain_snapshot.get("segments") or []),
            "segments_headline": chain_snapshot.get("segments_headline") or "自主闭环分段观察",
        }
    else:
        chain_view = {
            "mode": "observation_unavailable",
            "headline": "还没有 API-B 闭环快照",
            "hero_summary": "还没有这一轮闭环总览。",
            "summary": "监督者还没给出可展示的闭环快照。",
            "primary_focus": {},
            "api_b_judgement": 0,
            "employee_running": 0,
            "employee_dispatch": 0,
            "candidates": 0,
            "writebacks": 0,
            "stage_cards": [],
            "rail_entries": [],
            "segments": [],
            "segments_headline": "自主闭环分段观察",
        }

    return {
        "now": datetime.now().isoformat(),
        "status": supervisor_status,
        "chain": chain_view,
        "employee_observation": employee_observation,
        "recent_activity": recent_activity,
        "observation_input": observation_input,
    }


def print_dashboard() -> None:
    """Print a rich terminal dashboard with autonomous-chain visibility."""
    db = build_dashboard()

    status = dict(db.get("status") or {})
    chain = db["chain"]
    employee_observation = db.get("employee_observation") or {}
    recent_activity = db.get("recent_activity") or {}
    observation_input = db.get("observation_input") or {}

    # ── Header ──────────────────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║         VoidCube Supervisor · API-B 观测板              ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    scene_label = str(status.get("scene_label") or "静置")[:10]
    snapshot_label = _human_snapshot_source(status.get("snapshot_source"))
    read_model_version = str(status.get("read_model_version") or "—")[:8]
    title = str(status.get("title") or "自主链路观测")[:20]
    print(
        f"  ║  监督者 {scene_label:<10s} 快照 {snapshot_label:<10s} 读模 v{read_model_version:<8s} ║"
    )
    print(f"  ║  {title:<54s}  ║")

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
            f"  ║  候选形成 {chain.get('candidates', 0)}  ·  API-B 判断在途 {chain.get('api_b_judgement', 0)}  ·  "
            f"执行中 {chain.get('employee_running', 0)}  ·  已转交 {chain.get('employee_dispatch', 0)}  ·  回流 {chain.get('writebacks', 0)}   ║"
        )
        print(
            f"  ║  {chain.get('hero_summary', chain.get('summary', 'API-B 主视角下的闭环观测'))[:54]:<54s}  ║"
        )
        focus = dict(chain.get("primary_focus") or {})
        if focus:
            focus_line = f"当前焦点 {focus.get('status', '等待中')} · {focus.get('title', '暂无')}"
            print(f"  ║  {focus_line[:54]:<54s}  ║")
        for item in list(chain.get("stage_cards") or [])[:4]:
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:12]
            print(f"  ║  ↻ {title:<28s} {status:<12s}                          ║")
        for item in list(chain.get("segments") or [])[:4]:
            group = str(item.get("label") or "?")[:12]
            title = str(item.get("title") or "?")[:28]
            status = str(item.get("status") or "?")[:10]
            print(f"  ║  • {group:<12s} {title:<28s} {status:<10s}              ║")
    else:
        print(
            f"  ║  {chain.get('headline', '还没有 API-B 闭环快照')[:46]:<46s}          ║"
        )
        print(
            f"  ║  {chain.get('summary', '监督者还没给出可展示的闭环快照。')[:54]:<54s}  ║"
        )
    if recent_activity:
        phase = str(recent_activity.get("phase_label") or "最近动作")[:10]
        title = str(recent_activity.get("title") or "最近暂无自主闭环动作")[:30]
        display_at = str(recent_activity.get("display_at") or "?")[:8]
        summary = str(recent_activity.get("summary") or "")[:44]
        print(f"  ║  最近自主动作 {phase:<10s} {title:<30s} {display_at:>8s}    ║")
        print(f"  ║  {summary:<54s}  ║")

    # ── 员工代理 execution observer ────────────────────────────────────
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  员工代理执行观察面                                   ║")
    execution_scene = str(employee_observation.get("current_scene_label") or "静置")[:10]
    status_label = str(employee_observation.get("status_label") or "API-B 判断中")[:18]
    fg_count = max(0, int(employee_observation.get("subagent_foreground_count") or 0))
    bg_count = max(0, int(employee_observation.get("subagent_background_count") or 0))
    sa_counts = f"{fg_count}+{bg_count}" if bg_count else str(fg_count)
    print(f"  ║    当前场景 {execution_scene:<10s} · 状态 {status_label:<18s} · SA {sa_counts:<7s} ║")
    print(f"  ║    会话 {_session_tail(employee_observation.get('session_id')):<10s}  这里只看 员工代理执行       ║")
    if str(employee_observation.get("task_id") or "").strip():
        task_kind = str(employee_observation.get("task_kind_label") or "自主链路项")[:8]
        task_id = str(employee_observation.get("task_id") or "")[:8]
        task_title = str(employee_observation.get("task_title") or "未命名")[:30]
        print(f"  ║    链路项 {task_kind:<8s} {task_id:<8s} {task_title:<30s} ║")
    focus_hint = str(
        employee_observation.get("subagent_focus_tool")
        or employee_observation.get("subagent_focus_preview")
        or ""
    ).strip()
    if focus_hint:
        print(f"  ║    聚焦 {focus_hint[:48]:<48s}  ║")
    print(f"  ║    {str(employee_observation.get('presence_summary') or '执行面: 当前没有可见的 员工代理执行会话')[:50]:<50s}    ║")
    chain_reason = str(employee_observation.get("chain_reason") or "").strip()
    if chain_reason:
        print(f"  ║    {chain_reason[:50]:<50s}    ║")
    activity_text = str(employee_observation.get("activity_text") or "").strip()
    if activity_text:
        print(f"  ║    {activity_text[:50]:<50s}    ║")

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

    # ── Footer ──────────────────────────────────────────────────────
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    print()


def watch_dashboard(interval: float = 3.0) -> None:
    """Print the dashboard in a loop, refreshing every *interval* seconds.

    Watch mode runs until the command process ends.
    """
    import os
    import sys

    # ANSI clear-screen sequence
    clear = "\033[2J\033[H"

    while True:
        # Clear and re-print
        sys.stdout.write(clear)
        sys.stdout.flush()

        # Timestamp
        from datetime import datetime
        print(f"  刷新时间 {datetime.now().strftime('%H:%M:%S')}")
        print()

        try:
            print_dashboard()
        except Exception as exc:
            print(f"  ⚠ Dashboard 错误: {exc}")
            print()

        time.sleep(interval)
