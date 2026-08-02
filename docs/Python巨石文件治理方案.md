# VoidCube Python 巨石文件治理方案

> 状态：Stage 0 + CLI-0、Stage 2 UI 纯投影器、Stage 3 shared contract、CLI-3 command domain、CLI-4 TUI runtime、CLI-5 runtime boundary、terminal TTS owner/async adapter、Stage 4 TaskProfilePolicy/ScheduleAllocator、Stage 5 candidate/evidence/proposal/context/snapshot/selection/factory/learning/materialization/body-mapping/eligibility/adaptive-policy/drive-context/drive-state/history/candidate-stream/agenda/self-iteration/task-priors/LM-evidence-assembly/reflection/cognitive-posture/meta-cognition/cognitive-memory/cognition-charter/self-model/API-B snapshot/research/shell-body-profile/body-projection/pressure pure pipeline、activity projection 与 endogenous persistence repository 当前批次已完成；下一重点为剩余 Supervisor orchestration boundary。
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
| `VoidCube_cli/app.py` | 6,245 行 | command domain 已大量分离；仍混合 Agent 编排、TUI、语音 runtime 和部分 host command operation | P0 |
| `systems/supervisor/planning_runtime.py` | 8,887 行 | JSON persistence、只读 state projection、task profile policy 与 schedule allocation 已外移；`PlanningRuntimeMixin` 仍混合认知、治理和执行交接 | P0 |
| `systems/supervisor/endogenous_drive.py` | 1,240 行 | `EndogenousDriveEngine` 仍混合最终候选准备和运行时编排 | P0 |
| `systems/supervisor/ui_runtime.py` | 1,189 行 | 静态资源与只读 UI 投影已外移；runtime 保留资料加载、并发编排与 HTTP/SSE adapter | P0 |
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

`VoidcubeCLI.__init__` 约 304 行，混合以下状态：

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
- 更新 `VoidCube_cli.main`、`autonomous_runner.py`、`api_config.py`、语言 command handler 及测试导入。
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

`PlanningRuntimeMixin` 将以下职责放在一个 8,889 行类体内：

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

`EndogenousDriveEngine` 当前主要负责 DTO 装配、reflection/needs 的 pipeline 交接、最终 candidate stream 与 LM proposal orchestration；drive-state、adaptive policy、intent/signal、body projection、pressure/urgency、外部 research 与 evidence/cognition projection 均已迁出为纯 owner。候选流与 materialization 仍是下一阶段的主要 orchestration 边界。

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

## 15. 已完成治理总览（截至 2026-08-02）

以下内容替代已完成批次的逐条实时记录。它只描述当前仍有效的结构、所有权与验证基线；已删除的迁移过程、阶段性行数和过期“下一步”不再作为后续实现依据。

### 15.1 已完成范围

- Stage 0 / CLI-0：已建立 AST 依赖护栏、P0 增长基线、最小 `VoidCube_app` 配置/Gateway/model-normalization 边界，并消除生产包对根 `cli.py` 的反向导入。
- Stage 2：Supervisor 静态 UI 资源与纯投影器已外移；wheel 资源与 DOM 契约受测试覆盖。
- Stage 3 shared contract：session identity、approval/clarify、tool event、cancel/queue 均已有共享 contract 与 CLI adapter；queue 的生产、消费、interrupt 和 requeue 继续由 turn queue/runtime owner 持有。
- CLI-3 command domain：`process_command()` 通过 execution table 和 registry 组合显式 ports。已完成的 session、history、display、model、tools、skills、tasks、auto、plan、background、btw、language、voice、preset 等 domain 均不再保留同名 host command wrapper。
- 失效 `/connect` slash command 已删除：它唯一依赖的 `tools.connection_profiles` 是禁用空壳且接口已与旧调用漂移；受支持的配置 profile 继续由 `VoidCube_cli.profiles` 与 `VoidCube profile` 子命令唯一持有，不保留 SSH profile、网络探测或环境投影的伪兼容入口。
- CLI-4 TUI layout：根 `HSplit` 的固定 widget 顺序已迁至 `VoidCube_cli.tui_layout.build_tui_layout_children()`；它只接收显式 widgets 与 display-only extension callback，不接收 `VoidcubeCLI` 或修改 session、turn、queue、model、Gateway 状态。旧 host layout 方法已删除。
- CLI-4 TUI application：固定 theme 与 `prompt_toolkit.Application` 创建已迁至 `VoidCube_cli.tui_application.create_tui_application()`；它只接收 layout、keybindings、cursor 参数，线程与 event loop lifecycle 仍由 `run()` 持有。旧 host-local style dictionary 已删除。
- CLI-4 resize/reflow：终端缩窄后的残留行清理由 `VoidCube_cli.tui_application.install_resize_reflow_cleanup()` 持有；它只修补 prompt-toolkit renderer 的重绘位置，不读取或改写 CLI 的业务状态。旧 `run()` nested resize handler 已删除。
- CLI-4 text keybindings：Alt/Ctrl+Enter 的多行输入与 Tab 的 completion/suggestion 行为已迁至 `VoidCube_cli.tui_keybindings`；该 adapter 只接收 `KeyBindings` 与 prompt-toolkit buffer，不访问 CLI host、会话、turn 或 modal state。原 `run()` nested handlers 已删除。
- CLI-4 history keybindings：普通输入模式的 Up/Down 历史浏览也迁至 `VoidCube_cli.tui_keybindings`；`run()` 仅以 explicit modal-state predicate 作为 port 传入，adapter 不直接检查或改写 CLI state。
- CLI-4 modal navigation：clarify、approval、model picker 的箭头选择已迁至 `VoidCube_cli.tui_modal_navigation`；它通过显式 state getter 和 invalidate ports 完成边界计算，CLI 继续是 modal state 的唯一所有者。原 `run()` nested handlers 已删除。
- CLI-4 modal widgets：clarify、sudo、secret、approval 与 model picker 的只读 prompt-toolkit widget 工厂已迁至 `VoidCube_cli.tui_modal_widgets.build_modal_widgets()`；它只接收各 modal state getter、clarify freetext predicate 与 approval fragments callback，不接收 CLI host，也不处理选择、提交或状态转移。原 `run()` panel helpers 与五个 nested widget factory 已删除。
- CLI-4 indicator widgets：spinner、hint、input rule、image bar、voice status、autonomous panel 与 status bar 的 prompt-toolkit 构造已迁至 `VoidCube_cli.tui_indicator_widgets.build_indicator_widgets()`；它只接收 fragments、height 与 visible callbacks，不读取或改写 CLI state。原 `run()` 的八个 nested indicator widget factory 已删除，fragment 计算、输入处理、语音/autonomous runtime 与线程 lifecycle 继续留在既有 owner。
- CLI-4 input widgets：multiline `TextArea`、slash completer/history/suggestion、动态行高、password mask 与 placeholder processor 已迁至 `VoidCube_cli.tui_input_widgets`；它只接收 prompt、可用命令、read-only/password predicates 与 history path 等显式 ports。粘贴落盘、buffer business fallback、submit routing、turn/queue 与 modal/voice state 仍由 CLI 持有。原 `run()` 的 input factory、height closure 和 nested processor 已删除。
- CLI-4 scheduled task polling：scheduled executor 的 daemon poll loop 已迁至 `VoidCube_cli.scheduled_task_polling`；它只接收 stop predicate、poll operation、sleep 与 failure-report callbacks。CLI 继续创建并拥有 scheduled executor 与 shutdown state，原 `run()` nested loop/thread factory 已删除。
- CLI-4 TUI refresh lifecycle：spinner/presence refresh 的 daemon loop 已迁至 `VoidCube_cli.tui_refresh_loop`；它只编排既有 5 秒 presence、command-active 与 idle invalidate cadence，并通过显式 activity、refresh、invalidate、clock 与 sleep ports 调用 CLI owner。原 nested spinner loop/thread factory 已删除。
- CLI-4 input process loop：pending-input queue、execution gate、idle callback 与 execution callback 的循环机制已迁至 `VoidCube_cli.input_process_loop`；它只接收显式 queue/gate/stop/sleep/error ports，不持有 turn、Gateway、modal、voice 或 autonomous state。CLI 继续持有 queue、gate、`_execute_pending_input()` 及 MCP/autonomous/process-notification idle maintenance；原 nested process loop/thread factory 已删除。
- CLI-4 TUI teardown：退出时的 autonomous stop、agent interrupt、voice recorder、temporary recording、callback unregister、session close、interrupted-session plugin hook、global cleanup 与 exit summary 的既有顺序已迁至 `VoidCube_cli.tui_teardown.run_tui_teardown()`；它只编排显式 cleanup ports，不持有 shutdown、turn、Gateway、modal、voice 或 autonomous state。每个资源检查、异常处理和实际副作用继续留在 CLI owner，原 `run()` 内联收尾序列已删除。
- CLI-5 voice runtime state：CLI 旧同步 terminal voice transport 的跨线程 lock、Event、recorder、mode、recording、processing、continuous 与无语音计数已收敛到 `VoidCube_cli.voice_runtime_state.CliVoiceRuntimeState`；构造期和 interactive run() 都通过同一 state factory 初始化，`app.py` 的既有属性访问只作显式代理。没有接入不兼容的 `systems.voice.VoiceSessionManager`，避免形成两套设备/线程 lifecycle 的兼容主路径。
- CLI-5 voice recording runtime：旧同步 terminal transport 的录音前置检查、silence callback、音量刷新、STT、临时录音清理与连续录音重启已迁至 `VoidCube_cli.voice_recording_runtime`；它只接收 `CliVoiceRuntimeState`、TUI 通知、队列、线程与环境 predicate ports，不接收 CLI host、Agent、Gateway 或 modal state。CLI 仅保留薄端口组装和按键/command 入口，旧内联录制与转写流程已删除。
- CLI-5 embedded autonomous loop：embedded component 的 daemon polling、thread-local stdout/stderr 隔离、workflow poll、pending-input 执行、idle scene 与重绘节奏已迁至 `VoidCube_cli.embedded_autonomous_loop`；它只接收 stop/gate、刷新、queue、execute、invalidate、error 与 scene ports。CLI 继续拥有 component host/runtime、Gateway refresh 实现、scheduled gate、Agent interrupt 和 stop state，原 `run()` 外的 nested autonomous loop/thread factory 已删除。
- CLI-5 embedded autonomous lifecycle：child CLI host 的复用/创建、gate 标记、parent binding 与 task session 初始化已迁至 `VoidCube_cli.embedded_autonomous_host`；停止时的 child deactivation、可选 Agent/task interruption 与 loop signal 顺序已迁至 `VoidCube_cli.embedded_autonomous_stop`。两者只编排显式 lifecycle ports，CLI 继续拥有 host 配置、runtime、Agent 和 stop Event；原内联装配/stop sequence 已删除。
- CLI-5 terminal TTS owner ADR：已在 `docs/ADR-terminal-voice-owner.md` 明确 `systems.voice` 是设备、配置、STT/TTS、播放和中断的唯一 canonical owner；CLI 仅是 terminal adapter。没有 async adapter 前，`/voice tts` 不是可用能力，状态不得显示为 enabled；不得以 wrapper 恢复 legacy synchronous recorder/player contract。
- CLI-5 terminal TTS async adapter：`VoidCube_cli.voice_tts_adapter.VoiceTtsAdapter` 通过独立 asyncio loop 持有一个 canonical `VoiceSessionManager`，只暴露 `status`、`speak`、`interrupt`、`close` 四个窄操作；`/voice tts` 无文本时只查询状态，`/voice tts <text>` 异步播报，`/voice off` 与 TUI teardown 会中断并关闭桥接器。旧 `tts_unavailable` command port、状态文案和双路径 TTS 入口已删除；CLI 不接收或包装旧录音器 contract。
- Stage 4 planning activity projection：gateway timestamp normalize、idle duration、runtime observation input、Auto activity allowlist 与 Auto drive-input boundary 已迁至 `systems.supervisor.activity_projection`；它们仅接收 payload、clock 和 evidence 参数，不持有 Supervisor、Gateway 或 store。`PlanningRuntimeMixin` 的五个同名投影方法和内部 `self` 调用均已删除，Auto 仍由 runtime owner 负责获取 Gateway snapshot 与决定执行。
- Stage 4 endogenous persistence repository：runtime root 下四类 endogenous JSON snapshot 的显式路径、损坏/非对象 JSON 回退与原子写入已迁至 `systems.supervisor.endogenous_state_repository.EndogenousStateRepository`，由 `assemble_supervisor_runtime_state()` 注入。repository 不接收 Supervisor，也不持有 snapshot default、history trim、strategy-memory normalize、event semantic de-dup 或 regulation decay；这些仍归 `PlanningRuntimeMixin` 的领域策略。四个旧 `_get_endogenous_*_path()` helper 与测试兼容壳均已删除。
- Stage 4 endogenous state projection：bounded drive-history、governance-event stream 与 corrective-mode read model 已迁至 `systems.supervisor.endogenous_state_projection`。strategy-memory normalization 继续由 Planning domain owner 提供为显式 callback，projector 不读取 `self`、文件、Gateway 或 configuration。三个旧 Mixin helper 和测试调用均已删除。
- Stage 4 TaskProfilePolicy：任务 taxonomy 的 normalize、runtime profile、governance type、execution kind、request type 和 execution-request eligibility 已迁至 `systems.supervisor.task_profile_policy.TaskProfilePolicy`；它只接收 task/request 显式输入，由 `runtime_assemblers.py` 注入。`PlanningRuntimeMixin` 的旧 task-profile 方法和调用路径已删除，task serialization、review、handoff、recovery 与 activity projection 全部改走 policy。
- Stage 4 ScheduleAllocator：schedule value/metadata normalize、task token、occupied token、slot 对齐/分配、candidate reallocation、deterministic task sort 和 conflict index 已迁至 `systems.supervisor.schedule_allocator.ScheduleAllocator`；它只接收显式 task snapshot、occupied set、clock 与 interval，active task 查询和任务写回仍归 Planning owner。旧排程 helper 与测试入口已删除。
- Stage 5 candidate factory/evidence channel：`EndogenousTaskCandidate`、API-B projection、scored candidate factory、evidence channel/confidence/conflict、evidence graph 与 research freshness 已分别迁至 `systems.supervisor.endogenous_candidate_pipeline` 和 `systems.supervisor.endogenous_evidence`。Planning 的 core-value 调用和 Supervisor 测试已切到新 owner，`EndogenousDriveEngine` 的旧 DTO、factory、channel、graph、freshness helper 与旧导入入口已删除。
- Stage 5 LM proposal boundary：模型 client resolution、prompt transport、response status、batch cognitive-assessment normalization、proposal task/risk/evidence/execution normalization、candidate-kind defaults/constraints、reference alignment 与 supervisor advisory 已迁至 `systems.supervisor.endogenous_proposals`。`EndogenousDriveEngine` 只保留 runtime 配置解析、generation diagnostics 状态、候选资格 orchestration、cognitive scoring 与 candidate materialization；旧 prompt transport、LM normalization constants/helper、私有测试入口和 `review_then_backlog` alias 均已删除，不保留 Engine 代理或双路径。
- Stage 5 LM context/snapshot/selection boundary：LM evidence channel/context layering、generation snapshot projection、LM/heuristic candidate merge 与 API-B active-kind projection 已分别迁至 `systems.supervisor.endogenous_evidence`、`systems.supervisor.endogenous_context`、`systems.supervisor.endogenous_generation_snapshot` 和 `systems.supervisor.endogenous_candidate_pipeline`。`EndogenousDriveEngine` 只组装显式输入、持有 latest-generation 状态并执行最终 candidate materialization；旧 context/snapshot/selection helper、私有测试入口和伪参数路径已删除。
- Stage 5 stable family/learning boundary：memory maintenance、truthfulness、governance hygiene、body improvement、shell baseline、exploratory learning 与 cognitive-assessment review 的 candidate factory，以及 learning topic 提取、去重、冷却、novelty/specificity policy 已迁至 `systems.supervisor.endogenous_candidate_factories` 与 `systems.supervisor.endogenous_learning`。Engine 继续负责 eligibility、backlog pressure、body projection、drive judgement 和 candidate stream 编排；旧 learning factory、topic policy、stable-key 和测试入口已删除，不保留 Engine 代理或双路径。
- Stage 5 materialization boundary：candidate-kind spec/eligibility、LM cognitive-alignment scoring、constraints/metadata/evidence projection 与 scored candidate materialization 已迁至 `systems.supervisor.endogenous_materialization`。Engine 只准备 body projection、治理信号、backlog pressure 和 drive-judgement ports；旧 308 行 materialization、旧 cognitive-alignment helper 与测试入口已删除，不保留 Engine 双路径。
- Stage 5 body mapping boundary：canonical editable roots、evolution boundary/path safety、forbidden-pattern filtering、learning evidence freshness/ranking、显式路径与关键词到 body structure domain 的映射已迁至 `systems.supervisor.endogenous_body_mapping`。Engine 继续持有 body projection orchestration；旧 mapping helper、重复常量和直接 Engine 测试入口已删除，不保留双路径。
- Stage 5 candidate eligibility signal boundary：当前计数治理卫生信号与历史拖滞信号已迁至 `systems.supervisor.endogenous_materialization` 的纯函数；Engine 只组装最小 perception/history 输入，不保留旧私有 signal helper 或兼容调用路径。
- Stage 5 candidate eligibility plan boundary：family-first decision resolution 与 governance-type fallback 已迁至 `systems.supervisor.endogenous_materialization.resolve_candidate_eligibility_plan`。Engine 只提供显式 decision maps，不再保留 `_decision_for()` 私有 policy helper 或兼容调用路径。
- Stage 5 needs policy gate boundary：truthfulness threshold、truthfulness signal 与 memory backlog recovery window 已迁至 `systems.supervisor.endogenous_policy`，PlanningRuntime 与 Engine 共用同一 canonical owner；旧 Engine 常量和 gate helper 已删除。
- Stage 5 needs calculation boundary：`DriveNeed` DTO 与 memory/truthfulness/learning/body/governance/observation need 计算已迁至 `systems.supervisor.endogenous_needs.detect_needs`，通过显式 Protocol 输入返回排序后的纯 need projections。Engine 只负责 deliberation 编排与后续 intent/signal 生成；旧 `_detect_needs()` 与 Engine-owned DTO 已删除。
- Stage 5 LM eligibility input boundary：active API-B candidate-kind extraction、self-evolution/body projection/quota 和当前/历史 governance signal 的组合已迁至 `systems.supervisor.endogenous_materialization.resolve_lm_candidate_eligibility`。Engine 只准备显式标量、history/task 列表与 body projection；candidate stream 的静态 active-kind gate 仍由 Engine 编排。
- Stage 5 candidate stream eligibility boundary：memory/truthfulness/shell baseline/exploratory/governance/body 的 active-kind、existing-key、signal、quota 与静态完成冷却 gate 已迁至 `systems.supervisor.endogenous_candidate_eligibility`。Engine 只准备 plans、perception、reflection、body projection 与治理信号，候选 factory 和 topic 去重仍由各自 owner 负责；旧 `_has_recent_static_governance_completion()` 已删除，不保留 Engine 双路径。
- Stage 5 adaptive policy boundary：历史 family success、strategy-memory effectiveness、observation/agenda pressure、bias、focus、candidate budget 与 learning/body quota projection 已迁至 `systems.supervisor.endogenous_adaptive_policy`。Engine 只负责历史输入归一化、pressure/context 准备和 `DriveAdaptivePolicy` DTO 装配；旧 517 行策略实现与 `_strategy_context_key()` 已删除，不保留 Engine 双路径。
- Stage 5 body eligibility boundary：learning quality/freshness score、canonical shell-slot readiness、body improvement in-flight matching 与 completion cooldown 已迁至 `systems.supervisor.endogenous_body_eligibility`。Engine 只保留 body mapping orchestration 与通用 timestamp normalization；旧 body quality/cooldown helper、旧 quality score helper 已删除，不保留双路径。
- Stage 5 proposal drift/meta-cognition boundary：recent cognitive alignment、proposal drift memory 与统一 meta-cognition profile 已迁至 `systems.supervisor.endogenous_meta_cognition`。Engine 只装配显式 cognition inputs；旧 `_build_proposal_drift_memory()`、`_build_recent_cognitive_alignment_summary()`、`_build_meta_cognition_profile()` 与直接 Engine 测试入口已删除，不保留双路径。
- Stage 5 cognitive memory boundary：LM cognitive assessment、self-iteration trend、stay/switch regulation 与 post-task effect memory 已迁至 `systems.supervisor.endogenous_cognitive_memory`，共享显式 history/assessment/alignment normalization。Engine 只装配 memory inputs；旧四个 memory helper 与直接 Engine 测试入口已删除，不保留双路径。
- Stage 5 cognition charter boundary：charter model serialization、core mission/task-generation fallback 与 context layering/prompt attention defaults 已迁至 `systems.supervisor.endogenous_cognition_charter.resolve_cognition_charter`。Engine 只传入显式配置值；旧 `_resolve_endogenous_cognition_charter()` 已删除，不保留 runtime-config wrapper 或双路径。
- Stage 5 self-model boundary：recent reference alignment、self-model snapshot 与 evidence credibility summary 已迁至 `systems.supervisor.endogenous_self_model`。Engine 只装配 perception/world/reflection/evidence inputs；旧 self-model/reference/evidence helper 与测试入口已删除，不保留双路径。
- Stage 5 API-B snapshot boundary：active API-B judgement backlog 的 bounded projection 已迁至 `systems.supervisor.endogenous_api_b_snapshot`。Engine 只传入显式 task/status 输入，不保留 backlog snapshot 旧实现或兼容入口。
- Stage 5 research boundary：configured/file external research evidence 的开关、路径解析、JSON normalization 与 bounded output 已迁至 `systems.supervisor.endogenous_research`。Engine 只读取 runtime/execution 配置并传入 owner，不保留旧 research loader。
- Stage 5 shell body profile boundary：shell slot/worktree、origin manifest、present roots、body flags 与 evidence quality projection 已迁至 `systems.supervisor.endogenous_shell_profile`。所有生产调用已切换，旧 `_build_shell_body_profile()` 与专用导入已删除，不保留 Engine wrapper 或双路径。
- Stage 5 body projection boundary：body improvement eligibility 与 learning-evidence structure mapping 的组合已迁至 `systems.supervisor.endogenous_body_projection`。Engine 只传入 drive context 与 shell slot，不保留 body projection wrapper 或双路径。
- Stage 5 pressure boundary：backlog pressure penalty、memory-maintenance urgency、governance-hygiene urgency 与 lane penalty assembly 已迁至 `systems.supervisor.endogenous_pressure`。Engine 只传入显式 context/input，不保留 pressure/urgency 私有 helper 或旧测试入口。
- Stage 5 drive-state boundary：perception snapshot 输入归一化、user/system posture 与 world-model pressure/readiness projection 已迁至 `systems.supervisor.endogenous_drive_state`。Engine 只装配既有 `DrivePerceptionSnapshot`/`DriveWorldModel` DTO，不保留旧 perception/world-model helper。

### 15.2 当前 CLI 命令边界

| 范畴 | 当前 owner / 边界 |
| --- | --- |
| session、history、retry、undo、queue | `VoidCube_app` session/turn contract、`turn_queue_adapter` 与窄 command ports；handler 只映射 slash 输入。 |
| model、provider、reasoning、fast、compression、API、doctor/debug | 各专属 operation/config owner；registry 只绑定 ports。 |
| tools、skills、MCP、browser | catalog/config/probe/lifecycle operation 各自持有副作用；handler 不接收 CLI host 或 backend。 |
| background、tasks、scheduled task | `_start_background_agent_task()` 是共享后台 operation；`/tasks` 仅投影视图，不能与 scheduled outbox 或 subagent lane 合并。 |
| btw | `_start_btw_side_question()` 冻结 history 与 route snapshot，使用无工具、无持久化的独立 Agent；不进入 background registry、scheduler 或主 session。 |
| auto、auto-q | `autonomous_gate.py` 与 autonomous runtime 持有 daemon、gateway、scene 与 recovery；handler 只请求 activate/deactivate。 |
| language、voice | i18n/config 与 CLI voice runtime 分别持有 locale cache、设备、录音、线程、按键、TUI 状态；handler 仅解析参数并调用 operation。 |
| preset | `tools.preset_engine` 是只读 catalog owner，预设 YAML 以 `tools/presets/*.yaml` package data 发行；`apply` 在缺少 approved execution runtime 时结构化拒绝，不执行系统动作。 |

固定规则：已迁移 command handler 不接收完整 `VoidcubeCLI`、Agent、Supervisor、Queue、线程或配置对象；旧 `_handle_*` 委托壳和双路径兼容入口必须删除。

### 15.3 当前安全与发行约束

- 三类已退役的模型集成保持零入口：活跃代码、可加载技能和 wheel 均不得保留入口；涉及模型、鉴权、请求协议、技能或打包的改动必须运行退役集成扫描与相关测试。
- skill 安装先进入 quarantine，经 scan 和 integration policy 允许后才安装；不能通过 force 类参数绕过退役集成拒绝。
- deployment preset 仅描述潜在破坏性运维步骤。实际执行必须先单独建立 approved execution runtime、环境目标和审批契约，不能从 slash handler 直接调用系统命令。
- wheel 合同覆盖 Python 源、CLI locale、Supervisor UI、skills 相关资源、command handler 和 `tools/presets/*.yaml`，已删除模块不得重新进入发行物。

### 15.4 验证基线

CLI-4/CLI-5 当前相关联合回归基线为 `332 passed`；已运行架构检查、compileall、`git diff --check`、退役集成扫描和 wheel source-to-artifact 验证。

Stage 4 activity projection 的 focused 行为回归为 `5 passed`，覆盖 pure projection、runtime observation 与 memory-activity Auto decision；另外 `activity projection + packaging contract + 退役集成扫描` 为 `32 passed`，并已重建和校验 wheel。已运行 compileall、架构检查和 `git diff --check`。聚合 Supervisor 测试文件在产品的 60 秒单命令上限内未能完整结束，因此不将它表述为全量通过。

Stage 4 endogenous persistence repository 的 focused 回归为 `12 passed`，覆盖 repository 的 root/path/read/write/invalid-JSON 边界、真实 Supervisor 装配，以及既有 Supervisor governance/cognition/self-regulation persistence 行为。已运行相关 compileall 和 `git diff --check`；本批的架构、退役集成扫描和 wheel 验证结果继续在下方验证更新后记录。

Stage 4 endogenous state projection 的 focused 回归为 `16 passed`，覆盖三项纯 projection 与 13 项相关 Supervisor history/governance/cognition/self-regulation 行为。已运行相关 compileall；本批的架构、退役集成扫描、wheel 和最终 diff 检查将在当前验证完成后记录。

本批 terminal TTS async adapter 的 focused 回归为 `2 passed`；CLI voice handler、command execution 与 TUI teardown 联合回归为 `147 passed`，并已运行 compileall 与 `git diff --check`。既有 `test_voice_transport.py` 在当前 Python 3.11 环境中因缺少可选语音依赖 `sherpa_onnx`、`truststore` 和 `numpy` 有 9 项环境失败，不能表述为语音 transport 全量通过。

本批 TaskProfilePolicy 的纯 policy 回归为 `3 passed`，既有 runtime task profile 回归为 `3 passed`，Supervisor wiring 相关回归为 `21 passed`，task/profile/schedule/serialization 精确回归为 `9 passed`。`PlanningRuntimeMixin` 从 9,213 行降至 9,094 行；完整 `test_supervisor_autonomous_chain_store.py` 仍超过当前 60 秒单命令上限，未将其表述为全量通过。

本批 ScheduleAllocator 的纯计算回归为 `3 passed`，既有排程集成回归为 `4 passed`；`PlanningRuntimeMixin` 从 9,094 行降至 8,889 行。完整 `test_supervisor_autonomous_chain_store.py` 仍超过当前 60 秒单命令上限，未将其表述为全量通过。

本批 Stage 5 candidate/evidence pure pipeline 的模块回归为 `7 passed`，与 gap coverage 合并为 `43 passed, 1 xfailed`；Supervisor endogenous/learning 精确回归为 `74 passed`；架构/退役策略为 `14 passed`，TaskProfile/Schedule/activity 为 `9 passed`，TTS/CLI/TUI 为 `61 passed`，packaging contract 为 `20 passed`。已完成 production compileall、`git diff --check`、退役集成扫描和 wheel source-to-artifact 校验；`EndogenousDriveEngine` 从 9,303 行降至 8,866 行。聚合 Supervisor wiring 与完整 autonomous-chain 文件仍受当前 60 秒单命令上限限制，不将超时表述为全量通过。

本批 Stage 5 candidate factory/evidence channel 的纯模块与 gap 联合回归为 `47 passed, 1 xfailed`，Supervisor LM evidence/external research/channel 回归为 `6 passed`，candidate budget/selection 回归为 `7 passed`，runtime wiring 精确回归为 `1 passed`；架构/退役策略为 `14 passed`，packaging contract 与纯模块联合为 `31 passed`。`EndogenousDriveEngine` 从 8,866 行降至 8,448 行；四个 P0 增长基线已按当前实际值收紧。已完成 production compileall、`git diff --check`、生产退役扫描和 wheel source-to-artifact 校验。

本批 Stage 5 LM proposal boundary 新增模块回归为 `12 passed`，proposal/candidate/evidence 三块纯模块联合为 `23 passed`，Supervisor LM/认知/引用 focused 回归为 `16 passed`；完整 `test_supervisor_autonomous_chain_store.py` 为 `265 passed, 4 skipped`。架构/退役策略为 `14 passed`，packaging/documentation contract 为 `28 passed`。已完成 production compileall、`git diff --check`、生产退役扫描和 wheel source-to-artifact 校验；`EndogenousDriveEngine` 从 8,448 行降至 7,927 行，`_materialize_lm_task_proposals()` 从 351 行降至 308 行，P0 行数与大方法增长基线已同步收紧。

本批 Stage 5 learning boundary 新增纯模块回归为 `4 passed`，learning topic/候选相关 Phase 1 回归为 `4 passed`，candidate/gap 联合回归为 `45 passed, 1 xfailed`；完整 `test_supervisor_autonomous_chain_store.py` 为 `265 passed, 4 skipped`。架构/退役策略、packaging/documentation contract 与 production compileall 已通过，`git diff --check` 已通过。`EndogenousDriveEngine` 从 7,927 行降至 6,510 行，`_candidate_stream()` 从 441 行降至 364 行；learning topic policy、shell baseline、exploratory learning 与 cognitive-review factory 已完成直接 owner 测试。

本批 Stage 5 materialization boundary 新增纯模块回归为 `3 passed`，proposal/pipeline/factory/materialization 联合回归为 `24 passed`，Supervisor LM/引用/body focused 回归为 `56 passed`；完整 `test_supervisor_autonomous_chain_store.py` 为 `265 passed, 4 skipped`。架构/退役策略、packaging/documentation contract 与 production compileall 已通过，`git diff --check` 已通过。wheel 已重建并完成 source-to-artifact parity 与退役 marker 零入口审计。`EndogenousDriveEngine` 从 6,510 行降至 6,099 行，`_materialize_lm_task_proposals()` 从 308 行降至 80 行，旧大方法增长例外已从架构护栏删除。

本批 Stage 5 body mapping boundary 新增纯模块与 gap body 回归为 `8 passed`，Supervisor body-improvement/structure-mapping focused 回归为 `7 passed`；完整 Supervisor 文件在当前 122 秒单命令上限内未完成，不将超时表述为全量通过。`EndogenousDriveEngine` 从 6,099 行降至 5,826 行；`_candidate_stream()` 保持 364 行，`_materialize_lm_task_proposals()` 保持 80 行。body mapping 已完成直接 owner 测试，架构基线和文档状态同步收紧。

本批 Stage 5 candidate eligibility signal boundary 的 materialization pure 回归为 `4 passed`，governance/body/LM focused Supervisor 回归为 `14 passed`。`EndogenousDriveEngine` 从 5,826 行降至 5,789 行；治理卫生当前/历史 signal 已完成直接 owner 测试，旧 Engine 私有 signal helper 已删除。架构/退役/打包/文档契约与 production compileall 已完成最终验证并保持同步。

本批 Stage 5 candidate eligibility plan boundary 的 materialization owner 回归为 `5 passed`，candidate/deliberation focused Supervisor 回归为 `39 passed`；`test_supervisor_runtime_wiring.py` 全量为 `96 passed`，gap coverage 全量为 `36 passed, 1 xfailed`。`EndogenousDriveEngine` 从 5,789 行降至 5,777 行；family-first decision resolution 已完成直接 owner 测试，旧 `_decision_for()` 已删除。架构/退役/打包/文档契约、production compileall 与 `git diff --check` 已通过。

本批 Stage 5 needs policy gate boundary 的纯 policy 回归为 `2 passed`，与 materialization/gap 联合回归为 `43 passed, 1 xfailed`；`test_supervisor_runtime_wiring.py` 全量为 `96 passed`。`EndogenousDriveEngine` 从 5,777 行降至 5,759 行；truthfulness threshold、memory recovery gate 与 PlanningRuntime 的共享 import 已完成直接 owner 测试，旧 Engine policy gate 定义已删除。架构/退役/打包/文档契约与 production compileall 已通过。

本批 Stage 5 needs calculation boundary 的直接 owner characterization 回归为 `8 passed`；gap coverage 全量为 `36 passed, 1 xfailed`，`test_supervisor_runtime_wiring.py` 全量为 `96 passed`。`EndogenousDriveEngine` 从 5,759 行降至 5,440 行；`DriveNeed` 与 `_detect_needs()` 已迁至 `systems.supervisor.endogenous_needs`，保留排序、历史欠交边界、观察 gate 和 API-A 未结算时 body growth gate 语义。架构/退役/打包/文档契约与 production compileall 已通过。

本批 Stage 5 LM eligibility input boundary 的 materialization owner 回归为 `6 passed`，LM/body focused Supervisor 回归为 `17 passed`；gap coverage 全量为 `36 passed, 1 xfailed`，`test_supervisor_runtime_wiring.py` 全量为 `96 passed`。`EndogenousDriveEngine` 从 5,440 行降至 5,438 行；LM eligibility 组合已完成直接 owner 测试，Engine 不再直接调用底层 LM-kind eligibility rule。

本批 Stage 5 candidate stream eligibility boundary 新增 owner 回归为 `4 passed`，candidate/materialization/policy/learning/factory 联合回归为 `25 passed`；gap coverage 为 `36 passed, 1 xfailed`，`test_supervisor_runtime_wiring.py` 为 `96 passed`，静态完成冷却 focused 回归为 `2 passed`。`EndogenousDriveEngine` 从 5,438 行降至 5,410 行；`_candidate_stream()` 当前为 370 行，active-kind/body gate 与静态完成冷却已迁出 Engine，架构基线已同步收紧。此前大型 autonomous-chain 文件在当前命令上限内未完成的事实仍不改写为全量通过。

本批 Stage 5 adaptive policy boundary 新增 owner 回归为 `4 passed`，adaptive/gap focused 回归为 `5 passed`，autonomous-chain adaptive subset 为 `20 passed`。`EndogenousDriveEngine` 从 5,410 行降至 4,933 行，`_build_adaptive_policy()` 从 517 行降至 50 行；架构基线已删除失效的大方法例外。完整 autonomous-chain 文件仍不改写为全量通过。

本批 Stage 5 body eligibility boundary 新增 owner 回归为 `4 passed`，body/mapping/adaptive/candidate focused 回归为 `15 passed`，gap body/adaptive subset 为 `9 passed`，body improvement Supervisor subset 为 `8 passed`。`EndogenousDriveEngine` 从 4,933 行降至 4,837 行；body quality、slot readiness 和 cooldown gate 已完成直接 owner 测试，架构基线已同步收紧。

本批 Stage 5 intent/signal projection boundary 新增纯 owner 回归为 `5 passed`，全部 endogenous 纯模块回归为 `71 passed`，精选 Supervisor autonomous-chain intent/signal 回归为 `30 passed`，完整 autonomous-chain 回归为 `265 passed, 4 skipped`；`EndogenousDriveEngine` 从 4,837 行降至 4,550 行，intent priority、candidate-kind mapping、governance/truthfulness/observation/posture signal projection 已迁至 `systems.supervisor.endogenous_intent_signal`，Engine 只保留 DTO 装配 wrapper。架构、gap coverage（`36 passed, 1 xfailed`）、runtime wiring（`96 passed`）、退役扫描和 wheel 验证均已完成。

本批 Stage 5 drive-context boundary 新增 owner 回归为 `3 passed`，与 intent/signal、adaptive policy 联合回归为 `10 passed`；`build_drive_context()`、strategy-memory normalization 与 timestamp parsing 已迁至 `systems.supervisor.endogenous_drive_context`，Engine 删除旧私有入口并降至 4,355 行。backlog/stale/API-A lane 计数语义保持不变，架构基线已同步收紧。

本批 Stage 5 history boundary 新增 owner 回归为 `3 passed`，与 gap/context 联合回归为 `39 passed, 1 xfailed`；historical outcome ordering、scope、drag/relapse pressure 与 underdelivery detection 已迁至 `systems.supervisor.endogenous_history`，Engine 删除旧历史 helper 并降至 4,221 行。adaptive policy 与 reflection 的历史输入契约保持不变，架构基线已同步收紧。

本批 Stage 5 candidate stream assembler boundary 新增 owner 回归为 `2 passed`，candidate/gap/learning/factory/eligibility 联合回归为 `49 passed, 1 xfailed`；memory、truthfulness、learning、governance、body candidate 组装、LM merge 与最终 budget 已迁至 `systems.supervisor.endogenous_candidate_stream`。`_candidate_stream()` 从 370 行降至 141 行，`EndogenousDriveEngine` 从 4,221 行降至 3,972 行，Engine 只保留显式准备和调用编排。

本批 Stage 5 agenda graph boundary 新增 owner 回归为 `2 passed`，并与 candidate stream owner 回归合并为 `4 passed`；need、intent、signal、evidence topic 及其关系边的 agenda projection 已迁至 `systems.supervisor.endogenous_agenda`。旧 `_build_agenda_graph()` 已删除，`EndogenousDriveEngine` 从 3,972 行降至 3,733 行，Engine 不保留旧代理或双路径。

本批 Stage 5 self-iteration hypothesis boundary 新增 owner 回归为 `2 passed`，self-iteration/grounding/LM focused 回归为 `6 passed`，context/snapshot/proposal/evidence 联合回归为 `25 passed`；readiness、grounding、research、proposal drift、trend、switch regulation 与 post-task effect 的 hypothesis projection 已迁至 `systems.supervisor.endogenous_self_iteration`。旧 `_build_self_iteration_hypotheses()` 和 Engine 私有测试入口已删除，`EndogenousDriveEngine` 从 3,733 行降至 3,466 行，Engine 不保留代理或双路径。

本批 Stage 5 task-type prior boundary 新增 owner 回归为 `2 passed`，proposal-drift focused 回归为 `3 passed`；observation、review、learning、maintenance、improvement 的 program prior、drift adjustment 与 reason projection 已迁至 `systems.supervisor.endogenous_task_priors`。旧 `_build_task_type_priors()`、`_task_type_prior_reasons()` 已删除，`EndogenousDriveEngine` 从 3,466 行降至 3,238 行，Engine 不保留代理或双路径。

本批 Stage 5 LM evidence assembly boundary 新增 owner 回归为 `2 passed`，LM/autonomous focused 回归为 `11 passed`，context/snapshot/proposal/evidence 联合回归为 `25 passed`，gap focused 回归为 `11 passed`；grounding focus projection、context-layer assembly、packet plans/diagnostics/evidence/backlog 截断已迁至 `systems.supervisor.endogenous_lm_evidence`。`EndogenousDriveEngine._build_lm_evidence_packet()` 从 231 行降至 145 行，只保留显式字段准备与 cognition orchestration，`EndogenousDriveEngine` 从 3,238 行降至 3,152 行。

本批 Stage 5 reflection projection boundary 新增 owner 回归为 `2 passed`，gap reflection/deliberation 回归为 `8 passed`，Supervisor reflection/history focused 回归为 `20 passed`，adaptive/intent/materialization 联合回归为 `13 passed`；learning yield、API-B blockage、body cooldown、historical pressure、autonomy readiness 与 dominant constraint projection 已迁至 `systems.supervisor.endogenous_reflection`。旧 `_build_reflection()` 已删除，Engine 仅装配 `DriveReflection` DTO，`EndogenousDriveEngine` 从 3,152 行降至 2,976 行。

本批 Stage 5 cognitive posture boundary 新增 owner 回归为 `8 passed`，Supervisor cognitive-posture/proposal-drift/explanation focused 回归为 `14 passed`，gap cognition/posture focused 回归为 `2 passed`；manual profile、service pressure、truthfulness correction、drift/readiness、evidence repair 与 explanation pressure 的 posture selection 已迁至 `systems.supervisor.endogenous_cognitive_posture`。旧 `_resolve_cognitive_posture_from_policy()` 已删除，Engine 只准备显式 posture inputs 并调用纯 projection，`EndogenousDriveEngine` 从 2,976 行降至 2,807 行。

本批 Stage 5 proposal drift/meta-cognition boundary 新增 owner 回归为 `4 passed`，endogenous owner 联合回归为 `101 passed`，Supervisor proposal-drift/meta-cognition/posture focused 回归为 `21 passed`，gap cognition/posture focused 回归为 `2 passed`；recent cognitive alignment、proposal drift memory 与统一 meta-cognition profile 已迁至 `systems.supervisor.endogenous_meta_cognition`，`EndogenousDriveEngine` 从 2,807 行降至 2,458 行。`test_supervisor_runtime_wiring.py` 在当前 60 秒单命令上限内未完成，不将其表述为全量通过。

本批 Stage 5 cognitive memory boundary 新增 owner 回归为 `4 passed`，Supervisor auxiliary/post-task/meta focused 回归为 `27 passed`；LM cognitive assessment、self-iteration trend、stay/switch regulation 与 post-task effect memory 已迁至 `systems.supervisor.endogenous_cognitive_memory`，`EndogenousDriveEngine` 从 2,458 行降至 1,986 行。架构、退役、打包合同、production compileall 与 `git diff --check` 已完成最终验证。

本批 Stage 5 cognition charter boundary 新增 owner 回归为 `3 passed`，Supervisor cognition-charter/cognition focused 回归为 `16 passed`，runtime cognition/endogenous wiring 回归为 `3 passed`；charter fallback、context layering defaults 与 prompt attention defaults 已迁至 `systems.supervisor.endogenous_cognition_charter`，`EndogenousDriveEngine` 从 1,986 行降至 1,870 行。完整 runtime wiring 文件仍受当前 60 秒单命令上限限制，不将其表述为全量通过。

本批 Stage 5 self-model/API-B/research/shell input boundary 新增 owner 回归为 `3 + 2 + 2 + 4 passed`，shell/body/cognition focused Supervisor 回归为 `25 passed`；recent reference alignment、self-model/evidence credibility、API-B snapshot、configured/file research evidence 与 shell body profile 已迁至专属模块。`EndogenousDriveEngine` 从 1,870 行降至 1,486 行，旧 shell profile helper 与专用导入已删除；架构基线、production compileall 与 focused runtime wiring 已通过。完整 `test_supervisor_runtime_wiring.py` 仍受当前 60 秒单命令上限限制，不将其表述为全量通过。

本批 Stage 5 body projection/pressure boundary 新增 owner 回归为 `5 passed`，gap/body/pressure focused 回归为 `7 passed`；body improvement projection、backlog pressure penalty、memory-maintenance urgency、governance-hygiene urgency 与 lane assembly 已迁至专属模块。`EndogenousDriveEngine` 从 1,486 行降至 1,382 行，旧 body/pressure/urgency helper 与 gap 测试私有入口已删除；架构基线、production compileall 与 focused runtime wiring 已通过。

本批 Stage 5 drive-state boundary 新增 owner 回归为 `3 passed`，drive-state/body/pressure focused 回归为 `15 passed`，gap focused 回归为 `7 passed`，autonomous-chain focused 回归为 `29 passed`；perception snapshot 与 world-model projection 已迁至 `systems.supervisor.endogenous_drive_state`，Engine 仅保留 DTO 装配。`EndogenousDriveEngine` 从 1,382 行降至 1,240 行，架构基线、production compileall 与 focused runtime wiring 已通过。

当前 P0 行数：

| 文件 | 行数 |
| --- | ---: |
| `VoidCube_cli/app.py` | 6,245 |
| `systems/supervisor/planning_runtime.py` | 8,887 |
| `systems/supervisor/endogenous_drive.py` | 1,240 |
| `systems/supervisor/ui_runtime.py` | 1,189 |

### 15.5 仍未完成的治理主线

- CLI-4：已分离 `run()` 的 TUI application、layout、keybindings、modal、输入队列、status bar、lifecycle 与 teardown；保持 turn/queue runtime 及各 cleanup resource 的既有 owner。
- CLI-5：语音 runtime state 已完成第一批收敛；后续只迁移剩余录音调用者并删除 `tools.voice_mode` 的 transitional facade，不复制设备、线程或后台生命周期。
- Stage 4 / 5：TaskProfilePolicy 与 ScheduleAllocator 已完成；Stage 5 candidate DTO/factory/scoring/adaptive budget/signature、evidence normalization/channel/graph/freshness、LM proposal transport/normalization/reference advisory、LM context/snapshot、selection merge、stable candidate families、learning topic policy、materialization、body structure mapping/eligibility、body projection、candidate eligibility、adaptive policy、pressure/urgency、drive-state、needs policy gates、needs calculation、LM eligibility input projection、intent/signal projection、drive-context normalization、history normalization、candidate stream assembler、agenda graph projection、self-iteration hypothesis projection、task-type prior projection、LM evidence assembly、reflection projection、cognitive posture projection、proposal drift/meta-cognition projection、cognitive memory projection、cognition charter、self-model、API-B snapshot、research 与 shell body profile projection 已迁至专属模块。`EndogenousDriveEngine` 仍持有最终 stream preparation 与剩余 runtime orchestration。endogenous JSON repository、只读 state projection 与 Planning 的纯排程计算已完成，不得重新把已迁移 helper 放回旧 owner。
- Stage 6：继续收口 Supervisor UI route/adapters，并删除已迁移的 Mixin owner。

## 16. 下一次实施起点

下一批继续 Stage 5，优先评估最终 stream preparation、backlog pressure/urgency 与 body projection 的剩余 orchestration 边界；保持 candidate kind、评分、冷却、body projection、intent/signal payload 和 API-B 判断语义不变。同时为剩余 terminal 录音调用者建立迁移清单；不得把已完成的 repository、projection、ScheduleAllocator、candidate/evidence/proposal/context/snapshot/learning/materialization/body-mapping/body-eligibility/eligibility/adaptive-policy/policy/needs/intent-signal/agenda/self-iteration/task-priors/LM-evidence-assembly/reflection/cognitive-posture/meta-cognition/cognitive-memory/cognition-charter/self-model/API-B-snapshot/research/shell-body-profile 模块扩张为 Supervisor facade 或恢复旧 transport/helper 入口。
