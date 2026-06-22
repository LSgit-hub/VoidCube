# Phase 1 核心闭环与内生任务驱动器

## 1. 文档定位

本文档是 [voidcube架构基线.md](./voidcube架构基线.md) 的哲学与运行机理补充。

基线定义了 VoidCube 是什么、哪些组件存在、职责如何划分；本文聚焦一个更根本的问题：

**母体凭什么"自己动起来"？**

答案不是定时器，不是外部触发，不是用户指令——而是监督者的**内生任务驱动器**。它是母体运行的"心跳"，是系统从被动工具转为主动培养者的关键机制。

本文同时是对 Phase 1 核心验证目标的完整展开：

> **在灵魂连续、用户优先、治理可审计、失败可回滚的前提下，安全地培养并替换一个子 Agent。**

---

## 2. VoidCube 不是什么：三种反模式的拒绝

在理解 VoidCube 是什么之前，必须先澄清它刻意拒绝的三条路径。

### 2.1 拒绝"热自改"

```
反模式：让正在服务用户的 Agent 直接修改自己的代码。
风险：服务中断、状态污染、记忆污染、无法回滚、无法判断失败原因。
```

VoidCube 的做法：**永远不在 active 身体上做改造。** 所有改造发生在另一个隔离的 shell 槽位中。候选体通过 probe 验证、监督者审批后，才可能切换为新的 active。旧 active 保留在 retired 状态，观察窗口内随时可回滚。

这不是"更慢的自改进"，而是**从根本上消除了"改坏自己"的可能性**。

### 2.2 拒绝"无头进化"

```
反模式：Agent 看到什么就学什么，学到什么就改什么。
风险：盲目试错、方向漂移、引入已废弃技术、退化而不自知。
```

VoidCube 的做法：**自学习系统只生产证据和结论，不直接执行任何变更。** 所有学习结论必须进入 Mem，由监督者基于长期记忆、核心价值和系统状态做出裁决后，才能进入执行链路。

这不是"更保守的学习"，而是**让进化始终有方向、有证据、可审计**。

### 2.3 拒绝"被动等待"

```
反模式：系统只在用户发出指令时才工作。
风险：长期记忆衰减、技术栈老化、错误积累、系统逐渐"变傻"。
```

VoidCube 的做法：**监督者的内生驱动器持续评估系统状态，主动派生候选任务。** 但派生不等于执行——所有候选任务必须经过 idle-window 校验、治理裁决和 executor 消费才能落地。

这不是"系统自己乱动"，而是**在严格的安全约束下，系统知道自己该想什么、该做什么**。

---

## 3. VoidCube 是什么：母体式 Agent 培养系统

### 3.1 核心定义

VoidCube 是一个**单机多进程母体系统**：

- **母体**（VoidCube 本身）不直接暴露给用户
- **子 Agent**（双槽位中的两个 Agent）是母体持续培养、验证、切换和回滚的对象
- 用户最终接触到的始终只是当前 **active 子 Agent**

```
用户视角：  CLI → active Agent → 完成任务

系统视角：  ┌─────────────────────────────────────────┐
           │            VoidCube 母体                │
           │                                         │
           │  监督者 ←→ Mem ←→ 自学系统              │
           │    ↓                                    │
           │  执行器 ←→ 网关                         │
           │    ↓                                    │
           │  slot-A (active) ←→ slot-B (shell)      │
           │                                         │
           └─────────────────────────────────────────┘
```

### 3.2 母体与子体的关系

| 母体（VoidCube） | 子 Agent |
|-----------------|----------|
| 持有长期记忆与身份真相 | 持有临时会话态 |
| 判断是否升级、何时切换 | 执行用户任务 |
| 维护双槽位与状态机 | 调用工具 |
| 不直接服务用户 | 是用户感知到的"AI" |
| 由开发者维护 | 可被母体培养和替换 |

### 3.3 系统的真正核心升级对象

当前阶段，母体的核心升级对象是**子 Agent 本身**，而非 CLI、网关、Mem、执行器或自学系统。后者是支撑系统，目标是让 Agent 能稳定变好。

这意味着：
- 每轮改造的目标是"让下一个 Agent 比当前更好"
- 支撑系统的稳定性优先于支撑系统的自我升级
- 只有当子 Agent 的可替换、可验证、可回滚做实后，才谈更复杂的自我演化

---

## 4. 内生任务驱动器：母体运行的"心跳"

### 4.1 定位

内生任务驱动器（`systems/supervisor/endogenous_drive.py`）是母体从"被动工具"转变为"主动培养者"的关键机制。它不是定时触发器，不是外部调度器，而是**监督者内部的价值驱动引擎**。

它的核心定位：

```
内生驱动器 ≠ 执行者
内生驱动器 = "该主动想到什么"的机制

它只生成候选任务，不直接执行；
候选任务必须经过 idle-window、治理裁决和 executor 消费；
它回答的是"系统应该关心什么"，不是"系统可以不经审查做什么"。
```

### 4.2 三大核心价值

内生驱动器由三条核心价值驱动，每一条都映射到可审计的候选任务类型：

```
┌──────────────────────────────────────────────────────────────┐
│                      三大核心价值                              │
├───────────────┬──────────────────┬───────────────────────────┤
│  延续          │  真实             │  创造                     │
│  continuity   │  truthfulness    │  creativity               │
├───────────────┼──────────────────┼───────────────────────────┤
│ 维护长期记忆、 │ 把错误、不确定性、 │ 在空闲容量中提出           │
│ 演化谱系、队列 │ 证据缺口转成      │ 受边界约束的学习           │
│ 健康和服务连续性│ 复核或学习任务    │ 和改进方向                 │
├───────────────┼──────────────────┼───────────────────────────┤
│ → memory_     │ → self_learning  │ → self_learning           │
│   maintenance │   (复核信号)      │   (空闲学习)               │
│ → queue_      │                  │                           │
│   hygiene     │                  │                           │
└───────────────┴──────────────────┴───────────────────────────┘
```

### 4.3 四类内生候选任务

内生驱动器在每次评估周期中，基于系统活动事实和 idle-window 状态，至多生成 4 类候选任务：

| # | stable_key | 价值标签 | 优先级 | 触发条件 |
|---|-----------|---------|--------|---------|
| 1 | `continuity:memory_maintenance_sweep` | continuity | high | memory_maintenance 可规划 |
| 2 | `truthfulness:review_correction_signals` | truthfulness | high/normal | 存在错误或高不确定性信号 |
| 3 | `creativity:idle_learning_thread` | creativity | normal | 无活跃会话 + self_learning 可规划 |
| 4 | `continuity:queue_hygiene_review` | continuity, truthfulness | normal | self_evolution 可规划 |

每类候选任务都带有：
- **stable_key**：去重键，防止重复派生
- **utility**：效用分数，决定排序优先级
- **evidence**：触发该候选的系统事实依据
- **constraints**：硬约束（如 `must_not_modify_active_body`、`learn_only`）

### 4.4 候选任务的生命周期

```
内生驱动器评估
  │
  ├─ 读取 gateway activity snapshot（系统活动事实）
  ├─ 读取 idle-window 状态（各类任务的空闲判定）
  ├─ 读取已存在的 drive keys（去重）
  │
  ▼
生成 EndogenousTaskCandidate[]  （候选任务列表）
  │
  ├─ 按 utility 降序排列
  ├─ 过滤已存在的 stable_key
  ├─ 限制 max_candidates 数量
  │
  ▼
转换为 SelfEvolutionTask 并入队
  │
  ├─ 携带 governance_task_type / task_family / execution_kind
  ├─ 携带 core_value_definitions（审计溯源）
  ├─ 携带 constraints（硬约束）
  │
  ▼
进入监督者规划循环
  │
  ├─ task planning → task decision → task review
  ├─ approved → executor 消费
  ├─ deferred / cancelled → 回到队列
  │
  ▼
executor 执行
  │
  ├─ 执行结果写回 Mem
  ├─ queue 元数据标记 execution_dispatched（防重复）
  │
  ▼
下一轮内生驱动器评估（闭环）
```

### 4.5 为什么内生驱动器是"心跳"

内生驱动器之所以是母体运行的"心跳"，不是因为它在某个固定频率上跳动，而是因为它完成了系统从"静态组件集合"到"活的培养系统"的关键跃迁：

1. **没有内生驱动器**：系统是一组等待调用的服务。没有人调用，就什么都不发生。记忆会衰减，技术栈会老化，错误会积累——系统没有"自己发现问题"的能力。

2. **有内生驱动器但无约束**：系统会变成一个不受控的自我修改机器——这正是 VoidCube 刻意拒绝的"热自改"反模式。

3. **有内生驱动器 + 完整约束链**：系统知道自己该关心什么，但每一步都要经过 idle-window 校验、治理裁决和 executor 消费。**这是一个"有心跳但手脚受控"的系统。**

正是这个"心跳"，让 VoidCube 可以从 Phase 1（安全替换一个子 Agent）逐步推进到 Phase N（自愈、自学习、自进化），而不需要每次跃迁都重新发明系统的主动性机制。

---

## 5. Idle-Window：心跳的安全阀

### 5.1 为什么要 Idle-Window

内生驱动器让系统"想要做事"，但"想要做"不等于"现在就可以做"。Idle-Window 是内生驱动和安全执行之间的**安全阀**。

### 5.2 多层空闲判定

系统不依赖单一的空闲信号，而是对不同类型的活动分别判定：

```
执行窗口：00:00-06:00（默认，每日凌晨）
用户空闲阈值：600s（10 分钟）
记忆任务空闲阈值：600s
工作流冲突空闲阈值：600s

最早实际放行时间 ≈ 00:10（执行窗口开始 + 10 分钟空闲确认）
```

Idle-Window 检查的 7 个活动时间戳（全部由网关统一维护）：

| 时间戳 | 用途 |
|--------|------|
| `last_user_request_at` | 用户请求绝对优先 |
| `last_agent_work_at` | Agent 工作中不抢占 |
| `last_memory_task_at` | 记忆任务不互相冲突 |
| `last_self_learning_activity_at` | 自学活动不互相冲突 |
| `last_self_evolution_plan_at` | 规划不互相冲突 |
| `last_self_evolution_execute_at` | 执行不互相冲突 |
| `last_self_evolution_activity_at` | 自进化活动不互相冲突 |

### 5.3 分任务类型的资格判定

不同类型的任务有不同的放行条件：

| 任务类型 | eligible_for_planning | eligible_for_execution |
|----------|----------------------|------------------------|
| user | 总是 | 总是 |
| self_learning | 用户空闲 | 用户+Agent+记忆+自学+进化规划 全部空闲 |
| self_evolution | 用户空闲 | 用户+Agent+记忆+进化规划+进化执行 全部空闲 |
| memory_maintenance | 用户空闲 | 用户+Agent+记忆 全部空闲 |

**self_evolution 执行要求最严**——它要求所有维度都空闲。因为 body upgrade / body switch 是最高风险动作，必须在系统最安静的时候进行。

### 5.4 抢占规则

执行窗口期间一旦出现用户请求：
- 监督者将相关自提升任务裁决为 `paused`
- 执行器停止或安全中断可中断动作
- 网关恢复用户服务优先级
- 未完成任务等待下一次执行窗口重新裁决

---

## 6. Phase 1 核心验证闭环：四重保障

Phase 1 要验证的不是"系统能不能无限自进化"，而是更基本但更关键的一件事：

> **在灵魂连续、用户优先、治理可审计、失败可回滚的前提下，安全地培养并替换一个子 Agent。**

这四重保障各自对应系统中的哪些机制：

### 6.1 灵魂连续（Mem 作为长期真相源）

```
保障机制：
├── Mem 保存身份定义、长期记忆、治理历史、演化谱系
├── 子 Agent 切换时，Mem 权威状态不受影响
├── body runtime 可丢失——runtime 状态 ≠ 长期真相
├── 每个子 Agent 只持有临时会话态，不持有唯一长期副本
└── 状态归属文档（state-boundary.md）定义了什么必须写回 Mem

验证点：
├── 切换后新 Agent 能否通过 Mem 恢复身份连续性？
├── 治理历史是否在切换前后完整可审计？
└── 旧 Agent 的 runtime 清理后，系统是否仍知道"自己是谁"？
```

### 6.2 用户优先（Idle-Window + 抢占规则）

```
保障机制：
├── 网关统一维护用户活动时间戳
├── Idle-Window 要求用户连续空闲 ≥ 10 分钟
├── 执行窗口限制在 00:00-06:00
├── 用户请求到达时立即抢占
└── 自提升任务被暂停而非取消（可恢复）

验证点：
├── 用户请求是否能在任何时候立即获得响应？
├── 自提升任务是否曾在用户活跃期间执行？
└── 抢占后自提升任务是否正确进入 paused 状态？
```

### 6.3 治理可审计（裁决 → 执行 → 写回 链）

```
保障机制：
├── 内生驱动器生成候选任务时携带完整证据
├── task planning → decision → review 三级裁决
├── 每条裁决有 decision_id
├── 执行器消费裁决后写回执行结果
├── Mem governor history 保存完整治理记录
└── trace_id 贯穿 plan → decide → execute → writeback

验证点：
├── 每次身体切换是否有完整 governance trail？
├── probe 结果是否可被追溯？
├── 切换决策是否能被独立审计？
└── trace_id 是否贯穿全链路？
```

### 6.4 失败可回滚（Dual-Slot + Watch-Window）

```
保障机制：
├── 双槽位：一个 active 服务用户，一个 shell 待改造
├── probe：候选体在隔离环境受控验证
├── 切换后旧 active 保留为 retired（不立即销毁）
├── 观察窗口持续监控新 active 健康
├── 回滚路径：retired → active（恢复旧体）
└── 回滚记录写入 Mem

验证点：
├── 候选体 probe 失败时，active 是否不受影响？
├── 切换后异常时，是否能在观察窗口内回滚？
├── 回滚后 active pointer 是否正确恢复？
└── 失败新体是否被清退并不再接流量？
```

---

## 7. 完整运行循环：从心跳到切换

将内生驱动器、Idle-Window、治理裁决、执行器、双槽位状态机串联起来，就是 Phase 1 的完整运行循环：

```
┌─────────────────────────────────────────────────────────────────┐
│                    Phase 1 完整运行循环                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ① 内生驱动器评估                                                │
│     ├─ 读取 gateway activity snapshot                           │
│     ├─ 读取 idle-window 状态                                    │
│     ├─ 基于三大核心价值派生候选任务                               │
│     └─ 候选任务入 self_evolution_queue                          │
│                         ↓                                       │
│  ② 监督者规划循环                                                │
│     ├─ task planning：审阅队列，生成规划                          │
│     ├─ task decision：对候选任务做出裁决                         │
│     │   ├─ approve → 生成 SelfEvolutionExecutionRequest          │
│     │   ├─ defer  → 推迟到下一周期                              │
│     │   ├─ cancel → 取消并记录原因                              │
│     │   └─ pause  → 暂停等待条件满足                            │
│     └─ task review：回溯已执行任务的结果                         │
│                         ↓                                       │
│  ③ 执行器消费裁决                                                │
│     ├─ 检查 execution_request 合法性                            │
│     ├─ 检查 changed_files 边界（母体路径不可混入）                │
│     ├─ 执行具体动作：                                            │
│     │   ├─ body_upgrade：shell → candidate → probe → active     │
│     │   ├─ memory_maintenance：记忆压缩/整理/衰减               │
│     │   ├─ self_learning：evidence plan → tool execution       │
│     │   └─ general_self_evolution：队列卫生/谱系维护             │
│     └─ 执行结果写回 Mem + queue                                 │
│                         ↓                                       │
│  ④ 身体切换（如果是 body_upgrade）                               │
│     ├─ prepare slot-B（shell → candidate）                      │
│     ├─ probe slot-B（受控验证）                                  │
│     ├─ governor approve switch                                 │
│     ├─ activate slot-B，retire slot-A                          │
│     ├─ gateway sync active body                                │
│     ├─ watch-window 监控                                        │
│     │   ├─ pass → retire → shell（回收）                       │
│     │   └─ fail → rollback（retired → active）                 │
│     └─ 结果写回 Mem                                             │
│                         ↓                                       │
│  ⑤ 闭环：回到 ①                                                  │
│     └─ 内生驱动器在下一周期观察到执行结果，派生新的候选任务         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. 当前实现状态与验证记录

### 8.1 已稳定具备的能力

1. **内生驱动器已启用**，支持环境变量控制（`SUPERVISOR_ENDOGENOUS_DRIVE_ENABLED` 等）
2. **Idle-Window 多层判定**已接入 gateway activity snapshot，支持分任务类型的空闲评估
3. **Self-Evolution Queue** 已支持 planned / deferred / approved / running / paused / cancelled / completed / failed 八种任务状态
4. **Executor 级身体轮换**已验收：正常升级、观察窗口通过回收、观察窗口失败回滚
5. **真实 Agent 子进程**已验收：启动、健康检查、gateway 注册、用户流量代理、gateway activity log trace
6. **自学任务执行闭环**已接入 canonical executor，bounded evidence-plan tool runner 可采集可审计证据
7. **监督者房间 UI**已接入：只读可观测，不提供执行写入口
8. **Runtime Trace 查询**已接入：按 trace_id 汇总 queue / supervisor activity / Mem governor history / gateway activity log

### 8.2 当前验证通过

```
83 passed（覆盖 gateway integration, agent runtime isolation, 
         gateway activity, body activation, supervisor body runtime, 
         execution adapters）
```

### 8.3 下一阶段目标

1. **自学系统接入完整 agent/subagent runner**：从 bounded evidence plan 升级为按技能契约自主多步规划
2. **Watch-Window + Runtime Trace 端到端回放**：把真实进程事件流串成完整可回放时间线
3. **正式自进化切换自动触发**：从"测试/验收驱动"推进到"Mem/监督者自动触发"

---

## 9. 从 Phase 1 到自进化：可行的阶梯

Phase 1 验证了最小安全闭环后，系统具备了向更高级能力推进的现实基础：

```
Phase 1（当前）
  │  安全地培养并替换一个子 Agent
  │  内生驱动器 → 治理 → 执行 → 闭环
  │
  ▼
Phase 2：自愈
  │  内生驱动器主动发现异常信号
  │  自动触发诊断 → 修复 → probe → 切换
  │  系统能自己"治好自己的小病"
  │
  ▼
Phase 3：自学习
  │  完整 agent/subagent runner 驱动自学
  │  外部技术情报 + 内部经验 → 结构化知识底座
  │  学习结论成为内生驱动器的优质输入
  │
  ▼
Phase 4：自进化
  │  知识底座 + 内生驱动器 + 安全闭环 = 有方向的进化
  │  不只是"修复问题"，而是"主动变得更好"
  │  每次进化都有证据支撑、治理审计和回滚保护
```

**关键洞察**：Phase 1 不是"完成之后再做 Phase 2"的一次性步骤，而是**为所有后续阶段铺设了同一套基础设施**——内生驱动器的主动性、Idle-Window 的安全性、监督者/执行器的裁决-执行分离、Mem 的连续性、双槽位的可回滚性。这些设施在 Phase 2/3/4 中不需要重建，只需要增强。

---

## 10. 总原则（不可妥协）

1. **监督者只判断，执行器只执行**——这条边界不可模糊
2. **用户服务绝对优先**——任何自提升行为不能抢占用户链路
3. **不能跳过 probe**——候选体未经 probe 不得成为 active
4. **不能无记录切换**——每次切换必须有完整 governance trail
5. **不能切换后立即销毁旧体**——观察窗口是回滚保护层
6. **长期真相只属于 Mem**——body runtime 状态可丢失，Mem 权威状态不可丢失
7. **内生驱动器只派生候选**——不直接执行，不绕过治理

---

## 11. 一句话结论

VoidCube 不追求"一个 Agent 直接无限自改"，而是建立一个**以内生任务驱动器为心跳、以监督者裁决为大脑、以 Mem 为灵魂、以执行器为手脚、以双槽位为安全网**的母体系统。Phase 1 的核心验证目标是在四重保障（灵魂连续、用户优先、治理可审计、失败可回滚）下安全完成一次子 Agent 的培养与替换。一旦这个闭环持续跑通，系统就具备了向自愈、自学习和自进化推进的同一套基础设施——不需要重建，只需要增强。
