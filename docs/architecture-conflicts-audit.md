# VoidCube 架构冲突与干扰点审计

## 1. 文档目的

本文档用于对照 [voidcube架构基线.md](./voidcube架构基线.md)，记录当前实现中仍会阻碍架构收口的冲突点与干扰点。

当前文档侧主线已经完成收口：

- 核心基线已经明确为最高优先级文档
- 组件规范已经拆分为 integration / lifecycle / switch / state boundary
- runbook 已收敛为操作手册
- 历史草稿已移入 [archive/](./archive/)

因此，本文后续重点不再是“文档目标不清”，而是：

**代码实现中仍残留的旧 supervisor 执行职责、网关活动分类不足、自学系统独立性不足、以及测试和配置仍固化过渡心智。**

## 2. 当前总体判断

当前项目已经形成稳定文档基线：

- CLI 是唯一用户入口
- 网关是内部组件入口
- Mem 是长期记忆与治理核心
- 监督者只判断
- 执行器只执行
- Agent 是当前主要升级对象
- 双 body slot 对应两个可培养、验证、切换、回滚的子 Agent

剩余问题主要属于实现层和接线完成度：

- `supervisor` 的执行型 HTTP 路由已从默认暴露面移除，也不再保留第二套 Python 执行对象面；剩余过渡语义主要体现在 execution-side route hint 元数据
- `systems/supervisor/` 已完成一轮模块化收口：execution route hint metadata、process/gateway runtime、planning runtime、service runtime、watch-window runtime 已拆到独立模块，`supervisor.py` 更接近装配层
- execution service 已提供标准 executor API wrapper，并可通过 gateway `/executor/` 路由接入；健康返回已显式声明 `/api/executor` 为推荐入口、`/executor` 为直连入口
- self-evolution task 已能在 Mem / 监督者批准时生成正式 `execution_request`，并由 executor `/self-evolution/execute` 消费
- body self-evolution 已建立路径边界：正式 `body_upgrade` / `body_switch` 必须携带子 Agent 身体层 `changed_files`，缺失或包含母体基础设施变更都会被阻断
- body registry 已能在 candidate/prepare 阶段从 Git diff 自动采集 `changed_files`，显式传入值仍优先
- 轻量 governor history 与正式 execution request 已写入 `evolution_boundary` 摘要，便于审核候选体是否越界
- self-evolution task 列表、详情、计划、决策与批量 review 返回已暴露 `evolution_boundary` 摘要，可在批准前发现越界风险
- 批量 review 会在 `evolution_boundary.ok=false` 时自动 defer 该 body task，并记录违规文件
- 边界违规 defer 已以 best-effort 方式写入轻量 governor history；写入失败不阻断 review 主链路
- VoidCube 母体就是本项目本身，由开发者按正常工程流程维护，不进入子 Agent 自进化自动执行链
- CLI 已具备 `body status`、`body upgrade`、`agent start` 测试/验收/应急命令，默认走 gateway `/api/executor/...`，executor 不可用时直接失败并提示检查执行器链路
- 正式身体切换应由 Mem / 监督者基于协议自动裁决触发，再以 `SelfEvolutionExecutionRequest` 交给执行器
- gateway 已区分 `self_learning`、self-evolution planning 与 execution 活动，并在 health / services / routes 管理面显式暴露 executor access policy 与 supervisor governance/runtime route policy；管理面主语义也已切到 `active_body`、`body_slots` 与 `body_routing`，旧 blue/green 公开入口已删除
- Mem / self-learning 是后续需要单独补完整的关键主线；主链路按 Mem 治理灵魂设计，当前以轻量适配层过渡
- CLI / Gateway 运维入口还没有完全覆盖内部调试接口

## 3. 已对齐基础

以下实现基础与当前基线方向一致，应继续沿用并加强：

| 能力 | 当前落点 | 判断 |
| --- | --- | --- |
| 内部网关 | [systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py) | 已具备注册、路由、`/executor/`、活动事实、body activation 雏形，并已显式暴露 executor access policy / route policy。 |
| 记忆服务 | [systems/memory/memory_service.py](../systems/memory/memory_service.py) | 已独立成面，可继续承接 API-B 入口。 |
| 治理语义 | [systems/governor.py](../systems/governor.py) | 已具备结构化裁决语义。 |
| 轻量治理历史 | [plugins/memory/mem/governor_bridge.py](../plugins/memory/mem/governor_bridge.py) | 当前仅作为本地 best-effort governor history，不等同于完整 Mem；已记录 review、execution outcome、Git lineage、`evolution_boundary` 摘要与 `boundary_defer` 失败样本。 |
| Mem 集成契约 | [mem-integration-contract.md](./mem-integration-contract.md) | 已明确目标链路仍以 Mem 作为长期记忆与治理灵魂，当前轻量实现只是适配层。 |
| body registry | [systems/body_registry.py](../systems/body_registry.py) | 已具备 slot、active pointer、watch-window、runtime/logs/meta。 |
| probe | [systems/probe.py](../systems/probe.py) | 已具备结构化 probe report。 |
| lifecycle executor | [systems/lifecycle.py](../systems/lifecycle.py) | 已承接治理批准后的确定性状态迁移。 |
| execution service / facade / adapters | [systems/execution/](../systems/execution/) | 已开始把动作从 supervisor 剥离，并提供标准 executor API wrapper；watch-window 后的清退动作已下沉到 executor adapter。 |
| formal self-evolution handoff | [systems/supervisor/task_queue.py](../systems/supervisor/task_queue.py)、[systems/execution/service.py](../systems/execution/service.py) | 已定义 `SelfEvolutionExecutionRequest`，正式执行请求必须包含治理批准、Git lineage、probe 引用、idle-window 与 rollback 证据。 |
| Agent evolution boundary | [systems/evolution_boundary.py](../systems/evolution_boundary.py)、[agent-evolution-boundary.md](./agent-evolution-boundary.md) | 已建立 Phase 1 路径边界，正式 body handoff 必须具备 `changed_files`，body registry 可自动从 Git diff 采集，并防止母体基础设施变更混入子 Agent 身体切换。 |
| self-evolution task API | [systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py) | 已在任务列表、详情、计划、决策和批量 review 返回中暴露 `evolution_boundary` 摘要。 |
| CLI executor ops | [VoidCube_cli/ops/executor.py](../VoidCube_cli/ops/executor.py)、[VoidCube_cli/main.py](../VoidCube_cli/main.py) | 已提供 gateway `/api/executor/...` 测试、验收、排障与应急客户端，并挂载 `body status`、`body upgrade`、`agent start`；旧 fallback 已不再静默。 |
| Mem 成熟度审计 | [mem-maturity-audit.md](./mem-maturity-audit.md) | 已列出 Mem 接回 VoidCube 主链路前必须补齐的治理事件 schema、索引、失败样本复用和证据摘要。 |
| self-learning | [systems/self_learning/service.py](../systems/self_learning/service.py) | 已具备学习结论与建议事项协议雏形。 |

## 4. 高优先级冲突点

| ID | 类型 | 现状 | 证据 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| C-01 | 监督治理越权 | canonical 启停入口已经收口到 executor，但 `supervisor` 本地 runtime 仍持有 agent 进程拉起/终止能力 | [systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py)、[systems/supervisor/process_gateway_runtime.py](../systems/supervisor/process_gateway_runtime.py)、[systems/execution/](../systems/execution/)、[systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py) | canonical surface 已清晰，但 supervisor 进程内仍保留执行 owner，后续容易继续长出新的本地 shortcut | 保持 executor 作为唯一对外执行面；新增调用一律接 canonical executor，不再给 supervisor 增长新的公共执行入口。 |
| C-02 | 监督治理越权 | 停止 Agent、清退旧体、处理失败体的执行动作已下沉到 watch-window execution adapter，但 watch-window 评估入口与循环仍在 supervisor | [systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py)、[systems/execution/adapters.py](../systems/execution/adapters.py) | 回收/回滚的动作面已收口，但协议驱动与后台循环仍未完全离开 supervisor | 继续让 supervisor 只保留证据汇总和裁决触发，把 watch-window protocol/loop 进一步迁到 executor 或专门运行时。 |
| C-03 | 监督治理越权 | `supervisor` 已不再保留独立执行 facade，execution route hint 元数据也已收口到 execution 域；但本地 process/runtime owner 仍可能被误读成长期公共执行面 | [systems/execution/route_hints.py](../systems/execution/route_hints.py)、[systems/supervisor/process_gateway_runtime.py](../systems/supervisor/process_gateway_runtime.py)、[systems/execution/service.py](../systems/execution/service.py)、[systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py) | 对象面与 route-hint owner 都已收口，但 supervisor 内部 runtime capability 仍可能诱发新的 shortcut | 继续缩小过渡面；替代路径落地后同轮删除旧残留，不再给 supervisor 增长新的公共执行接线。 |
| C-04 | 升级职责混合 | body upgrade pipeline 已可由 executor service 执行；正式触发已开始通过 self-evolution execution request 表达，但还未完全替代旧测试入口 | [systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py)、[systems/supervisor/task_queue.py](../systems/supervisor/task_queue.py)、[systems/execution/](../systems/execution/) | 旧测试命令仍可能被误当成正式自进化入口 | 继续让正式触发只走 Mem / 监督者批准后的 `/self-evolution/execute`，CLI `body upgrade` 保持测试/验收/应急定位。 |
| C-05 | 维护职责混合 | 记忆压缩入口已委托 `_memory_maintenance_executor`，并通过 executor 标准入口暴露推荐路径；但 supervisor 本地周期 loop 仍在触发这类执行维护 | [systems/supervisor/service_runtime.py](../systems/supervisor/service_runtime.py)、[systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py)、[systems/execution/service.py](../systems/execution/service.py) | facade 已删除，但“治理壳顺手做维护动作”的运行时混用还没完全退出 | 记忆维护应继续向 memory service job 或 executor 标准入口收口，supervisor 只保留调度/裁决所需最小 runtime。 |
| C-06 | Mem / self-learning 需要单独完善 | self-learning / Mem 深度集成尚未成熟，但主链路不能因此偏离 Mem 治理灵魂的目标设计 | [systems/self_learning/service.py](../systems/self_learning/service.py)、[plugins/memory/mem/](../plugins/memory/mem/)、[Mem/](../Mem/)、[mem-integration-contract.md](./mem-integration-contract.md) | 过早依赖未完成能力会不稳定；绕开 Mem 又会让架构跑偏 | 按 Mem 集成契约保留目标接法，先用轻量适配层占位，同时把 Mem 作为独立主线补完整。 |

## 5. 中优先级干扰点

| ID | 类型 | 现状 | 证据 | 干扰原因 | 建议 |
| --- | --- | --- | --- | --- | --- |
| D-01 | 活动分类仍需继续细化 | 网关已区分 `self_learning`、`self_evolution_plan` 与 `self_evolution_execute`；`planning_runtime` 已消费 plan / execute 分层事实，并在 idle-window、task payload、planning/review activity metadata 中显式保留 `governance_task_type`、`task_family` 与 `execution_kind`，从而区分 `body_upgrade`、`body_switch`、`memory_maintenance` 与一般 `self_evolution`。这些 canonical runtime 字段的归一化逻辑也已收敛到共享 helper [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)。 | [systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py)、[systems/supervisor/planning_runtime.py](../systems/supervisor/planning_runtime.py)、[systems/runtime_task_profile.py](../systems/runtime_task_profile.py) | 主链路已经不再只依赖聚合 `self_evolution` 活动，但这些事实仍可继续下沉到更多 runtime policy、trace 归因与执行窗口规则 | 继续把 gateway activity / trace / queue 事实下沉到更多 runtime policy，并维持 self-learning 独立运行面；后续不要再新增本地复制版 runtime-profile 推导。 |
| D-02 | body-first 管理面仍可继续清理文档叙事残留 | 网关对外管理面已切到 `active_body` / `body_slots` / `body_routing`，执行器公开切换入口也已改成 `body.activate`；gateway 内部 active body owner 命名也已同步收口 | [systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py)、[systems/execution/service.py](../systems/execution/service.py)、[systems/execution/route_hints.py](../systems/execution/route_hints.py) | 主链 body-first 语义已成立，但少量文档叙事仍可能把“active Agent”和“active body route target”混用 | 后续继续只把 “active Agent” 保留为用户服务叙事；新增管理面、协议和运维断言一律以 body slot / active body 为主语义。 |
| D-03 | API 面强化旧心智 | 执行型 HTTP 接口已从 supervisor 默认暴露面移除，legacy route 也已删除；当前主要风险转为 execution-side route hint 如果长期滞留，仍会延续旧调用面心智 | [systems/execution/route_hints.py](../systems/execution/route_hints.py)、[systems/execution/service.py](../systems/execution/service.py)、[systems/gateway/internal_gateway.py](../systems/gateway/internal_gateway.py) | 对外入口已收口，但过渡说明如果长期滞留，仍会把旧接口叙事留在项目里 | 继续删除不再需要的 route hint 和对应测试心智；CLI 手动入口只用于测试/应急。 |
| D-04 | 配置仍绑定旧角色 | `SupervisorConfig` 与系统级 `config.supervisor` 已收敛为 `execution` / `service_runtime` / `body_runtime` 分段 owner；当前主要风险转为旧调用习惯或新增字段重新回挂到 supervisor 根对象 | [systems/supervisor/config_models.py](../systems/supervisor/config_models.py)、[systems/supervisor/supervisor.py](../systems/supervisor/supervisor.py)、[systems/config.py](../systems/config.py) | 主体耦合已下降，但如果后续新增配置继续走扁平根字段，旧心智仍会复发 | 后续新增配置优先进入对应分段模型；替代路径稳定后同步删除旧字段、旧构造方式和旧测试断言。 |
| D-05 | 测试固化过渡实现 | 已新增 execution service / adapter 直测；supervisor 测试已开始收敛为治理接线、委托与 runtime followup 验证，仅保留少量有价值的 body runtime 集成链 | [tests/test_execution_service.py](../tests/test_execution_service.py)、[tests/test_execution_adapters.py](../tests/test_execution_adapters.py)、[tests/test_supervisor_body_runtime.py](../tests/test_supervisor_body_runtime.py) | 主要阻力已下降，但后续仍需持续防止把 executor 动作语义重新复制回 supervisor 测试 | 后续新增断言优先落在 executor tests；supervisor tests 只保留裁决、委托、路由接线与必要 wiring 保护。 |
| D-06 | 运维入口定位需防误解 | runbook 已说明 CLI/HTTP 示例只用于测试、验收、排障、应急恢复 | [body-runtime-runbook.md](./body-runtime-runbook.md)、[VoidCube_cli/ops/executor.py](../VoidCube_cli/ops/executor.py)、[VoidCube_cli/main.py](../VoidCube_cli/main.py) | 仍需防止后续把手动命令扩展成正式自进化触发器 | 后续 CLI 命令继续坚持测试/验收/应急定位，正式触发接 Mem / 监督者。 |
| D-07 | 母体边界需持续保护 | VoidCube 母体由开发者维护，不需要自动升级 executor；但 body 自进化必须持续阻断母体路径 | [agent-evolution-boundary.md](./agent-evolution-boundary.md)、[systems/evolution_boundary.py](../systems/evolution_boundary.py) | 若边界表滞后，新的母体目录可能被误归为未知或误放入 body 自进化 | 后续新增母体目录时同步更新边界表；body self-evolution 不扩权到母体维护范围。 |

## 6. 文档侧已处理事项

| 项目 | 当前状态 |
| --- | --- |
| 核心基线 | [voidcube架构基线.md](./voidcube架构基线.md) 已重写为最高优先级总基线。 |
| 组件接线 | [architecture-integration.md](./architecture-integration.md) 已收敛为接线说明。 |
| 核心闭环与内生驱动 | [phase1-core-loop-and-endogenous-drive.md](./phase1-core-loop-and-endogenous-drive.md) 已建立，定义三种反模式、母体心跳机理、四重保障与完整运行循环。 |
| body 状态机 | [body-lifecycle.md](./body-lifecycle.md) 已收敛为状态机规范。 |
| 切换协议 | [switch-protocol.md](./switch-protocol.md) 已收敛为切换协议。 |
| 状态边界 | [state-boundary.md](./state-boundary.md) 已收敛为状态归属规范。 |
| 运行手册 | [body-runtime-runbook.md](./body-runtime-runbook.md) 已收敛为操作与排障手册。 |
| 文档入口 | [README.md](./README.md) 已列出当前执行依据、实施文档、理论资料和 archive。 |
| 历史草稿 | `body-lifecycle-and-switch.md` 与 `single-repo-dual-body-experiment.md` 已移入 [archive/](./archive/)。 |

## 7. 建议推进顺序

建议后续按这个顺序进入代码层收口：

1. 先稳定子 Agent 本体，也就是 [agent-evolution-boundary.md](./agent-evolution-boundary.md) 中定义的 body 范围：`agent/`、`systems/agent/`、`tools/`、`skills/`、`presets/` 与 `run_agent.py`。优先修通启动链、工具链、sandbox/path 行为与 agent 级 smoke，而不是先继续叠加自学习/自改进编排。
2. 在 body 本体可稳定工作的前提下，再收口最小 body runtime 闭环：`prepare -> candidate -> probe -> activate -> rollback`。目标是先确保“修好的 agent”可以被验证、切换和回退，而不是只在单进程局部看起来可用。
3. 然后继续稳定 body self-evolution 的任务队列、边界校验、Git lineage 和 executor handoff，但此时它服务的是“已可运行的 body”，不再承担替代 agent 本体修 bug 的职责。
4. 继续删除不再需要的过渡 notice、legacy route 与 supervisor 残留执行 helper，不再新增执行职责；同时扩大 executor 层测试覆盖，逐步把动作细节从 supervisor 测试中迁出。
5. 再继续细化 gateway activity schema，把 `self_learning` / self-evolution / memory maintenance 的事实继续下沉到 `governance_task_type`、`task_family`、`execution_kind`、idle policy 与 trace 里。
6. 将 Mem 作为独立主线补完整，先按 [mem-maturity-audit.md](./mem-maturity-audit.md) 的 Phase M1 新增 governance event schema，并把 Git 演化谱系继续下沉到 body slot meta、probe report 与轻量治理历史。
7. 等 Mem / self-learning 方案单独成熟后，按既定契约增强现有适配层，而不是在 agent 本体和 body runtime 还不稳定时提前把大规模自治链路接回主线上。

补充判断：

- 当前 `supervisor` 的周期任务与自动 review/runtime loop 仍属于配套运行时，不应被误当成当前第一优先级主战场。
- 在 agent body 尚未稳定前，过早推进 Mem / self-learning / 自动自改进大架构，只会放大错误来源并增加排障难度。

## 8. 一句话结论

文档目标已经基本稳定，当前架构收口的主战场已经转到代码：让 supervisor 退出执行职责，让 executor 成为唯一动作执行面，让 gateway 成为空闲判断与路由事实源；Mem / self-learning 是下一条需要单独补完整的关键主线，但 VoidCube 主链路从现在起就按 Mem 治理灵魂的目标契约设计。
