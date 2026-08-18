# VoidCube 架构与目录规划

本文是 VoidCube 的职责地图和后续迁移基线。目标不是把所有文件机械地搬到新目录，而是让每个模块只有一个含义、依赖方向可检查、旧入口不会继续被误认为主逻辑。

## 1. 当前问题

当前代码同时使用了三种命名维度：

- `VoidCube_app` 表示一部分应用服务，但也包含配置、Provider、鉴权、运行时和领域契约。
- `VoidCube_cli` 表示 CLI，但还包含调度器、自治执行、语音运行时、配置管理和应用组装。
- `VoidCube_core` 只有少量基础设施，却使用了最容易被误解的泛化名称。
- 顶层 `agent`、`systems`、`tools`、`plugins`、`Mem` 又是另一套按能力划分的包，导致调用者很难判断“谁拥有业务规则、谁只是适配器”。

因此，问题的根因是职责边界混合，而不是目录层级不够深。

## 2. 目标架构

推荐最终采用一个规范 Python 包名 `voidcube`，把入口、业务、适配器和扩展分开。`src` 布局是目标状态；迁移期间可以先在现有包内建立同名子包，不要求一次性改完全部导入。

```text
VoidCube/
├─ src/voidcube/                       # 唯一规范运行时代码包
│  ├─ domain/                          # 纯领域模型和跨层协议，不访问网络/终端/文件
│  │  ├─ agent/                        # 回合、上下文、消息、模型选择的领域规则
│  │  ├─ session/                      # 会话、历史、身份、目标
│  │  ├─ execution/                    # 工具调用、审批、执行状态、事件
│  │  └─ contracts/                    # ports、events、artifacts 等稳定协议
│  ├─ application/                     # 用例编排；依赖 domain，不依赖 CLI/UI
│  │  ├─ chat/                         # 单轮、连续对话、流式回合
│  │  ├─ autonomous/                   # daily_companion / auto_evolution
│  │  ├─ scheduling/                   # turn/task 调度
│  │  └─ sessions/                     # 新建、恢复、分支、标题、回滚
│  ├─ runtime/                         # 进程级运行时组装和生命周期
│  │  ├─ agent/                        # API-A agent executor
│  │  ├─ supervisor/                   # API-B/supervisor worker
│  │  └─ bootstrap.py                  # 唯一依赖注入和启动组装点
│  ├─ infrastructure/                  # 外部世界适配器
│  │  ├─ providers/                    # OpenAI-compatible、模型和鉴权
│  │  ├─ persistence/                 # session DB、文件、日志、缓存
│  │  ├─ memory/                       # MemAI/Mem 插件适配器
│  │  ├─ gateway/                      # 服务发现、HTTP、活动/任务泳道
│  │  ├─ execution/                    # terminal/browser/container 等执行后端
│  │  └─ config/                       # 配置文件、环境变量、profile
│  ├─ interfaces/                      # 面向用户或外部系统的入口
│  │  ├─ cli/                          # parser、command、TUI、renderer
│  │  ├─ http/                         # FastAPI/health/admin API
│  │  ├─ voice/                        # 录音、STT、TTS、声纹适配
│  │  └─ desktop/                      # Electron 只保留前端壳和协议
│  ├─ systems/                         # 可独立启停的产品系统
│  │  ├─ supervisor/                   # 伴侣观察、治理、投影
│  │  ├─ evolution/                    # authoring/candidate/evaluation
│  │  ├─ research/                     # research_knowledge
│  │  └─ voice/                        # 若需要独立服务则从 interfaces/voice 拆出
│  └─ extensions/                      # 可发现、可替换、可禁用的扩展
│     ├─ tools/                        # 工具定义 + backend + policy
│     ├─ skills/                       # SKILL.md 和技能元数据（内容优先）
│     └─ plugins/                      # plugin manifest、hooks、注册器
├─ packages/memai/                     # 独立记忆产品/库，保持自己的测试和发布边界
├─ desktop/                            # Electron 工程；不放 Python 业务代码
├─ tests/                              # 跨层集成和入口契约测试
├─ scripts/                            # 一次性维护、构建、扫描脚本
├─ docs/                               # 架构、协议、运行手册
├─ voidcube.py                         # 极薄 launcher，仅调用 interfaces.cli
└─ pyproject.toml
```

`Mem` 不应成为 `voidcube` 内部的普通目录。它有自己的存储、索引、模型配置和测试，适合保持为 `packages/memai`（或独立仓库）；主应用只通过 `domain/contracts` 定义的 memory port 访问它。

## 3. 现有目录到目标目录的映射

| 当前目录/文件 | 目标归属 | 处理原则 |
|---|---|---|
| `VoidCube_core/constants.py`, `time.py`, `redaction.py`, `logging.py` | `infrastructure/persistence`、`infrastructure/config` 或 `shared` | 按职责拆分；禁止继续增加新的 `core` 杂项 |
| `VoidCube_core/state.py` | `infrastructure/persistence/session_db.py` | 它是存储实现，不是核心领域 |
| `VoidCube_app/contracts/*`、`interaction_contract.py`、`tool_events.py`、`turn_contract.py` | `domain/contracts` | scheduler、turn、event、tool、execution、interaction 和 artifact 协议均已迁移；旧模块仅为兼容 alias |
| `agent/memory_provider.py` | `domain/contracts/memory.py` | Memory provider port 已迁移；Mem 插件只实现 canonical port，旧模块仅为 alias |
| `VoidCube_app/application.py`、`session_lifecycle.py`、`turn_scheduler.py` | `application/*` | shared application runtime 已迁移为 `application/application_runtime.py`；turn scheduler 已迁移为 `application/scheduling`；其余应用用例不应依赖 TUI/CLI |
| `VoidCube_app/configuration.py` | `application/configuration.py` | 进程级 application config snapshot 已迁移；旧模块仅为兼容 facade |
| `VoidCube_app/companion_workers.py` | `application/companion_workers.py` | Companion worker role/route rules 已迁移；旧模块仅为兼容 facade |
| `agent/memory_manager.py` | `application/memory_manager.py` | Mem provider selection、lifecycle and turn orchestration 已迁移；旧模块仅为 alias |
| `src/voidcube/application/session_title.py` | `application` | Session title generation and first-turn persistence orchestration；旧 `agent.title_generator` 仅模块 alias |
| `VoidCube_app/default_identity.py` | `domain/identity/defaults.py` | Persistent identity defaults 已迁移；旧模块仅为兼容 facade |
| `VoidCube_app/use_cases/sessions.py` | `src/voidcube/application/sessions.py` | Session 新建、恢复、分支、标题和历史变更用例；旧 `use_cases/sessions.py` 仅为兼容 facade |
| `VoidCube_app/config.py`、`provider_auth.py`、`runtime_provider.py` | `infrastructure/config`、`infrastructure/providers` | `config.py` 已迁移为 `src/voidcube/infrastructure/config/configuration.py`；旧模块只转发，配置与外部 Provider 适配器分离 |
| `VoidCube_app/infrastructure/config/provider_selection.py` | `infrastructure/config/provider_selection.py` | CLI 模型切换写入配置的唯一适配器；CLI 通过 callback 调用，不直接持有配置实现 |
| `src/voidcube/infrastructure/providers/registry.py`、`auth.py`、`runtime.py`、`endpoints.py` | `infrastructure/providers` | Provider 元数据、鉴权、endpoint 和 runtime resolution 的唯一规范实现；`VoidCube_app/infrastructure/providers/*` 仅模块对象兼容 facade |
| `VoidCube_app/models.py` | `infrastructure/providers/model_catalog.py` | Provider 模型目录、价格、tier 过滤和 `/models` 探测的唯一实现；旧模块仅为兼容 alias |
| `agent/model_metadata.py` | `infrastructure/providers/model_metadata.py` | 模型 context length、token estimation 和 endpoint metadata probing 的唯一实现；旧模块仅为模块 alias |
| `src/voidcube/infrastructure/providers/models_dev.py` | `infrastructure/providers` | models.dev 的轻量 `ModelInfo`/`ProviderInfo` 与查询协议；旧 `agent.models_dev` 仅模块 alias |
| `src/voidcube/application/model_routing.py` | `application` | cheap-vs-strong turn route 策略唯一实现；旧 `agent.smart_model_routing` 仅模块 alias |
| `src/voidcube/infrastructure/llm/response.py`、`tool_schema.py` | `infrastructure/llm` | Chat response normalization 与 function-tool schema normalization 的唯一实现；旧 `agent.api_response`、`agent.tool_schema` 仅模块 alias |
| `src/voidcube/infrastructure/llm/{stream_response,retry_policy}.py` | `infrastructure/llm` | 流式 response assembler 与 retry policy 的唯一实现；旧 `agent.stream_response`、`agent.retry_utils` 仅模块 alias |
| `src/voidcube/infrastructure/llm/transport_runtime.py` | `infrastructure/llm` | ChatTransport 的请求线程、流式传输、中断轮询和 provider fallback 唯一实现；旧 `agent.chat_transport` 仅模块 alias |
| `Mem/src/memai/redaction.py` | `packages/memai` | Mem 独立发布边界内的 Secret redaction 唯一实现；主应用通过 infrastructure facade 使用 |
| `src/voidcube/infrastructure/persistence/redaction.py` | `infrastructure/persistence` | Host 对 Mem redaction 的唯一适配实现；旧 `VoidCube_app/infrastructure/persistence/redaction.py` 仅为模块 alias |
| `VoidCube_app/infrastructure/persistence/session_db.py` | `infrastructure/persistence/session_db.py` | SQLite session persistence；`VoidCube_core.state` 仅为兼容 facade |
| `agent/session_persistence.py` | `infrastructure/persistence/session_runtime.py` | Session transcript persistence and JSON mirror adapter；旧 `agent.session_persistence` 仅为模块 alias |
| `agent/action_journal.py` | `infrastructure/persistence/action_journal.py` | Durable side-effect intent/outcome/evidence journal 已迁移；旧模块仅为 alias |
| `src/voidcube/infrastructure/config/runtime_paths.py` | `infrastructure/config/runtime_paths.py` | 运行时 home、profile、cache、config、skills 和 subprocess 路径的唯一实现；旧 `VoidCube_app`/`VoidCube_core` 仅转发 |
| `src/voidcube/infrastructure/persistence/file_store.py` | `infrastructure/persistence/file_store.py` | 原子 JSON/YAML 写入与跨进程文件锁的唯一实现；旧路径仅转发 |
| `src/voidcube/infrastructure/persistence/checkpoint_manager.py` | `infrastructure/persistence` | 工作区 shadow-git checkpoint/rollback 持久化唯一实现；旧 `tools/checkpoint_manager.py` 仅模块 alias |
| `src/voidcube/infrastructure/shared/value_helpers.py` | `infrastructure/shared/value_helpers.py` | 环境变量、JSON、字符串和字典等无领域 helper；旧路径仅转发 |
| `src/voidcube/infrastructure/shared/clock.py` | `infrastructure/shared/clock.py` | 配置驱动的时区感知时钟；旧路径仅转发 |
| `src/voidcube/domain/tasks/runtime_profile.py` | `domain/tasks/runtime_profile.py` | runtime task family/governance/execution profile 规范化唯一实现；旧 `systems.runtime_task_profile` 仅模块 alias |
| `src/voidcube/infrastructure/observability/logging.py` | `infrastructure/observability/logging.py` | 日志配置、会话上下文和脱敏 handler；旧路径仅转发 |
| `src/voidcube/infrastructure/runtime/environment.py` | `infrastructure/runtime/environment.py` | Termux、WSL、容器探测；旧路径仅转发 |
| `VoidCube_app/environment.py` | `infrastructure/config/environment.py` | `.env` 加载和 placeholder 过滤已迁移；旧模块仅为兼容 facade |
| `src/voidcube/infrastructure/runtime/layout.py` | `infrastructure/runtime/layout.py` | canonical/legacy runtime data layout；旧路径仅转发 |
| `src/voidcube/infrastructure/providers/endpoints.py` | `infrastructure/providers/endpoints.py` | Provider endpoint 常量；旧路径仅转发 |
| `src/voidcube/infrastructure/network.py` | `infrastructure/network.py` | 显式 IPv4 preference patch；旧路径仅转发 |
| `agent/{effect_outcomes,iteration_control,conversation_turn,conversation_runtime,response_disposition,message_sanitizer,context_references,api_attempt,manual_compression_feedback,tool_scheduler,context_engine}.py` | `domain/agent` | 单回合状态、效果结果、迭代预算、API attempt、响应处置、工具调度、context engine port 和输入清洗已迁移；旧 `agent.*` 仅为模块 alias |
| `agent/{tool_turn,turn_finalization}.py` | `runtime/agent` | tool-turn、turn finalization 和执行期状态编排已迁移；旧 `agent.*` 仅为模块 alias |
| `src/voidcube/runtime/agent/tool_execution.py` | `runtime/agent` | ToolExecutionCoordinator、准备/分类/取消结果的唯一实现；旧 `agent.tool_execution` 仅模块 alias |
| `agent/{client_lifecycle,client_initialization,session_initialization}.py` | `runtime/agent` | Agent client/session bootstrap、生命周期和资源 ownership 已迁移；旧 `agent.*` 仅为模块 alias |
| `agent/context_compressor.py` | `runtime/agent/context_compressor.py` | Context compression execution runtime 已迁移；旧模块仅为 alias |
| `agent/stream_handler.py` | `runtime/agent/stream_handler.py` | Safe stdio and stream output handling 已迁移；旧模块仅为 alias |
| `agent/subdirectory_hints.py` | `runtime/agent/subdirectory_hints.py` | Progressive context-file discovery runtime 已迁移；旧模块仅为 alias |
| `src/voidcube/runtime/agent/prompt_builder.py` | `runtime/agent` | 系统提示、项目上下文、技能索引和环境提示组装唯一实现；旧 `agent.prompt_builder` 仅模块 alias |
| `agent/*`（其余运行时模块） | `runtime/agent` + `infrastructure/llm` | `models`、状态和策略进 domain；HTTP/重试/流传输进 runtime/infrastructure，按依赖边界继续迁移 |
| `src/voidcube/interfaces/cli/application.py` | `interfaces/cli/application.py` | CLI 交互 host 的唯一真实实现；旧 `VoidCube_cli/app.py` 仅模块对象 facade |
| `VoidCube_cli/launcher.py` | `interfaces/cli/launcher.py` | 唯一真实的启动编排：参数路由、worktree、daemon policy、单轮/交互模式 |
| `src/voidcube/interfaces/cli/session_runtime.py` | `interfaces/cli/session_runtime.py` | 组装 session browser/history runtime；`VoidCube_cli/session_display_adapter.py` 仅模块 alias |
| `src/voidcube/interfaces/cli/provider_runtime.py` | `interfaces/cli/provider_runtime.py` | Provider/model picker、模型切换结果和 CLI 会话状态适配；旧 `VoidCube_cli/provider_runtime.py` 仅模块 alias |
| `src/voidcube/interfaces/cli/{model_picker,history_display,session_browser,application}_runtime.py` | `interfaces/cli/*_runtime.py` | model picker、history/session presentation 和 prompt-toolkit application wait loop 的唯一实现；旧 `VoidCube_cli/cli_*_runtime.py` 仅模块 alias |
| `src/voidcube/interfaces/cli/lifecycle/*` | `interfaces/cli/lifecycle` | guard、idle maintenance、input/refresh loop、lifecycle assembly/runtime 的唯一实现；旧 lifecycle runtime 路径仅模块 alias |
| `src/voidcube/interfaces/cli/{session_resume,single_query_resume,session_lifecycle}.py`、`turn/{input_preparation,agent_executor_runtime}.py` | `interfaces/cli/session*`、`interfaces/cli/turn/*` | session hydration、turn input preparation 和 scheduler agent executor 适配已迁移；旧路径仅模块 alias |
| `src/voidcube/interfaces/cli/{status_bar,middle_status,subagent_observability,status_snapshot,git_status,background_response,command_availability,voice_status,exit_summary,btw,dynamic_command,turn_agent_route}_runtime.py` | `interfaces/cli/*_runtime.py` | CLI status/command/route projections 的唯一实现；旧 `cli_*_runtime.py` 仅模块 alias |
| `src/voidcube/interfaces/cli/{runtime_credentials,agent_initialization,chat_error,chat_finalization,pending_input,session_teardown,voice_recording}_runtime.py` | `interfaces/cli/*` | application host 的 credential、agent、turn presentation、pending input、teardown 和 voice ports 已迁移；旧路径仅模块 alias |
| `src/voidcube/interfaces/cli/tui/{application,dynamic_text,prompt_runtime,layout_metrics_runtime,teardown}.py` | `interfaces/cli/tui` | TUI application、动态文本、prompt/layout metrics 和 teardown 的唯一实现；旧 TUI 路径仅模块 alias |
| `src/voidcube/interfaces/cli/tui/composition.py` | `interfaces/cli/tui/composition.py` | 从 CLI host ports 组装 prompt、modal、indicator 和扩展 widget；旧 assembly runtime 仅模块 alias |
| `VoidCube_cli/turn*_runtime.py`, `cli_agent_turn*_runtime.py` | `interfaces/cli/turn/*` | 单轮执行、scheduler adapter、结果应用和 agent turn composition；旧路径仅模块 alias |
| `VoidCube_cli/chat_*.py` | `interfaces/cli/chat/*` | chat block、流式处理/渲染和 response adapter；旧路径仅模块 alias |
| `VoidCube_cli/entrypoints/*`、`command_handlers/*` | `interfaces/cli/entrypoints`、`interfaces/cli/commands` | parser、dispatch、session、management、provider entrypoints 以及 27 个 command handlers 已迁移；旧路径仅为 alias，技能端口依赖 canonical extensions |
| `VoidCube_cli/i18n.py` | `interfaces/cli/i18n.py` | 国际化 manager、locale 选择和翻译缓存已迁移；旧模块仅为 alias，locale JSON 资产在迁移期间保持原包数据边界 |
| `VoidCube_cli/config_validator.py` | `interfaces/cli/config_validator.py` | 配置与运行时诊断已迁移；canonical operations 直接依赖规范模块，旧路径仅为 alias |
| `VoidCube_cli/config_commands.py` | `interfaces/cli/config_commands.py` | 配置展示、编辑、写入和命令 dispatch 已迁移；旧路径仅为 alias |
| `VoidCube_cli/providers.py`、`model_switch.py`、`model_normalize.py` | `interfaces/cli/providers.py`、`model_switch.py`、`model_normalize.py` | Provider identity overlay、模型切换 pipeline 和 CLI model normalization 已迁移；旧路径仅为 alias |
| `VoidCube_app/model_normalization.py` | `infrastructure/providers/model_normalization.py` | Provider-neutral model identifier handling 已迁移；旧模块仅为 alias |
| `VoidCube_cli/auth.py`、`status.py` | `interfaces/cli/auth.py`、`status.py` | Provider login/logout 与 CLI status presentation 已迁移；旧路径仅为 alias |
| `VoidCube_cli/commands.py`、`command_router.py`、`command_execution.py` | `interfaces/cli/commands/catalog.py`、`router.py`、`execution.py` | slash command catalog、解析和 builtin execution 已迁移；旧路径仅为 alias |
| `VoidCube_cli/session_command_adapter.py` | `interfaces/cli/session_command_adapter.py` | Session resume/branch 的纯 CLI projection 已迁移；旧模块仅为 alias |
| `VoidCube_cli/platforms.py`、`colors.py`、`cli_output.py` | `interfaces/cli/platforms.py`、`colors.py`、`cli_output.py` | CLI platform metadata、ANSI styling 和 shared output helpers 已迁移；旧路径仅为 alias |
| `VoidCube_cli/attachments.py`、`interaction_adapter.py`、`tool_event_adapter.py`、`cli_tool_progress.py` | `interfaces/cli/{attachments,interaction_adapter,tool_event_adapter,cli_tool_progress}.py` | CLI 输入附件、交互请求、工具事件和进度投影已迁移；旧路径仅为 alias |
| `VoidCube_cli/cli_handlers.py` | `interfaces/cli/runtime_handlers.py` | worktree 生命周期、进程通知和改进 diff 适配已迁移；旧模块仅为 alias |
| `VoidCube_cli/banner.py`、`cli_ui.py`、`clear_command_adapter.py` | `interfaces/cli/{banner,cli_ui,clear_command_adapter}.py` | Banner、ChatConsole 和 clear projection 已迁移；旧路径仅为 alias |
| `agent/display.py`、`agent/subagent_display.py` | `interfaces/cli/{display,subagent_display}.py` | 工具预览、差异展示、spinner 和子 Agent 生命周期展示已迁移；旧 `agent.*` 仅模块 alias |
| `VoidCube_cli/tui_*`、`chat_*`、`display*` | `interfaces/cli/tui`、`interfaces/cli/chat` | 仅负责展示和输入，不承载业务规则；chat block/stream 已迁移至 canonical chat 包 |
| `VoidCube_cli/ops/*` | `interfaces/cli/ops` | CLI 到 gateway/system 的薄适配器 |
| `src/voidcube/infrastructure/gateway/executor.py` | `infrastructure/gateway/executor.py` | ExecutorOpsClient 和 gateway URL 的唯一实现；旧 `VoidCube_cli/ops/executor.py` 仅模块 alias |
| `systems/gateway/agent_adapter.py` | `infrastructure/gateway/agent_adapter.py` | GatewayAgentAdapter、AgentProxy 和 gateway health/query 协议的唯一实现；旧模块仅为 alias |
| `tools/managed_tool_gateway.py` | `infrastructure/gateway/managed_tool_gateway.py` | Managed tool gateway adapter 已归入 gateway；旧模块仅为 alias |
| `src/voidcube/infrastructure/execution/process_registry.py` | `infrastructure/execution` | 后台进程 session、输出 spool、持久化 registry 和 process tool 的唯一实现；旧 `tools.process_registry` 仅模块 alias |
| `src/voidcube/infrastructure/execution/{code_execution_tool,process_spool_wrapper}.py` | `infrastructure/execution` | sandbox code execution 与 detached output spool 的唯一实现；旧 `tools.*` 仅模块 alias |
| `VoidCube_cli/daemon_runtime.py` | `infrastructure/gateway` | Gateway/Memory/Supervisor 的启动、停止和进程所有权状态；不依赖 TUI |
| `VoidCube_app/autonomous_execution_runtime.py` | `application/autonomous/execution_runtime.py` | UI-independent autonomous loop/stop ports；旧模块仅兼容转发 |
| `VoidCube_cli/autonomous_{events,observation,panel,status_host,presence,runtime_host,execution_host,execution_output}.py` | `interfaces/cli/autonomous/*` | CLI autonomous host、观测、面板和事件适配器；旧模块仅模块 alias，执行器仍归 `systems/supervisor` |
| `VoidCube_app/voice_session_runtime.py` | `interfaces/voice/session_runtime.py` | 独立 event-loop 的 voice manager adapter；旧模块仅兼容转发 |
| `systems/voice/*` | `src/voidcube/systems/voice/*` | 录音、声纹、STT/TTS、VAD、唤醒和会话 runtime 的唯一系统实现；旧 `systems.voice.*` 仅模块 alias |
| `src/voidcube/extensions/tools/registry.py`、`backend_helpers.py` | `extensions/tools` | 工具注册协议与 backend policy helper 唯一规范实现；`tools/registry.py`、`tools/tool_backend_helpers.py` 仅 facade |
| `src/voidcube/infrastructure/execution/{task_execution,terminal_tool,path_runtime,path_utils,interrupt,approval,tirith_security,podman_sandbox}.py`、`execution/environments/*` | `infrastructure/execution` | 严格任务契约、终端分发、跨平台路径、审批/安全扫描和 local/container/remote backend 的唯一实现；旧 `tools.*` 仅为兼容 alias |
| `src/voidcube/infrastructure/execution/{ansi_strip,credential_files,env_passthrough,windows_host_executor}.py` | `infrastructure/execution` | ANSI 输出清理、凭据/技能挂载、沙箱环境透传和 Windows host execution 的唯一实现；旧 `tools.*` 仅为兼容 alias |
| `src/voidcube/infrastructure/execution/task_environment.py` | `infrastructure/execution` | Autonomous task environment/worktree lifecycle adapter；只依赖 canonical terminal backend |
| `src/voidcube/extensions/tools/{registry,toolsets,backend_helpers,configuration,provider_configuration,token_estimation}.py` | `extensions/tools` | 工具协议、工具集目录和 policy 的唯一实现；旧 `tools.*` 仅为兼容 alias |
| `src/voidcube/extensions/tools/files/*` | `extensions/tools/files` | file read/write/search/patch、fuzzy matching、binary and path security policy 的唯一实现；旧 `tools/file_*.py`、`fuzzy_match.py`、`patch_parser.py`、`path_security.py`、`binary_extensions.py` 仅模块 alias |
| `src/voidcube/extensions/tools/browser/*` | `extensions/tools/browser` | browser tool、Camofox state/backend 与 Browserbase/Browser Use/Firecrawl adapters 唯一实现；旧 `tools/browser*.py`、`tools/browser_providers/*` 仅模块 alias |
| `src/voidcube/extensions/tools/web/*` | `extensions/tools/web` | web search/extract/crawl、local backend、URL safety 和 website policy 的唯一实现；旧 `tools/web_tools*.py`、`website_policy.py`、`url_safety.py` 仅模块 alias |
| `src/voidcube/extensions/tools/media/*` | `extensions/tools/media` | media playback/delivery、Agnes image/video generation 和 vision analysis 的唯一实现；旧 `tools/media_tool.py`、`media_generation_tool.py`、`vision_tools.py` 仅模块 alias |
| `src/voidcube/extensions/tools/mcp/*` | `extensions/tools/mcp` | MCP stdio/HTTP client、dynamic tool registration 和 OAuth/PKCE token flow 的唯一实现；旧 `tools/mcp_tool.py`、`mcp_oauth.py` 仅模块 alias |
| `src/voidcube/extensions/tools/osv_check.py` | `extensions/tools` | MCP 外部包的 OSV malware advisory 检查唯一实现；旧 `tools/osv_check.py` 仅模块 alias |
| `src/voidcube/extensions/tools/preset_engine.py`、`presets/*` | `extensions/tools` | 只读部署 preset catalog 唯一实现；旧 `tools/preset_engine.py` 仅模块 alias |
| `src/voidcube/extensions/tools/{model_tools,clarify_tool,todo_tool,session_search_tool,scheduled_task_tool}.py` | `extensions/tools` | 工具发现、dispatch、clarification、todo、session search 和 scheduled-task tool 的唯一实现；旧 `tools.*` 仅模块 alias |
| `src/voidcube/extensions/tools/{delegate_tool,mixture_of_agents_tool,ops_register,dependency_checker}.py` | `extensions/tools` | delegation、MoA、系统运维和 dependency bootstrap 工具的唯一实现；旧 `tools.*` 仅模块 alias |
| `src/voidcube/infrastructure/providers/openrouter_client.py` | `infrastructure/providers` | OpenRouter client helper 唯一实现；旧 `tools/openrouter_client.py` 仅模块 alias |
| `VoidCube_cli/tools_config.py`、`mcp_config.py` | `interfaces/cli/tools_config.py`、`mcp_config.py` | toolset/MCP 配置交互、探测和持久化入口已迁移；旧路径仅为 alias |
| `src/voidcube/extensions/skills/catalog.py` | `extensions/skills` | 技能 frontmatter、目录发现和外部目录协议唯一实现；`agent/skill_utils.py` 仅模块对象 facade |
| `src/voidcube/extensions/skills/{commands,tool}.py` | `extensions/skills` | `/skill`、`/plan` 命令、技能发现、查看、配置和工具注册唯一实现；旧 `agent.skill_commands`、`tools.skills_tool` 仅模块 alias |
| `src/voidcube/extensions/skills/manager.py` | `extensions/skills` | Agent-created skill create/edit/patch/delete/write operations and security gate 唯一实现；旧 `tools.skill_manager_tool` 仅模块 alias |
| `tools/skills_guard.py`、`agent/integration_policy.py` | `extensions/skills/guard.py`、`domain/contracts/integration_policy.py` | 技能安全扫描和退役集成策略已迁移；旧路径仅为兼容 facade |
| `skills/*` | `extensions/skills` | 技能内容保持 Markdown 优先并独立同步，不混入 Python 运行时代码 |
| `src/voidcube/extensions/plugins/*` | `extensions/plugins` | manifest、生命周期和 hook 协议的唯一规范实现；`plugins/manifest.py`、`VoidCube_app/plugins.py` 仅 facade |
| `VoidCube_cli/plugins.py` | `extensions/plugins/cli_adapter.py` | CLI plugin discovery/toolset/command adapter 已迁移；旧模块仅为 alias |
| `systems/*` | `systems/*` | 以独立系统/服务为单位，系统内部再按 domain/application/adapters 分层 |
| `VoidCube_cli/autonomous_executor.py` | `systems/supervisor/autonomous_executor.py` | Autonomous task prompt/run/completion runtime 已迁移；旧模块仅为兼容 facade |
| `systems/supervisor/scheduled_tasks.py` | `src/voidcube/systems/supervisor/scheduled_tasks.py` | ScheduledTaskStore、claim/lease/writeback 数据边界的唯一实现；旧模块仅为模块 alias |
| `systems/supervisor/config_models.py` | `src/voidcube/systems/supervisor/config_models.py` | Supervisor、body、endogenous 和 UI 配置模型的唯一实现；旧模块仅为模块 alias |
| `systems/supervisor/autonomous_chain_store.py`、Supervisor planning/service/UI/runtime 及 endogenous/evolution modules | `src/voidcube/systems/supervisor/*` | Supervisor 组合根、planning/service runtime、UI projections、autonomous chain services 和 endogenous/evolution rules 已迁移；旧模块仅为模块 alias |
| `src/voidcube/infrastructure/config/system.py` | `infrastructure/config` | Gateway/Agent/System 配置模型与环境加载唯一实现；旧 `systems.config` 仅模块 alias |
| `src/voidcube/systems/{evolution_boundary,body_runtime_migration,governance_runtime_migration,mem_source_binding}.py` | `systems` | Evolution boundary、body runtime、governance migration 与 Mem source binding 唯一实现；旧 `systems/*` 路径仅模块 alias |
| `src/voidcube/systems/{body_registry,governor,lifecycle,probe}.py` | `systems` | Body registry、governor policy、lifecycle executor 和 probe runner 唯一实现；旧顶层 `systems.*` 仅模块 alias |
| `src/voidcube/systems/execution/*` | `systems/execution` | execution adapters、facade、route hints 和 service 唯一实现；旧 `systems.execution.*` 仅模块 alias |
| `src/voidcube/systems/{evolution_authoring,evolution_candidate_generation,evolution_evaluation,research_knowledge,self_cognition}/*` | `systems` | 演化 authoring、candidate/evaluation、研究知识和 self-cognition 的唯一实现；旧子包仅模块 facade/alias |
| `src/voidcube/infrastructure/memory/{governor_bridge,host_integration}.py` | `infrastructure/memory` | Mem governance audit 与 host callback 适配唯一实现；旧 `plugins.memory.mem.*` 仅模块 alias |
| `VoidCube_app/application.py` | `src/voidcube/application/application_runtime.py` | shared session/turn state、event sink 和 application lifecycle 的唯一实现；旧模块仅为模块 alias |
| `VoidCube_app/contracts/*`、`interaction_contract.py`、`tool_events.py`、`turn_contract.py` | `src/voidcube/domain/contracts/*` | UI-independent artifact、execution、event、interaction、turn、tool 和 port 协议唯一实现；旧路径仅为 alias/re-export |
| `systems/supervisor/account_store.py` | `src/voidcube/systems/supervisor/account_store.py` | 平台账号、Cookie 解析/验证和脱敏摘要的唯一实现；旧模块仅为模块 alias |
| `systems/supervisor/provider_pool_service.py` | `src/voidcube/systems/supervisor/provider_pool_service.py` | Provider pool、员工角色分配和模型目录探测的唯一实现；旧模块仅为模块 alias |
| `systems/supervisor/endogenous_state_repository.py` | `src/voidcube/systems/supervisor/endogenous_state_repository.py` | endogenous 状态快照的文件边界和原子读写唯一实现；旧模块仅为模块 alias |
| `systems/supervisor/ui_{activity,delivery_state,media_state}_adapters.py` | `src/voidcube/systems/supervisor/ui_{activity,delivery_state,media_state}_adapters.py` | Supervisor UI 活动、交付和媒体状态持久化适配器唯一实现；旧模块仅为模块 alias |
| `src/voidcube/interfaces/cli/launcher.py` | `interfaces/cli/launcher` | 规范包中的唯一公开 launcher 实现 |
| `voidcube.py`, `cli.py`, `run_agent.py` | `interfaces` / `runtime` 入口 | `src/voidcube/interfaces/cli/root_launcher.py` 持有统一 daemon/CLI 启动编排；根文件只保留兼容转发且不进入 wheel |

## 4. 依赖规则

依赖方向固定为：

```text
interfaces  ->  application  ->  domain
runtime     ->  application  ->  domain
infrastructure  ->  domain contracts
systems     ->  application + domain + infrastructure ports
extensions  ->  domain contracts（由 bootstrap 注册）
```

硬规则：

1. `domain` 不得导入 `VoidCube_cli`、`rich`、`httpx`、数据库驱动或操作系统执行器。
2. `application` 不得导入 TUI、终端颜色、CLI parser；需要外部能力时只接受 port/protocol。
3. `interfaces` 不直接读取数据库或环境变量，所有状态通过 application/infrastructure service 获取。
4. `tools` 不反向调用 CLI；工具事件通过 `domain.contracts.events` 发布。
5. `systems` 之间不互相深度导入，通过事件、port 或 gateway 通信。
6. 每个目录只能有一个公开组装点：`runtime/bootstrap.py`、`interfaces/cli/application.py`、各 system 的 `composition.py`。

## 5. 名称决策

- `VoidCube_core`：**退役名称**。先停止新增文件，再按职责迁移；不建立新的 `core.utils`。
- `VoidCube_app`：**过渡名称**。它不是最终的“应用大杂烩”；其中的 contracts、use cases、providers、config 必须拆开。
- `VoidCube_cli`：**过渡名称**。最终含义只应是命令行接口；agent、scheduler、autonomous 的业务实现移出。当前阶段的命令入口实现位于 `VoidCube_cli/entrypoints`；已有的 `VoidCube_cli/commands.py` 是动态斜杠命令注册模块，不与入口层混用。
- Provider 配置写入：`VoidCube_cli/provider_runtime.py` 只负责 CLI 状态和用户反馈；持久化由 `VoidCube_app/infrastructure/config/provider_selection.py` 提供，禁止在 CLI 适配器中重新内联 `load_config`/`save_config` 流程。
- `agent`：保留为内部语义，但最终放在 `voidcube/domain/agent` 和 `voidcube/runtime/agent`，避免成为顶层第二个产品包。
- `tools`、`skills`、`plugins`：统一放在 `extensions` 语义下；其中执行后端仍属于 infrastructure。

## 6. 分阶段迁移

### 阶段 0：冻结边界（当前即可做）

- 把本文件作为架构基线。
- 新代码禁止使用 `VoidCube_core`；禁止在 `VoidCube_cli.app` 增加业务逻辑。
- 在代码审查中检查上述依赖规则。
- 记录公开入口：`voidcube:main`、`vc:main`，其余根级脚本标为内部入口。

### 阶段 1：低风险建立语义子包

- 在现有包内建立 `VoidCube_app/contracts`、`VoidCube_app/use_cases`、`VoidCube_app/adapters`、`VoidCube_cli/entrypoints`、`VoidCube_cli/tui` 等目录。
- 只做移动和导入更新，每组移动后运行对应测试。
- 旧模块只保留一行显式转发，并标注删除版本；不要保留两套实现。

### 阶段 2：拆分最大文件

- 优先拆 `VoidCube_cli/app.py`（当前约 4700 行）：启动、交互循环、会话、自治、展示分别移出。
- 再拆 `VoidCube_cli/api_config.py`、`tools_config.py` 和 Provider/CLI 的剩余组装层；auxiliary client 已迁移后只继续收敛 provider lookup。
- 每次拆分新增针对 use case 的测试，而不是继续增加 facade 的分支。

### 当前迁移状态

| 边界 | 当前真实实现 | 旧路径状态 | 下一步 |
|---|---|---|---|
| CLI 入口 | `src/voidcube/interfaces/cli/entrypoints/*`, `launcher.py` | `VoidCube_cli/entrypoints/*`、`entrypoint_*.py` 仅兼容转发 | 统一公开 launcher 后删除旧别名 |
| daemon 生命周期 | `src/voidcube/infrastructure/gateway/daemon_runtime.py` | `VoidCube_cli/daemon_runtime.py` 仅为兼容 facade | daemon ownership 已迁移；服务启动/停止由 gateway service launcher 提供 |
| Gateway service launcher | `src/voidcube/infrastructure/gateway/service_launcher.py`, `presence.py` | `VoidCube_cli/ops/serve.py`、`VoidCube_app/gateway.py` 仅为兼容 facade | service start/stop/health 和 presence client 已迁移 |
| 启动编排 | `src/voidcube/interfaces/cli/launcher.py` | `VoidCube_cli/launcher.py` 兼容 facade；`app.main()` 仅 facade | 迁移其余 interfaces/runtime 模块 |
| CLI application host | `src/voidcube/interfaces/cli/application.py` | `VoidCube_cli/app.py` 模块对象兼容 facade | autonomous、voice、lifecycle、chat/turn 和 scheduled runtime 已外移；application 保留 composition root |
| Autonomous execution loop | `src/voidcube/application/autonomous/execution_runtime.py`, `systems/supervisor/autonomous_executor.py`, `src/voidcube/interfaces/cli/autonomous/*` | `VoidCube_app/autonomous_execution_runtime.py`、`VoidCube_cli/autonomous_executor.py` 与 autonomous host/panel 旧模块兼容 facade | loop/stop、autonomous task executor、CLI host/panel/observation 适配均已迁移 |
| Voice session runtime | `src/voidcube/interfaces/voice/session_runtime.py`, `src/voidcube/systems/voice/*` | `VoidCube_app/voice_session_runtime.py`、`systems.voice.*` 兼容 facade/alias | voice manager bridge 和系统级 voice adapters 已独立打包；主应用只通过 voice ports 组装 |
| session 展示 | `src/voidcube/interfaces/cli/session_runtime.py` | `VoidCube_cli/session_display_adapter.py` 模块 alias | 已迁移并与 session application port 对齐 |
| Provider/model CLI 交互 | `src/voidcube/interfaces/cli/provider_runtime.py` | `VoidCube_cli/provider_runtime.py` 模块 alias | 已迁移；持久化仍通过 infrastructure/config 服务 |
| Provider 配置服务 | `src/voidcube/infrastructure/config/provider_config.py`, `configuration.py` | `VoidCube_cli/api_config.py` 仅为兼容 facade | 配置服务、图像/视频路由写入已迁移 |
| CLI 配置向导接口 | `src/voidcube/interfaces/cli/configuration.py` | `VoidCube_cli/api_config.py` 仅为兼容 facade | 向导交互、摘要和 Provider 配置入口已迁移 |
| TUI 组装 | `src/voidcube/interfaces/cli/tui/composition.py` | `VoidCube_cli/cli_interactive_tui_assembly_runtime.py` 模块 alias | assembly 已迁移；lifecycle/state 组装也已迁移，剩余 chat/turn adapter 按边界收敛 |
| CLI lifecycle/runtime adapters | `src/voidcube/interfaces/cli/lifecycle/{assembly,runtime,preflight,registration,state,run,startup}.py` | 对应 `VoidCube_cli/cli_*_runtime.py` 仅模块 alias | interactive lifecycle、run-state、startup 和 registration 组装已迁移 |
| CLI chat/turn adapters | `src/voidcube/interfaces/cli/chat/*`, `src/voidcube/interfaces/cli/turn/*` | 对应 `VoidCube_cli/chat_*.py`、`turn*_runtime.py` 仅模块 alias | chat block/stream、turn execution/postprocessing/result/scheduler 和 agent turn adapters 已迁移 |
| Provider 注册、鉴权与 runtime resolution | `src/voidcube/infrastructure/providers/registry.py`, `auth.py`, `runtime.py`, `endpoints.py` | `VoidCube_app/infrastructure/providers/*`、`provider_auth.py`、`runtime_provider.py` 兼容 facade | Provider 基础边界已迁移；后续只收敛 CLI 配置/鉴权交互适配 |
| Provider model catalog/probing | `src/voidcube/infrastructure/providers/model_catalog.py` | `VoidCube_app/models.py` 模块 alias | provider:model 解析、价格/tier 过滤、模型目录探测和 fast-mode policy 已迁移 |
| Media generation provider routes | `src/voidcube/infrastructure/providers/media_generation.py` | `VoidCube_app/media_generation_provider.py` 仅为兼容 facade | image/video route defaults and credential checks 已迁移 |
| Auxiliary routing policy | `src/voidcube/infrastructure/providers/auxiliary_client.py`, `auxiliary_policy.py`, `client_factory.py`, `auxiliary_orchestration.py`, `auxiliary_execution.py`, `auxiliary_fallback.py`, `auxiliary_vision.py`, `auxiliary_vision_clients.py` | `agent/auxiliary_client.py` 仅为模块对象兼容 alias | provider resolution、vision 选择、sync/async client construction、fallback 和调用入口已迁移；后续仅收敛剩余 model normalization |
| Auxiliary client lifecycle | `src/voidcube/infrastructure/providers/auxiliary_client_cache.py` | `agent/auxiliary_client.py` 保留 `_client_cache` 等兼容别名 | 已迁移缓存、跨 event-loop 隔离和关闭清理 |
| Auxiliary response transport | `src/voidcube/infrastructure/llm/transport.py`, `request.py` | `agent/api_request.py` 兼容 facade；`agent/auxiliary_client.py` 为 provider facade | 已完成真实 ChatRequest protocol 和 client orchestration 迁移 |
| Chat transport runtime | `src/voidcube/infrastructure/llm/transport_runtime.py` | `agent/chat_transport.py` 仅模块 alias | ChatTransport 的 interruptible completion/stream、retry 和 fallback 已迁移 |
| LLM error classification | `src/voidcube/infrastructure/llm/error_classifier.py` | `agent/error_classifier.py` 仅为兼容 facade | 分类 taxonomy、重试提示和 stream-drop 判定已迁移 |
| Session 生命周期 | `src/voidcube/application/sessions.py`, `domain/session/identity.py` | `VoidCube_app/use_cases/sessions.py`、`session_identity.py` 仅为兼容 facade | 用例、身份规则和 shared application runtime 已迁移；继续收敛 repository ports |
| Session title generation | `src/voidcube/application/session_title.py` | `agent/title_generator.py` 仅模块 alias | 首轮会话标题生成与持久化已迁移到 application 层 |
| Turn scheduling contract/runtime | `src/voidcube/domain/contracts/scheduler.py`, `application/scheduling/turn_scheduler.py` | `VoidCube_app/contracts/scheduler.py`, `turn_scheduler.py` 仅为兼容 facade | 已迁移；scheduler 只负责 admission/lifecycle，不依赖 CLI 或模型 API |
| Scheduled task runtime adapters | `src/voidcube/application/scheduling/background_task_runtime.py`, `scheduled_execution_host.py`, `scheduled_task_polling.py`, `scheduled_executor.py` | `VoidCube_cli/*` 对应模块仅为兼容 facade | polling、background task state、lease/writeback 和 scheduled executor 已迁移；CLI 只组装 ports |
| Supervisor scheduled-task store/config | `src/voidcube/systems/supervisor/scheduled_tasks.py`, `config_models.py` | 对应 `systems/supervisor/*.py` 模块 alias | store、claim、lease、历史、迁移和配置模型已迁移 |
| Supervisor account store | `src/voidcube/systems/supervisor/account_store.py` | `systems/supervisor/account_store.py` 模块 alias | Cookie 解析、平台验证、账号持久化和 URL 匹配已迁移；工具调用点改用 canonical 路径 |
| Supervisor Provider pool | `src/voidcube/systems/supervisor/provider_pool_service.py` | `systems/supervisor/provider_pool_service.py` 模块 alias | Provider/员工角色配置、鉴权环境变量、模型目录和 canonical `extensions.tools.toolsets` 目录已迁移 |
| Supervisor endogenous state repository | `src/voidcube/systems/supervisor/endogenous_state_repository.py` | `systems/supervisor/endogenous_state_repository.py` 模块 alias | 状态路径解析、对象校验和原子 JSON 持久化已迁移 |
| Supervisor UI state adapters | `src/voidcube/systems/supervisor/ui_activity_adapters.py`, `ui_delivery_state_adapters.py`, `ui_media_state_adapters.py` | 对应 `systems/supervisor/*` 模块 alias | activity、delivery 和 media state 的读写适配器已迁移，UI runtime 仍作为后续 composition 边界处理 |
| Supervisor autonomous chain and composition | `src/voidcube/systems/supervisor/{autonomous_chain_*,planning_runtime,service_runtime,runtime_assemblers,ui_runtime,supervisor}.py` | `systems/supervisor/*.py` 模块 alias | task lifecycle、execution lease、Mem governance recovery、planning/service/UI composition 和 Supervisor HTTP host 均已迁移 |
| 规范包 launcher/application | `src/voidcube/interfaces/cli/launcher.py`, `application.py` | `VoidCube_cli/launcher.py`、`app.py` 兼容 facade | 迁移其余 interfaces/runtime 模块 |
| CLI main/startup/console | `src/voidcube/interfaces/cli/{main,entrypoint_startup,console_fix}.py` | `VoidCube_cli/main.py` 是不承载业务的薄 wrapper；`entrypoint_startup.py`、`console_fix.py` 为 alias | 规范 CLI main、profile/env startup 和 Windows console setup 已迁移 |
| Desktop control protocol | `src/voidcube/interfaces/desktop/desktop_control.py` | `VoidCube_cli/desktop_control.py` 仅 alias | Desktop shell 的 service lifecycle JSON protocol 已迁移 |
| Core redaction | `VoidCube_app/infrastructure/persistence/redaction.py` | `VoidCube_core.redaction` 兼容 facade | 已完成 |
| Session DB | `VoidCube_app/infrastructure/persistence/session_db.py` | `VoidCube_core.state` 兼容 facade | 已完成 |
| Core paths, file store, value helpers, clock, environment and runtime layout | `src/voidcube/infrastructure/config/runtime_paths.py`, `persistence/file_store.py`, `shared/value_helpers.py`, `shared/clock.py`, `runtime/environment.py`, `runtime/layout.py` | `VoidCube_app`、`VoidCube_core` 兼容 facade | 已完成 |
| Provider endpoints and network preference | `src/voidcube/infrastructure/providers/endpoints.py`, `infrastructure/network.py` | `VoidCube_app`、`VoidCube_core` 兼容 facade | 已完成 |
| Plugin manifest and manager | `src/voidcube/extensions/plugins/manifest.py`, `manager.py` | `plugins.manifest`、`VoidCube_app.plugins` 兼容 facade | 已完成 |
| CLI plugin adapter | `src/voidcube/extensions/plugins/cli_adapter.py` | `VoidCube_cli/plugins.py` 模块 alias | canonical CLI application, tools configuration and command registry use the canonical plugin manager adapter |
| Skill catalog and operations | `src/voidcube/extensions/skills/{catalog,models,sync,hub,guard,commands,tool}.py` | `agent.skill_utils`、`agent.skill_commands`、`tools/skills_tool.py`、`tools/skills_sync.py`、`tools/skills_hub.py`、`tools/skills_guard.py` 仅为兼容 facade | catalog、slash commands、skill list/view/config and security backend 已迁移；后续只收敛其惰性工具后端依赖 |
| Prompt assembly | `src/voidcube/runtime/agent/prompt_builder.py` | `agent/prompt_builder.py` 仅模块 alias | system prompt、项目上下文和 skills prompt 已迁移 |
| Skill command adapter | `src/voidcube/extensions/skills/commands.py` | `agent/skill_commands.py` 仅模块 alias | slash command 扫描、`/plan` 和技能消息构建已迁移；后续收敛 `tools.skills_tool` 操作后端 |
| Provider credentials and pool | `src/voidcube/infrastructure/providers/credentials.py`, `credential_pool.py` | `providers/auth.py` 保留认证状态与兼容私有别名；`agent/credential_pool.py` 仅为 module facade | API-key lookup、环境变量、auth store、pool runtime 和 config lookup 已迁移；后续收敛 Nous refresh 适配 |
| Provider rate-limit tracking | `src/voidcube/infrastructure/providers/rate_limit.py` | `agent/rate_limit_tracker.py` 仅模块 alias | Provider header capture、usage buckets 和 CLI display projection 已迁移 |
| Provider usage pricing | `src/voidcube/infrastructure/providers/usage_pricing.py` | `agent/usage_pricing.py` 仅模块 alias | Usage normalization、pricing routes and cost estimation 已迁移 |
| Provider model alias/credentials/usage | `src/voidcube/infrastructure/providers/{model_alias_resolver,credential_manager,usage_tracker}.py` | `VoidCube_cli/*` 仅模块 alias | CLI 遗留的 provider alias、凭据和用量状态已迁移 |
| SOUL configuration | `src/voidcube/infrastructure/config/soul_config.py` | `VoidCube_cli/soul_config.py` 仅模块 alias | SOUL frontmatter、personality 和 runtime config parsing 已迁移 |
| Atomic file writer | `src/voidcube/infrastructure/persistence/file_atomic_writer.py` | `VoidCube_cli/file_atomic_writer.py` 仅模块 alias | CLI 原子写入实现已归入 persistence |
| Toolset configuration policy | `src/voidcube/extensions/tools/configuration.py`, `provider_configuration.py`, `token_estimation.py`; `src/voidcube/interfaces/cli/tools_config.py`, `tools_mcp.py` | `VoidCube_cli/tools_config.py` 模块 alias | platform/provider policy、token estimation、MCP UI 和 CLI toolset wizard 已迁移；旧平台解析/保存实现已删除 |
| Background process registry | `src/voidcube/infrastructure/execution/process_registry.py` | `tools/process_registry.py` 仅模块 alias | process spawn/poll/wait/kill、output spool 和 task-scoped cleanup 已迁移 |
| Core logging | `VoidCube_app/infrastructure/observability/logging.py` | `VoidCube_core.logging` 兼容 facade | 已完成 |
| CLI command handlers and registry | `src/voidcube/interfaces/cli/commands/handlers/*`, `registry.py` | `VoidCube_cli/command_handlers/*` 模块 alias | 27 个 handler 与 registry 已迁移；后续只收敛剩余 handler 的 UI/application 依赖 |
| CLI internationalization | `src/voidcube/interfaces/cli/i18n.py` | `VoidCube_cli/i18n.py` 模块 alias | canonical CLI 入口、session、application 和 command registry 已改用规范 i18n 服务 |
| CLI configuration diagnostics | `src/voidcube/interfaces/cli/config_validator.py` | `VoidCube_cli/config_validator.py` 模块 alias | configuration validation and diagnosis now live in the canonical CLI interface |
| CLI configuration commands | `src/voidcube/interfaces/cli/config_commands.py` | `VoidCube_cli/config_commands.py` 模块 alias | operations entrypoint uses the canonical configuration command layer |
| CLI Provider/model switching | `src/voidcube/interfaces/cli/providers.py`, `model_switch.py`, `model_normalize.py` | `VoidCube_cli/providers.py`、`model_switch.py`、`model_normalize.py` 模块 alias | canonical command registry and provider runtime now use the migrated Provider/model pipeline |
| Provider model normalization | `src/voidcube/infrastructure/providers/model_normalization.py` | `VoidCube_app/model_normalization.py` 模块 alias | shared normalization no longer lives under the application compatibility package |
| CLI authentication and status | `src/voidcube/interfaces/cli/auth.py`, `status.py` | `VoidCube_cli/auth.py`, `status.py` 模块 alias | provider entrypoint now uses canonical authentication and status adapters |
| CLI toolset/MCP configuration | `src/voidcube/interfaces/cli/tools_config.py`, `mcp_config.py`, `tools_mcp.py` | `VoidCube_cli/tools_config.py`, `mcp_config.py` 模块 alias | management entrypoint, command registry and status use canonical tool/MCP configuration adapters |
| CLI command catalog/routing | `src/voidcube/interfaces/cli/commands/catalog.py`, `router.py`, `execution.py` | `VoidCube_cli/commands.py`, `command_router.py`, `command_execution.py` 模块 alias | handlers, registry and application host now consume canonical command protocol modules |
| CLI session command adapter | `src/voidcube/interfaces/cli/session_command_adapter.py` | `VoidCube_cli/session_command_adapter.py` 模块 alias | session handlers use canonical application session result types and CLI projection |
| CLI presentation primitives | `src/voidcube/interfaces/cli/platforms.py`, `colors.py`, `cli_output.py` | `VoidCube_cli/platforms.py`, `colors.py`, `cli_output.py` 模块 alias | canonical configuration/MCP/status/toolset modules no longer import these helpers from the legacy package |
| CLI input/event adapters | `src/voidcube/interfaces/cli/attachments.py`, `interaction_adapter.py`, `tool_event_adapter.py`, `cli_tool_progress.py` | 对应 `VoidCube_cli/*` 模块 alias | application host, launcher, registry and handlers use canonical adapter paths |
| CLI startup/worktree adapters | `src/voidcube/interfaces/cli/runtime_handlers.py` | `VoidCube_cli/cli_handlers.py` 模块 alias | launcher and application host use canonical worktree/process-notification adapter |
| CLI display/clear adapters | `src/voidcube/interfaces/cli/banner.py`, `cli_ui.py`, `clear_command_adapter.py` | 对应 `VoidCube_cli/*` 模块 alias | application, registry and chat renderer consume canonical display primitives |
| CLI tool/subagent display | `src/voidcube/interfaces/cli/display.py`, `subagent_display.py` | `agent/display.py`, `agent/subagent_display.py` 仅模块 alias | tool preview/diff/spinner 与 delegated subagent lifecycle 展示已迁移 |
| CLI runtime adapters | `src/voidcube/interfaces/cli/{application_runtime,model_picker_runtime,history_display_runtime,session_browser_runtime}.py`, `lifecycle/*`, `turn/*` | 对应 `VoidCube_cli/*_runtime.py` 模块 alias | application host 已改用 canonical lifecycle/session/turn/runtime ports |
| Gateway executor adapter | `src/voidcube/infrastructure/gateway/executor.py` | `VoidCube_cli/ops/executor.py` 模块 alias | canonical operations/status/autonomous surfaces use infrastructure gateway client |
| Agent domain turn contracts | `src/voidcube/domain/agent/{effect_outcomes,iteration_control,conversation_turn,conversation_runtime,response_disposition,message_sanitizer,context_references,api_attempt,manual_compression_feedback,tool_scheduler,context_engine}.py` | 对应 `agent/*` 仅模块 alias | 单回合状态、效果结果、预算、API attempt、响应处置、工具调度、context engine port 和上下文引用处理已迁移；其余 Agent runtime 按 provider/LLM 边界继续收敛 |
| Agent runtime turn orchestration | `src/voidcube/runtime/agent/{tool_turn,turn_finalization}.py` | 对应 `agent/*` 仅模块 alias | tool-turn、turn finalization 和执行期状态编排已迁移；其余 client/session runtime 按边界继续收敛 |
| Agent runtime bootstrap | `src/voidcube/runtime/agent/{client_lifecycle,client_initialization,session_initialization}.py` | 对应 `agent/*` 仅模块 alias | client/session bootstrap、lifecycle 和资源 ownership 已迁移；其余 Agent runtime 按 provider/LLM 边界继续收敛 |
| Session transcript persistence | `src/voidcube/infrastructure/persistence/session_runtime.py` | `agent/session_persistence.py` 仅模块 alias | SQLite transcript、JSON mirror and session log persistence 已迁移 |
| Execution backends and toolsets | `src/voidcube/infrastructure/execution/*`, `src/voidcube/extensions/tools/toolsets.py` | `tools/terminal_tool.py`、`task_execution.py`、`environments/*`、`toolsets.py` 仅兼容 alias | 终端、任务契约、环境 backend、路径与安全 guard 已迁移，规范包内部不再依赖旧入口 |

判定规则：表中“当前真实实现”才是新代码应依赖的主路径；旧路径即使暂时可导入，也不得继续添加业务分支。

### 阶段 3：统一规范包名

- 已建立 `src/voidcube/interfaces/cli`，公开 entry point 已指向规范包。
- 当前 launcher 已迁入 `src/voidcube/interfaces/cli/launcher.py`；`VoidCube_cli.launcher` 仅保留兼容 facade。
- `voidcube.py`、`cli.py` 仅保留兼容入口；root `voidcube.py` 已退出 wheel，待弃用周期后删除。
- `VoidCube_app`、`VoidCube_cli`、`VoidCube_core`、顶层 `agent`/`tools`/`systems` 的业务实现已收敛到规范包；旧路径只保留兼容 alias 或薄入口 wrapper。

### 兼容 facade 退役窗口

兼容路径只允许修复性变更，不再增加业务分支。已迁移边界统一以 `2.0.0` 为删除目标，计划在 **2026-12-31** 前删除对应 facade；尚未迁移的 supervisor 大型系统模块不适用此日期，须先完成 canonical implementation 后再登记删除日期。

### 阶段 4：插件化和可选服务

- tool/skill/plugin 已有 manifest 和版本化 protocol；`PluginManifest` 在加载 entrypoint 前完成校验。
- 扩展通过显式 registry/manager 注册，manifest discovery 不会隐式 import entrypoint。
- `MemAI`、voice、desktop、gateway 已按 optional dependency 与 infrastructure/interface ports 分离打包。

## 7. 判断文件放在哪里

问三个问题即可：

1. 没有网络、终端、数据库时仍能运行吗？能，放 `domain`。
2. 它是在编排一个用户用例吗？是，放 `application`。
3. 它是在适配具体外部技术吗？是，放 `infrastructure` 或 `interfaces`；若可插拔，再放 `extensions`。

不要按“文件名里有 runtime 就放 runtime”判断，`runtime` 只表示进程生命周期和组装；业务规则仍应回到 domain/application。

## 8. 验收标准

- 新开发者只看 `interfaces/cli` 就能找到命令入口，只看 `application` 就能找到用例。
- `domain` 的测试不需要启动 CLI、Gateway 或真实 Provider。
- 每个扩展可以单独禁用、测试和打包。
- 仓库中只有一个真实实现；旧路径只有明确的兼容转发，并有删除日期。
- 模型、鉴权、请求协议、技能或打包改动仍需运行项目规定的退役集成扫描和相关测试。
