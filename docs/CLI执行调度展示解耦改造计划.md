# CLI 执行、调度与展示解耦改造计划

> 计划版本：v1.0
>
> 建立日期：2026-08-07
>
> 当前状态：P4 已完成，P5 进行中；P0/P1/P2/P3 已完成，单 turn 执行已抽取为无 TUI ports runtime，自主链路和 scheduled-task 都已改为窄 execution owner，旧完整后台 CLI host 已删除。

## 1. 目标

将当前由 `VoidcubeCLI` 组合承载的三类责任拆开：

1. **执行**：创建和运行一个 Agent turn，处理工具、流式输出、取消和结果。
2. **调度**：决定哪个 turn 可以运行，维护用户链路与自主链路的队列、优先级、门控和状态。
3. **展示**：接收结构化快照/事件，更新 TUI；不直接读取另一个 host 的私有字段。

改造完成后，`app.py` 仍可作为 CLI 组合根，但不再作为调度状态的唯一实际 owner。用户链路和自主链路继续保持独立 session、conversation history、tool policy 和 scene lane。

## 2. 非目标和兼容约束

- 本计划不改变模型、Provider、鉴权、Gateway 或 Supervisor 的业务语义。
- `/auto` 仍是临时启用自主链路，`/auto-q` 仍是临时停用自主链路；不得改成启动时常驻或全局永久开关。
- 第一阶段不追求并行执行两个模型 turn；默认仍保持 API-A 同时只有一个活动 turn。
- 不把完整 `VoidcubeCLI` 搬到新文件，不建立新的无边界 `runtime.py` 或 `helpers.py` 聚合层。
- 生产调用者切换后删除失效字段、重复写入和无调用兼容分支；不保留永久双路径。

## 3. 当前基线

当前实现是“父 TUI + 两个窄 execution owner + 单一 Scheduler owner”：

- 父 host 负责用户 TUI 和 `user_chat`。
- `AutonomousExecutionHost` 负责 `supervisor_task`，只拥有执行和自主任务状态，不提供 TUI/快捷键/语音入口。
- `TurnScheduler` 统一维护活动 turn、队列、优先级、门控和取消协议。
- 父 host 与 execution owner 各自拥有自己的 Agent/turn-local 状态，展示层只读取 Scheduler snapshot 和 owner snapshot。
- 现有 `TurnExecutionRuntime`、`PendingInputRuntime`、命令 handler 和 autonomous ports 可作为迁移边界，不重复实现已有 turn 逻辑。

基线参考：

- [自主链路与用户链路冲突分析.md](./自主链路与用户链路冲突分析.md)
- [CLI展示与gateway双槽设计.md](./CLI展示与gateway双槽设计.md)
- [VoidCube_cli/app.py](../VoidCube_cli/app.py)

## 4. 目标结构

```text
TUI / Supervisor adapter
        │ submit / cancel / pause / resume
        ▼
   TurnScheduler  ───── publishes ─────►  CLI event projector / TUI
        │ dispatches one admitted request
        ▼
   AgentExecutor
        │ uses
        ▼
   existing turn, tool, session and Gateway ports
```

### 4.1 状态所有权

| 状态 | 唯一 owner | 其他组件如何读取 |
| --- | --- | --- |
| 待执行/活动 turn、lane、优先级、排队原因 | `TurnScheduler` | `SchedulerSnapshot` |
| 取消请求、取消令牌、抢占结果 | `TurnScheduler` | `SchedulerEvent` |
| 当前 Agent、工具循环、流式结果 | `AgentExecutor` | `ExecutorEvent` 和结果 contract |
| session 与 conversation history | 现有 session/turn owner | 通过 `TurnRequest` 的 session 端口 |
| TUI 输入框、spinner、布局 | CLI 展示层 | 只由结构化事件驱动 |
| Supervisor 自主任务记录 | Gateway/Supervisor 现有 owner | 通过 autonomous executor ports |

“状态只在 Scheduler”仅适用于调度状态；Agent 的 turn-local 状态和 TUI 的呈现状态仍由各自 owner 持有。

### 4.2 最小 contract

建议先定义无界面的数据 contract，名称可在实现阶段调整：

- `TurnLane`: `user_chat`、`supervisor_task`
- `TurnRequest`: `request_id`、`lane`、`session_id`、`prompt`、`priority`、`tool_policy`、`source`
- `SchedulerSnapshot`: `active`、`queued`、`autonomous_gate`、`blocked_reason`、`updated_at`
- `SchedulerEvent`: `queued`、`started`、`waiting`、`cancel_requested`、`finished`、`failed`、`cancelled`、`gate_changed`
- `TurnScheduler.submit()`、`.cancel()`、`.pause_autonomous()`、`.resume_autonomous()`、`.snapshot()`
- `AgentExecutor.execute(request, cancellation)` 和 `AgentExecutor.cancel(request_id)`

控制使用显式命令/方法，观察使用事件/快照；不要让 TUI 通过事件反向驱动业务状态。

## 5. 调度规则（先固定语义，再写实现）

1. 同一时刻最多一个活动模型 turn。
2. 用户 turn 默认高于自主 turn；自主 turn 等待时不阻塞输入采集。
3. 用户提交期间，新的自主任务只能排队，不能抢占当前用户 turn。
4. `/auto-q` 立即关闭自主门控，并请求取消当前自主 turn；已完成的用户 turn 不受影响。
5. 取消和退出只接受显式命令：`/cancel` 取消用户 turn，`/auto-q` 停用并取消自主链路，`/quit` 退出 CLI。
6. 取消必须幂等；取消失败、超时和 Agent 已退出都要产生明确事件。
7. 队列中的用户输入不得静默丢失；若被延迟，TUI 显示排队原因和当前活动 lane。

## 6. 分阶段实施

### P0：行为基线与 contract（已完成）

- [x] 为用户 turn、自主 turn、`/auto`、`/cancel`、`/auto-q`、`/quit` 和超时补 characterization tests。
- [x] 定义 `TurnRequest`、`SchedulerSnapshot`、`SchedulerEvent` 的字段和序列化边界。
- [x] 记录当前锁粒度、队列顺序、session lane 和错误语义，作为迁移前基线。

**退出条件**：新 contract 可脱离 `VoidcubeCLI` 构造；基线测试在不改生产逻辑时通过。已由 `tests/test_application_scheduler_contract.py` 与现有 CLI 基线测试验证。

### P1：实现纯 `TurnScheduler`（已完成）

- [x] 用显式状态机实现 `idle → queued → running → cancelling → finished/failed/cancelled`。
- [x] 实现用户优先级、自主门控、幂等取消、队列快照和事件发布。
- [x] 先使用内存队列和可注入时钟/执行器，不连接真实模型或 TUI。
- [x] 为优先级、重复取消、自主门控、队列 FIFO、并发提交、取消竞态和异常传播补单元测试。

**退出条件**：调度器核心测试不导入 `VoidCube_cli.app`，且能证明没有第二个调度状态 owner。已由 `tests/test_application_turn_scheduler.py` 验证。

### P2：接入现有 CLI 输入链路（已完成）

- [x] 将 `PendingInputRuntime` 的用户输入转换为 `TurnRequest`，由 Scheduler admission。
- [x] 将 autonomous polling 的认领结果转换为 `supervisor_task` request（execution owner pending input 统一经 Scheduler adapter admission）。
- [x] 用 Scheduler 的 admission/dispatch 替换旧用户/自主 turn 锁的直接等待。
- [x] 保留现有 `/auto`、`/auto-q` 命令入口，并同步 Scheduler autonomous gate。
- [x] TUI 中间状态栏通过只读 snapshot 显示活动 lane 和排队数量，不改变输入内容和 session 归属。

**退出条件**：用户/自主两条链路的现有回归测试通过；用户/自主旧锁不再作为业务调度入口；scheduled-task 专用锁仍属于独立任务域，待后续明确迁移。已由 183 个定向测试验证。

### P3：抽取 `AgentExecutor`（已完成）

- [x] 定义无 TUI 的 `AgentExecutor` contract，并新增 CLI `CliAgentExecutor` adapter。
- [x] 从 `VoidcubeCLI.chat()` 提取单 turn 执行所需的显式 ports，并删除旧双执行路径。
- [x] 通过 `SingleTurnExecutor` 复用 `TurnExecutionRuntime` 的线程、命令取消、超时和流刷新逻辑。
- [x] 将自主任务 tool policy、Agent 生命周期和用户回调能力声明为 request/executor 配置。
- [x] 让 autonomous executor 不再创建完整 TUI host；自主链路改由窄 `AutonomousExecutionHost` 持有执行 ports。
- [x] 生产调用者切换后删除 autonomous 和 scheduled-task 的旧完整后台 host 分支、重复 cprint/输出兜底和无调用字段。

**退出条件**：Executor 可在无 TUI 环境执行测试 turn；用户与自主 session/history/scene lane 不互相污染。已由 `tests/test_cli_agent_turn_executor_runtime.py`、`tests/test_autonomous_execution_host.py` 和自主门控回归验证。

### P4：统一事件投影与 TUI 状态（已完成）

- [x] TUI 中间状态栏通过 Scheduler snapshot 读取活动 lane/排队数量，不读取 execution owner 私有字段。
- [x] 合并自主面板与状态栏的宽度计算、截断和 spinner 投影。
- [x] 补窄终端、退出中、取消中、队列等待和自主执行中的渲染测试。
- [x] 将日志标记统一为 `user_chat` / `supervisor_task`，保留可检索的 request id。

**退出条件**：父 host 不需要了解 execution owner 的内部状态；状态栏和自主面板能准确显示活动 lane、等待原因、request id 和取消结果。已由 projector、状态栏、自主面板和 Scheduler 回归测试验证。

### P5：清理与全量验收（进行中）

- [x] 删除失效兼容分支、重复参数、旧锁接线和无调用后台 host 能力。
- [x] 更新架构文档、命令帮助和测试契约，删除过期描述。
- [ ] 运行 owner 测试、CLI 自主链路回归、scheduled-task、并发/取消测试、文档测试、架构依赖检查和生产编译。
- [ ] 涉及模型/请求链路时运行退役集成扫描；涉及打包时运行 wheel 契约和 source-to-artifact parity。
- [ ] 执行 `git diff --check`，确认没有残留调试输出或临时兼容代码。

**退出条件**：行为、结构、文档和发行物验收全部通过；本计划更新为“已完成”，并记录实际剩余风险。

## 7. 测试矩阵

| 风险 | 最小测试 |
| --- | --- |
| 用户优先于自主 | Scheduler priority/admission tests |
| 自主等待不吞用户输入 | queue integration test |
| `/cancel` 与 `/auto-q` 路由 | Enter fast-path 与 `tests/test_cli_autonomous_gate.py` 相关回归 |
| 用户/自主 session 隔离 | lane/history/scene tests |
| 取消竞态和幂等性 | scheduler cancellation tests |
| TUI 不读 host 私有状态 | projector/ports contract tests |
| 退出清理 | lifecycle and async cleanup tests |
| 架构依赖不回退 | `scripts/python_architecture.py` 及现有架构测试 |

## 8. 进度记录规则

- 每完成一个阶段，只更新本文件对应 checkbox、阶段状态和退出条件，不追加流水账。
- 若发现行为变化，先新增 ADR 或修订“调度规则”，再改代码。
- 若阶段被阻塞，记录具体阻塞条件、已尝试替代方案和恢复所需的外部条件。
- 任何后续会话开始前，先读取本文件“当前状态”和未完成阶段，禁止把已删除的完整后台 CLI host 逻辑重新当成目标设计。

## 9. 下一步

继续 P5：运行完整 owner/CLI/Scheduler 回归、架构检查、退役集成扫描、打包契约和 source-to-artifact parity；确认发行物不含旧后台 host 入口后，将计划标记为已完成。
