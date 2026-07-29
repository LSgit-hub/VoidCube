# VoidCube Python 巨石文件治理方案

> 状态：Stage 0 + CLI-0 第二批已完成，进入 CLI-0 组合根收口。  
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
| `cli.py` | 10,345 行 | `VoidcubeCLI` 有 178 个方法，运行时、TUI、命令、会话和语音共享大量可变状态 | P0 |
| `systems/supervisor/planning_runtime.py` | 9,460 行 | `PlanningRuntimeMixin` 有 186 个方法，持久化、认知、排程、治理和执行交接混合 | P0 |
| `systems/supervisor/endogenous_drive.py` | 9,303 行 | `EndogenousDriveEngine` 有 124 个方法，感知、候选、LM 上下文、证据和策略记忆混合 | P0 |
| `systems/supervisor/ui_runtime.py` | 10,396 行 | 6,700 余行前端源码与 83 个状态投影/API 方法混在一个 Python 文件 | P0 |
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

`VoidcubeCLI.__init__` 约 317 行，混合以下状态：

- Provider、模型、凭证和 Agent；
- session、conversation、checkpoint 和 resume；
- TUI application、状态栏、spinner 和模态输入；
- 工具进度、审批、secret、sudo 和 clarify；
- 语音录制、TTS 和持续监听；
- 自主组件、后台任务、scheduled execution 和执行锁。

`run()` 约 1,733 行，`chat()` 约 521 行。已有 `chat_stream_*`、`command_execution.py` 等模块，但部分模块仍通过 `host: Any` 或反向导入 `cli.py` 访问聚合状态，说明拆分尚未形成稳定依赖方向。

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

## 17. 下一次实施起点

下一批执行 **CLI-0 组合根收口**：

1. 为 `VoidcubeCLI` 机械迁移补充 import、single query、interactive startup 和 resume characterization tests。
2. 将实现迁到 `VoidCube_cli.app`，同步切换 `VoidCube_cli.main` 与自主诊断入口，随后删除两个根导入例外。
3. 根 `cli.py` 收口为稳定导出与入口；内部调用全部切换后删除全局配置兼容访问和无调用 helper。

下一批仍不展开 session/turn/TUI 责任迁移，避免在 canonical 实现移动时同时改变行为。
