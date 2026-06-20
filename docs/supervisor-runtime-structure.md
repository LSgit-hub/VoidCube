# Supervisor Runtime Structure

## 1. 文档目的

本文档只解释当前 `systems/supervisor/` 的代码结构，不重新定义 VoidCube 总架构。

它回答两个问题：

1. `Supervisor` 现在还负责什么。
2. 新拆出的 mixin / helper 分别承接了哪一类职责。

如果要看总原则，优先回到：

- [voidcube架构基线.md](./voidcube架构基线.md)
- [architecture-integration.md](./architecture-integration.md)
- [architecture-conflicts-audit.md](./architecture-conflicts-audit.md)

## 2. 当前判断

`Supervisor` 已经不再是“大杂烩式实现体”，而更接近一个装配层：

- 负责初始化核心依赖
- 负责把 execution / governor / body runtime / queue 接起来
- 负责持有分段配置对象，给 execution / service runtime / body runtime 使用
- 负责注册 FastAPI 路由，并把 runtime/governance 路由装配到 supervisor app
- 保留少量真正还属于监督者装配层的共享状态

原先混在一个文件里的职责已经按切面拆开：

- execution route hint metadata
- process / gateway runtime
- planning / idle-window / self-evolution runtime
- service runtime
- endogenous drive planning
- supervisor room observability
- watch-window runtime state

这意味着后续阅读 `supervisor.py` 时，可以先把它理解为“组合这些能力”，而不是“独自实现所有能力”。

## 3. 目录分工

当前 `systems/supervisor/` 目录建议按下面的心智阅读：

| 文件 | 角色 |
| --- | --- |
| [supervisor.py](../systems/supervisor/supervisor.py) | 装配层。定义 `Supervisor`、配置模型、Agent 运行时数据结构、依赖注入与路由注册。 |
| [process_gateway_runtime.py](../systems/supervisor/process_gateway_runtime.py) | 本地进程拉起/终止/重启，以及与 gateway 的注册和 body activation 协调。 |
| [planning_runtime.py](../systems/supervisor/planning_runtime.py) | gateway activity、idle-window、自进化任务队列、自学习结论入队。 |
| [runtime_assemblers.py](../systems/supervisor/runtime_assemblers.py) | supervisor 构造期装配 helper，负责 runtime state 与 execution runtime 的组装。 |
| [service_runtime.py](../systems/supervisor/service_runtime.py) | supervisor 本地健康检查、周期维护任务、supervisor 自身 gateway 注册与后台 loop 装配。 |
| [endogenous_drive.py](../systems/supervisor/endogenous_drive.py) | 内生驱动器。把核心价值观、idle-window 与活动事实映射为可审计候选任务，不直接执行。 |
| [ui_runtime.py](../systems/supervisor/ui_runtime.py) | 监督者房间 UI 与 SSE 状态事件流。只负责可观测性，不提供执行入口。 |
| [trace_runtime.py](../systems/supervisor/trace_runtime.py) | 运行态 trace 只读聚合。按 `trace_id` 汇总 queue、supervisor activity、Mem governor history 与 gateway activity log。 |
| [task_queue.py](../systems/supervisor/task_queue.py) | self-evolution task 与 formal execution request 的数据模型和存储。 |

当前 execution route hint 元数据已经收口到：

| 文件 | 角色 |
| --- | --- |
| [systems/execution/route_hints.py](../systems/execution/route_hints.py) | execution 结果上的推荐入口、route hint catalog 与受控迁移方向元数据。 |

## 4. `Supervisor` 本体还保留什么

当前 `Supervisor` 主体仍然适合保留在一个类里的内容主要有：

- `SupervisorConfig`
- `SupervisorExecutionConfig`
- `SupervisorServiceRuntimeConfig`
- `SupervisorBodyRuntimeConfig`
- `AgentInstance`
- 装配入口：
  - runtime state 装配
  - execution runtime 装配
- 分段配置对象：
  - `config.execution`
  - `config.service_runtime`
  - `config.body_runtime`
- FastAPI app 装配入口：
  - `_setup_routes()`
- 少量跨切面共享的装配层状态：
  - `_agents`
  - `_watch_window_runtime`
- 启动顺序协调：
  - `start()`

这部分现在更像“Supervisor composition root”，而不是“大型业务脚本”。

## 5. 现有 mixin 的职责边界

### 5.1 `ProcessGatewayRuntimeMixin`

负责真正偏“运行时 plumbing”的部分：

- `list_agents()`
- `get_agent()`
- 拉起 / 杀掉 agent 进程
- 监控进程退出与重启
- agent 与 gateway 的 register / unregister
- body activation 向 gateway 回写

这部分是 supervisor 当前仍未完全退出的本地 runtime 能力。

### 5.2 `PlanningRuntimeMixin`

负责高层策略流：

- `get_governor_history()`
- gateway activity snapshot
- idle-window evaluation
- self-evolution task planning / decision / review
- self-learning conclusion -> self-evolution task 的转换

这部分不直接执行 body/runtime 动作，但负责把“是否该做事”整理成结构化队列与 execution request。
当前它除了 broad `governance_task_type`，还会显式保留：

- `task_family`
  - 例如 `body_upgrade`、`body_switch`、`memory_maintenance`、`general_self_evolution`
- `execution_kind`
  - 供 idle-window、task payload、planning/review activity metadata 与正式 handoff 复用

这样可以避免 body family 在 supervisor 规划层再次被揉回一个模糊的 `self_evolution` 桶里。
其中 `idle-window` 请求入口现在也优先按 `task_family` / `execution_kind` 解析，而不是再用模糊的 `task_type` 驱动窗口判断。
这些字段现在也不只停留在“规划时临时判断”里，而是会继续保留到：

- `task_queue.py` 的 queue snapshot
- `decision_history`
- formal `SelfEvolutionExecutionRequest`
- governor history / boundary defer / lifecycle writeback 等治理回写

同时，queue / trace 上仍可保留 broad `task_type` 作为原始来源标签；但只要进入 runtime policy、idle-window、planning/review 或 formal handoff，就不应只靠 `task_type` 做判断。
对外的规划/提交流水输入也应优先传 `governance_task_type`、`task_family`、`execution_kind`；如果 broad `task_type` 缺失，由内部归一化逻辑回填，而不是反过来要求调用方继续显式喂旧字段。
formal execution handoff 也应遵守同一条规则：如果 `execution_request` 已经是正式契约，就不要再在 facade 包装层或 executor outward response 里把 broad `task_type` 和同一组 runtime facts 顶层重复铺一遍。`execution_request` / queue / trace 保留 broad 标签即可，executor 对外摘要元数据优先只暴露 canonical runtime profile。
当前这套归一化已进一步收敛到共享 helper [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)：`task_queue`、`planning_runtime`、`self_learning`、`gateway`、`governor`、`execution adapters`、`lifecycle` 与 Mem governor bridge 都应从这里派生 canonical `governance_task_type` / `task_family` / `execution_kind`，而不是各自再复制一份分支判断。

因此后续如果新增 queue、history、handoff 或 writeback 相关字段，不应只写回 `task_type` 而丢掉这三层 runtime profile。
同样地，一旦某条 canonical runtime surface 已经稳定，应在同一轮删除旧残留或重复镜像字段，不保留“新旧两套一起长”的兼容外壳。

### 5.3 `Supervisor._setup_routes()`

负责把 supervisor 的 runtime / governance HTTP surface 安装到 FastAPI。

这一步现在已经直接回收到 `supervisor.py` 的 `_setup_routes()` 中，不再单独保留 `route_installers.py`。
其中 `body/review` 已直接在 `_setup_routes()` 内绑定 canonical `GovernorReviewExecutionAdapter`，而不再经由独立 compatibility 层。

### 5.4 `RuntimeAssemblers`

负责把 `Supervisor.__init__` 中的大段 wiring 收成几步明确装配：

- runtime state 装配
- execution runtime 装配

当前内部保留的是 execution runtime 的明确装配步骤：

- agent lifecycle executor
- body lifecycle executor
- watch-window executor
- body upgrade executor
- memory maintenance executor
- self-learning executor
- governor review executor
- execution facade

这样 `supervisor.py` 主体只保留装配顺序，而不再保留大段 executor/facade 实例化细节。

### 5.5 `ServiceRuntimeMixin`

负责 supervisor 仍保留的本地服务循环：

- health check task 状态
- memory compression task 状态
- `health_check()`
- `register_with_gateway()`
- `run_health_checks()`
- `_wait_for_health()`
- `_start_periodic_tasks()`

这部分仍属于 supervisor 本地运维 runtime，而不是治理决策本身。

### 5.6 `Supervisor` 内的 watch-window runtime state

当前 watch-window 已不再保留单独的 runtime mixin 文件，而是把少量本地状态直接收回 `Supervisor`：

- `_watch_window_runtime`
- `_watch_window_task`
- `_watch_window_last_outcome`
- `_ensure_watch_window_task()`
- `_watch_window_loop()`

真正的 watch-window 执行动作仍通过 execution adapter 下沉；`Supervisor` 这里只保留最小 runtime state 与 loop 接线。

当前 executor 级 Phase 1 验收已经覆盖两条 watch-window 后续链路：

- 正常升级后，post-switch review 通过 governor / lifecycle 执行 `retired -> shell`，并由 `WatchWindowExecutionAdapter` 清退旧 active 进程。
- 观察窗口失败时，rollback review 通过 governor / lifecycle 执行 `retired -> active`，失败新体进入 `retired`，并由 `WatchWindowExecutionAdapter` 先把 gateway active body 同步回恢复后的旧 agent，再清退失败体进程。

这证明 registry / governor / lifecycle / body-upgrade executor / watch-window executor 的进程内串联已经可验收；当前也已具备真实 gateway 进程与真实 agent 子进程下的 pass/recycle 与 failure/rollback smoke，后续重点转向把这些真实进程事件接进 supervisor runtime trace 查询回放。

### 5.7 `SupervisorUIMixin`

负责监督者拟人化房间 UI 的可观测性表面：

- `GET /ui`
  - 返回内置 HTML/CSS/JS 房间页面
- `GET /ui/state`
  - 返回当前监督者状态快照
- `GET /ui/events`
  - 使用 SSE 推送 `state` 事件，供页面实时更新

这层只读取已有 queue、内生驱动评估、supervisor 状态和统一 trace timeline，并把它们映射为 UI scene，例如：

- `memory`
- `learning`
- `planning`
- `execution`
- `idle`

它不提供任何写入口，不新增任务，不绕过 gateway / supervisor / executor 的既有边界。后续如果 UI 需要用户干预按钮，也必须走标准 gateway / supervisor governance API，而不是在 UI runtime 中新增旁路执行能力。

当前 UI activity buffer 是 supervisor-local 活动输入缓冲，并会同步保存到 `.soul-runtime/supervisor-ui-activity.json`，用于让房间在 supervisor 重启后仍能恢复最近发生的监督者动作。新发生的 UI activity 也会 best-effort 镜像到 Mem governor history，记录类型为 `supervisor_activity`。它可以记录：

- endogenous-drive evaluation
- endogenous-drive planning
- task planning
- task decision / review
- self-learning submission
- self-learning completion
- execution dispatch

这不是新的事实源，也不替代 gateway activity、Mem governance history 或 task queue。当前镜像只让 supervisor activity 能被治理历史看见；统一 trace 查询视图由 `TraceRuntimeMixin` 负责，UI runtime 通过该视图观察最近 timeline，不承担第三套权威历史存储职责。

### 5.8 `TraceRuntimeMixin`

负责监督者运行态的只读 trace 聚合查询：

- `GET /runtime/traces`
  - 汇总当前已知 trace 列表、来源计数和 canonical runtime profile 摘要
- `GET /runtime/traces/{trace_id}`
  - 按 `trace_id` 汇总 task queue、supervisor activity、Mem governor history 与 gateway activity log
- `GET /runtime/timeline`
  - 返回最近统一 trace 记录，供 UI 和外部观察器按同一条时间线查看 queue、gateway activity 与 Mem governance history

这层读取的来源包括：

- `self_evolution_queue`
- `supervisor_activity`
- `mem_governor_history`
- `gateway_activity_log`

当前 gateway activity log 是内存中的 bounded runtime log，经 `GET /admin/activity/log` 暴露；它与 idle-window 需要的 activity snapshot 共用 `_touch_activity` 入口。trace 视图读取这条 log，而不是从 UI activity buffer 或 recent metadata 伪造 gateway 历史。

这层不提供任何写入口，不创建任务，不改变队列状态，不派发执行。它的定位是把已有事实源按 `trace_id` 和最近发生时间拼成可读时间线，方便 UI 观察、排障和阶段验收。

## 6. 与 `systems/execution/` 的关系

当前边界可以这样理解：

- `systems/supervisor/`
  - 负责治理、规划、本地 runtime、装配
- `systems/execution/`
  - 负责标准执行面、runtime adapter、正式 executor API、execution route hint 元数据

特别是现在以下链路已经基本从 supervisor 本体抽离：

- governor review 协调
- watch-window 实际执行动作
- body upgrade 执行管线
- self-learning learn-only follow-up 执行与结论提交包生成
- legacy upgrade 残留链路（现已整体删除）

因此后续如果发现某段代码“主要是在执行动作”，优先考虑落在 `systems/execution/`，而不是再放回 `supervisor.py`。

`SelfLearningExecutionAdapter` 是当前自学任务的 canonical executor 入口：它通过 `SelfLearningSkillDelegate` 读取 bundled `skills/self-learning` 的技能契约、技术评估指南和学习总结模板，生成 bounded `evidence_plan` 并调度工具采集证据，生成包含 `tool_execution` 的 `skill_execution` 证据包，然后创建学习 topic / session / experiment / conclusion，并返回 `supervisor_submission`。adapter 会把 `tool_execution` 派生为 `skill_evidence_summary`，同步写入 experiment observations 与 `supervisor_submission.metadata`，方便 trace / Mem governance history 不展开原始包也能看到证据来源、调用计数、失败工具和少量证据预览。它不生成正式 `SelfEvolutionExecutionRequest`，也不触发 body upgrade、body switch 或 memory mutation；supervisor cycle 只负责派发 approved `self_learning` 任务、提交返回的 conclusion payload、写入 `execution_dispatched` 防重复标记。

当前 delegate 默认使用 executor 侧的 bounded evidence-plan runner，只允许 `web_search`、`search_files`、`read_file` 一类证据收集工具；任务约束可以要求外部搜索、本地仓库搜索和参考文件读取，但 disallowed 工具会记录为 rejected，不会被调用。每次调用都会记录成功/失败、摘要和错误，不把失败伪装成成功。后续如果 active Agent 暴露稳定的完整 agent/subagent runner，应优先替换 `SelfLearningSkillDelegate` 后端，而不是在 supervisor 里新增研究执行逻辑。

## 7. 阶段结论与剩余技术债

这一轮收口后，可以把当前状态概括成三句话：

1. `supervisor.py` 已基本收敛为装配层和启动入口，不再承载大段执行实现。
2. 真正的执行动作越来越集中到 `systems/execution/` 与少量 supervisor runtime primitive。
3. 当前最值得继续维护的是边界清晰度，而不是继续为了“拆而拆”制造更多 mixin。

在这个前提下，剩余技术债更适合按“保留 / 继续下沉 / 等兼容退场后删除”来看，而不是继续按文件机械拆分。

这次结构整理并不代表 supervisor 已完全收口，当前仍有几类残留：

1. `SupervisorConfig` 已收口为 `execution` / `service_runtime` / `body_runtime` 三段 owner；后续主要风险不再是模型本身，而是新增调用点把配置重新扁平化。
2. `run_health_checks()`、周期任务与 watch-window 的少量本地 runtime state 仍保留在 supervisor 侧，后续可继续评估哪些还能再下沉。
3. `FastAPI` route 注册已经直接收回 `Supervisor._setup_routes()`；如果未来路由面再次明显膨胀，再评估是否重新抽成更结构化的 installer 组合。
4. mixin 目前是工程性拆分，不是独立可复用框架；它们的目的主要是降噪和明确职责。

### 7.1 剩余执行味 helper 债单

如果下一步要继续推进，当前残留方法更适合分成三类看：

#### A. 可以继续下沉的

这些方法本身已经没有多少“监督者判断”含义，主要是在做 runtime/plumbing：

- `AgentLifecycleExecutionAdapter` 的 counter owning 方式
  - 现在 `VoidCubeExecutionFacade` 与 `VoidCubeExecutionService` 上层都已经统一改成 `start_managed_agent()`，而 counter owner 也已经收回 `AgentLifecycleExecutionAdapter` 自身；如果未来 agent lifecycle 再出现跨进程或持久化需求，再评估是否需要独立 runtime owner。
- `runtime_assemblers.py` 里少量 composition-root 参数拼装
  - 例如 governor storage root 提供现在已经收敛成单一装配流程内的参数拼装，而不再保留成多层 `_assemble_*` helper。它本身不是大问题，但如果未来出现稳定 owning service，仍可以继续从 assembler 中退出。
- adapter 间的最小循环协作
  - `watch-window`、`body-upgrade` 与 `governor-review` 现在已经改成 assembler 里的直接绑定，不再经由 supervisor shim 转手；这类残留如果再次出现，也优先收成显式协作对象，而不是再长回 mixin helper。
- `Supervisor._setup_routes()`
  - 现在已经直接挂在 composition root 上；只有在 HTTP surface 明显继续扩张时，才值得再重新抽离成独立 installer。

#### B. 应该保留在 supervisor runtime 的

这些方法虽然带 runtime 味道，但它们服务的是 supervisor 作为本地母体进程的运维壳，而不是“升级决策”本身：

- `ProcessGatewayRuntimeMixin._spawn_agent_process()`
- `ProcessGatewayRuntimeMixin._terminate_agent_process()`
- `ProcessGatewayRuntimeMixin._monitor_agent()`
- `ProcessGatewayRuntimeMixin._restart_agent()`
- `ProcessGatewayRuntimeMixin._register_agent_with_gateway()`
- `ProcessGatewayRuntimeMixin._unregister_agent_from_gateway()`
- `ProcessGatewayRuntimeMixin._sync_gateway_body_activation()`
  - 这组属于本地进程管理与 gateway plumbing；在没有独立 process supervisor/runner 之前，继续放在 supervisor runtime 内是合理的。
  - 当前已有真实 gateway HTTP server 覆盖 agent service registration 与 active body sync；也已有真实 agent 子进程 smoke 覆盖 `/health`、gateway 自动注册、用户请求经 `/v1/agent/query` 代理到 agent，以及 gateway activity log trace 回放。
  - 当前 body upgrade pipeline 已能驱动真实 agent 子进程、等待健康并同步 gateway active body；watch-window pass/recycle 已覆盖停止旧 active 真实子进程。
  - watch-window failure/rollback 已覆盖恢复旧 active slot 后同步 gateway active body 回旧 agent、停止失败新 agent，并继续通过 gateway 用户代理访问恢复后的旧 body。
  - 下一步仍需要把 pass/failure 的真实进程事件流纳入 supervisor runtime trace 查询验收，才能把 process runtime 验收从 smoke 推到完整回放。
- `ServiceRuntimeMixin.register_with_gateway()`
- `ServiceRuntimeMixin.run_health_checks()`
- `ServiceRuntimeMixin._wait_for_health()`
- `ServiceRuntimeMixin._check_agent_health()`
- `ServiceRuntimeMixin._start_periodic_tasks()`
  - 这组更像 supervisor 的本地运维循环，而不是治理判断；可以继续优化实现，但不必为了“纯粹”强行挪回 execution 域。
  - 其中 supervisor 自注册、agent 注册/注销、body activation sync 虽然都经过 gateway，但当前 service identity payload、生命周期触发点和失败语义并不相同；在这些契约真正稳定统一之前，不要只为了去重再抽一个新的通用 gateway registration helper。
- `Supervisor._ensure_watch_window_task()`
- `Supervisor._watch_window_loop()`
- `Supervisor._watch_window_runtime`
  - 真正的 watch-window 动作已经下沉到 execution adapter；这里保留最小任务状态和 loop 接线，边界是干净的。
- `runtime_assemblers.py`
  - 这层已经承担了 composition-root 的主要价值。除非未来 constructor wiring 再次显著膨胀，否则现在停在这里是合理的，不需要为了形式继续拆散。

#### C. 已完成的一批整体删除

- 已删除的 legacy upgrade 链路
  - `/upgrade/legacy` executor route 已删除。
  - `LegacyUpgradeExecutionAdapter` 已删除。
  - `legacy_upgrade_runtime.py` 已删除。
  - `VoidCubeExecutionFacade` 不再保留 `handle_legacy_upgrade_request()`。
- 已删除的旧 execution facade
  - `execution_compatibility.py` 与 `ExecutionCompatibilityMixin` 已整体删除。
  - `body/review` 治理路由改成直接绑定 canonical execution facade；不再把 supervisor 保留成第二套执行 API。
### 7.2 过渡面现状与删除纪律

这一步对当前旧过渡面做了一轮代码级盘点，结论可以直接作为后续删减顺序的依据。

#### A. 已删除的 HTTP transitional routes

这一轮之后，supervisor 已不再默认安装 deprecated 执行型 HTTP 路由：

- `install_transitional_execution_routes()` 已删除
- `/runtime/transitional-interfaces` catalog 已删除
- `health_check()` 不再暴露 `transitional_interfaces`

这意味着过渡面已经从“HTTP 与对象面双壳并存”收缩成“只保留 notice 元数据与极少数 legacy 链路”。

#### B. 已删除的旧 Python facade

这一轮之后，`ExecutionCompatibilityMixin` 与 `execution_compatibility.py` 已整体删除：

- `review_body_event()` 不再作为 `Supervisor` 公共兼容方法存在
- `GovernorReviewExecutionAdapter.review_body_event()` 这层 dict-wrapper 也已删除
- `_execute_governor_request()` legacy shim 已删除
- `body/review` 路由已直接在 `Supervisor._setup_routes()` 内绑定 canonical `GovernorReviewExecutionAdapter`

这意味着 supervisor 已不再保留第二套 Python 执行对象面。

#### C. 已删除的旧私有 alias

这一轮之后，supervisor 不再安装旧私有 execution 属性 alias：

- `install_supervisor_execution_compatibility_aliases()` 已删除
- `_agent_execution`、`_governor_review_execution`、`_legacy_governor_execution` 已退出默认对象面
- `_body_upgrade_execution`、`_body_lifecycle_execution`、`_legacy_upgrade_execution`、`_memory_maintenance_execution` 已退出默认对象面

现在的 canonical wiring 只保留 `_agent_lifecycle_executor`、`_body_upgrade_executor`、`_governor_review_executor` 等真实命名，避免测试或临时 patch 再把旧私有名重新养回来。

#### D. 还在主动依赖过渡语义的调用点

当前能明确看到的依赖点主要分三类：

- supervisor runtime wiring tests
  - `tests/test_supervisor_runtime_wiring.py`
  - `tests/test_supervisor_body_runtime.py`
- execution-side route hint 文档仍需要解释推荐入口迁移方向

这说明当前剩余的过渡语义主要体现在测试、notice 与 legacy 链路，而不是 `Supervisor` 的公共执行对象面本身。

#### E. 已经完成的一处内部去兼容化

本轮还顺手清掉了一个真实的内部反向依赖：

- `ServiceRuntimeMixin._start_periodic_tasks()` 里的周期 memory compression

它现在改为直接调用：

- `MemoryMaintenanceExecutionAdapter.trigger_memory_compression()`

而不再走：

- 任何旧 facade 壳

这意味着 supervisor 自己的后台 runtime loop，已经不再依赖旧 facade 壳。

#### F. 已完成的一处 facade 收缩

这一轮又继续删除了一批只剩测试公共方法价值的 supervisor execution facade：

- 已删除 `start_agent()`
- 已删除 `stop_agent()`
- 已删除 `switch_agent()`
- 已删除 `handle_upgrade_request()`
- 已删除 `trigger_memory_compression()`
- 已删除 `mark_body_candidate()`
- 已删除 `prepare_body_slot()`
- 已删除 `execute_body_upgrade()`
- 已删除 `record_body_probe_report()`
- 已删除 `run_body_probe()`
- 已删除 `evaluate_watch_window()`
- 已删除 `get_watch_window_status()`

对应测试已经改成直接断言 `_execution_facade` 或 canonical executor，而不是继续把 `Supervisor` 当执行型对象面。

同样，`VoidCubeExecutionFacade` 也继续避免长成“给内部测试和临时排障使用的第二套 adapter API”：

- 只保留真实对外需要的 canonical facade 方法
- 已删除仅剩直通测试价值的 governor / watch-window 辅助直通方法

#### G. 已经完成的一处 bridge 去残留化

这一步还顺手清掉了一批只剩透传价值的 execution bridge shim：

- 已删除整个 `execution_runtime_bridge.py` 与 `ExecutionRuntimeBridgeMixin`
- `get_body_registry()`、`list_body_slots()`、`get_body_slot()`、`get_active_body_target()` 已直接收回 `Supervisor`
- 已删除 `ExecutionRuntimeBridgeMixin._start_agent_execution()`
- 已删除 `ExecutionRuntimeBridgeMixin._stop_agent_execution()`
- 已删除 `ExecutionRuntimeBridgeMixin._switch_agent_execution()`
- 已删除 `ExecutionRuntimeBridgeMixin._run_body_probe_execution()`
- 已删除 `ExecutionRuntimeBridgeMixin._list_running_agents()`
- 已删除 `ExecutionRuntimeBridgeMixin._resolve_launch_target()`
- 已删除 `ExecutionRuntimeBridgeMixin._get_governor_storage_root()`

对应调用已经改成：

- body registry / slot / active target 只读查询直接留在 `Supervisor` 路由面
- assembler 直接注入 canonical executor 协作
- process runtime 直接读取 `body_registry` active pointer
- body upgrade executor 直接调用 `_agent_lifecycle_executor`

这一步的重点不是“换个地方包一层”，而是把已无独立价值的桥接方法真的移除，避免后续重复生长出新的 shim。

#### G-2. 已经完成的一处 watch-window runtime 壳收缩

- 已删除整个 `watch_window_runtime.py` 与 `WatchWindowRuntimeMixin`
- `_watch_window_task`、`_watch_window_last_outcome`、`_ensure_watch_window_task()`、`_watch_window_loop()` 已直接收回 `Supervisor`

对应调整后，watch-window 执行动作仍留在 execution adapter；supervisor 只保留本地 runtime state，而不再为这层状态单独保留一个空壳 mixin 文件。

#### H. 可以据此执行的删除顺序

如果下一轮开始真正收缩 compat surface，建议按这个顺序：

1. 先继续清点是否还有新的 caller 试图把 supervisor 重新当成执行对象面。
2. 如果出现新的治理路由包装需求，优先直接挂 canonical facade 或 executor，而不是重建 compatibility 层。
3. 只要新路径已经稳定，就在同一轮删除旧路由、旧 helper、旧提示、旧测试和旧文档表述，不给残留二次生长机会。

### 7.3 下一步建议顺序

如果按投入产出比排序，建议优先级如下：

1. `runtime_assemblers.py` 里成串的 `_assemble_*` 执行装配 helper 已经收回到单一装配流程；后续如果再出现只剩顺序转手价值的 assembler 私有方法，应同轮直接删掉，不再重新长回多层 helper。
2. 对 process/gateway、service loop、watch-window runtime state 与 `runtime_assemblers.py`，当前优先目标是守住边界和可读性，不急着为了“全进 execution”而继续拆分。

## 8. 后续维护规则

后续改动 `systems/supervisor/` 时，建议遵守下面几条：

- 新增“执行动作”优先放到 `systems/execution/`。
- 不再新增新的 `execution_compatibility.py`、旧 facade 壳或同类转发层。
- 如果治理路由需要包装，优先直接绑定 canonical facade / executor；只有当路由面再次显著膨胀时，再考虑重新抽出 installer。
- 新增“策略判断 / idle-window / queue”优先放到 `planning_runtime.py`。
- 如果是 self-evolution 规划语义，优先保留 `governance_task_type`、`task_family`、`execution_kind` 三层表达，不要只回传一个模糊 `self_evolution`。
- 新增“健康检查 / 周期后台任务”优先放到 `service_runtime.py`。
- 新增“watch-window runtime 状态或 loop 接线”优先直接收敛在 `Supervisor` 本体的少量状态，除非后续再次形成稳定独立 owner。
- 新增“说明性 catalog / execution route hint metadata”优先放到 `systems/execution/route_hints.py`。
- 新增“本地进程 / gateway plumbing”优先放到 `process_gateway_runtime.py`。
- 只有在确实涉及装配、共享本地状态或路由接线时，才优先修改 `supervisor.py`。
- 完成替代实现后，优先在同一轮修改中删除旧路径、旧参数、旧提示和旧测试，避免兼容壳继续共存。
- 不以“先立规则、以后再删”替代真实删除；如果新路径已经接稳，就应同步移除旧残留。
- 删除旧残留时，连带清理对应文档、注释、alias、catalog 和测试断言，避免后续会话把旧线路误当成当前设计重新长回来。
- 不再新增仅为 CLI fallback 或“临时双路径”服务的 supervisor 执行型路由、旧 facade 壳、alias 或提示文案。
- 新增 executor / CLI 接线默认应 fail closed。
  - 如果某条新能力必须经过 executor，executor 不可用时应直接失败，而不是静默绕回 supervisor。

## 9. 一句话结论

当前 `Supervisor` 已经从“实现所有事情的类”收敛成“装配监督者能力的类”；真正继续演进时，重点应该放在保持这个边界，而不是把新职责再塞回主文件。
