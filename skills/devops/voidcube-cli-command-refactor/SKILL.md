---
name: voidcube-cli-command-refactor
description: 修改 VoidCube CLI 内建斜杠命令（如 /model、/provider、/api）时的架构地图与安全改造流程。当需要新增/删除命令、增删标志位、改变量选择器行为、或调整命令帮助文案时使用。含 .venv 测试运行方式与 AGENTS 提交纪律。
---

# VoidCube CLI 斜杠命令改造

## 何时用
- 要给某个内建命令增删参数/标志位（如去掉 `/model --provider`）。
- 要改变交互（如把两阶段选择器改成单阶段）。
- 要改命令补全或帮助文案。
- 需要定位"某命令从输入到执行"的完整链路。

## 命令分层架构（port/handler 模式，务必逐层对齐）
一次命令改动通常横跨以下层，改一层要同步改上层 port 定义与测试桩：

1. 路由/执行表：`src/voidcube/interfaces/cli/commands/execution.py`、`commands/registry.py`
   - `commands/registry.py` 内 `install_cli_command_execution(...)` 的 dict 把命令名映射到 handler，并构造 ports（如 `_model_command_ports`，约 1750 行）。
2. Handler（纯逻辑）：`src/voidcube/interfaces/cli/commands/handlers/*.py`
   - 每个 handler 用 `@dataclass(frozen=True, slots=True)` 的 `XxxCommandPorts` 注入依赖，handler 自身不碰全局状态（便于单测）。
3. 运行时适配层：`src/voidcube/interfaces/cli/*_runtime.py`
   - 例如 `provider_runtime.py`（`CliProviderRuntime`）、`model_picker_runtime.py`（选择器状态机）。
4. 业务管线：`src/voidcube/interfaces/cli/model_switch.py`（解析标志、解析模型、校验、取元数据）。
5. TUI 渲染/键位：`src/voidcube/interfaces/cli/tui/modal_widgets.py`（面板内容）、`tui/modal_navigation.py`（上下键 clamp）。
6. 补全：`src/voidcube/interfaces/cli/commands/catalog.py` 的 `SlashCommandCompleter`（`_model_completions` 等）。
7. 帮助文案：`commands/handlers/display.py`（`handle_provider_display_command`）、`commands/catalog.py` 里的 `CommandDef`。

/`/model` 具体链路：入口 → `handlers/model.py::handle_model_command` → `model_switch.switch_model`（无参时 `provider_runtime.open_picker` → `model_picker_runtime.submit` → 再次 `switch_model`）。

## 改造流程
1. **先摸链路**：用 search_files 找命令字面量、handler、ports、测试。命令名在 `catalog.py` 的 CommandDef 与 `execution.py` 里都有登记。
2. **先澄清范围**：这类改动常有多种实现范围（彻底删标志 vs 保留但报错 vs 只改展示），先向用户确认，避免大范围返工。
3. **按层改**：管线 → handler/ports → runtime → TUI → 补全 → 帮助文案。
4. **同步测试桩**：测试里手写构造 `XxxCommandPorts(...)`；签名一变，所有桩都要改（字段名、元组长度）。
5. **删失效代码**（仓库硬规则）：删掉被替换的旧分支、参数、兼容类；若旧代码块只靠 `except Exception: pass` 兜底而实际已 ImportError，那是死代码，一并删。
6. **跑测试**（见下）。

## 运行测试（关键操作细节）
在本仓库的 bash 终端里，Windows venv 的 python 是 `.venv/Scripts/python.exe`：

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_model_switch.py \
  tests/test_cli_provider_runtime.py tests/test_cli_model_picker_runtime.py \
  tests/test_tui_modal_widgets.py tests/test_tui_keybindings.py -q
```

- `.venv/bin/python` 是指向 `/usr/local/bin/python` 的坏软链，别用它。
- 涉及命令执行全链路的再加 `tests/test_cli_command_handlers.py`、`tests/test_cli_command_execution.py`、`tests/test_cli_command_router.py`、`tests/test_cli_interactive_tui_assembly_runtime.py`。
- AGENTS 规则 5：改技能发现/索引/registry 时必须额外跑 `pytest tests/test_skill_registry.py`（本次命令改造不涉及）。
- **提交前必须跑全量门禁**（AGENTS.md 第 3 条，2026-09 更新）：`.venv/Scripts/python.exe scripts/run_ci_tests.py`（等价 `pytest tests Mem/tests -q`，实测约 17–20 分钟）。前台会超时，务必用 `background=true, notify_on_complete=true`。
- 收窄文件集只适合迭代中快速验证，**标记子集不能替代全量**：实测 `-m "cli or smoke or unit"` 未覆盖 `tests/test_commands_autocomplete.py`——一次只跑目标用例+标记子集的改动，漏掉了被它改坏的补全用例，回归一直留到下次全量才暴露。改内建命令后按 AGENTS 跑全量门禁再提交。

## 已知坑
- **选择器索引语义**：`_model_picker_maximum` 的 clamp 上界。单阶段下 choices = `模型列表 + ["Cancel"]`，最大下标 = `len(model_list)`；两阶段时代是 `len(model_list)+1`。改渲染一定要同步 `modal_navigation.py` 的 clamp，否则上下键越界。
- **locale 结构真相（2026-09 实测修正，替换旧结论）**：`translations.prompts` 是**嵌套 dict**，含 ~2576 个干净键 + ~100 个历史遗留的"带 `prompts.` 前缀"死键。`i18n.translate("prompts.X")` 会按 `.` 分段查找，即取 `translations["prompts"]["X"]`，因此：
  - 代码里 `ports.translate("字面英文串")`（如 `/provider` 帮助文案）传的是**原始串当 key**，永远匹配不到 → 回退返回该串本身，等于没翻译。改这类文案**不必**动 locale（清失效键是加分项）。
  - 但若你用了 `translate("prompts.X", default=...)` 这种 key 形式，就必须在 `prompts` 对象里加**干净键 `X`（不要带 `prompts.` 前缀）**——带前缀的键永远命中不到。
  - 加完务必实测：`from voidcube.interfaces.cli.i18n import get_i18n; i=get_i18n(); i.init(); i.set_locale('zh_CN'); print(i.translate('prompts.X', default='FB'))`。en_US 无该键时回退 default（正常，不必给 en_US 补）。
  - 那 ~100 个带前缀键是死数据，可另行批量清理（先用 search_files 核验无代码引用再删）。
- **删除跨 provider 能力的落地方式**：`parse_model_flags` 收成 2 元组 `(model_input, is_global)`；`--provider` 文本会留在 model_input，由 handler 检查 `"--provider" in model_input.split()` 后 emit 明确报错并指向 `/api`。`switch_model` 去掉 `explicit_provider`/`current_model` 参数与 `detect_provider_for_model` 兜底，新增 `list_current_provider_models`。
- **ports 字段改名**：例如把 `list_configured_providers` 改成 `list_current_models` 时，`handlers/model.py` 的 dataclass 字段名、`registry.py` 构造处、所有测试桩三处同时改，否则 `TypeError: unexpected keyword`。
- **补全的 provider 上下文**：`SlashCommandCompleter` 无 host/config，需自行 `load_config()` + `get_active_provider_key(config)` 定位当前 provider。注意 `get_active_provider_key` 读的是 `config["runtime"]["active_provider"]`，**缺失即返回空**（不会从 providers 推导），补全会静默为空。收窄到当前 provider 后必须同步更新 `tests/test_commands_autocomplete.py`：其 mock 配置原本只有 `providers` 段，需补 `"runtime": {"active_provider": "<provider-key>"}`，否则该用例失败（且它不在 model_switch 的目标测试集里，极易漏）。这与 picker 走 `host.provider` 是两个口径，改动时留意一致性。

## 提交纪律（AGENTS.md）
- 提交必须**路径限定**：`git add -- <paths>`，禁止 `git add -A`。
- 技能文件与产品代码**不得混在同一提交**（`.githooks/pre-commit` 会拦，逃生口 `git commit --no-verify`）。
- 测试只跑通过后再报告完成。
