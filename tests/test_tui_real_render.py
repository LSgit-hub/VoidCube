"""真实 TUI 渲染测试：pyte 终端模拟器 + 真实按键 + 真实装配链路。

本测试不用 mock/打桩断言组件内部状态，而是把 VoidCube 的完整 TUI 装配链
（CliTuiModalStateRuntime → CliTuiIndicatorAssemblyRuntime →
CliTuiHostAssemblyRuntime，等价 composition.CliInteractiveTuiAssemblyRuntime.build
但注入 pipe input + vt100 output）接入 prompt_toolkit 的真实事件循环，
再让 pyte 模拟真实终端回放 ANSI 字节流，对最终屏幕快照做断言——
覆盖渲染、重绘、键盘路由的全链路。

关键坑位（曾导致模态框"白框"，已修复）：
    非 full_screen 模式下 prompt_toolkit 渲染高度 =
    max(_min_available_height, preferred_height)。真实终端通过 CPR 响应把
    _min_available_height 设为整屏；pyte 不响应 CPR，若测试 output 不提供
    get_rows_below_cursor_position()，根 write_position 高度只剩
    preferred_height（约 3~4 行），float 模态框被压到 2 行，正文与选项
    全部截断。SizedVt100Output.get_rows_below_cursor_position() 提供
    等价信息（"光标在顶行、下方整屏可用"），见下。
"""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pyte
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from voidcube.domain.contracts.interaction import ClarificationRequest
from voidcube.interfaces.cli.lifecycle.registration import CliInteractiveRegistrations
from voidcube.interfaces.cli.tui.composition import (
    CliInteractiveTuiPorts,
    CliInteractiveTuiStatePorts,
)
from voidcube.interfaces.cli.tui.dynamic_text_runtime import (
    TuiDynamicTextPorts,
    TuiDynamicTextRuntime,
)
from voidcube.interfaces.cli.tui.host_assembly import (
    CliTuiCompositionPorts,
    CliTuiExtensionPorts,
    CliTuiHostAssemblyPorts,
    CliTuiHostAssemblyRuntime,
    CliTuiInputPorts,
    CliTuiModalNavigationPorts,
    CliTuiModalPorts,
    CliTuiModalStatePorts,
    CliTuiModalStateRuntime,
    CliTuiPastePorts,
)
from voidcube.interfaces.cli.tui.image_indicator import CliTuiImageIndicatorPorts
from voidcube.interfaces.cli.tui.indicator_assembly import (
    CliTuiIndicatorAssemblyPorts,
    CliTuiIndicatorAssemblyRuntime,
)

COLS, ROWS = 80, 24


class SizedVt100Output(Vt100_Output):
    """固定尺寸的 VT100 output：模拟真实终端"光标在顶行、下方整屏可用"。

    get_rows_below_cursor_position() 返回整屏行数，等价于真实终端对 CPR
    的响应（光标在 y=0 时 rows_below = rows）。缺了它，渲染高度退化为
    布局 preferred_height，模态框 float 会被截断成 2 行白框。
    """

    def __init__(self, stdout: io.StringIO, columns: int = COLS, rows: int = ROWS) -> None:
        self._size = Size(rows, columns)  # 注意：Size 第一参数是行数
        super().__init__(stdout, self.get_size, enable_cpr=False)

    def get_size(self) -> Size:
        return self._size

    def get_rows_below_cursor_position(self) -> int:
        return self._size.rows


def make_output() -> tuple[SizedVt100Output, io.StringIO]:
    buf = io.StringIO(newline="")
    return SizedVt100Output(buf), buf


def screen_snapshot(buf: io.StringIO) -> list[str]:
    """把累计 ANSI 字节流回放到 pyte 终端，返回 24 行去尾随空白的屏幕。"""
    screen = pyte.Screen(COLS, ROWS)
    pyte.ByteStream(screen).feed(buf.getvalue().encode("utf-8"))
    return [row.rstrip() for row in screen.display]


def _register_extras(key_bindings, events: list[str]) -> None:
    """真实扩展点：注册 c-q 退出，验证 register_extra_keybindings 链路。"""
    events.append("extra_kb")

    @key_bindings.add("c-q")
    def _quit(event) -> None:
        events.append("c-q-pressed")
        event.app.exit()


def build_ports(pipe, output, holder: dict, events: list[str], tmp: Path) -> CliInteractiveTuiPorts:
    """构造真实端口桩：状态从 holder 读取，重绘转发到真实 application。"""
    dynamic_text = TuiDynamicTextRuntime(
        TuiDynamicTextPorts(
            voice_recording=lambda: False,
            voice_processing=lambda: False,
            sudo_active=lambda: bool(holder.get("sudo")),
            secret_active=lambda: bool(holder.get("secret")),
            approval_active=lambda: bool(holder.get("approval")),
            clarify_freetext=lambda: False,
            clarify_active=lambda: bool(holder.get("clarify")),
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

    def invalidate() -> None:
        # 真实运行时 invalidate 指向 application；这里在 app 注册后转发，
        # 使方向键等模态导航处理完能触发真实重绘。
        app = holder.get("app")
        if app is not None:
            app.invalidate()

    registrations = CliInteractiveRegistrations(
        enter=SimpleNamespace(handle=lambda _e: events.append("enter")),
        voice=SimpleNamespace(handle=lambda _e: events.append("voice")),
        suspend=SimpleNamespace(handle=lambda _e: events.append("suspend")),
        dynamic_text=dynamic_text,
        voice_key="c-b",
    )
    prompt_runtime = SimpleNamespace(
        fragments=lambda: [("class:prompt", "> ")],
        text=lambda: "> ",
    )
    layout_metrics = SimpleNamespace(input_rule_height=lambda _text: 1)
    state = CliInteractiveTuiStatePorts(
        clarify_state=lambda: holder.get("clarify"),
        clarify_freetext_active=lambda: False,
        sudo_state=lambda: holder.get("sudo"),
        secret_state=lambda: holder.get("secret"),
        approval_state=lambda: holder.get("approval"),
        model_picker_state=lambda: holder.get("model_picker"),
        update_selection=lambda mutate: mutate(),
    )

    def store_application(app) -> None:
        holder["app"] = app

    return CliInteractiveTuiPorts(
        registrations=registrations,
        prompt_runtime=prompt_runtime,
        layout_metrics=layout_metrics,
        state=state,
        attached_images=lambda: [],
        image_counter=lambda: 0,
        format_image_badges=lambda _images: [],
        voice_fragments=lambda: [("class:voice-status", "正在聆听…")],
        voice_visible=lambda: holder.get("voice", False),
        autonomous_fragments=lambda: [("class:mc-body-text", "自动执行中")],
        autonomous_visible=lambda: holder.get("autonomous", False),
        status_fragments=lambda: [("class:status-bar", "就绪 · 状态栏")],
        status_visible=lambda: holder.get("status", False),
        should_attach_clipboard_image=lambda _text: False,
        attach_clipboard_image=lambda: False,
        paste_directory=tmp,
        timestamp=lambda: "000000",
        invalidate_event=lambda _event: None,
        invalidate=invalidate,
        history_path=str(tmp / "history.txt"),
        command_available=lambda _command: True,
        command_running=lambda: False,
        approval_fragments=lambda: [("class:approval-desc", "批准执行这条命令？")],
        register_extra_keybindings=lambda key_bindings, input_area=None: _register_extras(
            key_bindings, events
        ),
        cursor=None,
        store_application=store_application,
        install_resize_cleanup=lambda _application: None,
        extra_widgets=lambda: [],
    )


def build_application(ports: CliInteractiveTuiPorts, pipe, output, holder: dict):
    """复刻 composition.CliInteractiveTuiAssemblyRuntime.build() 的真实装配链，
    但向 CliTuiCompositionPorts 注入 pipe input + vt100 output（原装配器硬编码不注入）。

    注意：CliTuiHostAssemblyRuntime.build() 内部已通过 modal=... 端口自动构建
    modal 组件树（host_assembly.py 的 ModalWidgetPorts 挂接），测试无需手动
    调用 build_modal_widgets。
    """
    state = ports.state
    modal_state_runtime = CliTuiModalStateRuntime(
        CliTuiModalStatePorts(
            clarify_state=state.clarify_state,
            clarify_freetext_active=state.clarify_freetext_active,
            sudo_state=state.sudo_state,
            secret_state=state.secret_state,
            approval_state=state.approval_state,
            model_picker_state=state.model_picker_state,
            update_selection=state.update_selection,
        )
    )
    indicator_ports = CliTuiIndicatorAssemblyRuntime(
        CliTuiIndicatorAssemblyPorts(
            dynamic_text=ports.registrations.dynamic_text,
            layout_input_rule_height=ports.layout_metrics.input_rule_height,
            image=CliTuiImageIndicatorPorts(
                attached_images=ports.attached_images,
                image_counter=ports.image_counter,
                format_badges=ports.format_image_badges,
            ),
            voice_fragments=ports.voice_fragments,
            voice_visible=ports.voice_visible,
            autonomous_fragments=ports.autonomous_fragments,
            autonomous_visible=ports.autonomous_visible,
            status_fragments=ports.status_fragments,
            status_visible=ports.status_visible,
        )
    ).build()
    return CliTuiHostAssemblyRuntime(
        CliTuiHostAssemblyPorts(
            registrations=ports.registrations,
            paste=CliTuiPastePorts(
                should_attach_clipboard_image=ports.should_attach_clipboard_image,
                attach_clipboard_image=ports.attach_clipboard_image,
                paste_directory=ports.paste_directory,
                timestamp=ports.timestamp,
                invalidate=ports.invalidate_event,
            ),
            modal_navigation=modal_state_runtime.modal_navigation_ports(
                invalidate=ports.invalidate,
            ),
            normal_input_active=modal_state_runtime.normal_input_active,
            input=CliTuiInputPorts(
                history_path=ports.history_path,
                prompt_fragments=ports.prompt_runtime.fragments,
                prompt_text=ports.prompt_runtime.text,
                command_available=ports.command_available,
                command_running=ports.command_running,
                password_mask_active=modal_state_runtime.password_mask_active,
                input_locked=modal_state_runtime.input_locked,
            ),
            placeholder_text=ports.registrations.dynamic_text.placeholder,
            modal=modal_state_runtime.modal_widget_ports(
                approval_fragments=ports.approval_fragments,
            ),
            indicators=indicator_ports,
            extensions=CliTuiExtensionPorts(
                register_extra_keybindings=ports.register_extra_keybindings,
                composition=CliTuiCompositionPorts(
                    cursor=ports.cursor,
                    store_application=ports.store_application,
                    install_resize_cleanup=ports.install_resize_cleanup,
                    input=pipe,
                    output=output,
                ),
                extra_widgets=ports.extra_widgets,
            ),
        )
    ).build()


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    """轮询等待渲染结果满足条件，避免脆弱的固定 sleep。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"等待超时（{timeout}s）: 条件未满足")


@pytest.fixture
def tui_harness(tmp_path):
    """启动完整 TUI 应用（后台线程），提供快照与状态注入接口。

    用法：
        rows = harness.snapshot()                 # 当前屏幕（24 行）
        harness.set_modal("clarify", {...})       # 注入模态框状态 + 触发重绘
        harness.pipe.send_text("...")             # 真实按键
        harness.events                            # 扩展点事件记录
    """
    out, buf = make_output()
    holder: dict = {}
    events: list[str] = []
    thread_exc: list[BaseException] = []

    with create_pipe_input() as pipe:
        ports = build_ports(pipe, out, holder, events, tmp_path)
        app = build_application(ports, pipe, out, holder)
        assert holder.get("app") is app, "store_application 未收到真实 application"

        def run() -> None:
            try:
                app.run()
            except BaseException as exc:  # noqa: BLE001 - 线程内异常需回传主线程
                thread_exc.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def snapshot() -> list[str]:
            return screen_snapshot(buf)

        def set_modal(name: str, value) -> None:
            holder[name] = value
            ports.invalidate()

        # 等首帧渲染完成（主布局分隔线已绘制），而非仅缓冲非空
        _wait_until(lambda: any("─" in row for row in screen_snapshot(buf)[:4]), timeout=5.0)

        harness = SimpleNamespace(
            app=app,
            pipe=pipe,
            buf=buf,
            holder=holder,
            events=events,
            snapshot=snapshot,
            set_modal=set_modal,
            invalidate=ports.invalidate,
        )

        try:
            yield harness
        finally:
            if thread.is_alive():
                pipe.send_text("\x11")  # c-q 退出（0x11 = 17）
                thread.join(timeout=5.0)
            if thread.is_alive():
                pytest.fail("TUI 应用线程未能在 c-q 后退出")
            if thread_exc:
                raise thread_exc[0]


def _open_clarify(harness, choices=("自动执行", "手动确认")) -> None:
    harness.set_modal(
        "clarify",
        {
            "request": ClarificationRequest.create("选择执行路径", list(choices)),
            "choices": list(choices),
            "selected": 0,
        },
    )
    _wait_until(lambda: any("needs your input" in row for row in harness.snapshot()))


def test_initial_render_shows_prompt_and_input(tui_harness):
    rows = tui_harness.snapshot()
    # 分隔线与 prompt 已绘制
    assert any("─" in row for row in rows[:4]), "未渲染分隔线"
    # prompt 为 "> "，尾部空格被 rstrip 去除，故用 startswith 判断
    assert any(row.lstrip().startswith(">") for row in rows[:6]), "未渲染 prompt 提示符"
    # 模态框未打开时不应有输入需求框
    assert not any("needs your input" in row for row in rows)


def test_typing_echoes_in_input(tui_harness):
    """真实按键回显：字符输入出现在输入区。"""
    tui_harness.pipe.send_text("hello")
    _wait_until(lambda: any("hello" in row for row in tui_harness.snapshot()))


def test_clarify_modal_renders_full_content(tui_harness):
    """核心回归：模态框必须完整渲染标题、正文与选项（防"白框"截断）。"""
    _open_clarify(tui_harness)
    text = "\n".join(tui_harness.snapshot())
    assert "Voidcube needs your input" in text  # 标题栏
    assert "选择执行路径" in text  # 正文
    assert "❯ 自动执行" in text  # 默认选中第一项
    assert "手动确认" in text  # 第二项（无选中标记）


def test_clarify_arrow_keys_move_selection(tui_harness):
    """真实按键路由：方向键改变选中项，重绘后 ❯ 标记移动。"""
    _open_clarify(tui_harness)
    assert any("❯ 自动执行" in row for row in tui_harness.snapshot())

    tui_harness.pipe.send_text("\x1b[B")  # down 键
    _wait_until(lambda: any("❯ 手动确认" in row for row in tui_harness.snapshot()))
    text = "\n".join(tui_harness.snapshot())
    assert "❯ 自动执行" not in text  # 选中标记已移走


def test_clarify_close_restores_normal_screen(tui_harness):
    _open_clarify(tui_harness)
    tui_harness.set_modal("clarify", None)
    _wait_until(lambda: not any("needs your input" in row for row in tui_harness.snapshot()))


def test_ctrl_q_quits_via_extra_keybindings(tui_harness):
    """register_extra_keybindings 扩展点真实生效：c-q 退出应用。"""
    assert "extra_kb" in tui_harness.events, "扩展点未注册"
    tui_harness.pipe.send_text("\x11")  # c-q
    _wait_until(lambda: "c-q-pressed" in tui_harness.events)
    # fixture teardown 会 join 线程并断言其退出


def test_approval_modal_renders(tui_harness):
    """approval 模态框同样完整渲染（与 clarify 共用 float 渲染路径）。"""
    tui_harness.set_modal("approval", {"command": "rm -rf /tmp/x"})
    # approval 面板无 "needs your input" 标题，直接断言 approval_fragments 正文
    _wait_until(lambda: "批准执行这条命令？" in "\n".join(tui_harness.snapshot()))


@pytest.mark.parametrize(
    "key,text",
    [
        ("status", "就绪 · 状态栏"),
        ("voice", "正在聆听…"),
    ],
)
def test_bar_background_reaches_right_edge(tui_harness, key, text):
    """回归：状态栏/语音栏条形必须满宽（#1a1a2e 背景延伸到右边缘）。

    曾因 Window 缺少 style 参数，内容不足满宽时右侧缺口透出终端默认背景，
    蓝色条形不顶到右边缘。修复后 Window 自带 class 背景，文本后的填充空格
    与文本共享同一背景段，只有渲染到行尾（\\x1b[79C）才重置。
    断言基于原始 ANSI 字节流（快照 rstrip 会抹掉尾部空格，无法观测背景段）。
    """
    tui_harness.holder[key] = True
    tui_harness.invalidate()
    _wait_until(lambda: any(text in row for row in tui_harness.snapshot()))

    data = tui_harness.buf.getvalue()
    idx = data.rfind(text)
    assert idx != -1, f"{key} 条形文本未出现在字节流中"
    after = data[idx + len(text): idx + len(text) + 30]
    assert not after.startswith("\x1b[0m"), (
        f"{key} 条形文本后立即重置背景：右侧缺口未填充 #1a1a2e（修复回退）"
    )
