# VoidCube Python 巨石文件治理方案

> 状态：Stage 0 + CLI-0、Stage 2 UI 纯投影器、Stage 3 shared contract 与 CLI-3 session/clear/info/operations/attachments command adapter 拆分已完成。
> 编制日期：2026-07-29。  
> 决策：以“单仓库、共享应用核心、CLI/Windows 双前端、双发行物”为目标完成 Python 解耦，再实施 Windows 前端。

## 1. 决策摘要

当前阶段暂停 Windows 桌面端实现，优先处理 Python 巨石文件、反向依赖和状态所有权。原因不是单纯追求更短的文件，而是先从当前 CLI 中提取无界面的共享应用核心，避免未来 Windows 前端复制 CLI 业务逻辑，或把现有耦合固化为新的 HTTP、SSE 和进程接口。

长期产品形态固定为：

- 一个仓库、一套版本号和一套核心测试；
- 跨平台 CLI 发行物继续面向 Windows、Linux 和 macOS；
- Windows 应用发行物提供 WebView、托盘、通知、热键和图形工作区；
- 两个前端共享 session、turn、工具、审批、模型配置、Memory、Gateway、Supervisor 和 Execution 能力；
- 两个前端只隔离输入、渲染、平台集成和发行依赖，不能互相导入。

本轮核心范围：

| 文件 | 当前规模 | 主要问题 | 优先级 |
| --- | ---: | --- | --- |
| `VoidCube_cli/app.py` | 9,241 行 | `VoidcubeCLI` 仍混合 Agent 编排、TUI、命令和语音；shared contract 与 queue/statusbar/retry/title/resume/branch/new/clear/stop/profile/plugins/paste/image command handler 已迁出 | P0 |
| `systems/supervisor/planning_runtime.py` | 9,460 行 | `PlanningRuntimeMixin` 有 186 个方法，持久化、认知、排程、治理和执行交接混合 | P0 |
| `systems/supervisor/endogenous_drive.py` | 9,303 行 | `EndogenousDriveEngine` 有 124 个方法，感知、候选、LM 上下文、证据和策略记忆混合 | P0 |
| `systems/supervisor/ui_runtime.py` | 1,189 行 | 静态资源与全部只读 UI 投影已外移；runtime 仅保留资料加载、并发编排与 HTTP/SSE adapter | P0 |
| `agent/* -> VoidCube_cli/*` 边界 | 非单文件 | 第二批已归零，需持续由架构测试禁止回归 | P0 边界 |

次级观察对象包括 `run_agent.py`、Memory Service、Gateway、`VoidCube_cli/config.py` 和 `VoidCube_cli/main.py`。它们暂不与四条 P0 主线同时展开；只有在 P0 拆分需要明确依赖边界，或其修改频率和缺陷率达到阈值时才进入后续批次。

## 2. 治理目标

### 2.1 目标

- 根 `cli.py` 最终只保留稳定导出和 CLI 装配入口，不再是业务实现中心。
- 建立 `VoidCube_app` 共享应用层，承载与界面无关的 use case、session/turn runtime、事件、端口和应用配置。
- `VoidcubeCLI` 保留为兼容的薄 CLI facade/编排器，核心能力改为调用 `VoidCube_app`，不继续增加 Mixin。
- 消除 `VoidCube_cli/* -> cli.py` 的生产代码反向导入。
- 消除 `agent/*`、`systems/*` 和 `VoidCube_core/*` 对 `VoidCube_cli` 的业务依赖；CLI 不再充当共享配置或 Provider 层。
- Supervisor 保持唯一状态所有者，但将持久化、投影、策略和工作流拆为明确组件。
- 前端静态资源与 Python 状态/API 代码分离，`ui_runtime.py` 不再包含巨型 HTML 字符串。
- CLI 与未来 Windows 前端共享结构化应用事件和 use case，不共享终端文本、DOM、prompt_toolkit 控件或 Windows API。
- 同一套无界面 contract tests 可以驱动 CLI adapter 和未来 Windows adapter。
- 每次迁移都保持行为等价，并在迁移完成后删除旧实现、失效参数和永久兼容分支。

### 2.2 非目标

- 不在重构中改变模型调用、记忆真相、Gateway 路由或 Auto 语义。
- 不把一个大类机械切成多个仍共同读写 `host/self` 的 Mixin。
- 不同时重做 CLI 视觉、引入前端框架或创建 Windows 桌面壳。
- 不在共享应用层中引入 prompt_toolkit、Rich renderer、pywebview、pystray、pywin32 或浏览器 DOM 概念。
- 不以“每个文件必须小于某个硬性行数”驱动无意义拆分。
- 不一次性移动四个巨石；每个提交只完成一个可验证责任边界。
- 不恢复独立 Executor daemon，也不新增第二套会话或治理状态机。

## 3. 完成判据

巨石治理完成不能只看行数。需要同时满足以下结构性条件：

1. 生产包内不存在 `import cli` 或 `from cli import ...`。
2. `agent`、`systems`、`VoidCube_core` 和 `VoidCube_app` 不导入 `VoidCube_cli` 或未来 `VoidCube_windows`。
3. `VoidCube_cli` 与未来 `VoidCube_windows` 只向内依赖共享层，彼此没有导入边。
4. `cli.py` 不再持有命令实现、TUI 布局、语音流程和自主组件实现，只负责装配与公开兼容入口。
5. session、turn、取消、工具事件、审批和 clarify 有共享应用对象；CLI 模态焦点等纯显示状态仍归 CLI adapter。
6. Supervisor 的规划、内生驱动和 UI 投影组件通过显式参数/端口调用，不通过无限扩张的 Supervisor Mixin 共享所有内部属性。
7. `UI_HTML` 巨型字符串被删除，静态资源通过包资源加载，wheel 和源码运行一致。
8. 每个新组件可以脱离完整 TUI、Windows 宿主或完整 Supervisor 进行单元测试。
9. 原导入路径只在确有外部公开契约时保留短期导出；内部调用全部切换后删除兼容层。
10. 文档、测试、wheel、退役集成扫描和 smoke 测试通过。

行数只作为趋势指标：`cli.py` 和三个 Supervisor P0 文件应明显缩小，且不得出现另一个 8,000 行以上的替代文件。

## 4. 依赖方向

### 4.1 目标分层

`VoidCube_core` 当前明确定位为低层基础设施，`agent` 已承载模型、上下文、工具循环等领域能力。新的 session/use case 编排不应继续塞入二者，也不应留在 `VoidCube_cli`，因此新增 `VoidCube_app` 作为共享应用层。

目标依赖方向：

```text
VoidCube_cli（跨平台终端 adapter）        VoidCube_windows（未来 Windows adapter）
            \                                  /
             \                                /
              v                              v
                    VoidCube_app
       （use cases / session / turn / events / ports）
                    |          |
                    v          v
             agent / tools    systems clients
                    \          /
                     v        v
                   VoidCube_core

systems.supervisor.supervisor（HTTP 装配）
                         |
                         v
planning / endogenous / observation / UI projection services
                         |
                         v
stores / execution facade / Memory / Gateway（状态与端口）
```

固定规则：

- 包内模块不能导入根 `cli.py`。
- `VoidCube_app` 不能导入 `VoidCube_cli` 或 `VoidCube_windows`。
- `agent`、`systems` 和 `VoidCube_core` 不能导入任何前端包；现存 `VoidCube_cli` 依赖必须逐步上移到 `VoidCube_app` 或下沉到明确基础模块。
- `VoidCube_cli` 与 `VoidCube_windows` 禁止互相导入、共享全局变量或复用彼此的 renderer。
- 渲染层可以依赖领域事件；领域层不能依赖 prompt_toolkit、Rich 或 DOM。
- 投影器只读取快照并返回结构化数据，不负责持久化或执行副作用。
- store/repository 负责读写，不同时承担策略判断。
- HTTP 路由只做验证、调用和响应映射，不承载长业务流程。
- 编排器只协调组件，不复制组件内部逻辑。

### 4.2 共享与隔离边界

| 能力 | 归属 | CLI/Windows 是否共享 |
| --- | --- | --- |
| 模型请求、上下文、工具循环 | `agent` | 共享 |
| session、turn、queue、cancel、approval、clarify | `VoidCube_app` | 共享 |
| Provider/模型解析与 canonical 配置访问 | `VoidCube_app` + 明确配置基础模块 | 共享 |
| Memory、Gateway、Supervisor、Execution clients | `VoidCube_app`/`systems` | 共享 |
| 结构化事件、错误码、使用量和 diff/artifact contract | `VoidCube_app.contracts` | 共享 |
| slash 命令语法、ANSI、Rich、prompt_toolkit | `VoidCube_cli` | 仅 CLI |
| Web DOM、Markdown 组件、托盘、Toast、全局热键 | `VoidCube_windows` | 仅 Windows |
| 服务进程编排规则 | 共享 service controller | 共享 |
| CLI 文本格式化和 Windows 窗口生命周期 | 各自 adapter | 隔离 |

共享不等于强制同一进程。`VoidCube_app` 的 use case 应先定义 Python 端口；CLI 可以进程内调用，Windows 版可以在未来 ADR 中选择进程内调用或通过薄 HTTP/BFF 适配。传输方式不能渗入 use case。

### 4.3 共享应用 contract

首批稳定 contract 应覆盖：

- 输入 use case：创建/恢复会话、提交 turn、中断、排队、批准/拒绝、回答 clarify、切换模型、查询状态；
- 输出事件：`SessionEvent`、`TurnEvent`、`MessageDelta`、`ToolEvent`、`ApprovalRequested`、`ClarificationRequested`、`UsageUpdated`、`ArtifactCreated`；
- 输出端口：事件 sink、通知请求、日志/审计、服务状态；
- 基础端口：时钟、文件选择结果、Gateway/Memory client、credential/config provider。

CLI 的 slash command 只是 use case 的一种输入映射，不能成为共享 API。Windows 按钮和表单调用同一 use case，也不能模拟输入 slash command。

## 5. 先建立防回归护栏

在大规模移动代码前增加轻量结构测试：

- 禁止生产代码反向导入根 `cli.py`。
- 禁止 `VoidCube_app`/`agent`/`systems`/`VoidCube_core` 导入前端包，并为当前 `agent -> VoidCube_cli` 依赖建立递减例外清单。
- 禁止 CLI adapter 和未来 Windows adapter 互相导入。
- 记录四个 P0 文件的基线规模，允许逐步下降，禁止无理由继续增长。
- 禁止在 `cli.py`、`planning_runtime.py`、`ui_runtime.py` 新增大型方法；临时例外必须在同一批次说明。
- 包依赖测试检查领域模块不导入 TUI/Web 渲染层。
- 静态资源迁移后检查 wheel 中的资源清单和加载路径。

护栏不是永久限制所有文件行数。P0 收口后应将测试改为边界契约，删除只服务迁移过程的临时阈值。

## 6. CLI 主线

### 6.1 当前问题

`VoidcubeCLI.__init__` 约 291 行，混合以下状态：

- Provider、模型、凭证和 Agent；
- session、conversation、checkpoint 和 resume；
- TUI application、状态栏、spinner 和模态输入；
- 工具进度、审批、secret、sudo 和 clarify；
- 语音录制、TTS 和持续监听；
- 自主组件、后台任务、scheduled execution 和执行锁。

`run()` 约 1,732 行，`chat()` 约 505 行。已有 `chat_stream_*`、`command_execution.py` 等模块，但部分模块仍通过 `host: Any` 访问聚合状态，说明拆分尚未形成完整的显式端口边界。

### 6.2 目标结构

```text
VoidCube_app/
├─ contracts/
│  ├─ events.py                  跨前端稳定事件
│  ├─ commands.py                use case 输入对象，不是 slash command
│  └─ ports.py                   Gateway/Memory/config/event sink 端口
├─ runtime/
│  ├─ state.py                   共享 ApplicationState
│  ├─ session_runtime.py         session/resume/history/checkpoint
│  ├─ turn_runtime.py            chat/turn/cancel/queue
│  ├─ background_runtime.py      后台任务与完成事件
│  ├─ autonomous_runtime.py      内嵌 API-A 自主组件宿主
│  └─ voice_session.py           与 UI 无关的语音会话协调
├─ services/
│  ├─ model_service.py
│  ├─ configuration.py
│  └─ service_controller.py
└─ app.py                        共享应用组合根

VoidCube_cli/
├─ app.py                         CLI adapter 组合根
├─ command_handlers/                避免与现有 commands.py 冲突
│  ├─ router.py                  解析和分派
│  ├─ session.py
│  ├─ model.py
│  ├─ tools.py
│  ├─ skills.py
│  ├─ voice.py
│  └─ diagnostics.py
├─ tui/
│  ├─ application.py             prompt_toolkit Application 生命周期
│  ├─ layout.py
│  ├─ status_bar.py
│  ├─ modal.py
│  └─ keybindings.py
├─ renderers/                    ANSI/Rich/stream/tool 输出
└─ voice_adapter.py              终端录音控制与 CLI 提示

VoidCube_windows/                未来才创建
├─ app.py                         Windows adapter 组合根
├─ web/                           Work/Home 前端资源
├─ bridge.py
├─ tray.py
├─ notifications.py
└─ voice_adapter.py
```

这是目标责任图，不要求一次创建全部文件，也不创建空占位模块。现有职责清晰的 `chat_stream_renderer.py`、`command_router.py` 等优先保留，并按新依赖方向接入。

### 6.3 CLI 拆分顺序

#### CLI-0：消除反向导入

- 将 `CLI_CONFIG` 访问改为共享配置服务的显式 load/reload/update 接口；过渡期可从现有 `VoidCube_cli.config` 抽取，但 canonical owner 不能继续属于 CLI。
- 将 `_is_gateway_running`、注册和 scene 上报移到 `VoidCube_app` 的明确 Gateway client/runtime 模块。
- 先消除包内模块对根 `cli.py` 中配置、Gateway helper 和全局状态的依赖；这些依赖归位后，再将 `VoidcubeCLI` 实现机械迁到包内稳定模块，根 `cli.py` 只做短期 re-export 和入口。
- 更新 `VoidCube_cli.main`、`autonomous_runner.py`、`api_config.py`、`language_command.py` 及测试导入。
- 内部导入全部迁移后删除根模块反向依赖，不保留双路径 fallback。

验收：完成 CLI-0 全部批次后，生产代码 `rg "import cli as|from cli import"` 为零；第一批只要求例外数量下降且有明确清单。CLI 启动、单次查询、resume 和自主调试入口通过。

#### CLI-1：共享应用状态与 CLI 显示状态

- 在 `VoidCube_app` 引入 `SessionState`、`TurnState`、`VoiceSessionState`、`BackgroundTaskState`。
- 在 `VoidCube_cli` 仅保留 `ModalState`、焦点、spinner、status bar 等显示状态。
- 先搬数据和不变量，不搬全部行为。
- 逐组替换散落属性，禁止长期维护 `self.foo` 与 `self.state.foo` 双写。
- 每迁移一组属性，就删除旧属性和同步分支。

验收：状态创建和转换可独立测试；并发锁、deadline、Event 和 Queue 的所有者明确。

#### CLI-2：会话与 turn runtime

- 提取 session 创建、恢复、切换、分支、retry、undo、history 和保存。
- 提取 `chat()` 中与渲染无关的 turn 执行、取消、队列和 Agent 生命周期。
- 在 `VoidCube_app.contracts` 形成结构化 `TurnEvent` / `ToolEvent`，现有 CLI renderer 订阅事件，未来 Windows renderer 使用同一事件。
- 保持 SessionDB、Memory、Gateway lane 的现有真相边界。

验收：无 prompt_toolkit Application 也能运行单个 turn 的测试；中断和工具结果顺序测试通过。

#### CLI-3：命令域

- 将 `process_command()` 收口为解析、查表和调用。
- 按 session/model/tools/skills/voice/diagnostics 分组迁移 handler。
- handler 接收显式 context/protocol，不接收整个 `VoidcubeCLI` 作为无边界对象。
- 删除已经迁移的 `_handle_*` 方法和兼容分派分支。

验收：所有 slash command 路由测试通过；未知命令、参数错误和异步命令行为不变。

#### CLI-4：TUI application

- 最后拆 `run()`，因为它依赖前述状态和事件边界。
- 分离 layout、keybindings、modal、input queue、status bar 和 application lifecycle。
- `VoidcubeCLI.run()` 最终只创建 TUI、绑定 runtime 并等待退出。
- UI 组件只消费 view state，不直接修改模型、会话数据库或 Gateway 状态。

验收：交互启动、Ctrl+C、排队输入、审批、secret、resize、退出摘要和后台完成通知通过。

#### CLI-5：语音和自主组件

- 将与 UI 无关的 voice session 协调和 autonomous component host 提取到 `VoidCube_app`。
- 麦克风采集、终端提示和 Windows 原生设备/窗口交互留在各自 adapter。
- 两者通过共享 turn runtime 和事件端口交互，不直接操纵 TUI 或 Windows 私有状态。
- Scheduled task 与用户 turn 继续服从现有执行互斥。

验收：语音开关/录制/中断、Auto 进出、scheduled execution 和后台任务测试通过。

## 7. Supervisor Planning 主线

### 7.1 当前问题

`PlanningRuntimeMixin` 将以下职责放在一个 9,396 行类体内：

- 内生历史、治理事件、认知状态和 self-regulation 的持久化；
- 认知判断、候选注释、策略记忆和观察议程；
- task profile、schedule、排序和冲突；
- Gateway 活动投影和 drive 输入；
- 自主任务创建、决策、review、恢复和执行交接；
- body improvement 质量评分和审查。

这种 Mixin 拆法虽然减少了 `supervisor.py` 行数，但没有缩小隐式 `self` API。继续增加 Mixin 只会把依赖藏到运行时属性中。

### 7.2 目标组件

建议逐步形成：

- `EndogenousStateRepository`：历史、事件、认知和 regulation 的原子读写。
- `CognitionProjector`：从显式输入构建认知、判断和不确定性快照。
- `StrategyMemoryService`：agenda、observation、meta-governance 记忆。
- `TaskProfilePolicy`：task family/type/profile 和 execution kind。
- `ScheduleAllocator`：时间槽、冲突、排序和 active task 查询。
- `AutonomousTaskService`：plan/decide/review/clear/recover 的状态转换。
- `ExecutionHandoffService`：执行请求、Gateway owner 和失败回写。
- `BodyImprovementReviewer`：diff、probe、质量和稳定性审查。
- `PlanningRuntime`：协调上述服务并提供 Supervisor 路由所需端口。

### 7.3 拆分顺序

1. 先抽 repository 和纯 normalize/label/profile/schedule 函数。
2. 再抽 projector/policy，它们接收不可变快照并返回结果。
3. 再抽 task service 和 execution handoff，明确副作用边界。
4. 最后缩小 `evaluate_drive_input`、`evaluate_endogenous_drive` 和 review cycle 编排。
5. Supervisor 初始化通过 `runtime_assemblers.py` 注入组件，不在 Mixin 内懒建隐藏对象。
6. 所有消费者切换后删除对应 Mixin 方法，不保留委托壳堆积。

验收：核心 projector/policy 无需构造完整 Supervisor 即可测试；任务状态仍先写治理事件；Gateway、Memory、Execution 所有权不变。

## 8. Endogenous Drive 主线

### 8.1 当前问题

`EndogenousDriveEngine` 同时负责感知、world model、reflection、adaptive policy、need/intent/signal、候选流、LM evidence/context、外部研究、策略记忆和 body candidate，导致单个候选流方法约 592 行，策略构建约 517 行。

### 8.2 目标流水线

```text
DrivePerceptionBuilder
  -> WorldModelBuilder
  -> ReflectionBuilder
  -> AdaptivePolicyBuilder
  -> NeedIntentDetector
  -> CandidateGenerator
  -> EvidenceAssembler
  -> LmProposalService
  -> CandidateScorer/Selector
  -> DriveDeliberationReport
```

辅助组件：

- `CognitiveContextAssembler`
- `StrategyMemoryNormalizer`
- `ResearchEvidenceLoader`
- `BodyImprovementCandidateFactory`
- `TopicNoveltyPolicy`

### 8.3 拆分规则

- 先提取已有 dataclass/输入输出模型和无状态纯函数。
- 流水线阶段只接收需要的数据，不持有完整 Supervisor。
- LM Proposal 与 deterministic candidate stream 保持两个明确来源，在选择阶段合并。
- 文件读取、时间和模型调用通过端口注入，测试不依赖真实文件系统和网络。
- `EndogenousDriveEngine` 最终作为 facade 编排流水线；消费者稳定迁移后可进一步改名为 `EndogenousDrivePipeline`。
- 不在迁移中改变候选种类、评分、冷却或 API-B 判断语义。

## 9. Supervisor UI Python 主线

### 9.1 第一阶段：静态资源外移

先执行行为等价迁移：

```text
systems/supervisor/web/
├─ index.html
└─ assets/
   ├─ app.css
   ├─ app.js
   ├─ api_client.js
   ├─ house.js
   ├─ voice.js
   └─ auto.js
```

- 使用 FastAPI 静态资源与 `importlib.resources` 定位，不依赖当前工作目录。
- 首阶段使用原生 ES modules，不引入 Node/Vite 或新框架。
- 更新 package-data、wheel 契约和前端资源测试。
- 测试从直接断言 `UI_HTML` 字符串改为加载资源并验证 DOM/行为契约。
- 资源迁移完成后删除 `UI_HTML`，不保留内嵌 fallback。

### 9.2 第二阶段：Python 职责拆分

建议组件：

- `UIActivityStore`：近期活动的原子持久化。
- `UIEventBroker`：state、voice、media SSE 队列和断线处理。
- `SupervisorStateProjector`：组合 UI state。
- `AutonomousObservationProjector`：自主观察、stage、rail、trace。
- `CognitionUIProjector`：判断、不确定性和标签。
- `BodyTreeProjector`：身体槽位和升级树。
- `UIMetricsService`：Memory 和 tier 指标。
- `UIRoutes`：薄 HTTP/SSE endpoints。

先抽纯标签、normalize 和 projector，再抽 store/broker，最后缩小 `get_supervisor_ui_state()`。`SupervisorUIMixin` 最终删除，由 route object 或显式组合服务替代。

### 9.3 前端安全收口

静态资源拆分后再实施 CSP、动态 HTML 净化、媒体来源白名单和 UI 写会话。此阶段仍服务浏览器版 Python Web UI，不创建 Windows 桌面宿主。

## 10. 次级巨石处理策略

完成四条 P0 主线后重新测量：

- `run_agent.py`：优先把编排继续下沉至 `agent/` 已有模块。
- `systems/memory/memory_service.py`：按 repository、recall、governance、backup 和 HTTP routes 评估。
- `systems/gateway/internal_gateway.py`：按 registry、auth、session lease、scene projection 和 routes 评估。
- `VoidCube_cli/config.py`：先拆 migration、schema、credentials/env 和 persistence。
- `VoidCube_cli/main.py`：先把 1,242 行 `main()` 的 argparse/dispatch 拆成命令注册，不与 `cli.py` TUI 拆分同时进行。

满足任一条件才提升为 P0：

- 阻塞当前主线形成单向依赖；
- 单个方法继续超过约 300 行且频繁变化；
- 近阶段缺陷集中在该文件的跨职责区域；
- 无法在不构造全系统的情况下测试核心逻辑。

## 11. 单仓库双发行契约

### 11.1 源码与版本

- 仓库只维护一套 `VoidCube_core`、`agent`、`VoidCube_app` 和 `systems`。
- CLI 与 Windows 应用使用同一版本号、配置 schema、数据迁移和领域事件版本。
- 不复制共享源码到 Windows 专用目录，不使用两个长期漂移的分支维护产品。
- 前端可独立迭代，但破坏共享 contract 必须在同一仓库中同步升级两个 adapter 的测试。

### 11.2 发行物

| 发行物 | 平台 | 内容 | 不包含 |
| --- | --- | --- | --- |
| VoidCube CLI wheel/包 | Windows/Linux/macOS | 共享核心、服务、CLI adapter | pywebview、pystray、pywin32、Windows 静态资源 |
| VoidCube Windows 安装包 | Windows 10/11 | 同版本共享核心、服务、Windows adapter、Web 资源和原生依赖 | prompt_toolkit TUI 运行入口可按安装策略选择，不作为 GUI 依赖 |

项目初期可以由一个 Python distribution 配合 `desktop` optional extra 生成两种产物，降低发布复杂度；若后续安装包和 wheel 的依赖/资源契约差异过大，再评估 workspace 内多个 distribution。无论采用哪种打包形式，源码层依赖边界不能改变。

### 11.3 入口和依赖

- CLI entry point 只导入 `VoidCube_cli`，不得探测或导入 Windows adapter。
- Windows entry point 只导入 `VoidCube_windows` 和共享层，不导入 `VoidCube_cli`。
- 平台依赖通过 environment marker/optional extra 隔离；Linux 安装解析阶段不应下载 Windows 包。
- 两种发行物都通过包资源定位自身资源，不依赖仓库根目录或当前工作目录。
- wheel/安装包分别有内容清单测试，防止 GUI 资源进入 CLI 包或 CLI 私有模块成为 Windows 必需依赖。

### 11.4 数据兼容

- 两种前端读取同一个 canonical 配置和 runtime layout，不复制用户配置。
- 同一时刻对 session/store 的写入必须经过共享 repository/锁，不允许 CLI 与 Windows 各写一套 JSON/SQLite 逻辑。
- CLI 和 Windows 可以同时运行，但 session owner、后台服务 owner 和执行互斥必须明确。
- 任一前端升级数据 schema 后，另一个前端必须能识别版本或明确拒绝降级，不能静默损坏数据。

## 12. 分阶段实施计划

### Stage 0：基线和护栏

- 固化四个 P0 文件的行为测试和依赖扫描。
- 建立反向导入、巨型方法新增和包层级约束。
- 建立目标包依赖矩阵：共享层不得导入 CLI/Windows，两个 adapter 不得互相导入。
- 为现有 `agent`/`systems`/`VoidCube_core -> VoidCube_cli` 依赖生成例外清单并要求只减不增。
- 记录 smoke、关键单测和冷启动基准。
- 给每条主线建立组件清单，不预建空目录。

验收：基线可重复；测试失败能指出边界回归，而不是只比较行数。

### Stage 1：CLI 反向依赖与组合根

- 完成 CLI-0。
- 创建最小 `VoidCube_app`，只接收第一批已经找到明确双前端价值的配置/Gateway contract，不预建完整目录。
- 将根 `cli.py` 从包内实现依赖中心变为兼容入口。
- 保持 CLI 用户行为不变。

验收：生产代码无根 `cli` 反向导入；CLI 关键测试、single query、interactive startup 通过。

### Stage 2：UI 静态资源与纯投影器

- 等价移出 `UI_HTML`。
- 抽取 UI、planning、endogenous 中最独立的 normalize/label/projector。
- 更新资源、DOM 和 wheel 契约。

验收：浏览器 UI 行为一致；纯投影器无需完整 Supervisor；无内嵌前端 fallback。

### Stage 3：CLI session/turn/command

- 完成 CLI-1 至 CLI-3。
- 在 `VoidCube_app` 建立结构化 turn/tool/approval/clarify 事件和 use case。
- 收口命令 handler 的 context。

验收：CLI renderer 只消费事件/视图状态；会话和工具链测试无回归。

### Stage 4：Planning repository/policy/task workflow

- 抽 repository、cognition/strategy projector、task profile、schedule 和 task service。
- 缩小 drive evaluation 和 autonomous review 编排。

验收：治理事件、任务真相、恢复和执行交接全链路保持一致。

### Stage 5：Endogenous pipeline

- 拆感知、反思、策略、候选、证据、LM proposal 和 selection。
- 保留 `EndogenousDriveEngine` 的短期稳定 API，内部改为组合。

验收：同一输入产生等价候选、分数和 deliberation；模型和文件端口可替换测试。

### Stage 6：TUI、语音、自主组件与 UI route 收口

- 完成 CLI-4/CLI-5。
- 删除 `PlanningRuntimeMixin` 和 `SupervisorUIMixin` 的已迁移方法/类。
- HTTP/SSE 路由变为薄适配层。

验收：四个 P0 巨石不再是实现中心，旧兼容入口已清理。

### Stage 7：全量回归与次级评估

- 运行全量主项目和 Mem 测试、smoke、wheel 构建和退役扫描。
- 重新测量导入、首次 CLI、首次 turn、服务启动和 UI state 延迟。
- 评估次级巨石是否需要后续治理。
- 只有达到第 14 节门槛，才开始实现 Windows adapter 和重新决定后台传输/进程模型。

## 13. 每个迁移批次的固定流程

1. 写明被迁移责任、输入、输出、状态所有者和副作用。
2. 为现有行为补最小 characterization test。
3. 创建目标组件并移动一个完整责任，不做顺手功能改造。
4. 将所有生产调用者切到新路径。
5. 删除旧实现、重复字段、旧参数和无调用兼容分支。
6. 运行针对测试、依赖扫描和 `git diff --check`。
7. 检查是否出现双写、循环依赖、catch-all context 或另一个聚合巨石。
8. 更新本文阶段状态和下一批次入口。

禁止以这些方式“完成拆分”：

- 新模块函数仍接收完整 `VoidcubeCLI`/`Supervisor` 并任意访问属性；
- 原方法仅变成几十个无意义委托壳且永久保留；
- 同一状态在旧类和新组件双写；
- 使用 `Any`、`getattr` 和 `hasattr` 模拟未定义接口；
- 为避免修改调用者而长期保留两个导入路径；
- 把多个大文件简单拼成一个新的 `helpers.py`/`runtime.py`。

## 14. 启动 Windows adapter 的 Go/No-Go 门槛

满足以下条件后，才重新审查 `Windows桌面端方案.md`：

- 根 `cli.py` 已成为薄入口，生产代码不反向导入它。
- `VoidCube_app` 已成为无界面的共享应用层，且不导入任一前端 adapter。
- API-A session/turn/tool/approval/clarify 已有共享 use case 和结构化事件。
- `agent`、`systems`、`VoidCube_core` 不再以 `VoidCube_cli` 作为配置、Provider、插件或展示能力所有者。
- CLI contract tests 通过 `VoidCube_app` 公共端口运行，未来 Windows adapter 无需模拟 slash command 或解析 ANSI。
- Supervisor UI 静态资源已外移，UI state/observation projector 有明确边界。
- `PlanningRuntimeMixin` 和 `EndogenousDriveEngine` 的主流水线已组件化，不再依赖不可枚举的共享 `self` 状态。
- CLI、Gateway、Memory、Supervisor、Execution 的状态所有权文档与代码一致。
- 全量测试、wheel、退役集成扫描通过，且首次 CLI/turn/服务/UI 性能没有显著回归。
- 没有为了迁移遗留大规模双路径兼容层。

达到门槛代表可以在同一仓库开始实现 Windows adapter，但仍需根据已经形成的 API-A runtime 和 UI 边界，用 ADR 决定进程内调用、薄本机 API/BFF 或混合模式。无论选哪种传输，两种前端都只能调用同一应用 use case。

## 15. Stage 0 + CLI-0 第一批实施记录

2026-07-29 已完成：

1. 新增基于 AST 的包依赖护栏，控制根 `cli.py` 反向导入、共享层导入前端、CLI/Windows 互相导入，并记录只减不增的现有例外。
2. 固化四个 P0 文件的行数和现有大方法基线，禁止迁移期间无理由增长或新增 300 行以上方法。
3. 建立最小 `VoidCube_app`，只包含已经产生双前端价值的配置运行时、Gateway presence client 和模型 ID 规范化接口。
4. 删除 `api_config.py`、delegation 配置读取对 `cli.CLI_CONFIG` 的依赖；运行时配置刷新统一通过共享配置 owner 且保持存量引用有效。
5. 将 Gateway 存活检查、session 注册和 scene 上报移出根 `cli.py`；`autonomous_runner.py` 不再调用根模块的配置或 Gateway helper。
6. 将模型 ID 规范化 canonical 实现迁到 `VoidCube_app`，删除一条 `agent -> VoidCube_cli` 例外；旧 CLI 模块仅保留公开重导出。
7. `VoidcubeCLI` 的包内 canonical 目标固定为 `VoidCube_cli.app.VoidcubeCLI`。第一批不创建反向导入根模块的空壳，待实现机械迁移时一次切换生产调用者。
8. `VoidCube_app` 已进入 setuptools 与 wheel 源码契约；退役集成扫描覆盖新增共享包。

本批完成后，运行时根导入只剩 `VoidCube_cli.main -> cli.main` 和自主诊断入口对 `VoidcubeCLI` 的临时依赖；类型标注和其他生产模块不再反向导入根模块。

## 16. CLI-0 第二批实施记录

2026-07-29 已完成：

1. 配置 schema、持久化、环境访问和默认身份的 canonical owner 已迁至 `VoidCube_app`；旧 CLI 路径仅保留共享同一模块对象的兼容别名。
2. Provider 注册、凭证存储和运行时解析已迁至 `VoidCube_app`；交互式登录/登出仍由 CLI adapter 持有。
3. 模型发现与验证目录已迁至 `VoidCube_app.models`，所有生产调用者使用 canonical 路径；`VoidCube_cli.models` 仅保留模块别名。
4. 通用插件注册表和 lifecycle hook contract 已迁至 `VoidCube_app.plugins`；插件发现和 CLI 命令/工具集 adapter 仍留在 CLI。
5. 删除指向不存在模块且永久返回空结果的订阅提示回退，不建立新的隐藏兼容入口。
6. 共享包到 `VoidCube_cli`/`VoidCube_windows` 的运行时导入例外已从四条降为零，架构测试改为强制零入口。
7. CLI 视觉固定为 `VoidCube_cli.style` 中唯一一套内建样式；已删除皮肤引擎、`/skin` 命令、`display.skin` 默认值、动态切换、皮肤插件覆盖、皮肤帮助和本地化入口。
8. 配置迁移只清除历史 `display.skin`，不会误删其他未知或未来的 `display` 设置；旧值会在迁移时从用户配置中持久化移除。

## 17. CLI-0 组合根收口实施记录

2026-07-29 已完成：

1. `VoidcubeCLI` 与 `main()` 的 canonical 实现已机械迁至 `VoidCube_cli.app`，根 `cli.py` 仅保留稳定模块别名和脚本入口。
2. `VoidCube_cli.main` 与自主诊断入口均直接导入 canonical 模块；生产代码对根 `cli.py` 的运行时导入已归零。
3. 根模块兼容别名与 canonical 模块指向同一对象，既保留公开 `cli` import，也不会形成第二套全局状态。
4. P0 增长护栏已随实现转移到 `VoidCube_cli/app.py`，并删除根导入例外清单。

## 18. Stage 2 静态资源外移实施记录

2026-07-29 已完成：

1. `UI_HTML` 已从 `systems/supervisor/ui_runtime.py` 完整移至 `systems/supervisor/web/supervisor.html`；Python 模块不再保留模板字符串或开发路径回退。
2. `systems.supervisor.ui_assets.load_supervisor_ui_html()` 通过包资源加载并缓存模板，`/ui` 路由只负责响应映射。
3. `systems` package data、源码到 wheel 内容契约和资源加载测试均已覆盖该 HTML；P0 增长护栏随实现降至 3,691 行。

## 19. Stage 2 首批 UI 纯投影器实施记录

2026-07-29 已完成：

1. `ui_projection.py` 接管 SSE 格式化、默认 observation 快照、活动标签、近期自主活动、observation board 与 segment/stage 投影；所有函数都只接收显式快照。
2. `ui_cognition_projection.py` 接管认知判断、不确定性、文案标签和百分比计算；它仅依赖输入快照和 `observation_count`，不持有或读取 `Supervisor`。
3. 所有调用者已切换，旧 Mixin 方法与委托壳均已删除；P0 增长护栏降至 3,075 行，并增加无 Supervisor 构造的直接投影测试。

## 20. Stage 2 链路 observation card 投影实施记录

2026-07-29 已完成：

1. `ui_observation_projection.py` 接管 task family、状态、排序、卡片、group、stage card 与 rail entry 的纯结构化映射。
2. 新模块只依赖显式 task/stage 数据、状态规范化和活动标签；它不持有或读取 `Supervisor`、repository、trace 或 HTTP client。
3. 所有调用者已切换，原 Mixin helper 与委托壳均已删除；P0 增长护栏降至 2,691 行，并增加独立 card/stage 测试。

## 21. 下一次实施起点

2026-07-29 已完成 Stage 2 链路活动与 trace 协调：

1. `ui_trace_projection.py` 接管分段事件筛选、trace 聚合、链路状态/focus 投影、trace detail 裁剪，以及 observation 中 trace detail 的纯合并。
2. trace records 的仓储、Supervisor activity、治理历史读取，以及 detail loader 的并发编排仍留在 runtime；投影器只接收显式 records、summary、timeline 和 observation 数据。
3. 所有原 `SupervisorUIMixin` 分段 timeline/event/trace helper 已删除，不保留委托壳；P0 增长护栏降至 2,327 行，并增加独立 trace 投影测试。

## 22. 下一次实施起点

2026-07-29 已完成 Stage 2 首批 UI state 组合收口：

1. `ui_state_projection.py` 接管 scene 决策、metrics 和 slot overview；它只消费已加载的 observation/body 快照和显式错误计数。
2. body registry、memory service、voice、cognition 和 trace loader 的读取继续留在 runtime；daily-companion 的运行时覆盖规则保持在 `get_supervisor_ui_state()`。
3. 所有原 Mixin state helper 已删除，测试改为替换运行时导入的纯 scene 投影；P0 增长护栏降至 2,099 行。

## 23. 下一次实施起点

2026-07-29 已完成 Stage 2 body observation 投影：

1. `ui_body_projection.py` 接管 slot role/state 标签、upgrade signal 映射、树节点和 slot card 的纯结构化投影。
2. worktree 文件系统枚举、body registry 元数据读取和完整性报告加载继续留在 runtime；投影器只接收 registry、meta、integrity 与已枚举目录快照。
3. 原 Mixin body helper 与 registry object 兼容读取均已删除；slot-card 用例改为直接调用投影器，P0 增长护栏降至 1,833 行。

## 24. 下一次实施起点

2026-07-29 已完成 Stage 2 autonomous observation 主组装收口：

1. `ui_autonomous_projection.py` 接管 task/candidate 去重与分类、writeback 摘要、segment、loop stage、rail、board 和 count 的完整只读组装。
2. task/drive/timeline 的加载、trace detail 关联和 HTTP/SSE 路由继续留在 runtime；新模块只接收显式 snapshots，零 `Supervisor` 或 `self` 访问。
3. 原 600 行 `_build_autonomous_observation()` 和 writeback helper 已删除，新增独立投影 characterization test，P0 增长护栏降至 1,204 行。

## 25. 下一次实施起点

下一批进入 **Stage 3 CLI session/turn/command**：

1. 先盘点 `VoidCube_cli.app` 中 session、turn、slash command 的状态所有权与现有 helper 边界。
2. 只为已有双前端价值的 session/turn/tool/approval/clarify contract 创建 `VoidCube_app` use case；CLI renderer 继续作为 adapter。
3. 每完成一个 command/turn 责任即切换全部调用者、删除旧实现或兼容分支，并运行 CLI contract、架构、打包和退役扫描。

## 26. Stage 3 session identity 实施记录

2026-07-29 已完成：

1. `VoidCube_app.session_identity` 成为显式 resume、自动恢复和新会话 ID 生成的唯一无界面 owner；它只依赖只读 session index port 与显式时间/随机输入。
2. 保持原有优先级：显式 session 优先；随后是未结束的 `cli_supervisor_task_lane` owner session；再是有用户消息的最新 `cli` session；均不可用时创建 `YYYYMMDD_HHMMSS_<6hex>` ID。
3. `VoidcubeCLI.__init__` 只负责创建 `SessionDB`、日志和 UI 状态赋值，旧的重复查询与 ID 选择分支已删除；索引异常仍会记录诊断并安全创建新 session。
4. 新模块已纳入 wheel 源码契约，并有不构造 CLI 的直接 characterization tests。

## 27. 下一次实施起点

2026-07-29 已完成 **Stage 3 session lifecycle command**：

1. `VoidCube_app.session_lifecycle` 接管新建、恢复和分支的 session transition；它通过显式 repository port 完成结束/重开、history 装载/复制、title 和 parent link，不接收 `VoidcubeCLI` 或终端对象。
2. CLI 保留 recent-session 选择、title/ID 解析、交互提示与 history 渲染，并只应用共享层返回的 `SessionLifecycleState`。
3. `AIAgent.activate_session()` 成为 adapter 同步 session 的公开端口，统一重置 token/context、Todo、system prompt 和 `SessionPersistence` 游标；CLI 对已经删除的 `_last_flushed_db_idx` 兼容探测已清理。
4. 删除无人读取的 `VoidCube_cli.session_state` JSON 状态旁路及 `run_agent.py` 初始化，不再保留与 SQLite/session lifecycle 并行的旧状态 owner。
5. 新建、恢复、分支的成功/失败顺序和 Agent 同步均已有脱离 TUI 的 characterization tests；P0 增长护栏降至 10,052 行。

## 28. 下一次实施起点

2026-07-29 已完成 **Stage 3 session hydration**：

1. `hydrate_session()` 通过 repository 公开端口统一 session 校验、history 读取、`session_meta` 过滤和 reopen，并显式返回 `missing`、`empty` 或 `ready`。
2. `_preload_resumed_session()` 与 `_init_agent()` 共享同一个 hydration cache；交互启动、single query 和首个 turn 不再重复读取或重复显示空/缺失会话。
3. `/resume` 直接复用 transition 已加载的 hydration 结果，首个 turn 前切换到空会话也不会再次查询。
4. CLI 中两处 `SessionDB._conn` 私有写入已删除，session reopen 只经过 repository 公共方法；缺失、空、仅 metadata 和有历史四类结果均有直接测试。
5. P0 增长护栏降至 10,045 行。

## 29. 下一次实施起点

2026-07-29 已完成 **Stage 3 history mutation command**：

1. `remove_last_user_turn()` 接管最后 user turn 的定位和内存 transition，显式区分 empty、no-user、persistence-failed 与 applied。
2. `SessionDB.truncate_last_user_turn()` 原子删除 SQLite 中最后 user 消息及其后的 assistant/tool 消息，并重算 message/tool-call 计数；resume 不再恢复已撤销内容。
3. Agent 的公开 history mutation 端口同步增量写游标并允许显式缩短 JSON transcript；普通 `save_log()` 仍保留防止短暂局部历史覆盖完整日志的保护。
4. CLI 保留 retry 入队、undo/rollback 提示与渲染；旧的单调用者 `retry_last()` 委托层已删除。SQLite 截断失败时不再只改内存形成双真相。
5. 共享 transition、真实 SQLite、JSON transcript 和 CLI/Agent 接线测试均已覆盖；`VoidCube_cli/app.py` 为 10,050 行，较 Stage 3 lifecycle 批次前净减少 107 行。

## 30. 下一次实施起点

2026-07-29 已完成 **Stage 3 session title command**：

1. `get_session_title()` / `set_session_title()` 接管 title 查询、canonical sanitize、唯一性判断、立即更新或 pending 结果，并返回结构化状态。
2. 共享层仅通过 repository port 调用 sanitize/get/set，不导入 concrete `SessionDB`；CLI 已删除动态 `SessionDB.sanitize_title` 和重复存在性分支。
3. 首次 Agent 创建后的 pending title 写入也复用同一 use case，不再保留第二条 CLI 私有落库逻辑。
4. current、pending、unset、unavailable、updated、queued、conflict、invalid 与过长 title 均有直接测试；过长 title 不再额外误报“清理后为空”。
5. `VoidCube_cli/app.py` 为 10,060 行，较本轮 Stage 3 开始前净减少 97 行。

## 31. 下一次实施起点

2026-07-29 已完成 **Stage 3 首个 turn contract**：

1. `VoidCube_app.turn_contract` 新增 `TurnInput` / `TurnOutcome`，统一 prior/current history、Agent result、response、failed/partial/interrupted/error、reasoning 与 preview 状态。
2. `chat()` 已切换 user input/history transition 和最终 Agent result normalization；线程、interrupt queue、TTS、renderer 与自主超时编排仍留在 CLI adapter。
3. malformed messages 使用当前 history，no-result 显式为失败，空失败/部分结果统一生成 error response；自主 observation 同步用户实际看到的 fallback response，不再保留空写回。
4. 纯 contract tests 覆盖 multimodal input、不原地修改 history、无结果、完整结果、interrupt payload、malformed history 和 error fallback。
5. `VoidCube_cli/app.py` 为 10,053 行，较本轮 Stage 3 开始前净减少 104 行。

## 32. Stage 3 approval/clarify event contract 实施记录

2026-07-29 已完成：

1. `VoidCube_app.interaction_contract` 新增 `ApprovalRequest/Decision/Status/Sink` 与 `ClarificationRequest/Decision/Status/Sink`；共享层只表达请求、决定和保守解析，不包含 Queue、锁、deadline、ANSI、键位或 CLI host。
2. `VoidCube_cli.interaction_adapter` 接管 clarify/approval 的阻塞等待、超时清理、长命令展开和审批面板；secret 与 sudo 继续作为 CLI 私有敏感输入，不混入共享 contract。
3. Agent 的 `clarify_callback` 已替换为结构化 `clarification_sink`，并修复模型 schema 使用 `options`、Agent 却读取 `choices` 且旧工具完全忽略 callback 的断链。
4. dangerous-command guard 改为读取 `ApprovalDecision.approved`；旧实现会把非空字符串 `"deny"` 当真值从而错误放行，当前缺失、异常、非法和明确拒绝均 fail closed。无人使用的全局 `ApprovalGate`、auto-approve 分支，以及实际没有缓存/持久化语义的 session/always 选项均已删除。
5. history mutation 的显式空列表现在可写出 `message_count: 0` 与空 JSON transcript，撤销全部历史不会再残留旧日志。共享 contract、Agent/tool 路由、CLI adapter、架构与打包契约均有直接测试。
6. `VoidCube_cli/app.py` 降至 9,872 行，较本轮 Stage 3 开始前净减少 285 行；`chat()` 为 505 行，`run()` 为 1,732 行，P0 增长基线已同步下调。
7. 最终相关回归为 181 passed，完整 smoke 为 324 passed / 1169 deselected；wheel 构建、源码清单和退役集成扫描均通过。归档包含 interaction/session/turn contract 与 CLI interaction adapter，且不包含已删除的 `VoidCube_cli/session_state.py`。

## 33. Stage 3 tool event contract 实施记录

2026-07-29 已完成：

1. `VoidCube_app.tool_events` 新增 `ToolEvent`、`ToolEventKind` 与 `ToolEventSink`；真实工具调用统一发出共享 `call_id` 的 started/completed 事件，并携带只读 arguments 快照、result、duration 与 error 状态。共享 contract 不包含 ANSI、spinner、inline diff 或 prompt_toolkit。
2. Agent 的 `tool_progress_callback`、`tool_start_callback`、`tool_complete_callback` 已收敛为单一 `tool_event_sink`；全部生产调用者完成切换，旧参数与兼容分派归零。sink 异常只记录诊断，不改变工具执行结果。
3. `VoidCube_cli.tool_event_adapter` 接管 spinner、autonomous activity、voice cue、scrollback 与 inline diff 投影；CLI 的 `_on_tool_event()` 仅做薄转发。completed event 自带 arguments，旧 `_pending_tool_info` 第二套状态 owner 已删除。
4. delegation 子 Agent、gateway 批量进度与 rich subagent display 已全部消费结构化事件；reasoning 与 subagent progress 使用专用 event kind，不再借用工具 callback。started/completed 的并行顺序和相同 `call_id` 已有直接测试。
5. `tool_progress_mode="new"` 现在只抑制重复 scrollback，不会再提前跳过 inline diff 等 completed 消费者；未实际启动的 skipped tool call 不产生 started/completed 事件。
6. `VoidCube_cli/app.py` 降至 9,750 行，较本轮 Stage 3 开始前 10,157 行净减少 407 行；`chat()` 为 505 行，`run()` 为 1,732 行，`VoidcubeCLI.__init__` 为 290 行，P0 增长基线已同步下调。
7. 最终相关回归为 222 passed，完整 smoke 为 332 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。归档包含 interaction/session/turn/tool event contract 与两类 CLI adapter，且不包含已删除的 `VoidCube_cli/session_state.py`。

## 34. Stage 3 cancel/queue contract 实施记录

2026-07-29 已完成：

1. `VoidCube_app.turn_queue` 新增 canonical busy-input mode、pending/interrupt route、`TurnInterrupt` reason、multimodal interrupt text 与 interrupted-input batch transition；共享层不持有 Queue、锁、键位或 prompt_toolkit 对象。
2. `VoidCube_cli.turn_queue_adapter` 接管实际 Queue route、poll、drain 与 requeue，并显式返回 EMPTY、DEFERRED、READY；`chat()` 和 Enter handler 只消费结构化结果。全字符串中断仍合并为一个下一 turn，多模态 payload 保持附件和输入顺序。
3. 已删除 `app.py` 的 `_interrupt_text` / `_requeue_interrupted_payloads` 重复实现，以及没有任何生产者的 `__AUTONOMOUS_Q_EXIT__` / `__FORCE_QUIT__` sentinel 兼容判断。普通 `__HELLO__` 形式的用户输入不会再被误判并静默吞掉。
4. autonomous timeout 与 Ctrl+C 分别映射为 TIMEOUT / USER_CANCELLED；取消不再借用 `__AUTONOMOUS_TIMEOUT__` 魔法用户消息，也不会被 Agent outcome 当作下一条 prompt 重新入队。新输入中断仍把原始 text/image payload 保留给下一 turn。
5. clarify 活跃期间已落入 interrupt queue 的竞态 payload 现在转入 pending queue，不再 `continue` 后永久丢失。Queue adapter 不使用 `Queue.empty()` 作为并发 guard，而是 drain 到 `queue.Empty`。
6. `VoidCube_cli/app.py` 降至 9,729 行，较本轮 Stage 3 开始前 10,157 行净减少 428 行；`chat()`、`run()` 与 `VoidcubeCLI.__init__` 未突破既有大方法基线，P0 总行数基线已同步下调。
7. 最终相关回归为 240 passed，完整 smoke 为 350 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。归档包含 turn queue contract 与 CLI Queue adapter，且继续不包含已删除的 `VoidCube_cli/session_state.py`。

## 35. 下一次实施起点

2026-07-29 已完成 **CLI-3 首批 command handler 分域**：

1. 新增 `VoidCube_cli.command_handlers`，首批迁出 `/queue`、`/statusbar` 与 `/retry`；input/display handler 只接收 dataclass ports 和 `ParsedCliCommand`，不接收 `VoidcubeCLI`。
2. execution table 新增显式 `handler_key`，已迁命令从 registry 获取 callable；未迁命令暂时保留 method handler。registry 是 CLI 组合根，负责把 Queue、状态 getter/setter 和文本输出映射为窄 ports。
3. `VoidcubeCLI._handle_queue_command`、`_handle_statusbar_command`、`_handle_retry_command` 已删除，不保留同名委托壳；原参数大小写、queue/idle 提示、statusbar toggle 与 multimodal retry payload 行为保持不变。
4. `VoidCube_cli/app.py` 降至 9,696 行，较本轮 Stage 3 开始前 10,157 行净减少 461 行；P0 总行数基线已同步下调。
5. 该首批最终相关回归为 261 passed，完整 smoke 为 355 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。归档包含 `command_handlers` package、turn queue contract 与全部既有 shared contract，且继续不包含已删除的 `VoidCube_cli/session_state.py`。

## 36. CLI-3 session command handler 实施记录

2026-07-29 已完成：

1. 新增 `command_handlers.session`，`/title` 现在通过 `TitleCommandPorts` 调用共享 `get_session_title()` / `set_session_title()` use case；handler 不访问 repository、CLI host 或 TUI 对象。
2. current、pending、unset、unavailable、updated、queued、conflict、invalid 与 not-found 的 CLI 文案投影均有直接测试；queued title 只通过显式 setter port 回写。
3. `VoidcubeCLI._handle_title_command` 已删除，不保留翻译或 title 委托壳；Agent 首次创建 session 后的 pending title 落库仍直接复用共享 use case。
4. `/resume` 与 `/branch` 当前仍拥有 recent-session selection、title/ID resolution、history display 与 branch summary 等 CLI adapter 责任；本批不以整个 host port 强行迁移。
5. `VoidCube_cli/app.py` 降至 9,641 行，较本轮 Stage 3 开始前 10,157 行净减少 516 行；P0 总行数基线已同步下调。
6. 最终相关回归为 273 passed，完整 smoke 为 367 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。归档包含 `command_handlers.session` 与全部 shared contract/CLI adapter，且继续不包含已删除的 `VoidCube_cli/session_state.py`。

## 37. CLI-3 session adapter 实施记录

2026-07-29 已完成：

1. 新增 `VoidCube_cli.session_command_adapter`，集中拥有 `/resume` 的目标选择与 resume/branch summary 纯投影；数字目标按 1-based recent-session 快照解析，零、负数和超范围索引显式失败，title/ID 继续复用现有 resolver。非数字目标不再触发无意义的 recent-session repository 查询。
2. `command_handlers.session` 新增 `ResumeCommandPorts` / `BranchCommandPorts`；handler 只编排显式 callable ports、共享 `resume_session()` / `branch_session()` 和 state/hydration application，不访问 repository、CLI host 或 TUI 对象。recent-session 表格和完整 resumed history renderer 继续留在 CLI adapter。
3. registry 作为 CLI 组合根快照 branch 的 source、model、reasoning config、history 与时间，并把 resume 的 recent selection、named resolver、翻译标签和 history renderer 接入 execution table；默认翻译器也遵循 `default=` fallback 语义。
4. `VoidcubeCLI._handle_resume_command` / `_handle_branch_command` 已删除，不保留同名委托壳；连同此前迁出的 queue/statusbar/retry/title，六个旧 handler 名称扫描为零匹配。
5. `VoidCube_cli/app.py` 降至 9,526 行，较本轮 Stage 3 开始前 10,157 行净减少 631 行；P0 总行数基线已同步下调。
6. 最终治理相关回归为 182 passed，完整 smoke 为 394 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。355 项归档包含五个 `command_handlers` 文件、`session_command_adapter.py` 与 shared turn queue，且继续不包含已删除的 `VoidCube_cli/session_state.py`。

## 38. CLI-3 new/clear command adapter 实施记录

2026-07-29 已完成：

1. `command_handlers.session` 新增 `NewSessionCommandPorts` / `ClearCommandPorts`；finalize hook、trace reset、共享 `start_new_session()`、state/Agent application、reset hook 与可选提示按显式顺序执行。Agent 存在性只在 transition 起点快照一次，同时决定 hook 对和 `create_record`，避免 hook 改变 runtime 后出现半套 transition。
2. `/new` 与 `/clear` 均切换为 execution table 的 `handler_key`；tools 配置变更后的内部 reset 也改走统一 `/new` route。`VoidcubeCLI.new_session()`、`_notify_session_boundary()` 与 `_handle_clear_command()` 已删除，不保留委托壳或 silent 参数。
3. 新增 `VoidCube_cli.clear_command_adapter`，只负责 prompt_toolkit erase/cursor/flush、compact/full banner 和 fresh-start 文案投影；共享 `start_new_session()` 不包含终端、banner 或翻译文本。standalone clear 不再先 `console.clear()` 后又由 `show_banner()` 重复清屏。
4. 通用 `ChatConsole` 迁入 `cli_ui.py`，compact banner builder 迁入 `banner.py`；根 `cli.ChatConsole` / `cli._cprint` patch 契约通过可注入 emitter 保持。恒定返回 `None` 且只会渲染 `Tip: None` 的孤立 `VoidCube_cli/tips.py` 及旧 clear tip 分支已删除。
5. `VoidCube_cli/app.py` 降至 9,359 行，较本轮 Stage 3 开始前 10,157 行净减少 798 行；P0 总行数基线已同步下调。
6. 最终阶段相关回归为 275 passed，完整 smoke 为 403 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。355 项归档包含 clear/session adapters 与迁出的 banner/UI helper，不包含已删除的 `tips.py` 和 `session_state.py`。

## 39. CLI-3 info/operations command handler 实施记录

2026-07-29 已完成：

1. 新增 `command_handlers.operations`，`/stop` 只通过 `StopCommandPorts` 获取 process snapshot 与执行 `kill_all()`；无运行任务不触发 mutation，有运行任务先输出 snapshot count，再输出真实 kill count。
2. 新增 `command_handlers.info`，`/profile` 与 `/plugins` 只消费路径、discovery/list 和输出 ports。profile 名改用 `Path.parts[0]`，修复旧实现通过 `str(relative).split('/')` 在 Windows 反斜杠路径下会把整个子路径误当 profile 名的问题。
3. plugin registry 的真实 `list_plugins()` 返回 dict；旧方法直接迭代后把字符串 key 当 record 访问，安装插件时会落入总异常分支。registry 现在显式投影 `values()`，handler 稳定消费 records 序列，并覆盖空 registry、enabled/disabled、version、tools/hooks 与 load error 文案。
4. `/stop`、`/profile`、`/plugins` 均切换为 execution table `handler_key`；三个 `VoidcubeCLI._handle_*` 旧 owner 已删除，不保留 host 委托壳。process registry 与 plugin discovery 保持 lazy import，不增加 CLI 启动导入面。
5. `VoidCube_cli/app.py` 降至 9,293 行，较本轮 Stage 3 开始前 10,157 行净减少 864 行；P0 总行数基线已同步下调。
6. 最终阶段相关回归为 283 passed，完整 smoke 为 411 passed / 1171 deselected；wheel 构建、源码清单与退役集成扫描均通过。357 项归档包含 `command_handlers.info` / `operations`，不包含已删除的 `tips.py` 和 `session_state.py`。

## 40. CLI-3 attachments command handler 实施记录

2026-07-29 已完成：

1. 新增 `command_handlers.attachments`，`/paste` 与 `/image` 仅通过显式 clipboard、attachment state、path/file helper 和 output ports 编排，不接收 `VoidcubeCLI` host；平台提示、ANSI 与 Termux 示例继续属于 CLI adapter。
2. `/paste` 与 `/image` 均切换为 execution table 的 `handler_key`；`VoidcubeCLI._handle_paste_command` / `_handle_image_command` 已删除，不保留同名委托壳，旧 `_IMAGE_EXTENSIONS`、`_resolve_attachment_path` 与 `_split_path_input` imports 也已清理。
3. `_try_attach_clipboard_image()` 继续留在 `app.py`，因为 bracketed paste、Ctrl+V、Alt+V 和 `/paste` 共用同一个键盘/命令图像提取 owner；command handler 只接收其窄 callable port，不复制提取状态。`/image` 直接消费原始 `ParsedCliCommand.arguments`，保留带空格的引号路径与 trailing prompt 的原始大小写。
4. characterization 与 registry integration tests 已覆盖 Termux 短路和后续提示、clipboard empty/extraction failure/success、桌面与 Termux usage、missing/unsupported/supported file、带空格路径、trailing prompt，以及 resolved image 写入 host `_attached_images`。
5. `VoidCube_cli/app.py` 降至 9,241 行，较本轮 Stage 3 开始前 10,157 行净减少 916 行；P0 总行数基线已同步下调。
6. attachments/direct command/packaging 回归为 57 passed，受影响回归为 194 passed，阶段治理集为 301 passed，完整 smoke 为 421 passed / 1171 deselected；最新 wheel 构建与源码清单审计通过。358 项归档包含 `command_handlers.attachments`、info/operations/session handlers 与 clear/session adapters，不包含已删除的 `tips.py` 和 `session_state.py`。

## 41. CLI-3 history/save/undo command domain 实施记录

2026-07-30 已完成：

1. 新增 `command_handlers.history`，以三个独立 ports 集合隔离 `/history` 只读投影、`/save` filesystem export 与 `/undo` history mutation；handler 不接收 `VoidcubeCLI` host。`/history` 已覆盖空 history 的 recent-session 回退、tool message 折叠和 user/assistant 顺序；`/save` 继续使用当前目录下 `VoidCube_conversation_<timestamp>.json` 的既有默认路径和覆盖语义，命令参数仍不改变该既有导出名称。
2. `/undo` 继续委托 shared `remove_last_user_turn()` 完成 repository transaction；adapter 仅在 mutation 成功后同步 CLI history、Agent JSON transcript cursor 和 hydration metadata。`/retry` 复用同一 mutation ports，删除 `app.py` 的旧 `_remove_last_user_turn()`，避免两条命令重新分叉状态写入规则。
3. `/history`、`/save`、`/undo` 均切换为 execution table `handler_key`；`show_history()`、`save_conversation()` 与 `undo_last()` 已从 CLI host 删除。`/rollback` 在文件恢复成功后经统一 `/undo` execution route 对齐聊天上下文，不再引用已删除的 host owner。
4. 新增 direct characterization tests 覆盖空 history、参数不改变既有导出路径、默认路径、覆盖、写入失败、最后 user turn rollback 边界、Agent/hydration 同步与用户提示；registry integration test 覆盖 `/undo` 真实路由。
5. `VoidCube_cli/app.py` 降至 9,096 行，较本轮 Stage 3 开始前 10,157 行净减少 1,061 行；P0 增长基线已同步下调。

## 42. CLI-3 rollback checkpoint command domain 实施记录

2026-07-30 已完成：

1. 新增 `command_handlers.rollback`，通过 `RollbackCommandPorts` 隔离 Agent checkpoint manager、working directory、list/diff/restore、terminal output 和聊天同步；handler 不持有 `VoidcubeCLI`、JSON export 或 session repository。
2. 保留 `/rollback` 的无参数 list、`diff <N>`、`<N>` 和 `<N> <file>` 语法，以及 one-based checkpoint index / hash reference 解析、80 行 diff 截断、原有成功/失败提示。checkpoint manager 继续独占 hash/path 校验、restore 前快照和 shadow git 操作。
3. restore 成功且 history 非空时只经已注册的 `/undo` execution route 对齐聊天上下文；restore 失败、无 history、disabled checkpoint 或无 Agent 均不会触发 history mutation。`VoidcubeCLI._handle_rollback_command()` 和 `_resolve_checkpoint_ref()` 已删除，不保留委托壳。
4. 直接 characterization tests 覆盖无 Agent、disabled、list、diff usage/empty/invalid/failure/no-change/截断、file restore success、restore failure、chat sync 和 reference resolution；registry integration test 覆盖真实 `/rollback -> restore -> /undo -> Agent transcript` 路由。
5. `VoidCube_cli/app.py` 降至 8,989 行，较本轮 Stage 3 开始前 10,157 行净减少 1,168 行；P0 增长基线已同步下调。

## 43. CLI-3 configuration/toolsets display command domain 实施记录

2026-07-30 已完成：

1. `command_handlers.display` 新增 `ConfigDisplayPorts` 与 `ToolsetsDisplayPorts`；`/config` 只读取 runtime snapshot、environment 与 config path，`/toolsets` 只读取 catalog、locale description 与当前 selection，不接收 `VoidcubeCLI` host，也不触碰配置写入、鉴权或 model wizard。
2. `/config` 与 `/toolsets` 均切换为 execution table `handler_key`；`VoidcubeCLI.show_config()`、`show_toolsets()` 和只供旧 toolsets owner 使用的 lazy helper 已删除。`--list-toolsets` 改用同一 display handler，避免 CLI flag 与 slash command 再次分叉输出。
3. characterization tests 覆盖 API key 脱敏、SSH terminal projection、toolset enabled marker、localized labels 和 `/config` registry route；配置 path 存在性仍按原逻辑显示 loaded/not found。
4. `/status` 暂不并入本批：它还组合 session metadata、autonomous/subagent observability 和 Rich console projection，应以独立 command domain 迁出，避免 display ports 重新成为无边界聚合器。
5. `VoidCube_cli/app.py` 降至 8,882 行，较本轮 Stage 3 开始前 10,157 行净减少 1,275 行；P0 增长基线已同步下调。

## 44. CLI-3 session status command domain 实施记录

2026-07-30 已完成：

1. `command_handlers.display` 新增 `SessionStatusDisplayPorts`；handler 只组合 session metadata、runtime counters、subagent snapshot、autonomous sections 和单一 render port，不接收 `VoidcubeCLI` 或 repository。
2. registry 保留 repository `get_session()` 的异常到空 metadata fallback、home path、subagent/autonomous snapshot 与 Rich `console.print(..., highlight=False, markup=False)` adapter。handler 保留 started/updated/last-activity timestamp fallback、token/running、active/idle subagent 与 focus projection。
3. `/status` 已切换为 execution table `handler_key`，`VoidcubeCLI._show_session_status()` 和失效 status-only imports 已删除，不保留委托壳。
4. characterization 与 registry integration tests 覆盖 invalid metadata timestamp fallback、idle/active subagent、focus preview、autonomous sections 与真实 `/status` route。
5. `VoidCube_cli/app.py` 降至 8,814 行，较本轮 Stage 3 开始前 10,157 行净减少 1,343 行；P0 增长基线已同步下调。

## 45. CLI-3 tools catalog command domain 实施记录

2026-07-30 已完成：

1. `command_handlers.display` 新增 `ToolsCatalogPorts` 与只读 `handle_tools_catalog_command()`；它只接收 tool snapshot、toolset lookup、翻译和输出端口，保留原有 78 列标题、名称/toolset 排序、`unknown` fallback、首行首句 description 截断和总数文案。
2. `/tools` 无参数目录展示和 `--list-tools` 共用 `render_tools_for_host()`；`VoidcubeCLI.show_tools()` 与仅由它使用的 `_get_toolset_for_tool` lazy helper 已删除。
3. `/tools list|enable|disable` 继续由 `_handle_tools_command()` 持有，因为其配置写入、toolset reload 与 `/new` session reset 是状态变更，未被错误迁入 information handler。
4. characterization tests 覆盖空 catalog、toolset/name 排序、description 首句规则、总数及 `/tools` 目录分支使用共享 renderer。
5. `VoidCube_cli/app.py` 降至 8,764 行，较本轮 Stage 3 开始前 10,157 行净减少 1,393 行；P0 增长基线已同步下调。

## 46. CLI-3 help information command domain 实施记录

2026-07-30 已完成：

1. `command_handlers.display` 新增 `HelpDisplayPorts`、`HelpDisplayText` 与只读 `handle_help_display_command()`；它只接收 command categories、可用性过滤、技能目录、Termux 判断和语义化 terminal renderer ports。
2. `/help` 切换为 execution table `handler_key`。`VoidcubeCLI.show_help()` 已删除；ANSI frame、Rich markup escape、command/skill 行渲染和末尾提示由 registry 的 CLI renderer adapter 负责，不再混入 host。
3. app 现有 `_get_skill_commands()` cache 作为显式 discovery port 注入，仍同时服务动态技能命令执行；帮助 handler 不持有 host，也不触碰技能安装、配置或 command execution。
4. characterization 与 registry integration tests 覆盖 `/fast` 可用性过滤、命令类别顺序、技能排序、普通/Termux attachment tip、真实 `/help` route 与 autonomous gate 下的 route。
5. `VoidCube_cli/app.py` 降至 8,716 行，较本轮 Stage 3 开始前 10,157 行净减少 1,441 行；P0 增长基线已同步下调。

## 47. CLI-3 usage diagnostics command domain 实施记录

2026-07-30 已完成：

1. 评估确认 `/usage` 是唯一可在本批按 snapshot 迁出的只读诊断；`/debug` 上传报告，doctor 读取配置诊断，API/browser/MCP 都含外部副作用，继续保留专属 owner。
2. `command_handlers.info` 新增 `UsageDisplaySnapshot`、`UsageCommandPorts` 和 `handle_usage_command()`；它只投影 Agent availability、API call count、rate limit display、token/context/cost snapshot，不读取或修改 CLI host。
3. `/usage` 切换为 execution table `handler_key`。registry 负责在命令调用时组装 rate-limit 与 pricing ports；`VoidcubeCLI._show_usage()` 已删除。
4. 旧 `_show_usage()` 末尾错误归属的 logger-level 更新已迁回 `_toggle_verbose()`，使 `/usage` 不再有隐藏状态变更；只供旧 usage owner 使用的 pricing wrappers 也已删除，startup status 保留单一 duration lazy import。
5. characterization 与 registry integration tests 覆盖无 Agent、零 API calls、rate-limit display、cost/context projection 和真实 `/usage` route。
6. `VoidCube_cli/app.py` 降至 8,617 行，较本轮 Stage 3 开始前 10,157 行净减少 1,540 行；P0 增长基线已同步下调。

## 48. CLI-3 doctor diagnostics operation adapter 实施记录

2026-07-30 已完成：

1. 审计确认 `/doctor` 不是只读诊断：它会执行 terminal probe，并在隔离临时目录进行 `write_file -> patch -> search_files -> read_file` smoke test；该副作用不能迁入 information handler。
2. `command_handlers.operations` 新增 `DoctorCommandPorts` 与 `handle_doctor_command()`，只接受一个 `run_diagnosis` operation port；诊断、临时环境、terminal cleanup 和输出仍由 `config_validator.print_diagnosis()` 唯一持有。
3. `/doctor` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_doctor_command()` 已删除，不保留委托壳。
4. direct adapter、registry route 及既有主 CLI doctor entry/diagnostic tests 共同覆盖 port 调用和真实诊断入口，确保没有绕开原有 probe/smoke 行为。
5. `VoidCube_cli/app.py` 降至 8,612 行，较本轮 Stage 3 开始前 10,157 行净减少 1,545 行；P0 增长基线已同步下调。

## 49. CLI-3 API configuration operation adapter 实施记录

2026-07-30 已完成：

1. 审计确认 `/api` 是交互式 credential/config 写入操作，不属于只读 display handler；向导继续独占 API Key 验证、Provider/model 选择、配置与环境变量持久化和配置 reload。
2. `command_handlers.operations` 新增 `ApiCommandPorts` 与 `handle_api_command()`，只接受一个 `run_wizard` operation port。registry 以 `ApiConfigRuntime` 明确传入 model、provider、requested provider 三个可选更新回调，向导不再接收完整 CLI host。
3. `/api` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_api_command()` 已删除；TUI 的 API 配置入口改复用相同 `/api` command route，避免产生第二条 host-to-wizard 分派。
4. direct adapter 与 registry route 测试覆盖 wizard port 调用；API 配置单元测试继续覆盖持久化和 credential 行为。
5. `VoidCube_cli/app.py` 当前为 8,606 行，较本轮 Stage 3 开始前 10,157 行净减少 1,551 行；P0 增长基线已按架构测试的 Python 行数同步下调。

## 50. CLI-3 model switch command adapter 实施记录

2026-07-30 已完成：

1. 审计确认 `/model` 的 provider/model 解析、credential resolution、模型规范化和 metadata lookup 已归 `model_switch` 与共享 config/runtime provider owner；CLI host 仅保留 prompt_toolkit picker modal 和运行中 Agent 的切换结果应用。
2. 新增 `command_handlers.model` 的 `ModelCommandPorts` 与 `handle_model_command()`；handler 只编排 flag 解析、configured-provider 快照、shared `switch_model()` 调用、picker 打开与 result application 回调，不接收 `VoidcubeCLI` host。
3. `/model` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_model_switch()` 已删除；无参数 picker、带 `--provider` / `--session-only` 的切换参数及 global persistence 语义保持不变。
4. direct handler、registry route、picker snapshot 与原有 Agent/runtime application tests 覆盖参数保留、result application、provider snapshot、Agent switch、turn note 和 session-only 投影。
5. `VoidCube_cli/app.py` 降至 8,537 行，较本轮 Stage 3 开始前 10,157 行净减少 1,620 行；P0 增长基线已按 Python 行数同步下调。

## 51. CLI-3 provider status command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `ops.provider.handle_slash_provider()` 仅返回 `None`；旧 `/provider status|list` 分派会把这个空值输出，既不配置 Provider，也不拥有任何 operation。
2. `command_handlers.display` 新增 `ProviderDisplaySnapshot`、`ProviderDisplayPorts` 与 `handle_provider_display_command()`；无参数路径只投影 active provider、configured provider、endpoint 和 model snapshot，不接收 `VoidcubeCLI` 或写入配置。
3. `/provider` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_provider_switch()` / `_handle_provider_command()` 和空 `VoidCube_cli/ops/provider.py` 已删除。带参数的旧无效兼容分派统一进入既有 usage 指引，不再输出 `None`；Provider 配置仍明确由 `/api` 持有，切换仍由 `/model` 持有。
4. direct projection 与 registry route tests 覆盖 active/current marker、endpoint、model list、argument usage 和无 `None` 输出；packaging contract 确认已删除空模块不会重新进入 wheel。
5. `VoidCube_cli/app.py` 降至 8,454 行，较本轮 Stage 3 开始前 10,157 行净减少 1,703 行；P0 增长基线已按 Python 行数同步下调。

## 52. CLI-3 memory status command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `/memory` 不读取 memory provider 配置、不调用 `memory_setup.py`、不触发数据库 setup 或 migration；它只展示统一 Mem 状态与 canonical runtime memory database path。
2. `command_handlers.display` 新增 `MemoryDisplayPorts` 与 `handle_memory_display_command()`；handler 仅接收数据库路径与输出 port，不接收 `VoidcubeCLI`、Memory Service 或配置对象。
3. `/memory` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_memory_switch()` 已删除；既有 Mem status、工具和审计文案保持不变。
4. direct projection 与 registry route tests 覆盖数据库路径和固定状态文案，明确断言没有 setup/migration operation。
5. `VoidCube_cli/app.py` 降至 8,444 行，较本轮 Stage 3 开始前 10,157 行净减少 1,713 行；P0 增长基线已按 Python 行数同步下调。

## 53. CLI-3 personality command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `/personality` 只操作 `agent.system_prompt` overlay：profile catalog 来自启动配置，选择或清除后将 Agent 置空以按新 prompt 初始化，并通过既有 `save_config_value()` 持久化。它不触碰 model/provider runtime。
2. 新增 `command_handlers.personality` 的 `PersonalityCommandPorts`、`resolve_personality_prompt()` 与 `handle_personality_command()`；handler 仅消费 catalog、prompt setter、Agent reset、persist 和 output ports，不接收 `VoidcubeCLI`。
3. `/personality` 切换为 execution table `handler_key`，`VoidcubeCLI._resolve_personality_prompt()` / `_handle_personality_command()` 已删除。`none/default/neutral`、structured profile 的 tone/style 拼接、保存失败的 session-only 提示、未知项与列表展示语义保持不变。
4. direct handler 与 registry route tests 覆盖结构化 prompt、保存与 Agent reset、清除别名、unknown/list 和 session-only fallback。
5. `VoidCube_cli/app.py` 降至 8,387 行，较本轮 Stage 3 开始前 10,157 行净减少 1,770 行；P0 增长基线已按 Python 行数同步下调。

## 54. CLI-3 reasoning command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `/reasoning` 有两条独立状态路径：`show/on` 与 `hide/off` 更新 display flag 并刷新现有 Agent 的 reasoning callback；effort 更新 `reasoning_config` 并丢弃 Agent，以新配置重建。状态展示、未知参数提示及所有原有文案保持不变。
2. 新增 `command_handlers.reasoning` 的 `ReasoningCommandPorts`、`parse_reasoning_config()` 与 `handle_reasoning_command()`；handler 只消费 state getter/setter、callback refresh、parser、persistence 和 renderer ports，不接收 `VoidcubeCLI`。启动时的 persisted effort 解析复用同一个 parser，避免出现两套有效值规则。
3. 单值配置持久化收敛为 `VoidCube_app.config.save_config_value()`；删除 `app.py` 的重复实现。`/personality` registry port 同时改为延迟绑定这个公共 owner，修复此前真实路由会引用未定义函数的问题。
4. `/reasoning` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_reasoning_command()` 与旧 app-local parser 均已删除。registry 还补齐了 `Mapping` runtime import，避免 `/model` 的 configured provider projection 在真实路径上触发 `NameError`。
5. direct handler、公共 config helper 与真实 registry route tests 覆盖 display/effort mutation、Agent callback refresh/Agent reset、未知项、session-only persistence fallback 和 personality 的实际持久化绑定。
6. `VoidCube_cli/app.py` 降至 8,294 行，较本轮 Stage 3 开始前 10,157 行净减少 1,863 行；P0 增长基线已按 Python 行数同步下调。

## 55. CLI-3 fast priority-processing command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `/fast` 只管理 OpenAI-compatible priority-processing 的 `service_tier` session state：无参数或 `status` 只读投影，`fast/on` 设为 `priority`，`normal/off` 清空 tier 并使 Agent 失效。模型 capability 判定仍由 host `_fast_command_available()` 持有，request override 与 Agent 初始化仍由现有 runtime 持有。
2. 新增 `command_handlers.fast` 的 `FastCommandPorts`、`parse_service_tier_config()` 和 `handle_fast_command()`；handler 只消费 capability、state getter/setter、persistence 和 renderer ports，不接收 `VoidcubeCLI`。启动读取 persisted service tier 复用同一个 parser，避免两套 tier normalization。
3. `/fast` 切换为 execution table `handler_key`，`VoidcubeCLI._handle_fast_command()` 与 app-local service-tier parser 均已删除，不保留委托壳。配置写入继续复用 `VoidCube_app.config.save_config_value()`。
4. direct handler 与真实 registry route tests 覆盖 unavailable gate、status、`on/off` aliases、未知参数、保存失败 session-only 提示、Agent reset、持久化 binding 以及 persisted `priority`/`normal` normalization。
5. `VoidCube_cli/app.py` 降至 8,247 行，较本轮 Stage 3 开始前 10,157 行净减少 1,910 行；P0 增长基线已按 Python 行数同步下调。

## 56. CLI-3 manual compression command domain 实施记录

2026-07-30 已完成：

1. 审计确认 `/compress` 只发起人工 context compression；Agent `_compress_context()` 独占压缩引擎、focus-aware summarization、Mem pre-compress hook、system prompt refresh、continuation session 建立、SQLite cursor reset 和 file-dedup reset。自动 preflight/recovery compression 不经过 CLI handler，保持原运行时路径。
2. 新增 `command_handlers.compression` 的 `CompressionCommandPorts` 与 `handle_compression_command()`；handler 只消费 history/Agent getters、压缩 operation、同步 operation、token estimate、summary 和 output ports，不接收 `VoidcubeCLI`。
3. `/compress` 切换为 execution table `handler_key`，`VoidcubeCLI._manual_compress()` 已删除，不保留委托壳。最少四条消息、无 Agent、disabled、focus argument、noop feedback 和 operation failure 的既有用户语义保持不变。
4. Agent 新增 `persist_compressed_session_history()`：continuation session 创建后，由 registry 的成功同步 port 将完整压缩 transcript 交给 Agent persistence，然后同步 CLI `conversation_history`、`session_id` 并失效 hydration cache。此举修复旧 host 保留已结束 session id、且 continuation transcript 未立即持久化的状态分叉。
5. direct handler、Agent persistence 与真实 registry port tests 覆盖所有前置条件、focus、失败、session/history/hydration 对齐和完整 transcript persistence；旧 manual-compress owner 扫描为空。
6. `VoidCube_cli/app.py` 降至 8,185 行，较本轮 Stage 3 开始前 10,157 行净减少 1,972 行；P0 增长基线已按 Python 行数同步下调。

## 57. CLI-3 debug report operation adapter 实施记录

2026-07-30 已完成：

1. 审计确认 `/debug` 的唯一职责是以固定默认值发起 debug report share；报告采集、日志 tail、配置脱敏、网络上传、local fallback 和用户输出均由 `VoidCube_cli.debug.run_debug()` 持有。
2. `command_handlers.operations` 新增 `DebugCommandPorts` 与 `handle_debug_command()`；handler 只接收 `run_debug_share` operation port，不接收 `VoidcubeCLI`、日志路径或网络 client。
3. `/debug` 切换为 execution table `handler_key`，registry 显式构造 `debug_command="share"`、`lines=200`、`expire=7`、`local=False` 的 request；`VoidcubeCLI._handle_debug_command()` 已删除。此前旧 host 调用不存在的 `run_debug_share` 符号，现已修复为真实的 `run_debug()` owner。
4. direct adapter、registry binding 和真实 command route tests 覆盖 operation 调用、默认 request 参数及 execution route；外部上传没有进入单元测试。
5. `VoidCube_cli/app.py` 降至 8,177 行，较本轮 Stage 3 开始前 10,157 行净减少 1,980 行；P0 增长基线已按 Python 行数同步下调。

## 58. 下一次实施起点

下一批评估 `/browser` command domain：

1. 先确认 browser backend、CDP connection、配置写入和用户投影的状态边界，避免把浏览器 runtime 重新合并到 CLI host。
2. 仅提取可验证的 command adapter 与显式 ports；保留 browser operation、配置 persistence 和外部 I/O 的专属 owner。
3. 每项切换执行架构测试、退役集成扫描、wheel 构建和归档审计。
