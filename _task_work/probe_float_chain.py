"""探针：运行时测量 modal float 的 preferred_width 计算链，定位 50 列来源。"""
import runpy
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.containers import FloatContainer

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
        time.sleep(0.6)

        # 遍历 layout 容器树找 FloatContainer
        def find_float_containers(node, path=""):
            found = []
            if isinstance(node, FloatContainer):
                found.append((path, node))
            for attr in ("content",):
                child = getattr(node, attr, None)
                if child is not None and child is not node:
                    found += find_float_containers(child, path + f".{attr}")
            return found

        root = app.layout.container
        fcs = find_float_containers(root)
        print("FloatContainer 数量:", len(fcs))
        for path, fc in fcs:
            for i, fl in enumerate(fc.floats):
                pw = fl.content.preferred_width(80)
                ph = fl.content.preferred_height(80, 24)
                # 打印内容容器类型链
                types = []
                node = fl.content
                for _ in range(6):
                    types.append(type(node).__name__)
                    node = getattr(node, "content", None)
                    if node is None:
                        break
                print(f"float[{i}] {path}: content类型={' -> '.join(types)}")
                print(f"   preferred_width(80)={pw.preferred} min={pw.min}  "
                      f"preferred_height={ph.preferred}  left={fl.left} right={fl.right}")

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
