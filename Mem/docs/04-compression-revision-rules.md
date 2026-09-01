# 压缩与修订规则 v1

## 1. 目的

本文档定义记忆系统如何压缩旧材料、修订过时摘要，以及如何在不损害历史连贯性的前提下遗忘低价值细节。

## 2. 核心理念

当前系统只有一个面向会话内容的压缩边界：

`Tier 1 Turn -> Tier 2 Event / Scene / Arc / Epoch`

近期材料保持详细；达到 Tier 1 保留条件后，系统从一批原始 Turn 中一次性生成
四类 Tier 2 结构。`Event -> Scene -> Arc -> Epoch` 描述的是同一批 Tier 2
结果中的语义包含和叙事组织，不是按年龄再次执行的自动压缩阶梯。

较旧的 Event、Scene、Arc、Epoch 可以参与检索、主题演变、状态分析和证据追踪，
但当前不会因为 14、60、180 或 365 天过去而自动生成 successor 并取代原记录。

## 3. Tier 1 保留与处理窗口

- `0-7 days`：原始 Turn 保持在 Tier 1，继续接受相关性衰减。
- `7+ days`：符合候选条件的 Turn 进入唯一的 `Tier 1 -> Tier 2` 压缩流程。
- 低相关性 Turn 正常等待；当积压达到容量上限时，允许最老优先的受控兜底。

四层 Tier 2 结构的生成不依赖后续年龄窗口。日、周、月摘要属于独立的永久时间
索引，不参与本规则的内容压缩。

## 5. 压缩优先级

压缩时，按以下顺序保留：
1. 转折点，
2. 修正与逆转，
3. 里程碑，
4. 活跃的未解决问题，
5. 结构连续性，
6. 仅在仍有用时保留支持性细节。

## 6. 压缩可以移除的内容

压缩可以移除或归并：
- 重复措辞，
- 近乎重复的事件，
- 影响较低的局部细节，
- 暂时性的措辞差异，
- 不影响轨迹的填充内容。

## 7. 压缩必须保留的内容

压缩必须保留：
- 事件顺序，
- 方向变化，
- 状态变化，
- 脉络分类变化，
- 证据可追溯性，
- 尚未解决且具有历史意义的矛盾。

## 8. 压缩处理类型

### 场景整合

场景整合只发生在 Tier 2 生成过程中：

- 合并重叠或冗余事件；
- 生成紧凑的局部摘要；
- 保留关键事件和局部转折点。

### 脉络整合

脉络绑定只发生在 Tier 2 生成过程中：

- 将同一批次中具有连续性的场景绑定到轨迹摘要中；
- 保留里程碑和转折点；
- 保留场景到脉络的证据引用，不自动退役原场景。

### 纪元整合

纪元生成只发生在 Tier 2 生成过程中：

- 总结同一批次中具有共同历史中心的脉络；
- 保留主要脉络和篇章变化；
- 保留脉络到纪元的证据引用，不自动退役原脉络。

## 9. 压缩前置条件

压缩对象前，系统应验证：
- 对象早于相关策略窗口，
- 其子对象具备足够证据来支持更高层级摘要，
- 没有尚待处理的修订，
- 且摘要目标会保留关键结构。

## 10. 修订触发条件

在以下情况下应触发修订：
- 时间戳或时间顺序有误，
- 事件依附到了错误的脉络，
- 支线成为主线，
- 主线被夸大，
- 先前摘要遗漏了转折点，
- 新证据与旧结论相矛盾，
- 压缩造成漂移或夸大。

## 11. 修订类型

建议的 v1 修订分类：

```text
time_correction
attachment_correction
classification_revision
factual_revision
compression_upgrade
confidence_update
closure_update
```

## 12. 修订流程

需要修订时：
1. 识别受影响的对象，
2. 定位支持证据与冲突证据，
3. 生成修订后的对象，
4. 将较旧对象标记为 `superseded`，
5. 通过 `supersedes` 链接新对象，
6. 必要时将更新传播到父级摘要，
7. 保持可审计性。

## 13. 修订不变量

- 修订绝不能静默覆盖历史状态。
- 修订后的摘要必须保持可追溯到证据。
- 除非请求审计或历史比较，否则已取代对象不应出现在默认检索中。
- 当子对象的含义发生实质变化时，必须刷新父级摘要。

## 14. 受控遗忘

系统分阶段遗忘，但自动流程必须先报告、再标记、最后才删除。

### 14.1 Dormant

`dormant` 是 `Arc` 的活动状态，不是删除状态，也不是新一轮内容压缩。

一条 `Arc` 可以在以下条件下进入休眠候选：

- `memory_type = 'arc'`；
- `status = 'active'`；
- 以 `max(timespan_end, last_accessed_at, active child timespan_end)` 为活动锚点；
- 活动锚点超过配置的休眠窗口，当前首版报告默认 30 天；
- 未被 pin、hide；
- 不属于 identity/founding 记忆。

休眠脉络仍然可检索，且仍然可以影响历史摘要。`dormant` 不复用 `status`
字段，而是写入独立的 `activity_state`；默认召回仍能读取休眠 Arc，但会在排序中
降权。通过 `get_compressed` 或 `search_compressed` 命中休眠 Arc 时，系统会自动唤醒
为 `active` 并清空休眠原因。

### 14.2 Purge review

服务端接口 `POST /compressed/retention-review` 是只读审查接口，不改写数据库，返回：

- `dormant_candidates`：可考虑休眠的 Arc；
- `purge_candidates`：可考虑逻辑遗忘的低价值 Event/Scene；
- `protected`：未入选 purge 的记录及保护原因；
- `overview`：当前作用域内 compressed memory 类型和状态分布。

自动 purge 候选只覆盖 `compressed_memories` 中的 `event` 和 `scene`，不覆盖：

- `turns` / `turns_archive`；
- `profile_memories`；
- `time_summaries` / `time_summary_links` / `session_summary_sources`；
- `arc` / `epoch`；
- identity/founding 记忆；
- pinned 或 hidden 记忆。

Event 候选必须同时满足：

- 活动锚点超过默认 180 天；
- `importance < 0.35`；
- `confidence < 0.5`；
- `event_kind` 不是 `decision/correction/shift/blocker/completion/conflict`；
- 存在 active Scene 父摘要；
- 直接 source turns 已归档；
- 没有 citation、relevant feedback、promotion 引用或待处理 promotion candidate。

Scene 候选必须同时满足：

- 活动锚点超过默认 365 天；
- `importance < 0.45`；
- `confidence < 0.5`；
- 存在 active Arc 父摘要；
- 没有 citation、relevant feedback、promotion 引用或待处理 promotion candidate。

### 14.3 自动遗忘状态机

自动维护流程使用以下持久状态字段：

```text
activity_state active|dormant|resolved
dormant_at
dormant_reason
retention_state retained|purge_candidate|purged
purge_candidate_at
purge_reason
purged_at
```

维护规则每轮重新评估候选：满足 purge 条件的 Event/Scene 会先标记为
`retention_state = 'purge_candidate'` 并记录原因；候选持续满足条件超过默认 30 天后，
进入逻辑 `purged`，同时 `status = 'purged'`、`activity_state = 'resolved'`、`weight = 0`。

逻辑 `purged` 后应立即退出默认召回，并清理或重建 FTS、embedding 和实体图引用；
物理删除仍需保留至少 90 天审计窗口，按 `purged_at` 计时。已经处于 `purged` 且超过
审计保留期的历史记录会被清理；用户明确发起的 `forget` 仍然有效。

## 15. 主线保留规则

主线应被压缩，而非丢弃。

即使主线进入休眠或已解决状态，系统也必须保留：
- 其脉络摘要，
- 其主要里程碑，
- 其转折点，
- 其关闭或休眠状态。

## 16. 支线退役规则

当以下所有条件均成立时，支线可以退役：
- 重要性低，
- 重新激活程度低，
- 没有重大下游依赖，
- 不参与篇章变化，
- 且内容已在其他位置得到表示。

退役前，系统应尝试生成最后一个压缩标签或句子级归档。

## 17. 漂移检测

当摘要变得比其证据所支持的内容更具解读性、更为确定或更具全局性时，就发生了压缩漂移。

漂移信号：
- 断言强于源证据，
- 使用记录中没有的特质式语言，
- 遗漏转折点，
- 颠倒事件顺序，
- 错误关闭。

漂移应触发审查，并可能触发修订。

## 18. 审查周期

建议的 v1 后台维护周期：
- 每次 Tier 2 桥接：从原始 Turn 生成 Event、Scene、Arc、Epoch，完成来源审计；
- 每日：更新相关性衰减并检查待压缩候选；
- 每月：重新评估脉络休眠状态；
- 每季度：审查纪元和遗忘候选，但不自动执行四层 successor 压缩。

## 19. 审计元数据

每次压缩或修订操作都应记录：

```json
{
  "action_type": "compression|revision|retirement",
  "reason": "aging_window_elapsed",
  "source_ids": ["scene_004", "scene_005"],
  "result_id": "arc_010",
  "timestamp": "2026-03-22T12:00:00Z"
}
```

## 20. 安全姿态

如果不确定是否应采取激进的压缩或修订，系统应选择更保守的操作：
- 少压缩，
- 多保留，
- 并明确标记不确定性。
