# Mem Integration Contract

## 1. 文档定位

本文是 VoidCube 与 Mem 的集成契约。

它只回答一个问题：

**即使当前 Mem 实现还没有完全成熟，VoidCube 的主链路应该如何按目标架构接入 Mem，而不是因为阶段性实现不足而改偏方向。**

## 2. 核心原则

Mem 是 VoidCube 的长期记忆与治理灵魂，不是可有可无的日志库。

当前实现可以分阶段：

- 轻量 governor history 可以先作为适配层
- Mem 查询、失败样本复用、自学习证据生产可以逐步补齐
- 写入失败可以先不阻断执行主链路

但目标架构不能改变：

```text
self-learning / runtime evidence
  -> Mem 长期记忆与治理记忆
  -> supervisor 基于 Mem 证据、Git lineage、probe report、idle-window 裁决
  -> SelfEvolutionExecutionRequest
  -> executor 执行
  -> execution outcome / failure sample 写回 Mem
```

一句话：

**当前代码可以临时降级，架构契约不能降级。**

## 3. Mem 在自进化中的目标职责

Mem 目标上应承担：

| 职责 | 说明 |
| --- | --- |
| 长期事实记忆 | 记录系统、用户、Agent、运行历史中的稳定事实。 |
| 治理记忆 | 记录每次批准、延期、拒绝、失败、回滚的理由和证据。 |
| 失败样本库 | 保存 `boundary_defer`、probe failure、watch-window rollback 等失败样本，避免重复犯错。 |
| 证据检索 | 为 supervisor 提供与当前候选体相关的历史证据。 |
| 自学习沉淀 | 将 self-learning 结论转成可追踪、可复查、可进入任务队列的建议。 |
| 身份连续性 | 维护 VoidCube 与子 Agent 在长期演化中的连续叙事和治理上下文。 |

Mem 不应承担：

- 直接执行切换
- 直接修改代码
- 绕过 supervisor 自动批准
- 替代 executor 执行动作
- 替代 Git 记录代码谱系

## 4. 当前适配层边界

当前 [plugins/memory/mem/governor_bridge.py](../plugins/memory/mem/governor_bridge.py) 是过渡适配层。

它可以先做：

- 记录 review
- 记录 execution outcome
- 记录 Git lineage
- 记录 `evolution_boundary`
- 记录 `boundary_defer` 失败样本

它暂时不代表完整 Mem：

- 不要求具备完整语义检索
- 不要求具备长期压缩与复用能力
- 不要求阻断主链路
- 不要求替代未来 MemAI 的正式治理记忆层

但是字段形状要尽量面向未来 Mem 设计，避免后续重接时推倒重来。

## 5. 接回主链路前的验收标准

Mem 真正接回 VoidCube 自进化主链路前，至少应满足：

| 能力 | 验收标准 |
| --- | --- |
| 治理事件 schema | 能统一表达 approve / defer / reject / rollback / boundary_defer / probe_failure。 |
| 查询接口 | 能按 task_id、body_id、commit、changed_files、event_type 查询历史事件。 |
| 失败样本复用 | 能检索相似 boundary violation 或 probe failure，并给 supervisor 作为证据。 |
| 证据摘要 | 能把历史证据压缩成 supervisor 可消费的简洁摘要。 |
| 写入可靠性 | 写入失败可降级，但不能静默丢失；至少要有错误记录与重试策略。 |
| 测试夹具 | 有覆盖多轮演化、失败、回滚、重复越界的 fixtures。 |
| 与 Git 谱系互引 | Mem 事件能引用 candidate commit、rollback commit、changed_files、probe report。 |

## 6. 推荐实施阶段

### Phase M1: 治理事件模型

先把治理事件建模稳定：

- `governance_event`
- `event_type`
- `task_id`
- `body_id`
- `git_lineage`
- `evolution_boundary`
- `decision`
- `reason`
- `evidence_refs`
- `created_at`

### Phase M2: 查询与索引

补齐检索能力：

- 按 commit 查询历史
- 按 changed_files 查询历史
- 按 violations 查询失败样本
- 按 body slot 查询演化轨迹
- 按 event_type 查询治理历史

### Phase M3: 监督者证据注入

让 supervisor 在裁决前读取 Mem 摘要：

```text
candidate git_lineage
  -> Mem query similar governance events
  -> evidence summary
  -> supervisor decision context
```

### Phase M4: 自学习闭环

让 self-learning 的结论进入 Mem，再由 Mem / supervisor 形成 self-evolution task。

但仍保持：

- supervisor 只判断
- executor 只执行
- CLI 只做测试、验收、排障、应急入口

## 7. 当前工程规则

当前阶段允许：

- 按目标契约设计字段
- 写入轻量治理历史
- 让写入失败不阻断 body review
- 在文档和测试中保留未来 Mem 接口方向

当前阶段不允许：

- 因 Mem 未完善而改掉 Mem 的架构位置
- 把未完成 Mem 查询当成正式裁决前置条件
- 让 Mem 直接执行切换
- 让 self-learning 直接绕过 supervisor 触发 executor

## 8. 一句话结论

VoidCube 的主链路应从现在起按 Mem 作为长期记忆与治理灵魂来设计；当前轻量实现只是过渡适配层，后续完善 Mem 时应沿着这个契约接回，而不是重新改写架构方向。
