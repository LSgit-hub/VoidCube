"""验证状态栏 Window style 修复：内容不足满宽时右侧缺口是否带 #1a1a2e 背景。

修复前：Window 无 style -> 缺口透出终端默认背景（无背景转义码）
修复后：Window style="class:status-bar" -> 缺口带 bg:#1a1a2e 转义码
"""
import sys
from pathlib import Path

REPO = Path(r"F:/My_code/VScode_py/VoidCube")
sys.path.insert(0, str(REPO / "src"))

import io
import pyte
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit, FormattedTextControl, Window, Layout
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.styles import Style

from voidcube.interfaces.cli.tui.application import TUI_STYLE

COLS, ROWS = 80, 24


def render(with_window_style: bool) -> str:
    buf = io.StringIO()
    out = Vt100_Output(buf, lambda: Size(columns=COLS, rows=ROWS))
    pipe = create_pipe_input()
    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(
                        content=FormattedTextControl(
                            [("class:status-bar", "Git <main> 改动 3")],
                        ),
                        height=1,
                        wrap_lines=False,
                        style="class:status-bar" if with_window_style else "",
                    ),
                    Window(char=" ", height=ROWS - 1),
                ]
            )
        ),
        style=Style.from_dict(dict(TUI_STYLE)),
        input=pipe,
        output=out,
    )
    app._is_running = True
    app._is_initialized = True
    app.renderer.render(app, app.layout, app.output)
    data = buf.getvalue()
    return data


for with_style in (False, True):
    data = render(with_window_style=with_style)
    # #1a1a2e 在 256 色深度下编码为 48;5;234，常与前景色合并成一条 SGR（如 0;38;5;248;48;5;234）
    bg_marker = "48;5;234"
    first_line = data.split("\n", 1)[0] if "\n" in data else data
    tail = first_line[-60:]
    print(f"window_style={with_style!s:5} 首行含背景标记={bg_marker in first_line}")
    print(f"   第一行尾 60 字节: {tail!r}")
    if not with_style:
        # 修复前：git 文本自带背景，但随后立即 \x1b[0m 重置，缺口无背景
        assert "改动 3\x1b[0m" in first_line, "修复前文本后应立即重置（缺口无背景）"
    else:
        # 修复后：文本与填充空格共享同一背景段，重置被推迟到行尾（\x1b[79C 行尾移动后）
        assert bg_marker in first_line, "修复后第一行必须带背景"
        assert "改动 3\x1b[0m" not in first_line, (
            "修复后文本后不应立即重置：右侧缺口必须保持背景填充"
        )
print("OK: 修复后状态栏行尾带 #1a1a2e 背景，条形满宽")
