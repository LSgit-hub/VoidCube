# VoidCube Stable Status and Next Stage

## 1. 文档目的

本文记录当前已验证的稳定状态、仍需推进的缺口，以及下一阶段目标。

它不是新的架构基线；最高优先级仍是 [voidcube架构基线.md](./voidcube架构基线.md)。本文只负责在阶段切换或新会话开始时提供可执行状态。

## 2. 当前稳定状态

截至当前阶段，VoidCube 已稳定具备以下能力：

0. Phase 1 核心闭环与内生驱动器哲学文档已建立。
   - 文件：[docs/phase1-core-loop-and-endogenous-drive.md](./phase1-core-loop-and-endogenous-drive.md)
   - 定义 VoidCube 拒绝的三种反模式（热自改、无头进化、被动等待）。
   - 精确定义母体与子体的关系、内生驱动器的三大核心价值与四类候选任务。
   - 详述 Idle-Window 多层安全阀、Phase 1 四重保障（灵魂连续/用户优先/治理可审计/失败可回滚）。
   - 给出完整运行循环（内生驱动→治理→执行→切换→闭环）与 Phase 1→4 进化阶梯。
   - 已纳入 docs/README.md 执行依据（第 9 份核心文档）并反向引用到架构基线。

0.1 网关错误追踪链路已补全。
   - 网关 activity state 新增 `error_count` 与 `uncertainty_high_count` 字段。
   - `_touch_activity` 新增 `agent_error` 与 `uncertainty_high` 两种 activity kind。
   - `route_request`、`chat_completions_proxy`、`agent_query_proxy` 三个代理方法在超时/异常时自动上报 `agent_error`。
   - 内生驱动器 `_candidate_stream` 的 fallback 链已优先对接网关 canonical 字段名（`error_count` / `uncertainty_high_count`）。
   - 此前 `truthfulness:review_correction_signals` 候选任务因缺少数据源永不触发，现已具备数据基础。
   - 验证：`tests/test_gateway_activity.py` + `tests/test_supervisor_self_evolution_queue.py` = 26 passed。

1. `Supervisor` 仍保持装配层定位。
   - 执行动作继续落在 `systems/execution/`。
   - 规划、idle-window、self-evolution queue 与内生驱动仍落在 supervisor planning/runtime 范围。

2. 内生驱动器已启用。
   - 文件：[systems/supervisor/endogenous_drive.py](../systems/supervisor/endogenous_drive.py)
   - 核心价值：`continuity`、`truthfulness`、`creativity`
   - 行为边界：只生成候选任务，不直接执行，不绕过 idle-window、queue、governance、executor、rollback。

3. 监督者房间 UI 已接入。
   - 页面：`GET /ui`
   - 快照：`GET /ui/state`
   - 事件流：`GET /ui/events`
   - 文件：[systems/supervisor/ui_runtime.py](../systems/supervisor/ui_runtime.py)
   - 行为边界：只做可观测性，不提供执行写入口。

4. UI 状态来源已收口到现有 supervisor 状态，并观察统一 trace 时间线。
   - self-evolution queue
   - endogenous-drive evaluation
   - supervisor scene mapper
   - runtime trace timeline
   - SSE `state` event payload 中的 `timeline`
   - 行为边界：UI 只读取统一观察流，不创建任务，不修改队列，不派发执行。

5. UI 活动时间线已覆盖首批监督者动作。
   - endogenous-drive evaluation
   - endogenous-drive idle
   - endogenous-drive planned
   - task planning
   - task decision
   - task review
   - self-learning submission
   - execution dispatch
   - self-learning completion

6. 自学任务执行闭环已接入 canonical executor。
   - executor 侧适配器：[systems/execution/adapters.py](../systems/execution/adapters.py)
   - self-learning skill delegate：[systems/self_learning/skill_delegate.py](../systems/self_learning/skill_delegate.py)
   - facade 方法：`execute_self_learning_followup`
   - HTTP route：`POST /executor/self-learning/execute`
   - supervisor cycle 会派发已批准的 `self_learning` 任务，并把 executor 返回的 `supervisor_submission` 交回 `submit_self_learning_conclusion`。
   - executor 会加载 bundled `skills/self-learning` 的 `SKILL.md`、技术评估指南和学习总结模板，生成 `skill_execution` 证据包。
   - `SelfLearningSkillDelegate` 当前默认使用 bounded tool runner 后端，先生成可审计 `evidence_plan`，再调用 executor 侧允许的证据收集工具，并把每次调用、成功/失败和摘要写入 `tool_execution`。
   - `SelfLearningExecutionAdapter` 会把 `tool_execution` 派生为 `skill_evidence_summary`，写入 experiment observations 与 `supervisor_submission.metadata`，让 trace / Mem 侧不展开原始包也能看见证据来源、调用计数、失败工具和少量证据预览。
   - 行为边界：不生成正式 `SelfEvolutionExecutionRequest`，不执行 body upgrade / body switch / memory mutation，不绕过 supervisor queue。
   - 当前 runner 只允许 `web_search`、`search_files`、`read_file` 一类证据工具，可按任务约束组合外部搜索、本地仓库搜索和参考文件读取；失败会作为失败证据保留，不伪装成外部搜索成功。
   - 防重复：已派发任务写入 `execution_dispatched` / `self_learning_dispatched` 元数据，后续周期不会重复执行同一 approved task。

7. 默认运行配置已接入环境变量。
   - `SUPERVISOR_ENDOGENOUS_DRIVE_ENABLED`
   - `SUPERVISOR_ENDOGENOUS_DRIVE_INTERVAL`
   - `SUPERVISOR_ENDOGENOUS_DRIVE_MAX_CANDIDATES`
   - `SUPERVISOR_UI_ENABLED`
   - `SUPERVISOR_UI_AUTO_OPEN`
   - `SUPERVISOR_UI_AUTO_OPEN_DELAY_SECONDS`
   - `SUPERVISOR_UI_EVENT_INTERVAL_SECONDS`
   - `SUPERVISOR_UI_ACTIVITY_BUFFER_SIZE`
   - `SUPERVISOR_UI_PATH`

8. 监督者运行态 trace 查询视图已接入。
   - 文件：[systems/supervisor/trace_runtime.py](../systems/supervisor/trace_runtime.py)
   - 最近时间线：`GET /runtime/timeline`
   - trace 列表：`GET /runtime/traces`
   - trace 明细：`GET /runtime/traces/{trace_id}`
   - 聚合来源：self-evolution queue、supervisor activity、Mem governor history、gateway activity log。
   - UI `GET /ui/state` / SSE `state` 事件现在消费同一条统一 timeline，而不是把本地 UI activity buffer 当成主观察流。
   - 行为边界：只读查询，不创建任务，不修改队列，不派发执行，不让 UI 成为控制面。

9. Gateway bounded activity log 已接入。
   - 入口：`GET /admin/activity/log`
   - 配置：`GatewayConfig.activity_log_limit`
   - 写入点：`InternalGateway._touch_activity`
   - 行为边界：只记录支持的 activity kind，不改变 idle-window 使用的 activity snapshot，不成为执行控制面。
   - supervisor trace 视图现在读取 gateway activity log，而不是从 recent metadata 伪造 gateway 历史。

10. Phase 1 executor 级身体轮换验收已补强。
   - 覆盖正常升级：`shell -> candidate -> probe -> active`，旧 active 进入 `retired`，active body pointer 指向新 active。
   - 覆盖观察窗口通过：post-switch review 经 governor/lifecycle 执行 `retired -> shell`，并清退旧 active 进程。
   - 覆盖观察窗口失败：rollback review 经 governor/lifecycle 执行 `retired -> active`，失败新体进入 `retired` 并被清退。
   - 当前覆盖范围是 executor / registry / governor / lifecycle / watch-window 的进程内串联验收；真实 gateway / agent 子进程验收见后续条目。

11. Gateway / supervisor active body 管理面验收已加深。
   - 覆盖 gateway 首个 agent 自动成为 active body，`/api/` route 指向 active service。
   - 覆盖 slot-B 激活后 active body、`/api/` route、`/agent/` route、body status 与 health view 一致。
   - 覆盖 unhealthy body activation 返回 503，且不会导致 active body 或 `/api/` route 漂移。
   - 覆盖 supervisor 通过真实 gateway HTTP server 注册两个 agent service，并同步 active body 到 slot-B。
   - 覆盖同一 gateway server 上的 `/admin/activity/log` 可按 trace_id 回放 self-evolution execution 活动。
   - 当前仍未启动真实 agent 子进程；这是 gateway 管理面与 supervisor HTTP 接线验收，不等同于完整多进程运行验收。

12. 真实 agent 子进程 gateway 用户代理 smoke 已接入。
   - agent instance 现在暴露 gateway 代理需要的 `POST /v1/agent/query` 与 `POST /v1/chat/completions`。
   - 无 `DEEPSEEK_API_KEY` 时 agent instance 使用确定性本地 fallback，避免运行时 smoke 依赖外网模型调用。
   - 覆盖真实 `systems/agent/run_agent_instance.py` 子进程启动、`/health` 可用、自动注册 gateway、active body 指向 slot-B。
   - 覆盖用户请求通过 gateway `/v1/agent/query` 代理到真实 agent 子进程，并在 gateway `/admin/activity/log` 中按 `trace_id` 回放 `user_request`。
   - 覆盖 body upgrade pipeline 启动真实 agent 子进程、等待健康、同步 gateway active body，并通过 gateway 用户代理访问新 active body。
   - 覆盖 watch-window pass 后将 retired slot 回收为 shell，并停止旧 active agent 真实子进程。
   - 覆盖 watch-window failure 后经 governor/lifecycle 回滚到旧 active slot、同步 gateway active body 回旧 agent、停止失败新 agent，并通过 gateway 用户代理访问恢复后的旧 active body。

13. Agent 子进程清理路径已更稳。
   - supervisor 停止 agent 进程时优先使用 `psutil` 终止进程树，避免 Windows `taskkill` 权限或 PID 包装问题成为主路径。
   - `taskkill` / POSIX kill 仍作为 fallback。

## 3. 当前验证记录

当前阶段已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_activity.py tests/test_supervisor_runtime_wiring.py -q --basetemp=.tmp-pytest-python314
```

最近一次结果：

```text
35 passed
```

本轮新增统一 trace timeline 后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_supervisor_runtime_wiring.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
26 passed
```

本轮新增 gateway / supervisor active body 管理面验收后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_body_activation.py tests/test_gateway_integration.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
12 passed
```

本轮新增真实 agent 子进程 gateway 用户代理 smoke 后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime_isolation.py tests/test_gateway_integration.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
15 passed
```

本轮新增 body upgrade pipeline 驱动真实 agent runtime smoke 后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_integration.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
12 passed
```

本轮新增真实进程 watch-window pass/recycle smoke 后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_integration.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
13 passed
```

本轮新增真实进程 watch-window failure/rollback smoke 后已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_execution_adapters.py tests/test_gateway_integration.py -q --basetemp=.tmp-pytest-watch-rollback
```

结果：

```text
47 passed
```

本轮相关宽回归已通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_integration.py tests/test_agent_runtime_isolation.py tests/test_gateway_activity.py tests/test_gateway_body_activation.py tests/test_supervisor_body_runtime.py tests/test_execution_adapters.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
83 passed
```

本轮还通过：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_execution_adapters.py -q --basetemp=.tmp-pytest-python314
```

结果：

```text
33 passed
```

运行环境：

```text
Python 3.14.6
pip 26.1.2
pytest 9.1.1
```

此前阶段还通过：

```text
python -m pytest tests/test_supervisor_runtime_wiring.py -q
```

其中覆盖：

- supervisor canonical executor wiring
- endogenous drive runtime loop
- room UI route mounting
- room UI disable switch
- room UI state scene mapping
- room UI SSE event frame formatting
- room UI bounded activity timeline
- room UI activity timeline local persistence
- room UI activity governance-history mirror
- room UI state reads do not create timeline events
- self-learning executor adapter
- self-learning skill delegate
- executor `/executor/self-learning/execute`
- supervisor cycle dispatch for approved `self_learning`
- self-learning submission writeback
- duplicate dispatch prevention
- runtime trace route mounting
- runtime timeline route mounting
- UI state timeline consuming unified trace records
- gateway bounded activity log
- gateway activity log trace filtering
- runtime trace aggregation across queue, supervisor activity, Mem governor history and gateway activity log
- runtime trace list summaries when gateway activity is unavailable
- body upgrade executor normal upgrade with formal lineage
- body upgrade -> watch-window pass -> retired slot recycle
- body upgrade -> watch-window failure -> rollback to retired slot
- gateway active body activation route/status consistency
- supervisor agent registration and active body sync through real gateway HTTP server
- gateway activity log trace replay on the same gateway server
- agent instance gateway query/chat-completions surfaces
- real agent subprocess health, gateway registration, user query proxy and trace activity smoke
- body upgrade pipeline starts a real agent subprocess, waits for health, syncs gateway active body and serves user traffic
- real-process watch-window pass recycles retired slot and stops old active agent
- real-process watch-window failure rolls back gateway active body to the restored old agent and stops the failed new agent

下一次阶段切换前，建议继续运行：

```text
.\.venv\Scripts\python.exe -m pytest tests/test_execution_adapters.py tests/test_execution_service.py tests/test_supervisor_runtime_wiring.py tests/test_supervisor_self_evolution_queue.py tests/test_self_learning_service.py tests/test_supervisor_activity_window.py tests/test_gateway_activity.py -q --basetemp=.tmp-pytest-python314

## 4. Phase 1 核心闭环 — 已完成 ✓

Phase 1 的核心验证目标已达成。完整的五步循环全部可运行、可测试、可观测：

```
内生驱动器评估 → 监督者规划循环 → 执行器消费裁决 → 身体切换 → 闭环
```

**① 内生驱动器**：三大核心价值（continuity / truthfulness / creativity）→ 四类候选任务。gateway error tracking 链路已补全，truthfulness 候选可被 error_count 触发。idle-window 资格判定已修正。

**② Idle-Window + 治理裁决**：7 个 activity timestamp 多层判定，分 task_family 的资格检查，auto-decision 逻辑完整。时区安全已加固。

**③ 执行器 handoff**：planning → decide → execution_request → facade.dispatch → executor 消费。execution_dispatched 防重复，结果写回 queue metadata。

**④ 身体切换**：7 条状态转换全部有治理路径（含新增 abandon_candidate）。body upgrade pipeline 经真实 agent 子进程验收。

**⑤ 闭环**：watch-window trace_id 回写（last_body_upgrade_trace_id → GovernorRequest → Mem/trace）。runtime trace 按 trace_id 聚合四大来源。

**自学系统**：SelfLearningSkillDelegate 已升级为迭代式多步推理（gap-aware 证据分析 + follow-up query 精炼 + 跨轮次合并）。

**测试覆盖**：149 passed，含 15 个 Phase 1 端到端测试。

## 5. Phase 2 目标（未来工作）

1. 自学系统接入 LLM 驱动的完整 agent/subagent runner。
2. 完整真实进程事件流接入 runtime trace 做端到端验收。
3. Web UI 用户干预能力（通过标准治理 API）。
4. Gateway activity log 磁盘持久化。

## 6. 当前阶段结论

**Phase 1 核心闭环已实现。** VoidCube 现在是一个以内生任务驱动器为心跳、以监督者裁决为大脑、以 Mem 为灵魂、以执行器为手脚、以双槽位为安全网的母体系统。

Phase 1 的四重保障（灵魂连续、用户优先、治理可审计、失败可回滚）已从设计文档落实为可运行的代码和可验证的测试（149 passed）。系统具备了向自愈、自学习和自进化推进的同一套基础设施——不需要重建，只需要增强。

当前最重要的边界保持不变：

**监督者只判断，执行器只执行，UI 只观察。**
