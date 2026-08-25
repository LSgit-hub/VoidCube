"""探针：hook 渲染线程的 fragments 输出 + Window 实际 wp，定位 50 列。"""
import runpy
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.containers import FloatContainer, Window

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import approval_choices, approval_display_fragments

G = {}

orig_draw = FloatContainer._draw_float


def hooked_draw(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index):
    r = orig_draw(self, fl, screen, mouse_handlers, write_position, style, erase_bg, z_index)
    if fl.z_index == 100 and G.get("ports") is not None:
        # 直接从 prompt_toolkit Screen 数据缓冲找 ╮ 像素位置
        for y in range(0, 14):
            col = screen.data_buffer.get(y, {})
            if not col:
                continue
            chars = "".join(cell.char for x, cell in sorted(col.items()))
            idx = chars.find("╮")
            if idx >= 0:
                print(f"[ptk-screen] y={y} ╮col={idx} 该行内容头={chars[:12]!r}")
        frags = G["ports"].approval_fragments()
        text = "".join(t for _, t in frags)
        widths = sorted(set(len(l) for l in text.splitlines()))[-3:]
        print(f"[float] 渲染线程 fragments 行宽(最大3个)={widths}  行数={len(text.splitlines())}")
    return r


FloatContainer._draw_float = hooked_draw

orig_win = Window.write_to_screen


def hooked_win(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
    # 只打印 modal float 区域的窗口（y>=1 且带边框内容的）
    if write_position.ypos >= 1 and write_position.width < 80 and z_index == 100:
        print(f"[win] y={write_position.ypos} x={write_position.xpos} w={write_position.width} h={write_position.height} z={z_index}")
    return orig_win(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)


Window.write_to_screen = hooked_win

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
        G["ports"] = ports
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
