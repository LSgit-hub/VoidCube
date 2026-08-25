"""探针：真实 approval_display_fragments 接入 harness，复现高危命令提示显示不全。"""
import runpy
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import (
    approval_choices,
    approval_display_fragments,
)

ns = runpy.run_path("tests/test_tui_real_render.py")
make_output = ns["make_output"]
build_ports = ns["build_ports"]
build_application = ns["build_application"]
screen_snapshot = ns["screen_snapshot"]

command = "sudo rm -rf /opt/backup/data/" + "x" * 120 + " --force --recursive --no-preserve-root"

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    out, buf = make_output()
    holder: dict = {}
    events: list[str] = []
    with create_pipe_input() as pipe:
        ports = build_ports(pipe, out, holder, events, tmp)
        ports = replace(
            ports,
            approval_fragments=lambda: (
                approval_display_fragments(SimpleNamespace(_approval_state=holder.get("approval")))
                if holder.get("approval")
                else []
            ),
        )
        app = build_application(ports, pipe, out, holder)
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        time.sleep(1.0)

        # 1) 80x24 + 长命令（默认截断）
        holder["approval"] = {
            "request": ApprovalRequest(command=command, description="此操作将永久删除文件，且不可恢复。"),
            "choices": approval_choices(command),
            "selected": 0,
        }
        ports.invalidate()
        time.sleep(0.8)
        print("=== 80x24 默认（长命令）===")
        for i, row in enumerate(screen_snapshot(buf)):
            print(f"{i:2d}|{row}")

        # 2) 选 view 展开
        holder["approval"]["show_full"] = True
        holder["approval"]["choices"] = [c for c in holder["approval"]["choices"] if c != "view"]
        ports.invalidate()
        time.sleep(0.8)
        print("=== 80x24 show_full ===")
        for i, row in enumerate(screen_snapshot(buf)):
            print(f"{i:2d}|{row}")

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
