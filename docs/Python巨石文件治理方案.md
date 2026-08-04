# VoidCube Python 巨石文件治理方案

> 状态：治理未完成。Stage 0、Stage 1、Stage 2、Stage 3、Stage 4 已完成；Stage 6 部分完成；Stage 5 基本完成；Stage 7 尚未开始。当前优先完成 Endogenous 等价验收和 Stage 6 的 adapter/lifecycle 边界，最后进入 Stage 7 全量验收。
> 基线日期：2026-08-04。
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

| 文件/边界 | 当前规模 | 当前判断 | 优先级 |
| --- | ---: | --- | --- |
| `VoidCube_cli/app.py` | 约 4,900 行 | 命令、TUI 和 turn 子责任已大量外移；共享 session/turn 状态已有唯一 runtime owner，类仍承担 Agent 生命周期和 host wiring | P0 |
| `systems/supervisor/planning_runtime.py` | 约 1,670 行 | 保留运行时投影、内生驱动输入/评估、跨快照组合和显式 service 委托；已拆出的业务责任不得回迁 | P0，持续观察 |
| `systems/supervisor/endogenous_drive.py` | 231 行 | 主流水线已组件化；Engine 仅保留 facade、LM proposal 交接和 latest-generation state 写回 | P0，接近收口 |
| `systems/supervisor/ui_runtime.py` | 420 行 | 静态资源和主要投影已外移；`SupervisorUIMixin` 仍保留 HTTP/SSE、缓存和生命周期 owner | P0 |
| `systems/supervisor/supervisor.py` | 约 600 行 | 组合根仍内联注册全部路由，`_setup_routes()` 尚未成为薄 route adapter | P0 边界 |
| 共享包到前端的依赖边界 | 0 条例外 | `agent`、`systems`、`VoidCube_core`、`VoidCube_app` 当前不依赖 `VoidCube_cli`，必须持续保持 | P0 护栏 |

次级观察对象包括 `run_agent.py`、Memory Service、Gateway、`VoidCube_cli/config.py` 和 `VoidCube_cli/main.py`。它们暂不与 P0 主线同时展开；只有在 P0 拆分需要明确依赖边界，或其修改频率和缺陷率达到第 10 节阈值时才进入后续批次。

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

### 2.3 治理基线与原则

本方案以当前源码、可加载包和边界测试共同构成事实基线。治理判断以责任所有权和依赖方向为主，文件行数只用于发现趋势，不作为完成条件。

- **单一责任 owner**：每类状态、持久化、策略判断和副作用只有一个生产写入 owner；编排层只组合，不复制业务规则。
- **显式边界优先**：跨模块调用使用稳定 contract、输入输出模型或端口，不以完整 host、`Any`、`getattr` 或隐式 `self` 作为接口。
- **行为等价迁移**：拆分只改变实现位置，不顺带改变模型、记忆、治理、执行和前端语义；行为变化必须另立设计与验收。
- **迁移完成即清理**：生产调用者切换后删除旧实现、重复字段、失效参数和无调用兼容路径，不保留双写或永久委托壳。
- **测试验证边界**：新 owner 必须可独立测试，阶段验收同时覆盖依赖护栏、受影响链路和发行物契约；局部测试通过不等于阶段完成。
- **文档只保留当前状态**：本文只维护有效约束、责任边界、阶段状态、退出条件和有限的后续顺序，不记录提交、批次、测试数量、旧规模或临时风险。

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

完成状态必须按责任边界判定：仅创建新模块、增加端口 dataclass、缩短单个方法或让 focused tests 通过，都不能单独把阶段标记为完成。对应生产调用者必须切换到新 owner，旧实现和双写路径必须删除，阶段验收项必须全部满足。

四条 P0 主线的收口含义固定为：

- `VoidCube_cli/app.py` 只保留 CLI adapter 组合、显示状态和具体设备/终端 wiring，不再拥有可被其他前端复用的 session、turn、审批、语音协调或自主任务业务规则。
- `PlanningRuntimeMixin` 被删除，或缩减为不含业务规则且只做显式 runtime 委托的短期装配层；认知、任务状态转换、review 和 execution handoff 不再依赖不可枚举的 `self` 属性。
- `EndogenousDriveEngine` 可以保留为稳定 facade，但内部阶段通过显式输入输出组合，runtime state 只有一个写入 owner。
- `SupervisorUIMixin` 被 route object、event broker、state projector 和 lifecycle owner 取代；`supervisor.py` 只负责服务装配与路由挂载。

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

`VoidCube_cli/app.py` 当前约 4,900 行。TUI、命令、turn execution 和显示投影已经形成专属组件，公共 session/turn use case、状态和应用事件也已进入 `VoidCube_app`；`VoidcubeCLI` 仍是聚合 host，并继续混合以下 adapter 状态与 wiring：

- Provider、模型、凭证和 Agent；
- 应用 runtime 端口、Agent 生命周期和 turn 执行 wiring；
- TUI application、状态栏、spinner 和模态输入；
- 工具进度、审批、secret、sudo 和 clarify；
- 语音录制、TTS 和持续监听；
- 自主组件、后台任务、scheduled execution 和执行锁。

当前剩余问题不是继续机械缩短 `run()`，而是继续清理 adapter 对 runtime 的无边界 wiring，并把 TUI、语音、自主组件和设备生命周期收口。CLI adapter 可以保留 prompt_toolkit、ANSI、设备回调和窗口生命周期，但不能重新成为共享业务能力的 canonical owner。

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

- `VoidCube_app` 统一持有 session identity/start/history/resume、title/hydration、active/busy turn、取消和输入队列等跨前端状态。
- `VoidCube_cli` 仅持有 modal、焦点、spinner、status bar、prompt_toolkit application 等显示或终端生命周期状态。
- 所有共享状态转换通过应用 runtime；CLI 不直接双写字段，也不以 setter callback 维持镜像同步。
- 并发锁、deadline、Event 和 Queue 必须有唯一 owner；纯设备或 UI 事件可以留在 adapter，但要与 turn queue 明确区分。

验收：状态创建和转换可脱离 CLI 测试；每个共享字段和并发原语只有一个生产写入 owner；CLI host 不再保存同义镜像。

#### CLI-2：会话与 turn runtime

- 统一 session 创建、恢复、切换、分支、title、hydration、retry、undo、history 和保存的公共 use case。
- 提取 `chat()` 中与渲染无关的 turn 执行、busy routing、取消、队列和 Agent 生命周期。
- 在 `VoidCube_app.contracts` 形成结构化 `TurnEvent` / `ToolEvent`，现有 CLI renderer 订阅事件，未来 Windows renderer 使用同一事件。
- 保持 SessionDB、Memory、Gateway lane 的现有真相边界。

验收：adapter contract 只依赖公共 runtime、端口和内存 repository，不依赖 slash command、ANSI、prompt_toolkit 或完整 `VoidcubeCLI`；session/turn 生产调用者不绕过公共 use case；中断、队列和工具结果顺序保持一致。

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

`PlanningRuntimeMixin` 当前约 1,672 行、50 个方法，仍是 Supervisor Planning 的运行时组合边界，但不再是自主任务治理责任的业务实现中心：

- 任务流、候选注释、策略记忆和观察议程的运行时组合；cognitive history summary、cognition state assembly、posture/alignment 与 self-regulation signal 由显式 service 承担；
- task profile、schedule、排序和冲突；
- Gateway 活动投影和 drive 输入；
- 跨快照状态组合以及 Supervisor route 所需的薄委托。

已有的 repository、治理/认知/self-regulation persistence、纯 projection/policy、task state mutation、batch review、review cycle/recovery、治理事件消费、cognitive history summary、cognition state assembly、cognitive posture/alignment、self-regulation、drive history persistence、body improvement review、execution handoff、autonomous-chain planning、任务 review、recovery、runtime reset、owner session、memory promotion、body consent 和 autonomous cycle service 已形成明确边界。Planning 只保留运行时组合、投影和显式 service 委托。继续增加 Mixin、回迁业务规则或只留下大量委托壳都不算治理收口。

### 7.2 目标责任边界

Planning 只保留组合和跨组件编排。责任边界固定如下：

| 责任 | owner | 约束 |
| --- | --- | --- |
| 历史、治理事件、认知与 regulation 持久化 | 对应 persistence service + state repository | service 负责默认结构、规范化、裁剪、衰减和时间戳；repository 只负责文件边界与原子写入 |
| 认知、策略、观察和 meta-governance 计算 | pure projector / policy | 只接收快照并返回结构化结果，不产生副作用 |
| self-regulation boost 计算 | `EndogenousSelfRegulationService` | 只接收 policy、posture、alignment 和 reasoning snapshot，返回有界调节信号 |
| strategy memory bucket mutation | strategy memory service | 只修改传入 history；持久化由 drive history persistence service 提交 |
| 任务状态、治理 transition 和 status 观察 | task state service | 统一写入治理真相，不调用 Gateway 或 Execution |
| task review、schedule 冲突、恢复和周期编排 | review policy / review cycle service | policy 无副作用；service 通过显式 ports 编排 |
| 任务决策、agent-pull 归属校验和执行请求准备 | `AutonomousTaskReviewService` | 只通过显式 task/Gateway/Execution ports 读取和写回，不依赖完整 Supervisor |
| Supervisor 治理 review adviser | `AutonomousTaskGovernanceReviewService` | 只输出结构化审查建议，不拥有任务真相 |
| Mem recovery 与 autonomous runtime reset | `AutonomousChainRecoveryService` / `AutonomousChainRuntimeResetService` | recovery 负责恢复投影；reset 负责清理运行时状态和对应外部 activity |
| Gateway owner session 查询 | `AutonomousTaskOwnerSessionService` | 只负责 Gateway 查询和响应规范化 |
| verified conclusion memory promotion | `AutonomousTaskMemoryPromotionService` | 负责候选记忆写入和 promotion request，不改变任务状态真相 |
| body-switch consent 写回 | `AutonomousBodySwitchConsentService` | 只协调 execution facade 结果到 Supervisor 运行态的写回 |
| 自主任务请求、任务创建、序列化和 judgement preview | `AutonomousChainPlanningService` | 负责输入规范化和读模型，不直接实现任务状态转换 |
| self-learning conclusion 提案转为判断在途任务 | `AutonomousChainPlanningService` | 只创建 API-B 判断在途提案，任务真相仍由 task state service 写入 |
| drive、planning、review 和 handoff 的完整周期 | `AutonomousCycleService` | 只协调显式 service/port，并写回周期时间和 UI activity |
| 治理事件消费与 self-regulation 写回 | governance event consumer | 统一消费标记和调节写回，不持有 Supervisor |
| body improvement 质量审查 | body reviewer | 只输出审查结果和可解释原因 |
| 执行请求、失败回写和互斥 | execution handoff service | Gateway、Execution 和 task state 的写入顺序明确 |
| Supervisor route 所需 runtime 组合 | Planning runtime / assembler | 只注入依赖和协调调用，不承载上述业务规则 |

已有 owner 继续保持；缺口只按上述责任边界补齐，不再以新增 Mixin 或通用 helper 作为拆分目标。

### 7.3 收口约束

1. Planning 只保留请求验证、运行时组合、投影、调用和响应映射；上述责任不得回迁到 Mixin。
2. 各 owner 通过显式 service/port 访问任务真相、Gateway、Memory 和 Execution，并保持既定写入顺序；不增加双写或隐式 `self` 依赖。
3. 后续生产消费者继续由 runtime assembler 显式装配；若责任边界再次变化，必须删除失效参数、旧入口和无调用兼容分支。

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

### 9.1 静态资源边界

该边界已经完成：canonical UI 资源位于 `systems/supervisor/web/supervisor.html`，由 `ui_assets.py` 使用 `importlib.resources` 加载，并由 package-data 与 wheel 合同覆盖。固定约束如下：

- 不恢复 `UI_HTML` 或任何内嵌 fallback。
- 资源加载不依赖仓库根目录或当前工作目录。
- 是否进一步拆分 CSS/JavaScript 文件由前端维护性决定，不作为 Python 巨石治理的完成条件。
- 修改 DOM、资源路径或 package-data 时必须同时更新源码与 wheel 合同。

### 9.2 Python 职责拆分

已形成的 projection/adapters 包括 state orchestration、observation、cognition、trace、body、memory、identity/proxy、media、activity、snapshot、stream 和 auto-open lifecycle。剩余目标组件：

- `UIEventBroker`：统一 state、voice、media SSE 队列和断线处理。
- `UIRoutes`：承接薄 HTTP/SSE endpoints 和请求/响应映射。
- `SupervisorUILifecycle`：承接缓存初始化、auto-open、关闭和并发资源清理。

`SupervisorUIMixin` 最终删除，由 route object、event broker 和显式 lifecycle owner 替代。`supervisor.py::_setup_routes()` 只挂载 route 集合，不逐条承载业务 endpoint wiring。

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

阶段状态只使用“已完成 / 部分完成 / 基本完成 / 未开始”四种值，并以验收条件是否满足为准。当前状态见第 15 节；本节只定义实施内容和退出条件。

### Stage 0：基线和护栏

- 固化四个 P0 文件的行为测试和依赖扫描。
- 建立反向导入、巨型方法新增和包层级约束。
- 建立目标包依赖矩阵：共享层不得导入 CLI/Windows，两个 adapter 不得互相导入。
- 为现有 `agent`/`systems`/`VoidCube_core -> VoidCube_cli` 依赖生成例外清单并要求只减不增。
- 固化 smoke、关键单测入口和导入/CLI/turn/服务/UI state 性能基准。
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

验收：共享 session/turn 状态只有一个 owner；公共 use case 和 adapter contract 不依赖 CLI；命令 handler 使用显式端口；CLI renderer 只消费事件/视图状态；旧字段、直接 repository 路径和同步分支已删除，会话与工具链行为无回归。

### Stage 4：Planning repository/policy/task workflow

- 保持 repository、projection/policy、task state、strategy memory mutation、batch review 和治理事件消费的 owner 边界。
- 固化 cognitive history summary、cognition state assembly、cognition posture/alignment/self-regulation、drive history persistence、body review 和 execution handoff 的 service owner 边界。
- 将自主任务规划、任务序列化、self-learning conclusion 提案、任务决策、恢复、runtime reset、owner session、治理 review adviser、memory promotion、body consent 和 drive→plan→review→handoff 周期置于显式 service。

验收：Planning 不再是上述责任的业务实现中心；任务规划、任务决策、任务真相、治理事件、恢复、body review 和执行交接的调用者都通过显式 owner 运行，关键写入顺序和全链路语义保持一致。完成后不得以新增 Mixin、委托壳或双路径兼容重新聚合责任。

### Stage 5：Endogenous pipeline

- 拆感知、反思、策略、候选、证据、LM proposal 和 selection。
- 保留 `EndogenousDriveEngine` 的短期稳定 API，内部改为组合。

验收：同一输入产生等价候选、分数和 deliberation；模型和文件端口可替换测试。

### Stage 6：TUI、语音、自主组件与 UI route 收口

- 完成 CLI-4/CLI-5。
- 收口 CLI host 的 TUI 显示状态、语音设备、后台任务和 autonomous adapter 生命周期；session/turn 所有权只按 Stage 3 验收，不在本阶段重复定义。
- 将 `SupervisorUIMixin` 的 route、SSE、缓存和 lifecycle 组合迁移到显式 owner，HTTP/SSE 路由变为薄适配层。

验收：CLI host 只保留终端/设备 adapter 和组合 wiring；Supervisor UI 不再是 HTTP/SSE 生命周期的实现中心；旧兼容入口已清理。Planning 的责任收口以 Stage 4 的验收为准。

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
8. 仅在阶段状态或优先级变化时更新第 15、16 节，不记录批次过程。

禁止以这些方式“完成拆分”：

- 新模块函数仍接收完整 `VoidcubeCLI`/`Supervisor` 并任意访问属性；
- 原方法仅变成几十个无意义委托壳且永久保留；
- 同一状态在旧类和新组件双写；
- 使用 `Any`、`getattr` 和 `hasattr` 模拟未定义接口；
- 为避免修改调用者而长期保留两个导入路径；
- 把多个大文件简单拼成一个新的 `helpers.py`/`runtime.py`。

### 13.1 方案文档维护规则

本文是治理基线和实施路线，不是实施日志。固定维护规则如下：

- 只记录当前有效的架构约束、owner、阶段状态、验收门槛和后续顺序。
- 不记录每次提交迁移了哪个 helper，不累计 focused test 数量，不保留文件从某个行数下降到另一个行数的过程。
- 单次批次的测试命令、通过数量、超时、临时风险和代码行变化放在提交说明、PR、CI 或会话交付中。
- 已完成能力只按责任域归纳，不枚举内部函数、DTO、projection 或 adapter 名称长串。
- 状态变化时覆盖旧快照；过期规模、旧入口和临时兼容说明直接删除，不追加“历史记录”。
- 第 15 节最多保留一份当前快照，第 16 节最多保留五项有顺序的后续工作。
- 阶段状态只在退出条件整体满足后前进；持续性护栏属于稳定边界，不作为阶段“剩余工作”重复列出。

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

当前判定为 **No-Go**：Endogenous 最终等价验收、Stage 6 adapter/lifecycle 边界和 Stage 7 全量验证尚未全部收口。

## 15. 当前实施快照

本节只描述当前有效事实。更新时直接替换，不追加历史批次。

### 15.1 阶段状态

| 阶段 | 状态 | 当前有效结果 | 退出前仍需完成 |
| --- | --- | --- | --- |
| Stage 0 | 已完成 | 反向导入、前端边界、P0 增长、打包、退役集成和可重复性能基线护栏已建立 | 无 |
| Stage 1 | 已完成 | 根 `cli.py` 已成为薄兼容入口；共享配置、Provider 和 Gateway 基础能力已进入 `VoidCube_app` | 无 |
| Stage 2 | 已完成 | Supervisor UI 静态资源、主要只读 projector 和 wheel 资源合同已外移 | 无 |
| Stage 3 | 已完成 | 公共 session/history/title/turn-control use case、结构化应用事件和无 CLI 依赖的 adapter contract 已建立；session、hydration、title、busy、queue/cancel 和 active turn 均由 `ApplicationRuntime` 统一持有，命令 handler 通过显式端口接入 | 无 |
| Stage 4 | 已完成 | Planning 的持久化、投影/策略、任务状态、review/recovery、执行交接和自主周期责任均由显式 owner 承担；Planning 只保留运行时组合、投影和 service 委托 | 无 |
| Stage 5 | 基本完成 | perception 到 candidate/LM/deliberation 的主要阶段已组件化，Engine 已成为小型 facade 和单一 runtime-state writer | 完成等价全链路验收，确认无旧 helper、旧调用路径和双写入口 |
| Stage 6 | 部分完成 | TUI、语音、后台任务、自主组件和 UI projection 已形成显式 runtime/ports | 收口 CLI 的显示/设备/autonomous adapter 生命周期；删除 `SupervisorUIMixin`；拆出薄 UI routes/lifecycle |
| Stage 7 | 未开始 | focused、架构、退役和 wheel 合同已有持续验证 | 运行全量主项目与 Mem 回归、smoke、发行物验证和性能复测，评估次级巨石 |

### 15.2 当前稳定边界

- 生产代码不反向导入根 `cli.py`；共享包不导入 CLI/Windows adapter。
- 根 `cli.py` 只保留兼容入口；CLI slash command、ANSI、Rich 和 prompt_toolkit 仍归 CLI adapter。
- Supervisor UI 资源通过包资源加载，源码与 wheel 使用同一 canonical 文件。
- session lifecycle、history/title mutation、hydration、turn 输入/结果、取消路由、工具、审批、clarify、usage 和 artifact 已有无界面公共 contract；对应 adapter contract 可脱离 CLI 运行。
- `ApplicationRuntime` 独占共享会话、hydration、title、busy/queue/cancel 和 active turn 状态，并统一发布结构化应用事件；CLI slash command、终端渲染和设备交互仍归 adapter。
- Planning 的持久化、纯投影/策略、任务状态、治理 review/recovery、Memory/Execution handoff 和自主周期已有显式 owner；副作用通过明确端口进入外部系统，已拆出的责任不得回迁。
- endogenous drive 的主要流水线已组件化，Engine 保持稳定 facade 和 runtime state 的单一写入 owner。
- `scripts/performance_baseline.py` 提供版本化 `voidcube.performance-baseline.v1` 基线，覆盖 import graph、CLI help、turn contract、Supervisor 初始化和 UI projection；测量使用冷进程，必要时同时记录无外部副作用的 operation timing。
- 语音设备实现归 `systems.voice` 与 adapter，退役的 `tools.voice_mode` 不得恢复。
- 已退役模型集成在活跃代码、可加载技能和 wheel 中保持零入口。

### 15.3 当前缺口

- CLI host 仍包含较多终端、设备、Agent 生命周期和 adapter 组合 wiring，属于 Stage 6 的收口范围，不再拥有共享 session/turn 真相。
- `SupervisorUIMixin` 和 `supervisor.py::_setup_routes()` 仍承担 route、SSE、缓存和 lifecycle 组合职责。
- Endogenous 尚缺最终等价全链路验收；全量测试、发行物验证和最终性能复测仍未完成，因此不能进入 Windows adapter 实施。

### 15.4 验证基线

每个 P0 批次至少运行直接 owner 测试、受影响集成测试、架构护栏、production compileall 和 `git diff --check`。涉及模型、鉴权、请求协议、技能或打包时，额外运行退役集成扫描、packaging contract 并验证 wheel source-to-artifact parity。

focused tests 只证明局部边界行为，不代表 Stage 7 全量验收。全量测试结果、性能数据和环境限制由 CI/交付报告保存，本文只在 Stage 7 是否通过时更新状态。

## 16. 后续实施顺序

1. **Stage 5 验收**：完成 Endogenous 等价全链路验证，确认没有旧 helper、双写或回迁入口后再标记完成。
2. **Stage 6 CLI adapter 收口**：完成 TUI application、语音设备、后台任务和 autonomous host 的显示/设备/共享业务边界。
3. **Stage 6 Supervisor UI 收口**：拆出 `UIRoutes`、`UIEventBroker` 和 lifecycle owner，删除 `SupervisorUIMixin`，缩短 `_setup_routes()`。
4. **Stage 7**：执行全量主项目/Mem 回归、smoke、wheel、退役扫描和性能复测，然后评估次级巨石并重新判定 Windows Go/No-Go。
