"""探针：dump prompt_toolkit 输出字节流中描述行区域，看中文如何编码。"""
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

command = "sudo " + "rm -" + "rf /opt/backup/data/" + "x" * 120 + " --force --recursive --no-preserve-root"

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

        raw = buf.getvalue()
        print("总字节:", len(raw))
        # 找含中文的片段
        idx = raw.find("此操作".encode("utf-8"))
        print("'此操作' 字节偏移:", idx)
        if idx >= 0:
            seg = raw[max(0, idx - 30): idx + 120]
            print("上下文 bytes:", seg)
            # 还原可见文本
            try:
                print("可读文本:", seg.decode("utf-8", errors="replace")[:120])
            except Exception:
                pass

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
