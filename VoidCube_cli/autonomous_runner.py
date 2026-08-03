from __future__ import annotations

import os
import time
from functools import partial
from typing import Any, Optional

from VoidCube_cli.autonomous_panel import has_visible_autonomous_work
from VoidCube_cli.cli_handlers import _git_head_commit, _git_improvement_diff


def _plain_cprint(message: str) -> None:
    print(str(message))


def _render_rows(host: Any) -> None:
    from VoidCube_cli.autonomous_panel import build_autonomous_execution_panel_rows

    for _style, text in build_autonomous_execution_panel_rows(
        host,
        state_ports=host._autonomous_panel_state_ports(),
        render_ports=host._autonomous_panel_render_ports(),
    ):
        print(f"  {text}")


def run_autonomous_component_debug(
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    interval: float = 2.0,
    once: bool = False,
    clear: bool = True,
    show_idle: bool = False,
) -> None:
    """Run a debug surface for the embedded API-A autonomous component.

    Normal operation uses /auto inside the main CLI. This command is retained
    only for diagnostics when the embedded component needs isolated inspection.
    """
    from VoidCube_cli.app import VoidcubeCLI
    from VoidCube_app.configuration import reload_application_config
    from VoidCube_app.gateway import (
        is_gateway_running,
        register_session,
    )
    from VoidCube_cli.autonomous_presence import push_cli_agent_scene
    from VoidCube_app.config import load_config

    reload_application_config(load_config)
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
        push_cli_agent_scene=push_cli_agent_scene,
        git_head_commit=_git_head_commit,
        git_improvement_diff=_git_improvement_diff,
        cprint=_plain_cprint,
    )

    has_rendered = False
    register_with_gateway = partial(register_session, source="cli")
    if show_idle:
        print("\n  VoidCube API-A 自主执行组件调试面")
        print("  正常使用请在主 CLI 内执行 /auto；此入口只用于隔离诊断。")
        print("  空态调试显示已开启。Ctrl+C 退出。\n")

    try:
        while True:
            refresh_supervisor_status(host)
            refresh_autonomous_gateway_status(host)
            refresh_gateway_autonomous_execute_snapshot(host)
            refresh_gateway_cli_presence(
                host,
                force=False,
                is_gateway_running=is_gateway_running,
                register_with_gateway=register_with_gateway,
                push_cli_agent_scene=push_cli_agent_scene,
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

            should_render = show_idle or has_visible_autonomous_work(
                host,
                state_ports=host._autonomous_panel_state_ports(),
            )
            if should_render:
                if clear and not once:
                    os.system("cls" if os.name == "nt" else "clear")
                if not has_rendered and not show_idle:
                    print("\n  VoidCube API-A 自主执行组件调试面")
                    print("  正常路径是主 CLI 内嵌组件；此处只显示隔离诊断信息。")
                    print("  Ctrl+C 退出。\n")
                print(f"  刷新时间 {time.strftime('%H:%M:%S')}  （Ctrl+C 退出）\n")
                _render_rows(host)
                print()
                has_rendered = True

            if once:
                break
            time.sleep(max(0.5, float(interval or 2.0)))
    except KeyboardInterrupt:
        print("\n  自主执行组件调试面已退出。\n")
    finally:
        try:
            push_cli_agent_scene(
                "idle",
                session_id=getattr(host, "session_id", None),
                agent_role="supervisor_task",
            )
        except Exception:
            pass
