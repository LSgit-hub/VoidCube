"""探针：决定性实验——注入 approval 后连续多次快照，确认面板宽度是否延迟到位。"""
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

        for tick in range(6):
            time.sleep(0.3)
            rows = screen_snapshot(buf)
            # 找 ╮ 列位（标题行边框右端）
            col = rows[1].find("╮") if len(rows) > 1 else -1
            frag_text = "".join(t for _, t in ports.approval_fragments())
            frag_w = max(len(l) for l in frag_text.splitlines())
            print(f"t+{(tick+1)*0.3:.1f}s  ╮col={col}  fragments宽={frag_w}  "
                  f"行数={sum(1 for r in rows if r.strip())}")

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
