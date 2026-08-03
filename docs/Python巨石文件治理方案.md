# VoidCube Python 巨石文件治理方案

> 状态：Stage 0 + CLI-0、Stage 2 UI 纯投影器、Stage 3 shared contract、CLI-3 command domain、CLI-4 TUI runtime、CLI-5 runtime boundary、terminal TTS owner/async adapter、Stage 4 TaskProfilePolicy/ScheduleAllocator、Stage 5 candidate/evidence/proposal/context/snapshot/selection/factory/learning/materialization/materialization-context/LM-evidence-packet/LM-generation-request/LM-generation-execution/candidate-stream-assembly/body-mapping/eligibility/adaptive-policy/drive-context/drive-input-normalization/drive-state/drive-models/deliberation/history/candidate-stream/candidate-stream-preparation/agenda/self-iteration/task-priors/LM-evidence-assembly/LM-evidence-context/reflection/cognitive-posture/meta-cognition/cognitive-memory/cognition-charter/self-model/API-B snapshot/research/shell-body-profile/body-projection/pressure/drive-judgement/LM-materialization-runtime/runtime-config-adapters/runtime-gate/latest-generation-state-projection/LM-application-state-port/cognition-state-projection/proposal-cognition-projection/proposal-memory-compaction pure pipeline、activity projection 与 endogenous persistence repository 当前批次已完成；adaptive policy 输入归一化、drive-judgement、LM evidence context/packet、LM generation request/execution、drive input normalization、candidate stream assembly、LM materialization runtime、runtime config adapters、LM runtime gate、deliberation、candidate preparation projection、latest-generation state application port、LM application state port、cognition state read-model projection、proposal cognition read-model projection 与 proposal memory compaction 已并入 owner；下一重点为剩余 Supervisor orchestration boundary。
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
| `systems/supervisor/planning_runtime.py` | 8,072 行 | JSON persistence、只读 state projection、task profile policy 与 schedule allocation 已外移；`PlanningRuntimeMixin` 仍混合认知、治理和执行交接 | P0 |
| `systems/supervisor/endogenous_drive.py` | 231 行 | `EndogenousDriveEngine` 仍保留 LM proposal 调用交接与 latest-generation state write-back | P0 |
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

`EndogenousDriveEngine` 当前主要负责最终 proposal 调用交接与 latest-generation state 写回；runtime config adapters、LM runtime gate、deliberation、drive-state、adaptive policy、intent/signal、body projection、pressure/urgency、外部 research、evidence/cognition projection、candidate stream assembly 与 LM materialization runtime 均已迁出为专属 owner。latest-generation state 已通过单一只读 application projection 暴露，PlanningRuntime 的 reasoning/proposal 双消费已收敛为一次 LM application snapshot；cognition state、proposal cognition read-model 与 proposal memory compaction 也已迁出为纯 projection owner。Engine 仍是该 runtime state 的唯一写入 owner，剩余边界集中在 state write-back 与更广的 Supervisor orchestration。

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


### 15.1 当前安全与发行约束

- 三类已退役的模型集成保持零入口：活跃代码、可加载技能和 wheel 均不得保留入口；涉及模型、鉴权、请求协议、技能或打包的改动必须运行退役集成扫描与相关测试。
- skill 安装先进入 quarantine，经 scan 和 integration policy 允许后才安装；不能通过 force 类参数绕过退役集成拒绝。
- deployment preset 仅描述潜在破坏性运维步骤。实际执行必须先单独建立 approved execution runtime、环境目标和审批契约，不能从 slash handler 直接调用系统命令。
- wheel 合同覆盖 Python 源、CLI locale、Supervisor UI、skills 相关资源、command handler 和 `tools/presets/*.yaml`，已删除模块不得重新进入发行物。

### 15.2 验证基线

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

本批 Stage 5 drive-model boundary 新增 serialization owner 回归为 `1 passed`，模型/state/pressure/body focused 回归为 `9 passed`，autonomous-chain focused 回归为 `29 passed`，runtime wiring focused 回归为 `10 passed`；drive DTO 与 deliberation serialization 已迁至 `systems.supervisor.endogenous_drive_models`，测试不再从 Engine 导入模型。`EndogenousDriveEngine` 从 1,240 行降至 1,036 行，架构基线、production compileall 与 wheel parity 已通过。

本批 Stage 5 adaptive-policy input boundary 新增 owner 回归为 `1 passed`（adaptive owner 合计 `5 passed`），runtime wiring focused 回归为 `10 passed`；strategy/history normalization 与 context-key assembly 已并入 `build_adaptive_policy`，Engine 删除 40 行组装壳并降至 996 行。架构基线、production compileall 与 diff check 已通过。

本批 Stage 5 drive-judgement boundary 新增 owner 回归为 `2 passed`，autonomous-chain 相关回归为 `31 passed`，runtime wiring 为 `10 passed`，candidate/materialization/adaptive/drive-judgement 联合回归为 `15 passed`；candidate judgement metadata 已迁至 `systems.supervisor.endogenous_drive_judgement`，Engine 删除旧 metadata helper 并降至 908 行。架构基线、production compileall 与退役/打包合同待本批最终验证后同步确认。

本批 Stage 5 LM evidence context boundary 新增 owner 回归为 `1 passed`（LM evidence owner 合计 `3 passed`），LM/cognition/context/snapshot focused 回归为 `18 passed`，candidate/materialization/drive-judgement/adaptive/proposal focused 回归为 `27 passed`；posture context 与 LM cognition projection 已并入 `systems.supervisor.endogenous_lm_evidence`，`EndogenousDriveEngine` 删除重复 context orchestration 与失效导入并降至 814 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 deliberation boundary 新增 owner 与输入 normalization 回归为 `2 passed`，deliberation/state/reflection/intent/policy focused 回归为 `15 passed`，candidate/LM/materialization/cognition 联合回归为 `15 passed`；完整 deliberation DTO pipeline 已迁至 `systems.supervisor.endogenous_deliberation`，Engine 删除三个 projection wrapper、neutral adaptive fallback 与 shell-slot 私有入口并降至 611 行。架构、退役、打包、production compileall 与 wheel 合同待本批最终验证后同步确认。

本批 Stage 5 materialization context 与 candidate stream preparation boundary 新增 owner 回归为 `2 passed`，materialization/candidate/LM/deliberation 联合回归为 `22 passed`；LM materialization inputs 已迁至 `systems.supervisor.endogenous_materialization`，candidate stream preparation 已迁至 `systems.supervisor.endogenous_candidate_stream`，Engine 仅保留 LM runtime/materialization callbacks 并降至 510 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 LM evidence packet boundary 新增 owner 回归为 `1 passed`，LM evidence/cognition/materialization focused 回归为 `18 passed`；LM evidence packet preparation 已迁至 `systems.supervisor.endogenous_lm_evidence`，Engine 删除重复 evidence/context/packet assembly 并降至 447 行，latest-generation diagnostics/proposals 状态保持在 Engine。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 LM generation request/execution boundary 新增 owner 回归为 `2 passed`，proposal/generation/evidence focused 回归为 `20 passed`；charter/role/limit request normalization、result filtering 与 diagnostics snapshot assembly 已迁至 `systems.supervisor.endogenous_proposals`，Engine 继续唯一持有 latest-generation state，当前为 437 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 drive-input normalization boundary 新增 owner 回归为 `1 passed`，drive-context/proposal/generation gap 回归为 `21 passed`；`_resolve_drive_input` 已删除，mapping/empty-input normalization 已迁至 `systems.supervisor.endogenous_drive_context`，Engine 当前为 423 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 candidate stream assembly 与 LM materialization runtime boundary 新增 owner/迁移回归为 `2 passed`，endogenous 全集为 `143 passed`，gap coverage 为 `36 passed, 1 xfailed`；preparation DTO assembly 已迁至 `systems.supervisor.endogenous_candidate_stream.assemble_prepared_candidate_stream`，LM materialization context、backlog-pressure/drive-judgement callback binding 已迁至 `systems.supervisor.endogenous_materialization.materialize_lm_proposals_for_deliberation`，Engine 删除 `_materialize_lm_task_proposals` 并降至 334 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 runtime-config adapter boundary 的 endogenous/proposal owner 回归为 `144 passed`，Supervisor evidence/generation focused 回归为 `7 passed`；LM evidence runtime config 读取已迁至 `systems.supervisor.endogenous_lm_evidence.build_lm_evidence_packet_from_runtime_config`，LM generation runtime config 读取已迁至 `systems.supervisor.endogenous_proposals.execute_lm_task_generation_from_runtime_config`，Engine 删除 `_build_lm_evidence_packet` 与 `_generate_lm_task_proposals` 私有入口，当前为 233 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 LM runtime gate boundary 新增 owner 回归为 `1 passed`，proposal/runtime wiring focused 回归为 `25 passed`；`is_lm_task_generation_enabled` 已成为 Engine 与 PlanningRuntime 共用 gate owner，Engine 删除冗余 `service_runtime is None` 分支并降至 232 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 latest-generation state application port boundary 新增 characterization 回归为 `1 passed`，endogenous 全集为 `146 passed`，Supervisor runtime wiring 为 `2 passed`，LM/evidence focused 为 `6 passed`；`get_latest_lm_task_generation_state()` 已成为唯一只读 state projection，联合返回 context/proposals 并以深复制隔离嵌套调用方修改，Engine 继续是 latest-generation state 的唯一写入 owner，当前为 231 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 LM application state port boundary 新增 owner characterization 回归为 `2 passed`，proposal owner/wiring 联合回归为 `21 passed`，Supervisor autonomous LM/evidence focused 回归为 `6 passed`；同一 evaluate cycle 的 LM reasoning state 与第二候选 pass proposal override 已由 `systems.supervisor.endogenous_proposal_port.project_lm_generation_application_state` 统一投影，PlanningRuntime 删除重复 getter/helper 路径并降至 8,858 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 cognition state projection boundary 新增 owner characterization 回归为 `3 passed`，cognition-focused Supervisor 回归为 `6 passed`，runtime wiring 为 `2 passed`；cognition read-model assembly 已迁至 `systems.supervisor.endogenous_cognition_state.build_cognition_state_projection`，PlanningRuntime 保留领域输入计算与 persistence owner，当前降至 8,786 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 proposal cognition projection boundary 新增 owner characterization 回归为 `1 passed`，proposal cognition focused 回归为 `8 passed`，runtime wiring 为 `2 passed`；proposal cognition 最终 read-model assembly 已迁至 `systems.supervisor.endogenous_proposal_cognition.build_proposal_cognition_projection`，PlanningRuntime 继续持有 history fallback 与认知计算，当前降至 8,711 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 proposal memory compaction boundary 新增 owner characterization 回归为 `1 passed`，endogenous 全集为 `151 passed`，Supervisor autonomous chain store 为 `265 passed, 4 skipped`；bounded auxiliary-memory projection 已迁至 `systems.supervisor.endogenous_proposal_cognition.compact_proposal_memory`，PlanningRuntime 删除旧 286 行 compaction 方法，当前降至 8,426 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 endogenous drive evaluation orchestration boundary 新增 owner characterization 回归为 `2 passed`；evaluation 输入准备、Engine 调用、LM snapshot 应用、self-regulation repass、persistence/read-model 分支、UI activity 与 response assembly 已迁至 `systems.supervisor.endogenous_drive_orchestration.evaluate_endogenous_drive`，通过显式 `EndogenousDriveEvaluationContext` 注入 runtime callbacks；PlanningRuntime 保留资源、history、UI 与 state write-back owner，当前降至 8,238 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 5 endogenous drive cycle boundary 新增 owner characterization 回归为 `1 passed`，cycle/posture focused 回归为 `15 passed`；posture candidate gate 与 evaluation-to-persistence-to-plan-to-gateway activity 编排已迁至 `systems.supervisor.endogenous_drive_cycle`，PlanningRuntime 仅保留 runtime callback wiring，并删除 `_gate_endogenous_candidates_by_posture` 私有实现，当前降至 8,072 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI state orchestration boundary 新增 owner characterization 回归为 `2 passed`，UI state/cognition focused 回归为 `6 passed`，完整 runtime wiring 为 `96 passed`；chain projection、observation/memory/body adapters、trace enrichment、cognition/LM panel assembly 与 final web-room snapshot 已迁至 `systems.supervisor.ui_state_orchestration`。`SupervisorUIMixin` 保留缓存、HTTP、store、voice/media 与生命周期 owner，当前 `ui_runtime.py` 降至 931 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI stream/route adapter boundary 新增 owner characterization 回归为 `2 passed`，完整 runtime wiring 为 `96 passed`；state、voice-level、media SSE transport 与 media enqueue request normalization 已迁至 `systems.supervisor.ui_stream_adapters`，`SupervisorUIMixin` 仅保留 callback wiring 和 media/voice state owner，当前 `ui_runtime.py` 降至 862 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI identity/proxy adapter boundary 新增 owner characterization 回归为 `4 passed`，完整 runtime wiring 保持 `96 passed`；identity archive/turns、evolution promotion audit/candidates、owner consent、identity experience verification 与 Gateway memory-service discovery 已迁至 `systems.supervisor.ui_identity_proxy_adapters`，`SupervisorUIMixin` 仅保留 route callback 与 gateway/header context wiring，当前 `ui_runtime.py` 降至 601 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI memory-status/trace adapter boundary 新增 owner characterization 回归为 `4 passed`，UI adapter/state focused 回归为 `8 passed`，完整 runtime wiring 为 `96 passed`；Tier 1/rules health HTTP loading 已迁至 `systems.supervisor.ui_memory_status_adapters`，trace record collection、observation timeline、trace detail loading 与 observation enrichment 已迁至 `systems.supervisor.ui_trace_adapters`，`SupervisorUIMixin` 仅保留 runtime callback/context wiring，当前 `ui_runtime.py` 降至 517 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI body/snapshot adapter boundary 新增 owner characterization 回归为 `3 passed`，UI owner focused 回归为 `11 passed`；body registry status/card loading 已迁至 `systems.supervisor.ui_body_status_adapters`，observation input 与 memory stats 的 timeout/cache/default normalization 已迁至 `systems.supervisor.ui_snapshot_adapters`，缓存与 body registry 仍由 Supervisor runtime 持有，当前 `ui_runtime.py` 降至 471 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI activity persistence boundary 新增 owner characterization 回归为 `2 passed`，UI owner focused 回归为 `13 passed`，完整 runtime wiring 保持 `96 passed`；activity load/persist/clear、合法 scene guard、recent projection 与 latest drive candidate snapshot 已迁至 `systems.supervisor.ui_activity_adapters`，`SupervisorUIMixin` 仅保留 deque/path/history callback wiring，当前 `ui_runtime.py` 降至 420 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 Stage 6 Supervisor UI media/lifecycle adapter boundary 新增 owner characterization 回归为 `5 passed`，UI owner focused 回归为 `18 passed`，完整 runtime wiring 保持 `96 passed`；media revision/current payload mutation 与 auto-open timer/browser scheduling 已迁至 `systems.supervisor.ui_media_state_adapters`、`systems.supervisor.ui_open_lifecycle_adapters`，`SupervisorUIMixin` 保留 media state callback 与 config route wiring，当前 `ui_runtime.py` 保持 420 行。架构、退役、打包、production compileall 与 wheel 合同已通过。

本批 CLI-5 terminal voice recording caller boundary 新增 `VoiceSessionManager.transcribe_once()` canonical operation 与 terminal adapter wiring；`voice_recording_runtime` 仅保留 CLI 状态投影、按键中断和输入队列映射，TTS/录音/转写共用一个 `VoiceTtsAdapter` 持有的 canonical manager，移除 beep、同步 recorder contract、临时录音清理 facade 与全部 `tools.voice_mode` 生产入口；voice recording/CLI focused 回归为 `155 passed`，新增 canonical transcribe characterization 通过。完整可选 voice transport 回归仍受本机缺少 `numpy`、`sherpa_onnx`、`truststore` 等依赖影响，未将这些环境缺失误记为本批逻辑失败。

本批 CLI-5 scheduled execution boundary 将 `ScheduledTaskExecutorRuntime` 从完整 `VoidcubeCLI` host 迁移到显式 `ScheduledTaskExecutorPorts`；scheduled executor 仅通过 busy-state、execution gate、session id、active flag 与 background-start callback 端口协调，不再反射读取或写入 CLI host 属性。scheduled task/polling 回归为 `25 passed`，CLI/架构 focused 回归为 `150 passed`，P0 `__init__` 增长护栏恢复通过。

本批 CLI-5 manual background task runtime boundary 将 `_start_background_agent_task` 的 tracking、agent 创建、timeout/interruption、completion callback 与 worker cleanup 迁至 `VoidCube_cli.background_task_runtime`；`BackgroundTaskState` 成为 tracking owner，`VoidcubeCLI` 仅组装显式 ports 与终端显示 callback，删除原 200 行内嵌 worker 实现。background/CLI/scheduled 联合回归为 `94 passed`，architecture growth guard 与 compileall 通过。

本批 CLI-5 embedded autonomous component lifecycle boundary 新增 `EmbeddedAutonomousComponentRuntime` 与 `EmbeddedAutonomousRuntimePorts`；child host ensure、loop start、pending input、status refresh、idle scene、stop sequencing 与 interrupt callback 已从 `app.py` 内联实现迁至 coordinator，CLI 仅提供显式生命周期 callback。embedded host/loop/stop/autonomous gate 回归为 `76 passed`，architecture focused 回归保持通过。

本批 CLI-5 `AutonomousExecutorRuntime` host-state boundary 新增 `AutonomousExecutorPorts`；session identity、running task/current task state、pending input、agent-running gate、last-turn result、timeout writeback 与 autonomous execution event callback 已通过显式 ports 注入，executor 不再持有或读取 CLI host，`app.py` 的 autonomous turn integration 改用 runtime state API。autonomous executor/embedded/CLI 回归为 `134 passed`，architecture/integration/documentation/packaging/scheduled 联合回归为 `65 passed`，production compileall、退役扫描、wheel contract 与 diff check 已通过。

本批 CLI-5 pending-input command/turn boundary 新增 `PendingInputRuntime` 与 `PendingInputExecutionPorts`；文件拖入、粘贴展开、slash command 分流、agent turn busy lifecycle、连续语音重启与 process completion notification 已从 `app.py` 内联实现迁至 runtime，CLI 仅提供命令、turn、UI 和队列 ports。pending-input/CLI/autonomous 回归为 `132 passed`，`app.py` 从 6,245 行降至 6,105 行，production compileall 已通过。

本批 CLI-5 threaded turn execution boundary 新增 `TurnExecutionRuntime` 与 `TurnExecutionPorts`；agent worker thread、interactive interrupt polling、clarification defer、autonomous timeout interruption、async-client cleanup 与 stream/output flush 已从 `chat()` 内联实现迁至 runtime，`chat()` 保留模型输入准备、outcome/session 状态和 response rendering owner。turn runtime/CLI/autonomous 回归为 `148 passed`，`app.py` 当前为 6,076 行，production compileall 已通过。

进度记录：conversation-history/result application、run-loop lifecycle 与 Enter keybinding routing 已迁移到对应 runtime；相关回归 `191 passed`，`app.py` 当前为 5,930 行。

进度记录：Ctrl+C/D 控制键、push-to-talk、bracketed/快捷键图片粘贴与大文本折叠已迁移到显式 ports runtime；既有 `TuiTeardownPorts` 继续作为退出收尾边界，本批 focused 回归 `27 passed`，`app.py` 当前为 5,829 行。

进度记录：Ctrl+Z 与 placeholder、modal hint、spinner 动态文本已迁移到显式 ports runtime，并补齐 `run()` 的 modal widget 组合 wiring；相关回归 `17 passed`，`app.py` 当前为 5,795 行。

进度记录：startup 展示、resume/recent session、tool/skill registry 计数与 application/layout 组合已迁移到显式 ports runtime；相关回归 `16 passed`，`app.py` 当前为 5,774 行。

进度记录：signal、asyncio exception、stdin preflight 与 teardown ports wiring 已迁移到显式 lifecycle boundary；相关回归 `11 passed`，`app.py` 当前为 5,763 行。

进度记录：idle maintenance、process completion drain 与 interactive application wait/atexit wiring 已迁移到 `CliIdleMaintenanceRuntime`、`CliApplicationRuntime` 显式 ports；新增 runtime focused 回归 `5 passed`，CLI/TUI/lifecycle 联合回归 `117 passed`，`app.py` 当前为 5,752 行。

进度记录：Enter、Ctrl+C/D/Z、voice、paste、文本编辑、modal navigation 与 history navigation 的注册 wiring 已迁移到 `TuiKeybindingAssemblyRuntime` 显式 ports；TUI/CLI focused 回归 `92 passed`，`app.py` 当前为 5,718 行。

进度记录：input、modal、indicator widget graph 的构造、placeholder 安装与 buffer text-change wiring 已迁移到 `TuiWidgetGraphRuntime` 显式 ports；TUI/CLI 联合回归 `86 passed`，`app.py` 当前为 5,713 行。

进度记录：interactive run 的队列、配置 watcher、modal、附件与 voice state snapshot 已迁移到 `CliInteractiveStateRuntime`，CLI 继续接管并持有状态；同步清理失效 voice state 测试断言，相关回归 `77 passed`，`app.py` 当前为 5,706 行。

进度记录：plugin manager 引用、command busy reset、terminal prompt callbacks 与 tirith security preflight 已迁移到 `CliInteractivePreflightRuntime` 显式 ports；CLI/TUI/autonomous 回归 `78 passed`，`app.py` 当前为 5,705 行。

进度记录：paste 文件 UTF-8 持久化已收口到 `TuiPasteRuntime` 显式目录/时钟端口，`TuiRuntimeFactory` 已统一 keybinding、widget graph 与 composition wiring；相关 CLI/TUI/架构/文档/打包回归 `88 passed`，production compileall、退役扫描与 wheel 合同通过，`app.py` 当前为 5,634 行。

进度记录：interactive lifecycle 的 loop/application 端口拼装已迁移到 `CliInteractiveLifecycleRuntime`，CLI 保留状态 callback 与具体 host wiring；相关 CLI/TUI/生命周期回归 `56 passed`，架构/文档/打包/退役合同 `47 passed`，`app.py` 当前为 5,624 行。

进度记录：idle maintenance ports 已纳入 `CliInteractiveLifecycleRuntime`，由 coordinator 统一创建并接入 `CliRunRuntime`；本阶段完整 CLI/TUI 回归 `56 passed`，架构/文档/打包/退役合同 `47 passed`，`app.py` 当前为 5,621 行。

进度记录：resumed-history 的过滤、ANSI/reasoning 清理、tool-call 摘要与截断展示已迁移到 `CliHistoryDisplayRuntime`，CLI 仅保留显式 display ports 入口；CLI/TUI/启动/恢复回归 `115 passed`，架构/文档/打包/退役合同 `47 passed`，`app.py` 当前为 5,441 行。

进度记录：status bar 的模型/上下文、middle/git 布局、窄终端裁剪与回退已迁移到 `CliStatusBarRuntime` 显式 display ports；runtime/CLI 相关回归 `16 passed`，`app.py` 当前为 5,328 行。

进度记录：supervisor memory/scene、error indicator 与 subagent 摘要的 middle status 格式化已迁移到 `CliMiddleStatusRuntime` 显式 ports；middle/status/CLI 回归 `19 passed`，`app.py` 当前为 5,193 行。

进度记录：subagent manager 任务投影与 session model/token/context snapshot 已迁移到 `CliSubagentObservabilityRuntime`、`CliStatusSnapshotRuntime` 显式 ports；status/autonomous/CLI 回归 `90 passed`，`app.py` 当前为 5,095 行。

进度记录：git status 的 60 秒缓存、后台刷新、remote/变更片段和异常回退已迁移到 `CliGitStatusRuntime` 显式 ports；git/status/CLI 回归 `90 passed`，`app.py` 当前为 5,036 行。

进度记录：后台任务完成/失败提示、prompt 截断与 response panel 已迁移到 `CliBackgroundResponseRuntime` 显式 display ports；后台/response/CLI 回归 `85 passed`，`app.py` 当前为 5,031 行。

进度记录：voice status footer 与退出 session resume 摘要已迁移到 `CliVoiceStatusRuntime`、`CliExitSummaryRuntime` 显式 display ports；voice/lifecycle/CLI 回归 `11 passed`，`app.py` 当前为 5,014 行。

进度记录：`/btw` ephemeral side-question 的线程、临时 agent、历史快照、结果展示与错误回退已迁移到 `CliBtwRuntime` 显式 ports；btw/command/CLI 回归 `150 passed`，`app.py` 当前为 4,970 行。

进度记录：quick/plugin/skill/redirect/ambiguous dynamic command 的解析后执行已迁移到 `CliDynamicCommandRuntime` 显式 ports，内置命令优先级保持不变；dynamic command/CLI 回归 `163 passed`，`app.py` 当前为 4,923 行。

进度记录：单回合 model/provider route 与 fast-mode request override 投影已迁移到 `CliTurnAgentRouteRuntime` 显式 ports；route/command/scheduled 回归 `94 passed`，`app.py` 当前为 4,914 行。

进度记录：runtime credential/provider/model resolution 与 interactive、background、`/btw` agent initialization wiring 已迁移到 `CliRuntimeCredentialsRuntime`、`CliAgentInitializationRuntime` 显式 ports；CLI 保留错误展示、session/Gateway 副作用和 agent 生命周期，相关回归 `83 passed`，`app.py` 当前为 4,884 行。

进度记录：recent-session 查询过滤与 in-chat 表格展示已迁移到 `CliSessionBrowserRuntime` 显式 ports；CLI 保留 session 状态与生命周期变更，command/session 回归 `147 passed`，`app.py` 当前为 4,882 行。

进度记录：model picker 的 provider/model 两级选择、返回/取消与 switch dispatch 已迁移到 `CliModelPickerRuntime` 显式 ports；CLI 保留 picker state、model mutation 与 UI callback，model/command/TUI 回归 `75 passed`，`app.py` 当前为 4,855 行。

进度记录：session hydration cache/history projection 与 interactive resume preload 状态展示已迁移到 `CliSessionHydrationRuntime`、`CliSessionResumeRuntime` 显式 ports；CLI 保留 session lifecycle state owner，session/startup/command 回归 `64 passed`，`app.py` 当前为 4,841 行。

进度记录：single-query resume status 与 session lifecycle state application 已迁移到 `CliSingleQueryResumeRuntime`、`CliSessionLifecycleRuntime` 显式 ports；CLI 保留 session 属性 owner 和 agent 生命周期 callback，resume/lifecycle/command 回归 `215 passed`，`app.py` 当前为 4,848 行。

进度记录：chat 内联 agent-call 的 voice prefix、model-switch note、trace id 与异常结果投影已迁移到 `CliAgentTurnCallRuntime` 显式 ports；CLI 保留 turn execution 与 response/session owner，agent/chat/autonomous 回归 `152 passed`，`app.py` 当前为 4,853 行。

进度记录：chat 的图片、`@` context expansion、surrogate 清理与 `begin_turn` 输入准备已迁移到 `CliTurnInputPreparationRuntime` 显式 ports；CLI 保留 conversation history owner，input/command/autonomous 回归 `147 passed`，`app.py` 当前为 4,836 行。

进度记录：chat outer exception 的 failed observation、autonomous timeout/writeback 与滚动输出抑制已迁移到 `CliChatErrorRuntime` 显式 ports；CLI 保留 finally 生命周期恢复，error/command/autonomous 回归 `147 passed`，`app.py` 当前为 4,834 行。

进度记录：response panel/rendering 与 interrupted follow-up requeue 的 finalization 组合已迁移到 `CliChatFinalizationRuntime` 显式 ports；CLI 保留 display/queue owner，response/follow-up/command 回归 `129 passed`，`app.py` 当前为 4,829 行。

进度记录：session close 与 interrupted-session hook 的 teardown 边界已迁移到 `CliSessionTeardownRuntime` 显式 ports；CLI 保留具体 repository、agent 与 plugin hook wiring，teardown/autonomous 回归 `71 passed`，`app.py` 当前为 4,830 行。

进度记录：interactive preflight、keybinding runtime 注册与 voice record key 规范化已迁移到 `CliInteractiveRegistrationRuntime` 显式 ports；CLI 保留插件、状态 callback 与 TUI factory wiring，registration/lifecycle/TUI 回归 `77 passed`，`app.py` 当前为 4,829 行。

进度记录：CLI-owned registrations、paste/modal/input/indicator/composition callback 到通用 TUI factory 的 host assembly 已迁移到 `CliTuiHostAssemblyRuntime`；CLI 保留状态 callback 与 widget extension wiring，TUI/lifecycle/autonomous 回归 `78 passed`，`app.py` 当前为 4,809 行。

进度记录：idle maintenance、Gateway presence 的 idle/forced refresh 与 interactive lifecycle ports assembly 已迁移到 `CliInteractiveLifecycleAssemblyRuntime`；CLI 保留具体状态、Gateway 与 teardown callback，lifecycle/TUI/autonomous 回归 `77 passed`，`app.py` 当前为 4,800 行。

进度记录：prompt symbol、profile suffix、voice RMS bar、compact rendering 与交互状态优先级已迁移到 `CliTuiPromptRuntime` 显式 ports；CLI 保留 prompt state callback 与 extension hook，prompt/TUI/lifecycle/autonomous 回归 `81 passed`，`app.py` 当前为 4,739 行。

进度记录：terminal width、窄终端 compact policy、input rule、agent spacer 与 spinner height 已迁移到 `CliTuiLayoutMetricsRuntime`；autonomous/status 现有 host consumer 仅保留转发 adapter，layout/prompt/voice/status/dynamic-text 回归 `82 passed`，`app.py` 当前为 4,721 行。

进度记录：autonomous panel 的 terminal width、trim、pad 已收口到 `AutonomousPanelRenderPorts`，CLI 主渲染路径不再直接读取 panel host 的显示方法；autonomous/layout/prompt 回归 `72 passed`，`app.py` 当前为 4,733 行。

进度记录：autonomous panel 的 gate、session、current task、agent/turn、pending input、spinner 与 execution events 已收口到 `AutonomousPanelStatePorts`，CLI 主渲染路径不再读取 `state_host`；autonomous/panel/TUI 回归 `74 passed`，`app.py` 当前为 4,765 行。

进度记录：autonomous panel 事件追加与 Supervisor 事件同步已收口到 `AutonomousPanelEventPorts`，删除无调用的 panel height 旧入口；focused 回归 `126 passed`，完整 CLI/TUI 回归 `450 passed`，架构/文档/集成/打包合同 `42 passed`，`app.py` 当前为 4,806 行。

进度记录：CLI/TUI wrapper 的额外 keybindings、application composition 与 extra widgets 已收拢为 `CliTuiExtensionPorts`，core TUI state ports 与扩展 hook wiring 分界明确；CLI/TUI 回归 `450 passed`，架构/文档/集成/打包合同 `42 passed`，`app.py` 当前为 4,809 行。

进度记录：clarify/approval/sudo/secret/model-picker 的 modal callback 投影与 normal-input/password-mask policy 已迁移到 `CliTuiModalStateRuntime`；CLI/TUI 回归 `451 passed`，架构/文档/集成/打包合同 `42 passed`，`app.py` 当前为 4,807 行。

进度记录：`/fast` 模型能力判断与 TUI/help/command availability 投影已迁移到 `CliCommandAvailabilityRuntime`，app 仅保留显式模型与 capability callback adapter；CLI/TUI 回归 `453 passed`，架构/文档/集成/打包合同 `42 passed`，`app.py` 当前为 4,826 行。

当前 P0 行数：

| 文件 | 行数 |
| --- | ---: |
| `VoidCube_cli/app.py` | 4,826 |
| `systems/supervisor/planning_runtime.py` | 8,072 |
| `systems/supervisor/endogenous_drive.py` | 231 |
| `systems/supervisor/ui_runtime.py` | 420 |

### 15.3 仍未完成的治理主线

- CLI-4：已分离 `run()` 的 TUI application、layout、keybindings、modal、输入队列、动态提示/状态文本、startup 展示、status bar、idle maintenance、process notification、application wait、atexit、signal/asyncio/stdin guards、lifecycle 与 teardown；保持 turn/queue runtime 及各 cleanup resource 的既有 owner。
- CLI-5：terminal voice recording caller 已迁移到 canonical `systems.voice` owner，并删除 `tools.voice_mode` transitional facade；scheduled execution、manual background task runtime、embedded autonomous component lifecycle、`AutonomousExecutorRuntime` host-state boundary、pending-input command/turn boundary、threaded turn execution、response rendering、turn postprocessing、interrupted-input queue、result application、run-loop lifecycle、Enter/control keybinding、push-to-talk 与 paste boundary 已迁移到显式 ports，CLI 仅保留命令、显示和具体 host wiring owner，不复制设备、线程或后台生命周期。
- Stage 4 / 5：TaskProfilePolicy 与 ScheduleAllocator 已完成；Stage 5 candidate DTO/factory/scoring/adaptive budget/signature、evidence normalization/channel/graph/freshness、LM proposal transport/normalization/reference advisory、LM context/snapshot/LM evidence context/packet、LM generation request/execution、runtime config adapters/runtime gate、deliberation、materialization context/runtime、candidate stream preparation/assembly、selection merge、stable candidate families、learning topic policy、materialization、body structure mapping/eligibility、body projection、candidate eligibility、adaptive policy/input normalization、pressure/urgency、drive-state/models、needs policy gates、needs calculation、LM eligibility input projection、intent/signal projection、drive-context normalization、history normalization、candidate stream assembler、agenda graph projection、self-iteration hypothesis projection、task-type prior projection、LM evidence assembly、reflection projection、cognitive posture/context projection、proposal drift/meta-cognition projection、cognitive memory projection、cognition charter、self-model、API-B snapshot、research、shell body profile、drive-judgement projection、latest-generation state application projection、LM application state port、cognition state projection、proposal cognition projection 与 proposal memory compaction 已迁至专属模块或明确 application port。`EndogenousDriveEngine` 仍持有 proposal 调用交接与 latest-generation state 写回，是 runtime state 的唯一 owner。endogenous JSON repository、只读 state projection 与 Planning 的纯排程计算已完成，不得重新把已迁移 helper 放回旧 owner。
- Stage 6：Supervisor UI 的 state、stream、identity/proxy、memory status、trace、body status、snapshot、activity persistence、media state 与 auto-open lifecycle 边界已收口；剩余 route registration 和 Supervisor 生命周期注册仍保留在 `supervisor.py` owner 内。

## 16. 下一次实施起点

下一批继续盘点 `run()` 中剩余的 indicator display assembly；保持阶段记录简短，并维护已完成 ports 与 `tools.voice_mode` 零旧入口约束。
