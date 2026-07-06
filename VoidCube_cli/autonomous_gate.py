from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict

from systems.supervisor.autonomous_chain_contract import AUTONOMOUS_CHAIN_CYCLE_ROUTE


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
    thread_factory: Any,
) -> None:
    parts = cmd.strip().split(maxsplit=1)
    focus = parts[1].strip() if len(parts) > 1 else ""

    cprint("  🧠 Activating autonomous chain...")
    if focus:
        cprint(f"     Focus: {focus}")

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
                host._autonomous_gate_active = True
                host._ensure_autonomous_executor_session()
                host._append_autonomous_execution_event(
                    "自主链路已激活，API-A 自主执行面等待任务",
                    tone="success",
                    stage="autonomous_gate",
                )
                host._refresh_gateway_cli_presence(force=True)
                cycle_result = None
                try:
                    cycle_result = host._trigger_autonomous_cycle(focus=focus)
                except Exception as exc:
                    cprint(f"     ⚠️  Initial autonomous cycle failed: {exc}")
                try:
                    host._poll_autonomous_workflow()
                except Exception:
                    pass
                cprint("  ✅ Autonomous chain [bold green]ACTIVE[/]")
                cprint(f"     Drive loop:  {'running' if resp.get('drive_loop_running') else 'stopped'}")
                cprint(f"     Review loop: {'running' if resp.get('review_loop_running') else 'stopped'}")
                if not resp.get("endogenous_drive_enabled", True):
                    cprint("     ⚠️  endogenous_drive_enabled=False in config — drive loop disabled")
                if isinstance(cycle_result, dict):
                    summary = cycle_result.get("summary", {}) if isinstance(cycle_result, dict) else {}
                    planned = summary.get("planned", 0)
                    dispatched = summary.get("dispatched", 0)
                    cprint(f"     Initial cycle: planned={planned}, dispatched={dispatched}")
                snapshot = host._fetch_supervisor_status_snapshot()
                if snapshot:
                    for line in host._format_supervisor_status_snapshot(snapshot)[:4]:
                        cprint(f"     {line}")
                cprint("     Foreground CLI interaction remains available.")
                cprint("     Use /auto-q to stop the autonomous chain.")
                cprint(f"     Monitor: {supervisor_url}/ui")
            else:
                cprint("  ⚠️  Autonomous chain activation failed.")
                if not resp.get("endogenous_drive_enabled", True):
                    cprint("     endogenous_drive_enabled is False in config.")
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
    push_cli_agent_scene: Any,
    thread_factory: Any,
) -> None:
    cprint("  🔄 Stopping autonomous chain...")

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
            host._interrupt_current_autonomous_task(
                reason="Autonomous chain exited by /auto-q; current task interrupted by user.",
                source="auto_q",
                timeout=5,
            )
            host._autonomous_gate_active = False
            push_cli_agent_scene(
                "idle",
                session_id=getattr(host, "session_id", None),
                agent_role="supervisor_task",
            )
            host._append_autonomous_execution_event("/auto 已退出", tone="warn")
            cprint("  💤 Autonomous chain [bold]STOPPED[/].")
            cprint("     The baseline health-check loop remains running.")
            cprint("     Use /auto to restart the autonomous chain.")
        else:
            cprint("  ⚠️  Autonomous chain could not be stopped (still active).")

    thread_factory(
        target=_call_deactivate_autonomous_chain_gate,
        daemon=True,
        name="autonomous-chain-gate-deactivate",
    ).start()


def exit_autonomous_gate_fast(
    host: Any,
    *,
    cprint: Any,
    push_cli_agent_scene: Any,
    record_supervisor_ui_activity_safe: Any,
) -> bool:
    if not host._autonomous_gate_active:
        return True

    cprint("  🔄 Exiting autonomous chain (fast path)...")
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
            host._interrupt_current_autonomous_task(
                reason="Autonomous chain exited via fast-path /auto-q; current task interrupted by user.",
                source="auto_q_fast_path",
                timeout=5,
            )
            host._autonomous_gate_active = False
            push_cli_agent_scene(
                "idle",
                session_id=getattr(host, "session_id", None),
                agent_role="supervisor_task",
            )
            host._append_autonomous_execution_event("/auto 已退出", tone="warn")
            cprint("  💤 Autonomous chain [bold]STOPPED[/].")
            cprint("     The baseline health-check loop remains running.")
            cprint("     Use /auto to restart the autonomous chain.")
            record_supervisor_ui_activity_safe(
                "autonomous_gate_exit",
                scene="idle",
                summary="Autonomous chain exited via fast-path /auto-q",
            )
            return True
        cprint("  ⚠️  Autonomous chain could not be stopped (still active).")
        return False
    except Exception as exc:
        host._interrupt_current_autonomous_task(
            reason="Autonomous chain exited locally while supervisor was unreachable; current task interrupted by user.",
            source="auto_q_local_exit",
            timeout=5,
        )
        host._autonomous_gate_active = False
        push_cli_agent_scene(
            "idle",
            session_id=getattr(host, "session_id", None),
            agent_role="supervisor_task",
        )
        host._append_autonomous_execution_event("/auto 本地已退出，但 supervisor 可能仍保持激活", tone="warn")
        cprint(f"  ⚠️  Supervisor unreachable: {exc}")
        cprint("     Local autonomous chain state deactivated (supervisor state may be stale).")
        cprint("     Run /auto to re-enter when supervisor is available.")
        return True


def force_quit_autonomous_gate(
    host: Any,
    *,
    cprint: Any,
    push_cli_agent_scene: Any,
) -> bool:
    cprint("\n  🚨 FORCE QUIT AUTONOMOUS CHAIN — attempting emergency cleanup...")

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
        cprint("  ✅ Autonomous chain stopped")
    except Exception as exc:
        cprint(f"  ⚠️  Autonomous chain deactivation failed: {exc}")

    current = getattr(host, "_current_autonomous_task", None)
    if current is not None:
        task_id = str(current.get("task_id") or "")
        execution_kind = host._autonomous_task_execution_kind(current)
        task_label = host._autonomous_task_label(execution_kind)
        if host._interrupt_current_autonomous_task(
            reason=f"Autonomous chain force-quit — {task_label} interrupted by user.",
            source="force_quit",
            timeout=5,
        ):
            cprint(f"  ✅ Autonomous {task_label} {task_id[:8]}... marked interrupted")
        else:
            cprint("  ⚠️  Could not report task completion to Gateway")

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

    host._autonomous_gate_active = False
    push_cli_agent_scene(
        "idle",
        session_id=getattr(host, "session_id", None),
        agent_role="supervisor_task",
    )
    cprint("  🛡️  Force quit complete — autonomous chain stopped.")
    return True
