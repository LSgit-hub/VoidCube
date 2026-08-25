"""Q2 验证：真实危险命令 approval 面板在完整 TUI 装配链下是否显示完整。

之前测试只用 mock 单行 approval_fragments；这里注入真实
approval_display_fragments（interaction_adapter），用 pyte 渲染后断言
面板标题、描述、命令、选项、边框全部出现在快照中，且面板行数充足
（回归"显示不全"问题——即 float 高度退化导致面板截断成小白框）。
"""
import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pyte
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.styles import Style

from voidcube.interfaces.cli.interaction_adapter import approval_display_fragments
from voidcube.interfaces.cli.tui.application import TUI_STYLE
from voidcube.interfaces.cli.tui.composition import CliInteractiveTuiPorts
from voidcube.interfaces.cli.tui.runtime_factory import build_cli_interactive_tui

COLS, ROWS = 80, 24


def make_approval_host(command: str, description: str):
    """构造带 _approval_state 的宿主桩，供 approval_display_fragments 读取。"""
    host = SimpleNamespace()
    host._approval_state = {
        "request": SimpleNamespace(command=command, description=description),
        "choices": ["approve", "deny", "view"],
        "selected": 0,
        "show_full": False,
    }
    return host


def build_app(host, tmp: Path):
    """复用真实装配链，但把 approval_fragments 接到真实面板生成器。"""
    buf = io.StringIO()
    output = Vt100_Output(
        buf, lambda: Size(columns=COLS, rows=ROWS), default_color_depth=None
    )
    pipe = create_pipe_input()

    holder = {"app": None, "approval": None, "show_full": False}

    def invalidate():
        app = holder.get("app")
        if app is not None:
            app.invalidate()

    def approval_state():
        return holder.get("approval")

    dynamic_text = __import__(
        "voidcube.interfaces.cli.tui.dynamic_text_runtime", fromlist=["TuiDynamicTextRuntime"]
    ).TuiDynamicTextRuntime(
        SimpleNamespace(
            voice_recording=lambda: False,
            voice_processing=lambda: False,
            sudo_active=lambda: False,
            secret_active=lambda: False,
            approval_active=lambda: holder.get("approval") is not None,
            clarify_freetext=lambda: False,
            clarify_active=lambda: False,
            command_running=lambda: False,
            command_spinner_frame=lambda: "⠋",
            command_status=lambda: "",
            agent_running=lambda: False,
            voice_mode=lambda: False,
            spinner_text=lambda: "",
            tool_start_time=lambda: 0.0,
            now=lambda: time.time(),
            agent_spacer_height=lambda: 0,
            spinner_height=lambda: 0,
            sudo_deadline=lambda: 0.0,
            secret_deadline=lambda: 0.0,
            approval_deadline=lambda: 0.0,
            clarify_deadline=lambda: 0.0,
            translate=lambda key, **_kw: f"[{key}]",
        )
    )

    ports = CliInteractiveTuiPorts(
        registrations=SimpleNamespace(
            enter=SimpleNamespace(handle=lambda _e: None),
            voice=SimpleNamespace(handle=lambda _e: None),
            suspend=SimpleNamespace(handle=lambda _e: None),
            dynamic_text=dynamic_text,
            voice_key="c-b",
        ),
        prompt_runtime=SimpleNamespace(
            fragments=lambda: [("class:prompt", "> ")], text=lambda: "> "
        ),
        layout_metrics=SimpleNamespace(input_rule_height=lambda _text: 1),
        state=SimpleNamespace(
            clarify_state=lambda: None,
            clarify_freetext_active=lambda: False,
            sudo_state=lambda: None,
            secret_state=lambda: None,
            approval_state=approval_state,
            model_picker_state=lambda: None,
            update_selection=lambda mutate: mutate(),
        ),
        attached_images=lambda: [],
        image_counter=lambda: 0,
        format_image_badges=lambda _images: [],
        voice_fragments=lambda: [("class:voice-status", "正在聆听…")],
        voice_visible=lambda: False,
        autonomous_fragments=lambda: [],
        autonomous_visible=lambda: False,
        status_fragments=lambda: [("class:status-bar", "就绪")],
        status_visible=lambda: True,
        should_attach_clipboard_image=lambda _text: False,
        attach_clipboard_image=lambda: False,
        paste_directory=tmp,
        timestamp=lambda: "000000",
        invalidate_event=lambda _event: None,
        invalidate=invalidate,
        history_path=str(tmp / "history.txt"),
        command_available=lambda _command: True,
        command_running=lambda: False,
        approval_fragments=lambda: approval_display_fragments(host),
        register_extra_keybindings=lambda key_bindings, input_area=None: None,
        cursor=None,
        store_application=lambda app: holder.update(app=app),
        install_resize_cleanup=lambda _application: None,
        extra_widgets=lambda: [],
    )

    app = build_cli_interactive_tui(ports, input=pipe, output=output)
    holder["app"] = app
    return app, buf, holder


def render(app, buf, holder):
    app._is_running = True
    app._is_initialized = True
    app.renderer.render(app, app.layout, app.output)
    data = buf.getvalue()

    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    stream.feed(data.encode("utf-8"))
    return "\n".join(screen.display)


def wait_until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def main():
    tmp = Path("_task_work/q2_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    host = make_approval_host(
        command="rm -rf /some/very/long/path/to/nowhere --force --recursive",
        description="此操作会永久删除文件，且不可恢复。请确认是否继续执行该危险命令。",
    )
    app, buf, holder = build_app(host, tmp)

    # 打开 approval 状态（真实路由会经 interaction_adapter 设置，这里直接注入）
    holder["approval"] = host._approval_state
    app.invalidate()
    time.sleep(0.3)

    text = render(app, buf, holder)
    print("===== 快照 =====")
    print(text)
    print("===== 结束 =====")

    checks = {
        "标题 [!] Dangerous Command": "[!] Dangerous Command" in text,
        "描述行": "永久删除" in text,
        "命令行": "rm -rf" in text,
        "选项 Approve": "Approve" in text,
        "选项 Deny": "Deny" in text,
        "选项 Show full command": "Show full command" in text,
        "顶部边框 ╭": "╭" in text,
        "底部边框 ╰": "╰" in text,
        "选中标记 ❯": "❯" in text,
    }
    ok = True
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
