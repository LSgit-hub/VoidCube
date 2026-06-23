# Phase 2 实现计划：架构基线对齐

## 概述

本文档将 [voidcube架构基线.md](./voidcube架构基线.md) 中已确认的架构变更分解为可逐步实现的 Phase 2 任务。

关键发现：`_try_auto_dispatch()` 已经跳过 self_learning 任务（注释："Self-learning task waiting for agent to pull via /v1/tasks"），Gateway 的 pull API（`GET /v1/tasks`、`POST /v1/tasks/{id}/complete`）已存在但是死代码。架构早已为 pull 模式预留了接线，只是消费者（Agent 侧拉取逻辑）从未实现。

---

## 任务 1：补齐 `running` 状态（数据模型）

**现状**：`SelfEvolutionTaskStatus` 只有 `planned/deferred/approved/paused/cancelled/completed/failed`，缺少 `running`。代码用 `metadata.execution_dispatched` 布尔值追踪"已分派"状态，但无法区分"已分派但尚未开始执行"和"正在执行中"。

**改动**：
- `systems/supervisor/task_queue.py` L18：`SelfEvolutionTaskStatus` 添加 `"running"`
- `systems/supervisor/planning_runtime.py`：Agent 拉取任务时设置 `status="running"`；Agent 完成/失败时设置 `status="completed"/"failed"`
- 废止 `metadata.execution_dispatched` 布尔值（用 `status="running"` 替代）

**依赖**：无

**验收**：任务状态流转 `planned → approved → running → completed` 在单元测试中可验证

---

## 任务 2：激活 Agent Pull 模式（核心变更）

**现状**：
- Gateway `GET /v1/tasks?status=approved&task_type=self_learning` 存在但无消费者
- Gateway `POST /v1/tasks/{task_id}/complete` 存在但无消费者
- CLI `_poll_auto_mode_workflow()` 是 no-op
- Agent 没有拉取任务列表的逻辑

**改动**：

### 2a. CLI 侧：激活 `_poll_auto_mode_workflow()`
- `cli.py` L2234：将 no-op 替换为实际的拉取-执行-回报循环
- 逻辑：
  1. 调用 Gateway `GET /v1/tasks?status=approved&task_type=self_learning`
  2. 取回 approved 学习任务列表
  3. 逐条执行（Agent 自主决定是否使用子代理辅助）
  4. 完成后 POST 回报到 Gateway `POST /v1/tasks/{task_id}/complete`
  5. 学习成果写入 Mem
- 不需要新增 HTTP 端点——复用 Gateway 已有的 pull API

### 2b. Agent 侧：无需新增路由
- Agent 执行学习任务的能力已存在（通过 CLI 的 Agent 实例执行）
- 不新增 `/v1/agent/governance-task` 类似端点——Agent 直接从 CLI 侧执行

**依赖**：任务 1（需要 `running` 状态追踪执行过程）

**验收**：AUTO 模式下，Agent 能拉取到监督者产出的学习任务、执行、并回报完成

---

## 任务 3：废弃 Push 路径（清理死代码）

**现状**：Push 路径仍然活跃：
- `planning_runtime._dispatch_self_learning_followup()` → facade → adapter → Gateway `governance_task_proxy` → Agent `handle_governance_task` → 子代理执行

Pull 模式激活后，这条路径变为死代码。

**改动**：
- `systems/supervisor/planning_runtime.py`：
  - 移除 `_dispatch_self_learning_followup()` 方法
  - `_run_self_evolution_cycle()` 中不再对 self_learning 任务调用 dispatch
  - `plan_self_evolution_task()` 保留——监督者仍需产出任务放入列表
- `systems/supervisor/service_runtime.py`：
  - `_try_auto_dispatch()` 中移除 self_learning 分支（L375-376 已经跳过，直接删除即可）
- `systems/execution/adapters.py`：
  - 移除 `SelfLearningExecutionAdapter.execute_self_learning_followup()` 中 push 到 Agent 的逻辑
  - 保留 `_dispatch_to_agent()` 仅作为 fallback（procedural delegate）
- `systems/gateway/internal_gateway.py`：
  - 移除 `governance_task_proxy()` 方法和路由注册 L153
- `systems/agent/run_agent_instance.py`：
  - 移除 `handle_governance_task()` 方法和路由注册 L86

**依赖**：任务 2（pull 模式先跑通，push 才能删）

**验收**：push 路径相关代码全部移除，相关测试更新，无遗留 import

---

## 任务 4：收口 Self-Learning Service（清理独立运行服务）

**现状**：`systems/self_learning/service.py` 作为 library 被 supervisor 实例化使用，不是独立运行服务。但代码中仍有独立运行的心智残留：
- `SelfLearningSkillDelegate` 作为"过程化 fallback"存在
- `SelfLearningExecutionAdapter` 包含完整的 push 链路（将在任务 3 移除）
- `service.py` 中的 `SelfLearningService` 的 CRUD 逻辑与 supervisor 紧耦合

**改动**：
- 保留 `systems/self_learning/models.py`（学习数据模型：Topic/Session/Experiment/Conclusion）
- 保留 `systems/self_learning/service.py` 中的学习记录 CRUD（供 Agent 完成后写入学习成果）
- 移除 `systems/self_learning/skill_delegate.py`（过程化 runner——Agent 直接执行后在 Mem 中记录）
- 移除 `systems/execution/adapters.py` 中 `SelfLearningExecutionAdapter` 的 push 相关逻辑（已在任务 3 移除）
- 学习任务成果的写入路径简化为：Agent 执行 → 写入 Mem

**依赖**：任务 3（push 路径移除后，adapter 的清理更干净）

**验收**：`systems/self_learning/` 目录只保留数据模型和 CRUD，不再有独立执行逻辑

---

## 任务 5：清理任务队列内容（移除 body_upgrade/body_switch）

**现状**：`SelfEvolutionTaskQueue` 是一个扁平队列，包含所有类型任务。`SelfEvolutionExecutionRequestKind` 包含 `body_upgrade`/`body_switch`。但基线明确：身体切换不由任务队列驱动，监督者内生判断后直接裁决→执行器执行。

**改动**：
- `systems/supervisor/task_queue.py` L19-24：
  - `SelfEvolutionExecutionRequestKind` 从 4 个缩减为 2 个：`memory_maintenance`、`general_self_evolution`
  - 移除 `body_upgrade`、`body_switch`——这些不由任务队列驱动
- `systems/supervisor/planning_runtime.py`：
  - `_task_execution_kind()` 移除 body_upgrade/body_switch 分支
  - `_dispatch_self_evolution_execution_request()` 移除 body_upgrade/body_switch 处理
- 身体切换的触发路径改为：监督者 → 直接调用执行器 → 执行切换

**依赖**：无（数据模型清理，不依赖其他任务）

**验收**：任务队列中不再出现 body_upgrade/body_switch 类型任务

---

## 任务 6：监督者监控 UI 重新设计

**现状**（见基线 §8.1 的问题列表）：
- 任务面板只展示 6 条（`tasks[:6]`），不区分执行路径
- 身体切换状态不可见
- 内生驱动候选与学习任务混杂
- 5 个静态场景无法表达系统真实状态
- 指标只有 4 个数字

**改动**（`systems/supervisor/ui_runtime.py`）：

### 6a. 任务面板重新设计
- 按执行路径分组，展示全量任务（带分页/滚动）：
  - **学习任务**：`task_type=learning`, 执行路径：监督者→任务列表→Agent
  - **身体切换**：直接读取 body_registry 状态（shell/candidate/probe/active）
  - **内生驱动候选**：展示最近内生驱动产出的四类候选
  - **记忆维护**：展示 memory_maintenance 任务
- 每条任务标注：源头、状态、执行路径、launched_at、completed_at

### 6b. 场景（scene）重定义
- 当前 5 个场景 → 改为任务活动驱动：
  - `idle`：(无任何活动)
  - `drive`：(内生驱动评估中)
  - `learning`：(Agent 执行学习任务中)
  - `body_switch`：(执行器执行身体切换中)
  - `maintenance`：(记忆维护/队列卫生)

### 6c. 指标面板升级
- 保留：队列总数
- 新增：按路径分组统计（学习任务 N、身体切换状态、内生候选数）
- 新增：最近学习任务执行结果（completed/failed）

**依赖**：任务 1-5（UI 展示的数据来自正确的数据模型）

**验收**：UI 中能看到全部任务、按路径分组、身体切换状态可见、场景由真实活动驱动

---

## 执行顺序与依赖关系

```
任务 1（running 状态）
  │
  ├──→ 任务 2（Agent Pull 模式）★ 核心
  │     │
  │     └──→ 任务 3（废弃 Push 路径）
  │           │
  │           └──→ 任务 4（收口 Self-Learning）
  │
  ├──→ 任务 5（清理队列内容）—— 独立，可与 2-4 并行
  │
  └──→ 任务 6（UI 重新设计）—— 最后，依赖 1-5 完成
```

**建议**：任务 1 和任务 5 可以同时开。任务 2 是核心变更，需要任务 1 完成。任务 3 和 4 在任务 2 验证通过后再做。

---

## 风险与注意事项

1. **任务 2 是最大风险点**：CLI `_poll_auto_mode_workflow()` 从 no-op 变为实际拉取执行，需要验证 Agent 在 AUTO 模式下能正确接收用户输入阻塞（用户服务优先）。
2. **任务 3 不可回滚**：push 路径代码删除后，如果 pull 模式有问题，需要从 git 恢复。建议在任务 2 验证通过后至少等一轮测试再执行任务 3。
3. **测试同步**：每完成一个任务，对应更新 `tests/` 中的测试断言。
4. **Gateway 路由清理**：任务 3 移除 `governance_task_proxy` 路由后，确保 Gateway 的 health check 和 route listing 同步更新。
