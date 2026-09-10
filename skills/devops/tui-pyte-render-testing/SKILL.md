---
name: tui-pyte-render-testing
description: 用 pyte 终端模拟器对 prompt_toolkit TUI 做真实渲染+交互集成测试（非打桩断言）。当需要断言 TUI 实际绘制内容、验证按键序列驱动 UI 变化、在无终端/CI 环境下测试 prompt_toolkit Application，或用户要求"真实 TUI 组件测试"时使用。
---

# TUI 真实渲染 + 交互集成测试（pyte + prompt_toolkit）

## 适用场景
- 用户要求对 TUI 做"真实测试"而非 SimpleNamespace/monkeypatch 打桩单测
- 需要断言终端上实际绘制的内容（prompt、输入框、模态框、分隔线）
- 需要验证真实按键序列（字符/控制键）驱动 UI 变化
- 无头环境（Git Bash / CI / Windows 无 console）下测试 prompt_toolkit Application

## 核心管线
真实组件树 + 真实事件循环 + 真实按键输入 → ANSI 输出 → pyte 解析 → 逐像素屏幕快照断言。

```
SizedVt100Output(io.StringIO(newline=""), get_size=...)  # 输出捕获
create_pipe_input()  # 真实按键输入
app.run() 在 daemon 线程            # 真实事件循环
pyte.Screen(COLS, ROWS) + ByteStream.feed(buf.encode("utf-8"))  # ANSI → 屏幕
[row.rstrip() for row in screen.display]  # 快照断言
```

## 关键组件

```python
class SizedVt100Output(Vt100_Output):
    def __init__(self, stdout, columns=COLS, rows=ROWS):
        self._size = Size(rows, columns)   # 注意顺序！见坑 1
        super().__init__(stdout, self.get_size, enable_cpr=False)

    def get_size(self):
        return self._size

    def get_rows_below_cursor_position(self):
        # 关键！非 full_screen 模式渲染高度 = max(_min_available_height, preferred_height)。
        # 真实终端通过 CPR 响应把 _min_available_height 设为整屏；pyte 不响应 CPR，
        # 缺此方法时根 write_position 高度退化为 preferred_height(3~4 行)，
        # float 模态框被压到 2 行 → 标题边框之外的正文/选项全部截断（"白框"）。
        # 返回整屏行数 = 模拟"光标在顶行、下方整屏可用"的真实终端。见坑 11。
        return self._size.rows

def screen_snapshot(buf):
    screen = pyte.Screen(COLS, ROWS)
    pyte.ByteStream(screen).feed(buf.getvalue().encode("utf-8"))
    return [row.rstrip() for row in screen.display]
```

线程模式：`threading.Thread(target=app.run, daemon=True)` 启动 → `time.sleep(~0.5)` 等首帧 → 断言快照 → 注入按键 → 再断言 → c-q 退出 → `thread.join(timeout=5)`。

## 必须遵守的坑（全部实测踩过）

1. **`Size(rows, columns)` 第一参数是行数！** `Size(60, 12)` = 60 行 12 列（屏幕宽度只有 12，分隔线全被截断）。传反是"渲染内容异常"第一嫌疑。
2. **`Vt100_Output.__init__` 必须传 `get_size`**，否则 TypeError。
3. **`enable_cpr=False`**：显式关闭 CPR，避免测试挂起。
4. **输出缓冲必须 `io.StringIO(newline="")`**：Vt100_Output 默认 write_binary=False。
5. **Windows/Git Bash 下 Application 不注入 output 必崩**：`NoConsoleScreenBufferError: Found xterm-256color, while expecting a Windows console`。触发链：Application 构造 → `create_output()` → Win32Output。解法：装配时把 `input=pipe, output=vt100_output` 注入到 composition ports（见 VoidCube 装配节）。
6. **退出勿用 `pipe.close()`**：抛 EOFError 且难收尾。改为通过真实扩展点注册 c-q 退出：
   ```python
   def _register_extras(key_bindings, events):
       @key_bindings.add("c-q")
       def _quit(event):
           event.app.exit()
   ```
   然后 `pipe.send_text("\x11")`（c-q = 0x11）触发。顺带验证扩展机制本身。
7. **pyte 新版无 `__version__`**：用 `from importlib.metadata import version; version('pyte')`。
8. **模态框状态必须是 mapping**：`clarify_state()` 返回 `{"request": ClarificationRequest.create(...), "choices": [...], "selected": 0}`，传字符串会导致 `dict(state)` 报 `dictionary update sequence element #0 has length 1`。
9. **`input_rule_height` 回调接收 text 参数**：`lambda _text: 1`，不是无参。
10. **命令去重机制**：完全相同的 terminal 命令第二次执行被 `duplicate_or_in_flight_action` 拦截。重跑必须变化命令串（追加 `; echo "EXIT=$?"`、改引号、换 sed 区间）。
11. **缺 `get_rows_below_cursor_position()` → 模态框白框**：非 full_screen 应用下 SizedVt100Output 必须实现它（见"已知问题与修复"节），否则根 write_position 高度只剩 preferred_height，所有 float 内容截断。这是"内容被截断/白框"的第一嫌疑，先于任何布局代码猜测。
12. **大文件修改用 write_file 全量重写，勿用 patch 做多行替换**：patch 的模糊匹配会匹配到错误位置并吞掉相邻函数定义（实测把 test 函数头删掉、函数体裸露在模块级，还把另一个函数覆盖成残缺版）。对 300+ 行测试文件的破坏性改动，直接 write_file 重写。
13. **host_assembly.build 已内置 ModalWidgetPorts 挂接**：装配链走到 `CliTuiHostAssemblyRuntime.build()` 就自动构建了 modal 组件树（host_assembly.py 225-232 行）。不要在测试里手动调 `build_modal_widgets`——重复挂接冗余且易错。
14. **轮询等待代替固定 sleep**：`_wait_until(predicate, timeout=5.0, interval=0.02)` 轮询快照条件，比 `time.sleep(0.5)` 稳且快。fixture teardown 用 `pipe.send_text("\x11")` + `thread.join(timeout=5)` + `is_alive` 断言，线程内异常收集后回抛主线程。
15. **fixture 首帧等待不能只等 `buf.getvalue() != ""`**：首个 ANSI 字节（如光标隐藏序列）就使缓冲非空，此时首帧未画完，立即断言会拿到残缺快照（首测必挂）。必须等布局实质性绘制，如 `any("─" in row for row in screen_snapshot(buf)[:4])`（分隔线出现）。
16. **approval 模态框没有 "needs your input" 标题**：那是 clarify 面板专属（modal_widgets.py 标题栏）。approval 面板直接渲染 `approval_fragments` 正文（如"批准执行这条命令？"），断言用正文文本，勿用 "needs your input"。
17. **快照 `rstrip()` 会去掉 prompt 尾部空格**：prompt `> ` 在快照中显示为 `>`，断言用 `row.lstrip().startswith(">")`，勿用 `"> " in row`。

## VoidCube 项目装配要点（Windows Git Bash）
- 虚拟环境：`cd /f/My_code/VScode_py/VoidCube && ./.venv/Scripts/python.exe ...`
- **不可直接用 `CliInteractiveTuiAssemblyRuntime.build()`**：它硬编码不注入 input/output。必须手写 `build_application()` 复刻 composition.py 装配链（`CliTuiModalStateRuntime` → `CliTuiIndicatorAssemblyRuntime` → `CliTuiHostAssemblyRuntime`），仅在 `CliTuiCompositionPorts` 注入 `input=pipe, output=vt100_output`。
- 导入路径易错点（NameError 高发）：`CliInteractiveRegistrations` 来自 `voidcube.interfaces.cli.lifecycle.registration`（**不是** tui.lifecycle_registration）；build_application 用到的 `CliTuiModalStatePorts` / `CliTuiModalStateRuntime` 也在 `host_assembly` 里（与其它 CliTui*Ports 同源），漏导入必 NameError。
- 动态文本用**真实 `TuiDynamicTextRuntime`**（22 个 ports 字段用 lambda 接 holder dict），不要用桩，否则快照内容不真实。
- 状态切换驱动 UI：改 holder → invalidate → 轮询快照断言（模态框出现/消失可验证）。invalidate 端口建议转发到真实 app（`app = holder.get("app"); app and app.invalidate()`），这样方向键等模态导航处理完能自动触发真实重绘，不需每次手动 invalidate。
- 模态 state 结构（dict）：clarify `{"request": ClarificationRequest.create(...), "choices": [...], "selected": 0}`；approval 直接渲染 `approval_fragments`（filter 是 `approval_state() is not None`）；sudo/secret/model_picker 同理。
- 正式测试模板：`tests/test_tui_real_render.py`（由 `_task_work/proto_vt100_pyte2.py` 固化而来，含全部上述修复；可复用 fixture `tui_harness`）。

## 已知问题与修复（2026-08-25 已解决）
### 模态框"白框"截断（已修复，勿再按旧方向排查）
现象：fragments 层完全正确（标题+问题+选项都在），但渲染只显示前 2 行（标题+边框），正文与选项被截断。
根因：非 full_screen 模式下 Renderer 用 `renderer._min_available_height` 与布局 `preferred_height` 取大来定根 write_position 高度。pyte 不响应 CPR → _min_available_height 为 0 → 根高度只剩 preferred_height（约 3~4 行）→ FloatContainer 得 3~4 行 → float 扣掉 top/bottom 只剩 2 行。旧猜测（HSplit/modal_stack/wrap_lines 时序，见 composition_runtime.py:92 / modal_widgets.py:232）是错的，白排查一场。
修复（测试侧最小改动，零生产代码变更）：在 SizedVt100Output 实现 `get_rows_below_cursor_position()` 返回 `self._size.rows`，等价于真实终端 CPR 响应"光标在 y=0、下方整屏可用"。
验证证据：修复前根 wp.height=3~4、float height=2、快照只有标题；修复后根 wp=80x24、float height=22、快照含完整正文+全部选项+❯ 选中标记，方向键移动选中项正常。
排查入口速查：渲染内容被截断/白框 → 先看根 WritePosition 高度是不是等于整屏；不是 → 检查 output 的 `get_rows_below_cursor_position`；别先怀疑布局代码。

## 验证步骤
1. 初始快照：断言分隔线行、prompt `> `、空输入区
2. `pipe.send_text("hello")` → 快照断言 `> hello`（真实按键回显）
3. 状态切换（如 holder["clarify"]=...）+ invalidate → 断言模态框标题出现
4. 状态置 None + invalidate → 断言模态框消失
5. `pipe.send_text("\x11")` → 断言线程退出、事件列表含 c-q 记录
6. 固化为正式测试后跑全量套件（VoidCube 另需 `pytest tests/test_skill_registry.py`）确认零冲突
