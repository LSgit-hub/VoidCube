"""探针：完整打印 approval 面板的 pyte 快照（带列号），分析 50 列面板结构。"""
import runpy
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import approval_choices, approval_display_fragments

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

        holder["approval"] = {
            "request": ApprovalRequest(command=command, description="此操作将永久删除文件，且不可恢复。"),
            "choices": approval_choices(command),
            "selected": 0,
        }
        ports.invalidate()
        time.sleep(0.8)

        rows = screen_snapshot(buf)
        print("=== 快照（0-13 行，带列号）===")
        for i, row in enumerate(rows[:14]):
            print(f"{i:2d}|{row}")
            # 标注边框字符列位
            marks = []
            for ch in ("╭", "╮", "╰", "╯", "│"):
                pos = 0
                while True:
                    pos = row.find(ch, pos)
                    if pos < 0:
                        break
                    marks.append(f"{ch}@{pos}")
                    pos += 1
            if marks:
                print(f"   边框位置: {', '.join(marks)}")

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
