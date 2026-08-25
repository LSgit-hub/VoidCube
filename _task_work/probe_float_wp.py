"""探针：hook FloatContainer._draw_float 打印 float 绘制时的真实 WritePosition。"""
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

orig_draw = FloatContainer._draw_float


def hooked_draw(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index):
    if fl.z_index == 100:
        print(f"[float] wp={write_position}  wp.width={write_position.width}  "
              f"fl.left={fl.left} fl.right={fl.right} fl.top={fl.top} fl.bottom={fl.bottom}")
        pw = fl.content.preferred_width(write_position.width)
        ph = fl.content.preferred_height(write_position.width, write_position.height)
        print(f"[float] content.preferred_width={pw.preferred}  preferred_height={ph.preferred}")
        print(f"[float] get_app size={__import__('prompt_toolkit').application.get_app().output.get_size()}")
    return orig_draw(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index)


FloatContainer._draw_float = hooked_draw

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
        print("渲染 ╮col =", rows[1].find("╮") if len(rows) > 1 else -1)

        pipe.send_text("\x11")
        thread.join(timeout=5)
        print("线程存活:", thread.is_alive())
