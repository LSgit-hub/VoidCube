from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict

from systems.supervisor.autonomous_chain_contract import AUTONOMOUS_CHAIN_CYCLE_ROUTE
from VoidCube_cli.autonomous_events import append_autonomous_execution_event
from VoidCube_cli.autonomous_executor import (
    autonomous_task_execution_kind,
    autonomous_task_label,
)
from VoidCube_cli.autonomous_presence import ensure_autonomous_executor_session
from VoidCube_cli.autonomous_status_host import (
    preview_supervisor_status_lines,
)

logger = logging.getLogger(__name__)


def _enter_autonomous_gate_locally(
    host: Any,
    *,
    refresh_gateway_cli_presence_callback: Any,
) -> None:
    host._autonomous_gate_active = True
    ensure_autonomous_executor_session(host, logger_debug=logger.debug)
    append_autonomous_execution_event(
        host,
        "自主链路已激活，API-A 自主执行面等待任务",
        tone="success",
        stage="autonomous_gate",
    )
    refresh_gateway_cli_presence_callback(force=True)


def _exit_autonomous_gate_locally(
    host: Any,
    *,
    push_cli_agent_scene_callback: Any,
    interrupt_current_task_callback: Any,
    interrupt_reason: str = "",
    interrupt_source: str = "",
    interrupt_timeout: float = 5,
    event_message: str = "",
    event_tone: str = "warn",
) -> None:
    if interrupt_reason and interrupt_source:
        interrupt_current_task_callback(
            reason=interrupt_reason,
            source=interrupt_source,
            timeout=interrupt_timeout,
        )
    host._autonomous_gate_active = False
    push_cli_agent_scene_callback(
        "idle",
        session_id=getattr(host, "session_id", None),
        agent_role="supervisor_task",
    )
    if event_message:
        append_autonomous_execution_event(host, event_message, tone=event_tone)


def _resolve_supervisor_url() -> str:
    try:
        from VoidCube_cli.config import load_config

        cfg = load_config()
        sv_cfg = cfg.get("supervisor", {}) if isinstance(cfg, dict) else {}
        host = sv_cfg.get("host", "127.0.0.1")
        port = sv_cfg.get("port", 6002)
        return f"http://{host}:{port}"
    except Exception:
        return "http://127.0.0.1:6002"


def trigger_autonomous_cycle(*, focus: str = "") -> Dict[str, Any] | None:
    supervisor_url = _resolve_supervisor_url()
    payload = json.dumps({"focus": focus}).encode()
    request = urllib.request.Request(
        f"{supervisor_url}{AUTONOMOUS_CHAIN_CYCLE_ROUTE}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def handle_auto_command(
    host: Any,
    cmd: str,
    *,
    cprint: Any,
    poll_autonomous_workflow_callback: Any,
    refresh_gateway_cli_presence_callback: Any,
    thread_factory: Any,
) -> None:
    parts = cmd.strip().split(maxsplit=1)
    focus = parts[1].strip() if len(parts) > 1 else ""

    cprint("  🧠 正在激活自主链路...")
    if focus:
        cprint(f"     聚焦: {focus}")

    def _ensure_supervisor_runtime() -> bool:
        try:
            from VoidCube_cli.ops.serve import ensure_running
        except ImportError:
            return False
        try:
            result = ensure_running(silent=False)
        except Exception:
            return False
        supervisor_state = dict(result.get("supervisor") or {})
        return bool(supervisor_state.get("healthy") or supervisor_state.get("running"))

    def _call_activate_autonomous_chain_gate():
        supervisor_url = _resolve_supervisor_url()
        try:
            payload = json.dumps({"focus": focus}).encode()

            def _activate_once():
                request = urllib.request.Request(
                    f"{supervisor_url}/autonomous-chain-gate/activate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                return json.loads(urllib.request.urlopen(request, timeout=30).read())

            try:
                resp = _activate_once()
            except Exception as first_exc:
                cprint("     Supervisor unavailable, attempting daemon recovery...")
                if _ensure_supervisor_runtime():
                    resp = _activate_once()
                else:
                    raise first_exc
        except Exception as exc:
            host._autonomous_gate_active = False
            cprint(f"  ⚠️  Supervisor unreachable: {exc}")
            cprint("     Ensure daemons are running (auto-started in interactive mode).")
            cprint("     Or run: voidcube serve start")
            return

        try:
            active = resp.get("autonomous_chain_gate_active", False)
            if active:
                _enter_autonomous_gate_locally(
                    host,
                    refresh_gateway_cli_presence_callback=refresh_gateway_cli_presence_callback,
                )
                cycle_result = None
                try:
                    cycle_result = trigger_autonomous_cycle(focus=focus)
                except Exception as exc:
                    cprint(f"     ⚠️  Initial autonomous cycle failed: {exc}")
                try:
                    poll_autonomous_workflow_callback()
                except Exception:
                    pass
                cprint("  ✅ 自主链路 [bold green]已激活[/]")
                cprint(f"     内生驱动循环: {'运行中' if resp.get('drive_loop_running') else '未运行'}")
                cprint(f"     治理复核循环: {'运行中' if resp.get('review_loop_running') else '未运行'}")
                if not resp.get("endogenous_drive_enabled", True):
                    cprint("     ⚠️  endogenous_drive_enabled=False，内生驱动循环未启用")
                if isinstance(cycle_result, dict):
                    summary = cycle_result.get("summary", {}) if isinstance(cycle_result, dict) else {}
                    planned = summary.get("planned", 0)
                    handed_off = summary.get("handed_off", 0)
                    cprint(f"     首轮循环: planned={planned}, handed_off={handed_off}")
                supervisor_preview = preview_supervisor_status_lines(host, limit=4)
                if supervisor_preview:
                    for line in supervisor_preview:
                        cprint(f"     {line}")
                else:
                    cprint("     监督者快照将在后台刷新后进入观测面。")
                cprint("     前台主 CLI 交互仍保持可用。")
                cprint("     使用 /auto-q 停止自主链路。")
                cprint(f"     监视地址: {supervisor_url}/ui")
            else:
                cprint("  ⚠️  自主链路激活失败。")
                if not resp.get("endogenous_drive_enabled", True):
                    cprint("     配置中的 endogenous_drive_enabled 为 False。")
        except Exception:
            pass

    thread_factory(
        target=_call_activate_autonomous_chain_gate,
        daemon=True,
        name="autonomous-chain-gate-activate",
    ).start()


def handle_auto_q_command(
    host: Any,
    *,
    cprint: Any,
    interrupt_current_task_callback: Any,
    push_cli_agent_scene_callback: Any,
    thread_factory: Any,
) -> None:
    cprint("  🔄 正在停止自主链路...")

    def _call_deactivate_autonomous_chain_gate():
        supervisor_url = _resolve_supervisor_url()
        try:
            request = urllib.request.Request(
                f"{supervisor_url}/autonomous-chain-gate/deactivate",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = json.loads(urllib.request.urlopen(request, timeout=30).read())
        except Exception as exc:
            cprint(f"  ⚠️  Supervisor unreachable: {exc}")
            return

        active = resp.get("autonomous_chain_gate_active", True)
        if not active:
            _exit_autonomous_gate_locally(
                host,
                push_cli_agent_scene_callback=push_cli_agent_scene_callback,
                interrupt_current_task_callback=interrupt_current_task_callback,
                interrupt_reason="自主链路已由 /auto-q 退出；当前链路项被用户中断。",
                interrupt_source="auto_q",
                interrupt_timeout=5,
                event_message="/auto 已退出",
                event_tone="warn",
            )
            cprint("  💤 自主链路 [bold]已停止[/].")
            cprint("     基线健康检查循环仍会继续运行。")
            cprint("     使用 /auto 可重新进入自主链路。")
        else:
            cprint("  ⚠️  自主链路未能停止，当前仍处于激活状态。")

    thread_factory(
        target=_call_deactivate_autonomous_chain_gate,
        daemon=True,
        name="autonomous-chain-gate-deactivate",
    ).start()


def exit_autonomous_gate_fast(
    host: Any,
    *,
    cprint: Any,
    interrupt_current_task_callback: Any,
    push_cli_agent_scene_callback: Any,
) -> bool:
    if not host._autonomous_gate_active:
        return True

    cprint("  🔄 正在退出自主链路（fast path）...")
    if host._agent_running:
        try:
            host._interrupt_queue.put("__AUTONOMOUS_Q_EXIT__")
        except Exception:
            pass

    supervisor_url = _resolve_supervisor_url()
    try:
        request = urllib.request.Request(
            f"{supervisor_url}/autonomous-chain-gate/deactivate",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(request, timeout=10).read())
        if not resp.get("autonomous_chain_gate_active", True):
            _exit_autonomous_gate_locally(
                host,
                push_cli_agent_scene_callback=push_cli_agent_scene_callback,
                interrupt_current_task_callback=interrupt_current_task_callback,
                interrupt_reason="自主链路已通过 fast-path /auto-q 退出；当前链路项被用户中断。",
                interrupt_source="auto_q_fast_path",
                interrupt_timeout=5,
                event_message="/auto 已退出",
                event_tone="warn",
            )
            cprint("  💤 自主链路 [bold]已停止[/].")
            cprint("     基线健康检查循环仍会继续运行。")
            cprint("     使用 /auto 可重新进入自主链路。")
            try:
                host._record_supervisor_ui_activity_safe(
                    "autonomous_gate_exit",
                    scene="idle",
                    summary="自主链路已通过 fast-path /auto-q 退出",
                )
            except Exception:
                pass
            return True
        cprint("  ⚠️  自主链路未能停止，当前仍处于激活状态。")
        return False
    except Exception as exc:
        _exit_autonomous_gate_locally(
            host,
            push_cli_agent_scene_callback=push_cli_agent_scene_callback,
            interrupt_current_task_callback=interrupt_current_task_callback,
            interrupt_reason="自主链路已在本地退出；supervisor 不可达，当前链路项被用户中断。",
            interrupt_source="auto_q_local_exit",
            interrupt_timeout=5,
            event_message="/auto 本地已退出，但 supervisor 可能仍保持激活",
            event_tone="warn",
        )
        cprint(f"  ⚠️  Supervisor unreachable: {exc}")
        cprint("     本地自主链路状态已关闭（supervisor 侧状态可能仍陈旧）。")
        cprint("     等 supervisor 可用后，可再次执行 /auto 重新进入。")
        return True


def force_quit_autonomous_gate(
    host: Any,
    *,
    cprint: Any,
    interrupt_current_task_callback: Any,
    push_cli_agent_scene_callback: Any,
) -> bool:
    cprint("\n  🚨 强制退出自主链路 —— 正在尝试紧急清理...")

    if host._agent_running:
        try:
            host._interrupt_queue.put("__FORCE_QUIT__")
        except Exception:
            pass

    supervisor_url = _resolve_supervisor_url()
    try:
        request = urllib.request.Request(
            f"{supervisor_url}/autonomous-chain-gate/deactivate",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        json.loads(urllib.request.urlopen(request, timeout=5).read())
        cprint("  ✅ 自主链路已停止")
    except Exception as exc:
        cprint(f"  ⚠️  自主链路停用失败: {exc}")

    current = getattr(host, "_current_autonomous_task", None)
    if current is not None:
        task_id = str(current.get("task_id") or "")
        execution_kind = autonomous_task_execution_kind(current)
        task_label = autonomous_task_label(execution_kind)
        chain_item_label = "改进" if execution_kind == "body_improvement" else "学习"
        if interrupt_current_task_callback(
            reason=f"自主链路被强制退出；{chain_item_label}链路项被用户中断。",
            source="force_quit",
            timeout=5,
        ):
            cprint(f"  ✅ {task_label} {task_id[:8]}... 已标记为中断")
        else:
            cprint("  ⚠️  未能向 Gateway 回报链路项终态")

    try:
        session_id = getattr(host, "session_id", "")
        if session_id:
            gateway_base = "http://127.0.0.1:6000"
            request = urllib.request.Request(
                f"{gateway_base}/v1/sessions/{session_id}",
                method="DELETE",
            )
            urllib.request.urlopen(request, timeout=5)
            cprint("  ✅ Gateway session unregistered")
    except Exception:
        pass

    _exit_autonomous_gate_locally(
        host,
        push_cli_agent_scene_callback=push_cli_agent_scene_callback,
        interrupt_current_task_callback=interrupt_current_task_callback,
        event_message="",
    )
    cprint("  🛡️  强制退出完成 —— 自主链路已停止。")
    return True
