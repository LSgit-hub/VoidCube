# Agent 工具简介列表

> 共 **58** 个内置工具（不含动态 MCP 工具），按功能分类。所有工具通过 `tools/registry.py` 统一注册管理。


## 一、Web 搜索与内容提取（2 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **web_search** | 搜索 Web，返回最多 5 条相关结果（标题、URL、摘要） | `query`（必填）, `allowed_domains`, `blocked_domains` |
| **web_extract** | 提取网页/PDF 内容为 Markdown。小于 5000 字符返回全文，超出则 LLM 摘要 | `url`（必填，支持多 URL 数组） |


## 二、终端命令执行（1 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **terminal** | 在 Linux 环境中执行 Shell 命令。文件系统在调用间持久化。**注意：不要用 `cat`/`grep`/`sed`/`ls`/`echo` → 用专用文件工具取代** | `command`（必填）, `background`, `timeout`, `workdir`, `pty`, `notify_on_complete`, `watch_patterns` |

> 支持 7 种后端：`local` / `docker` / `podman` / `ssh` / `modal` / `singularity` / `daytona`


## 三、文件操作（4 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **read_file** | 按行号读取文本文件，支持分页。替代 `cat`/`head`/`tail` | `file_path`（必填）, `offset`, `limit` |
| **write_file** | 写入内容到文件（完全覆盖）。替代 `echo`/cat heredoc | `file_path`（必填）, `content`（必填） |
| **patch** | 精确查找替换编辑。9 种模糊匹配策略，自动语法检查。替代 `sed`/`awk` | `file_path`（必填）, `old_string`（必填）, `new_string`（必填）, `replace_all` |
| **search_files** | 搜索文件内容（ripgrep 正则）或按 glob 查找文件。替代 `grep`/`rg`/`find`/`ls` | `pattern`, `path`, `target`（content/files）, `glob`, `output_mode` |


## 四、浏览器自动化（10 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **browser_navigate** | 打开 URL，初始化浏览器会话。必须在其他浏览器工具前调用 | `url`（必填） |
| **browser_snapshot** | 捕获当前页面的紧凑交互快照（含可交互元素和 ref ID） | `force` |
| **browser_click** | 通过 ref ID 点击元素（如 `@e5`） | `ref`（必填） |
| **browser_type** | 在输入框中输入文本（先清空再输入） | `ref`（必填）, `text`（必填） |
| **browser_scroll** | 按方向滚动页面，展示更多内容 | `direction`（up/down/left/right） |
| **browser_back** | 返回浏览器历史上一页 | 无 |
| **browser_press** | 按键操作（Enter/Tab/快捷键） | `key`（必填） |
| **browser_get_images** | 列出当前页面所有图片的 URL 和 alt 文本 | 无 |
| **browser_vision** | 截屏并用视觉 AI 分析（适合验证码、复杂布局） | `task` |
| **browser_console** | 读取浏览器控制台输出（JS 错误、日志等）；也可执行 JS 表达式 | `expression` |


## 五、代码执行（1 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **execute_code** | 在隔离沙箱中执行 Python 或 JavaScript，网络禁用，资源受限。**强制要求容器后端**（docker/podman 等），拒绝 local 执行 | `code`（必填）, `language`（python/javascript，默认 python） |


## 六、系统监控（6 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **system_info** | 获取系统信息概览 | 无 |
| **cpu_stats** | 获取 CPU 统计信息 | 无 |
| **memory_stats** | 获取内存统计信息 | 无 |
| **disk_usage** | 获取磁盘使用情况 | 无 |
| **top_processes** | 获取 Top 进程列表 | 无 |
| **process** | 获取进程列表 | 无 |


## 七、网络诊断（4 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **ping** | Ping 测试 | `host`（默认 google.com）, `count`（默认 4） |
| **check_port** | 检查端口是否开放 | `host`（默认 localhost）, `port`（默认 80） |
| **dns_lookup** | DNS 查询 | `domain`（默认 example.com）, `record_type`（默认 A） |
| **curl_check** | HTTP 请求测试 | `url`（默认 https://example.com） |


## 八、日志分析（3 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **read_log** | 读取日志文件指定行数 | `file_path`（必填）, `lines`（默认 100） |
| **log_errors** | 在日志文件中查找错误信息 | `file_path`（必填） |
| **analyze_log** | 分析日志文件 | `file_path`（必填） |


## 九、技能管理（3 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **skills_list** | 列出所有可用技能（名称 + 描述） | 无 |
| **skill_view** | 加载技能完整内容或访问关联文件（references/templates） | `name`（必填）, `file_path` |
| **skill_manage** | 创建/更新/删除技能。技能即 Agent 的程序性记忆 | `action`（必填：create/patch/edit/delete/write_file/remove_file）, `name`, `content` |


## 十、任务与委托（3 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **todo** | 管理当前会话的任务列表。3 步以上复杂任务或用户给定多任务时使用 | `todos`（数组）, `merge` |
| **scheduled_task** | 管理定时任务计划（仅管理计划，不立即执行） | `action`（必填：list/create/update/pause/resume/delete）, `title`, `instruction`, `schedule_type`, `run_at`, `time_of_day`, `weekdays` |
| **delegate_task** | 派生子 Agent 在隔离上下文中工作。支持单任务和批量并行（最多 3 个） | `goal`（与 tasks 二选一）, `tasks`（数组，最多 3 个）, `context`, `toolsets` |


## 十一、会话与交互（2 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **session_search** | 在当前会话历史中搜索过去的对话和内容 | `query`（必填） |
| **clarify** | 请求用户澄清模糊或不完整的请求 | `question`（必填） |


## 十二、媒体播放（1 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **media_play** | 将音乐/视频加入 VoidCube Web UI 播放队列。支持 YouTube、B站、直链 mp3/mp4 | `url`（必填）, `title`, `media_type`, `auto_play` |


## 十三、AI 协作（1 个）

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **mixture_of_agents** | 多个前沿 LLM 协同求解难题。5 次 API 调用（4 个参考模型 + 1 个聚合器），最大推理深度。适用：复杂数学、高级算法、多步推理 | `user_prompt`（必填） |


## 十四、Windows UI 自动化（17 个）

> 基于 Windows UIA（UI Automation）框架，适用于桌面应用自动化控制。

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| **uia_find_window** | 按名称/类名/进程 ID 查找窗口，支持正则 | `name`, `class_name`, `process_id`, `regex_match` |
| **uia_wait_for_window** | 等待窗口出现（阻塞至超时） | `name`（必填）, `timeout`（默认 30s） |
| **uia_search_controls** | 按类型/名称/automation ID 搜索控件 | `window_name`（必填）, `control_type`, `name_pattern` |
| **uia_click_button** | 点击按钮（支持重试） | `window_name`（必填）, `button_name`, `automation_id`, `retries` |
| **uia_set_text** | 在编辑框中设置文本 | `window_name`（必填）, `text`（必填）, `control_name`, `clear_first` |
| **uia_get_text** | 从控件获取文本 | `window_name`（必填）, `control_name`, `automation_id` |
| **uia_list_controls** | 列出窗口中所有控件及详细信息 | `window_name`（必填）, `include_hidden` |
| **uia_select_combo_item** | 在下拉框中选择项 | `window_name`（必填）, `combo_name`, `item_name`, `item_index` |
| **uia_toggle_checkbox** | 切换复选框状态 | `window_name`（必填）, `checkbox_name`, `checked` |
| **uia_send_keys** | 向窗口发送按键（支持 `{ENTER}`, `{TAB}`, `{CTRL}s` 等） | `window_name`（必填）, `keys`（必填）, `delay` |
| **uia_window_action** | 窗口操作（最大化/最小化/关闭/激活/还原/获取信息） | `window_name`（必填）, `action`（必填） |
| **uia_mouse_click** | 在指定坐标点击鼠标 | `x`, `y`（必填）, `double_click` |
| **uia_mouse_move** | 移动鼠标到指定坐标 | `x`, `y`（必填） |
| **uia_start_process** | 启动进程并可选等待其窗口出现 | `executable`（必填）, `arguments`, `wait_for_window` |
| **uia_wait** | 等待指定秒数 | `seconds`（默认 1.0） |
| **uia_get_control_info** | 获取特定控件的详细信息 | `window_name`（必填）, `control_name`, `automation_id` |
| **uia_scroll** | 上下滚动窗口内容 | `window_name`（必填）, `direction`（up/down）, `amount` |


## 十五、MCP 动态工具（数量不固定）

MCP（Model Context Protocol）工具由外部 MCP 服务器动态注册，数量取决于连接的 MCP 服务数量。每个 MCP 服务器的工具会以 `mcp-<服务器名>` 为 toolset 前缀自动注册到系统中。此外，每个 MCP 服务器还会注册 4 个辅助工具：

| 辅助工具 | 描述 |
|----------|------|
| `mcp-<server>_list_resources` | 列出该 MCP 服务器的资源 |
| `mcp-<server>_read_resource` | 读取指定资源 |
| `mcp-<server>_list_prompts` | 列出该 MCP 服务器的提示模板 |
| `mcp-<server>_get_prompt` | 获取指定提示模板 |


## 内置工具集（Toolset）

工具按 toolset 分组，可通过配置按需启用/禁用：

| Toolset | 包含工具 |
|---------|----------|
| `core` | terminal, read_file, write_file, patch, search_files, web_search, web_extract, browser_navigate, browser_snapshot |
| `extended` | memory_load, memory_persist, delegate_task, execute_code |
| `web` | web_search, web_extract, media_play |
| `file` | read_file, write_file, patch, search_files |
| `terminal` | terminal, process |
| `code_execution` | execute_code |
| `skills` | skills_list, skill_view, skill_manage |
| `system` | system_info, cpu_stats, memory_stats, disk_usage, top_processes |
| `network` | ping, check_port, dns_lookup, curl_check |
| `logs` | read_log, log_errors, analyze_log |
| `browser` | browser_navigate, browser_snapshot, browser_click, browser_type, browser_scroll, browser_back, browser_press, browser_get_images, browser_vision, browser_console |
| `session_search` | session_search |
| `scheduling` | scheduled_task |
| `todo` | todo |
| `assistant` | clarify, session_search |
| `moa` | mixture_of_agents |
| `uiautomation` | uia_find_window ~ uia_scroll（17 个 Windows UI 工具） |
| `ops` | system + network + logs 的全部工具 |
| `voidcube` | web + browser + terminal + file + skills + scheduling + code_execution + ops（综合工具集，默认） |
| `full` | voidcube + session_search（全工具集） |
| `learn` | web_search, web_extract, read_file, search_files, terminal, execute_code, browser_*（自学习子代理专用，无写入能力） |
| `mini` | web_search, terminal, read_file, write_file（最小工具集） |

> 动态 MCP 工具集的命名模式为 `mcp-<服务器名>`。


## 统计概览

| 分类 | 工具数 |
|------|--------|
| Web 搜索与内容提取 | 2 |
| 终端命令执行 | 1 |
| 文件操作 | 4 |
| 浏览器自动化 | 10 |
| 代码执行 | 1 |
| 系统监控 | 6 |
| 网络诊断 | 4 |
| 日志分析 | 3 |
| 技能管理 | 3 |
| 任务与委托 | 3 |
| 会话与交互 | 2 |
| 媒体播放 | 1 |
| AI 协作 | 1 |
| Windows UI 自动化 | 17 |
| **内置工具合计** | **58** |
| MCP 动态工具 | 不固定 |
