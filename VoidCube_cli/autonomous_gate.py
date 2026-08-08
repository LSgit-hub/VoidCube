from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, Tuple

from systems.supervisor.autonomous_chain_contract import AUTONOMOUS_CHAIN_CYCLE_ROUTE
from VoidCube_cli.autonomous_events import (
    AutonomousPanelEventPorts,
    append_autonomous_execution_event,
)
from VoidCube_cli.autonomous_presence import ensure_supervisor_task_session
from VoidCube_cli.autonomous_status_host import (
    preview_supervisor_status_lines,
)

logger = logging.getLogger(__name__)


def _enter_autonomous_gate_locally(
    host: Any,
    *,
    event_ports: AutonomousPanelEventPorts,
    refresh_gateway_cli_presence_callback: Any,
) -> None:
    host._autonomous_gate_active = True
    scheduler_runtime = getattr(host, "_turn_scheduler_runtime", None)
    if scheduler_runtime is not None:
        scheduler_runtime.enable_autonomous()
    append_autonomous_execution_event(
        event_ports=event_ports,
        message="自主链路已激活，API-A 自主执行面等待任务",
        tone="success",
        stage="autonomous_gate",
    )
    refresh_gateway_cli_presence_callback(force=True)


def _exit_autonomous_gate_locally(
    host: Any,
    *,
    event_ports: AutonomousPanelEventPorts,
    push_cli_agent_scene_callback: Any,
    interrupt_current_task_callback: Any,
    interrupt_reason: str = "",
    interrupt_source: str = "",
    interrupt_timeout: float = 5,
    event_message: str = "",
    event_tone: str = "warn",
) -> None:
    execution_host = getattr(host, "_autonomous_execution_host", None) or host
    if interrupt_reason and interrupt_source:
        interrupt_current_task_callback(
            reason=interrupt_reason,
            source=interrupt_source,
            timeout=interrupt_timeout,
        )
    host._autonomous_gate_active = False
    scheduler_runtime = getattr(host, "_turn_scheduler_runtime", None)
    if scheduler_runtime is not None:
        scheduler_runtime.cancel_autonomous()
    push_cli_agent_scene_callback(
        "idle",
        session_id=getattr(execution_host, "session_id", None),
        agent_role="supervisor_task",
    )
    if event_message:
        append_autonomous_execution_event(
            event_ports=event_ports,
            message=event_message,
            tone=event_tone,
        )
    stopper = getattr(host, "_stop_autonomous_execution", None)
    if callable(stopper):
        stopper(interrupt=False)


def _resolve_supervisor_url() -> str:
    try:
        from VoidCube_app.config import load_config

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


def activate_autonomous_execution(host: Any) -> Tuple[bool, str]:
    """Activate the API-A autonomous execution loop."""
    starter = getattr(host, "_start_autonomous_execution", None)
    if callable(starter):
        try:
            started = bool(starter())
        except Exception as exc:
            logger.warning("Failed to start autonomous execution component: %s", exc)
            return False, f"API-A 自主执行组件启动失败: {exc}"
        if not started:
            return False, "API-A 自主执行组件未启动。"
    return True, "API-A 自主执行组件已在当前 CLI 中启动；有链路项执行时会自动显示。"


def handle_auto_command(
    host: Any,
    cmd: str,
    *,
    event_ports: AutonomousPanelEventPorts,
    cprint: Any,
    refresh_gateway_cli_presence_callback: Any,
    thread_factory: Any,
) -> None:
    parts = cmd.strip().split(maxsplit=1)
    focus = parts[1].strip() if len(parts) > 1 else ""

    cprint("  🧠 正在启用自主链路...")
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
                    event_ports=event_ports,
                    refresh_gateway_cli_presence_callback=refresh_gateway_cli_presence_callback,
                )
                cycle_result = None
                try:
                    cycle_result = trigger_autonomous_cycle(focus=focus)
                except Exception as exc:
                    cprint(f"     ⚠️  Initial autonomous cycle failed: {exc}")
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
                launched, launch_message = activate_autonomous_execution(host)
                cprint(f"     {launch_message}")
                if not launched:
                    cprint("     自主执行组件未就绪，暂不会认领自主链路任务。")
                cprint("     使用 /auto-q 临时停用自主链路。")
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
    event_ports: AutonomousPanelEventPorts,
    cprint: Any,
    interrupt_current_task_callback: Any,
    push_cli_agent_scene_callback: Any,
    thread_factory: Any,
) -> None:
    cprint("  🔄 正在停用自主链路...")

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
                event_ports=event_ports,
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
            cprint("     使用 /auto 可重新启用自主链路。")
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
    event_ports: AutonomousPanelEventPorts,
    cprint: Any,
    interrupt_current_task_callback: Any,
    push_cli_agent_scene_callback: Any,
) -> bool:
    if not host._autonomous_gate_active:
        return True

    cprint("  🔄 正在停用自主链路（fast path）...")

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
                event_ports=event_ports,
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
            cprint("     使用 /auto 可重新启用自主链路。")
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
            event_ports=event_ports,
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
