"""探针：实测 modal_overlay 各层 preferred_width，定位 float 宽度被压到 40 的原因。"""
import runpy
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import approval_choices, approval_display_fragments
from voidcube.interfaces.cli.tui.host_assembly import CliTuiHostAssemblyPorts

ns = runpy.run_path("tests/test_tui_real_render.py")
build_ports = ns["build_ports"]

command = "sudo rm -rf /opt/backup/data/" + "x" * 120 + " --force --recursive --no-preserve-root"

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    holder: dict = {}
    events: list[str] = []
    from prompt_toolkit.input import create_pipe_input

    with create_pipe_input() as pipe:
        from prompt_toolkit.output.vt100 import Vt100_Output
        import io

        out = Vt100_Output(io.StringIO(newline=""), lambda: None, enable_cpr=False)
        ports = build_ports(pipe, out, holder, events, tmp)
        ports = replace(
            ports,
            approval_fragments=lambda: (
                approval_display_fragments(SimpleNamespace(_approval_state=holder.get("approval")))
                if holder.get("approval")
                else []
            ),
        )
        holder["approval"] = {
            "request": ApprovalRequest(command=command, description="此操作将永久删除文件，且不可恢复。"),
            "choices": approval_choices(command),
            "selected": 0,
        }

        # 直接看 fragments 文本行宽
        frags = ports.approval_fragments()
        text = "".join(t for _, t in frags)
        widths = [len(l) for l in text.splitlines()]
        print("approval fragments 行宽:", widths, "max=", max(widths))

        # 构建 host_assembly 的 modal 组件树，取 modal_overlay 结构
        # 简化：直接量 approval 独立 widget 的 preferred_width
        from prompt_toolkit.layout.containers import ConditionalContainer, Window, HSplit, Float, FloatContainer
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension

        w = Window(FormattedTextControl(ports.approval_fragments), wrap_lines=True)
        print("approval Window preferred_width(80) =", w.preferred_width(80).preferred)

        from prompt_toolkit.filters import Condition as PTCondition

        cc = ConditionalContainer(w, filter=PTCondition(lambda: True))
        print("ConditionalContainer preferred_width(80) =", cc.preferred_width(80).preferred)

        hs = HSplit([cc], height=Dimension(max=16))
        print("HSplit preferred_width(80) =", hs.preferred_width(80).preferred)
        print("HSplit preferred_height(80, 24) =", hs.preferred_height(80, 24).preferred)

        # float 尺寸走 _draw_float："Otherwise" 分支
        fl = Float(content=hs, top=1, left=2, right=2, bottom=1)
        print("Float 参数: top=1 left=2 right=2 bottom=1 width=", fl.width, "height=", fl.height)
