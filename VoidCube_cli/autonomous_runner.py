from __future__ import annotations

import os
import time
from typing import Any, Optional


def _plain_cprint(message: str) -> None:
    print(str(message))


def _render_rows(host: Any) -> None:
    from VoidCube_cli.autonomous_panel import build_autonomous_execution_panel_rows

    for _style, text in build_autonomous_execution_panel_rows(host):
        print(f"  {text}")


def run_autonomous_minicli(
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    interval: float = 2.0,
    once: bool = False,
    clear: bool = True,
) -> None:
    """Run the dedicated API-A autonomous execution surface.

    This is intentionally separate from the main CLI. It owns autonomous task
    polling, claim, execution, writeback, and the compact observation panel.
    """
    import cli as cli_module

    cli_module.CLI_CONFIG = cli_module._get_cli_config()
    VoidcubeCLI = cli_module.VoidcubeCLI
    from VoidCube_cli.autonomous_presence import (
        ensure_supervisor_task_session,
        refresh_gateway_cli_presence,
    )
    from VoidCube_cli.autonomous_runtime_host import autonomous_executor_runtime
    from VoidCube_cli.autonomous_status_host import (
        refresh_autonomous_gateway_status,
        refresh_gateway_autonomous_execute_snapshot,
        refresh_supervisor_status,
    )

    host = VoidcubeCLI(model=model, provider=provider, compact=True)
    host._autonomous_gate_active = True
    ensure_supervisor_task_session(host, logger_debug=lambda *args, **kwargs: None)

    runtime = autonomous_executor_runtime(
        host,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        git_head_commit=cli_module._git_head_commit,
        git_improvement_diff=cli_module._git_improvement_diff,
        cprint=_plain_cprint,
    )

    print("\n  VoidCube API-A 自主执行最小 CLI")
    print("  只处理自主链路的 API-A 执行位；主 CLI 可继续用于用户对话。")
    print("  Ctrl+C 退出观察窗口。\n")

    try:
        while True:
            refresh_supervisor_status(host)
            refresh_autonomous_gateway_status(host)
            refresh_gateway_autonomous_execute_snapshot(host)
            refresh_gateway_cli_presence(
                host,
                force=False,
                is_gateway_running=cli_module._is_gateway_running,
                register_with_gateway=cli_module._register_with_gateway,
                push_cli_agent_scene=cli_module._push_cli_agent_scene,
                monotonic_time=time.monotonic,
            )

            if not getattr(host, "_agent_running", False):
                runtime.poll_workflow()
                try:
                    pending = host._pending_input.get_nowait()
                except Exception:
                    pending = None
                if pending:
                    host._execute_pending_input(pending, app=None)
                    runtime.poll_workflow()

            if clear and not once:
                os.system("cls" if os.name == "nt" else "clear")
            print(f"  刷新时间 {time.strftime('%H:%M:%S')}  （Ctrl+C 退出）\n")
            _render_rows(host)
            print()

            if once:
                break
            time.sleep(max(0.5, float(interval or 2.0)))
    except KeyboardInterrupt:
        print("\n  自主执行最小 CLI 已退出。\n")
    finally:
        try:
            cli_module._push_cli_agent_scene(
                "idle",
                session_id=getattr(host, "session_id", None),
                agent_role="supervisor_task",
            )
        except Exception:
            pass
