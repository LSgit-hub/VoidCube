# VoidCube 记忆系统 — 完整逻辑审查报告

> 审查日期：2026-08-05
>
> 审查范围：`systems/memory/` 全部 23 个源文件 + `docs/项目说明书/记忆系统说明.md`
>
> 审查方法：4 个并行审查代理（权限/Scope、数据流/压缩生命周期、召回排序、Schema一致性）+ 独立交叉分析

---

## 总览

| 严重度 | 数量 | 含义 |
|--------|------|------|
| 🔴 Critical | 2 | 数据正确性受损，需立即修复 |
| 🟠 High | 4 | 显著影响召回质量或系统稳定性 |
| 🟡 Medium | 11 | 设计缺陷，建议尽快修复 |
| 🔵 Low | 15 | 改进建议、代码卫生问题 |

---

## 🔴 Critical

### C1. `_cmem_row_to_dict` 在增量迁移数据库上列索引错位

**文件：** [database.py:538-555](../systems/memory/database.py#L538-L555) vs [memory_service.py:346-391](../systems/memory/memory_service.py#L346-L391)

**根因：** `_migrate_compressed_memories_schema` 的迁移元组将 `created_at` 插在中间位置（`weight` 之后、`event_kind` 之前），但 CREATE TABLE 语句中 `created_at` 是最后一列。在通过增量迁移升级的数据库上（非全新安装），ALTER TABLE 按迁移顺序添加列，实际列顺序与 `_cmem_row_to_dict` 的硬编码索引不符。

**错位效果：**

| 代码期望 | 实际读到（迁移DB） |
|---------|------------------|
| `row[17]` = event_kind | `created_at`（时间戳字符串） |
| `row[21]` = pinned | `hidden` |
| `row[22]` = hidden | `identity_layer` |
| … | …（后续全部偏移 1 位） |
| `row[30]` = memory_domain | `workspace_id` |

**受影响 API：** `search_compressed`、`get_compressed`、`trace_compressed_by_turn`、`get_identity_archive`、`verify_identity_experience`、`remember`（均使用 `SELECT *`）

**影响：**
- `compute_dynamic_weight` 收到时间戳字符串作为 `event_kind`，content_bonus 静默失效
- 公开 API 返回错误的 `memory_domain`、`pinned`、`hidden`、`identity_layer`、`event_kind` 等字段
- 新安装数据库不受影响，但有迁移历史的数据库全部受影响

**修复方向：**
1. 将 `_cmem_row_to_dict` 改为按列名取值（使用 `row.keys()` 或显式 SELECT 列名）
2. 或将迁移元组中的 `created_at` 移至末尾，并重建受影响数据库的列顺序
3. 所有 `SELECT *` 改为显式列名以消除对列顺序的依赖

---

### C2. FTS5 词法搜索静默丢弃 2-字符 CJK 词

**文件：** [lexical_index.py:182](../systems/memory/lexical_index.py#L182) vs [recall.py:678](../systems/memory/recall.py#L678)

**根因：** `search_memory_fts` 中 `if len(term) < 3: continue` 丢弃所有长度小于 3 的搜索词。但 `_extract_terms` 使用 `(2, 4)` 权重生成 2-字符 CJK n-gram（如"星子"、"记忆"、"偏好"、"配置"等）。这种不对称导致大量中文搜索词的词法召回贡献为零。

**影响：**
- 查询"星子的偏好" → 2-gram "星子"和"偏好"被丢弃，只有 3-gram+ 参与 FTS5
- 短中文查询几乎完全依赖语义索引（可能同样缺失）
- 用户感知：中文关键词搜索效果差，尤其是 2-4 字的精确查询

**修复方向：**
1. 降低 `search_memory_fts` 的最小词长阈值至 2（需评估 FTS5 trigram 对 2-gram 的实际效果）
2. 或改用 SQLite FTS5 `unicode61` tokenizer 配合 CJK 分词，而非 trigram

---

## 🟠 High

### H1. 压缩生命周期升级完全绕过质量门禁

**文件：** [memory_service.py:470-617](../systems/memory/memory_service.py#L470-L617)

Tier1→Tier2 的首次压缩（bridge）有 7 项质量门禁指标（event_coverage、backlink_completeness、compression_ratio、degraded_fraction、source_support、identifier_fidelity、polarity_consistency）。但 `_apply_compression_lifecycle` 通过 `_llm_escalate_summary` 将 Event→Scene→Arc→Epoch 升级时**完全绕过所有质量检查**。LLM 产生的幻觉或低质量摘要会以 `status='active'` 直接写入 `compressed_memories`。

**修复方向：** 为升级路径添加至少 source_support 和 identifier_fidelity 两项检查；或复用 `_evaluate_quality` 的核心逻辑。

---

### H2. 质量门禁拒绝后无限重试，无退避策略

**文件：** [tier1_to_tier2_bridge.py:947-964](../systems/memory/tier1_to_tier2_bridge.py#L947-L964)

质量检查失败时，turns 保持 `compressed_to_tier2 = 0`。下一个维护周期会重新选择同一批次并重试压缩（重新消耗 LLM 费用）。没有重试计数器、指数退避、最大重试上限或"毒丸"标记。

结合 `tier2_min_identifier_fidelity` 和 `tier2_min_polarity_consistency` 的默认值均为 `1.0`（"完美或拒绝"），任何 LLM 引入新标识符或极性翻转的批次将**永远重试并持续消耗 LLM 费用**。

**修复方向：** 添加 `compression_retry_count` 列到 turns 表；超过上限（如 3 次）后强制接受或跳过；对 `1.0` 阈值放宽默认值至 `0.8`。

---

### H3. `_temporal_fit_score` 窗口外点记忆得分高于窗口内跨段记忆

**文件：** [recall.py:1787-1831](../systems/memory/recall.py#L1787-L1831)

点候选（单时间戳）在查询窗口外使用 `1.0 - distance / (query_seconds * 4)` 衰减，而跨段候选使用 `overlap_seconds / query_seconds`。对于 30 天窗口：
- 窗口外 20 天的点记忆：`1.0 - 20/120 = 0.83`
- 覆盖窗口内 20/30 天的跨段记忆：`20/30 = 0.67`

**窗口外的不相关记忆排在窗口内的相关记忆之上。** 对于更窄的窗口（如 1 天），效应更加极端。

**修复方向：** 统一两种候选的评分尺度，或为点候选在窗口外时使用更陡的衰减曲线（如移除 4× 乘数）。

---

### H4. "星子"等词触发 Identity Query 误判，召回完全错误

**文件：** [recall.py:85-87](../systems/memory/recall.py#L85-L87)、[recall.py:691-697](../systems/memory/recall.py#L691-L697)

`_IDENTITY_TOPIC_MARKERS` 包含 `"星子"`、`"小星"`、`"锚点"`。任何包含这些词的查询会被判为 identity 查询，导致：
- 跳过所有 Tier1、profile、archive、实体图候选
- 搜索词从 topic 参数提取（丢弃实际查询文本）
- 概念词被硬编码为 `("身份", "星子", "小星", "voidcube", "锚点", "信任", "identity", "self")`
- 只返回 `identity_layer='founding'` 且 `pinned=1` 的记忆
- 使用固定排序公式（0.69 基础分，词法权重仅 0.15）

用户查询"帮我查看星子的配置"或"星子昨天做了什么"会被完全误判。

**修复方向：** 将 identity 检测从纯关键词匹配改为多条件判断（如：查询长度 ≤ 15 字 + 不含功能性动词 + 包含身份词），或要求更精确的匹配（如正则 `^你是谁$` 等完整问句）。

---

## 🟡 Medium

### M1. GOVERNOR 可通过 Promotion 侧信道探测无权访问的域

**文件：** [promotion.py:204-227](../systems/memory/promotion.py#L204-L227)

`source_memory_exists` 通过原始 SQL 直接查询源表（绕过 `authorize_read`）。GOVERNOR 在 domain.py 中只能读写 `evolution`，但属于 `_PROMOTION_MANAGERS`，可通过 promotion API 探测 `agent_interaction` 中是否存在特定 `source_memory_id`——构成元数据侧信道。

**修复方向：** 在 `source_memory_exists` 中增加与 `authorize_read` 一致的域权限检查。

---

### M2. `rebuild_entity_graph` 默认全局破坏性操作

**文件：** [entity_graph.py:185-186](../systems/memory/entity_graph.py#L185-L186)、[memory_service.py:940-941](../systems/memory/memory_service.py#L940-L941)

默认参数 `owner_id=GLOBAL_SCOPE_ID, workspace_id=GLOBAL_SCOPE_ID`。无参调用或不带 owner/workspace 的 HTTP 请求会 DELETE 所有用户的 entity 数据后重建。

**修复方向：** 将默认值改为 `None`，在函数内部要求显式提供 scope 或默认使用当前请求的 scope。

---

### M3. 身份体验记录以全局 scope 写入

**文件：** [identity_experience.py:170-171](../systems/memory/identity_experience.py#L170-L171)、[identity_experience.py:209-210](../systems/memory/identity_experience.py#L209-L210)、[identity_experience.py:334-335](../systems/memory/identity_experience.py#L334-L335)

身份体验记录硬编码写入 `owner_id="*", workspace_id="*"`。由于几乎所有查询都使用 `(owner_id = X OR owner_id = '*')` 模式，任何 scope 的查询都会看到所有用户的身份体验记录。在单用户环境中无实际影响，但设计上违反了 scope 隔离。

**修复方向：** 将身份体验记录的 scope 对齐到实际的 owner_id/workspace_id。

---

### M4. 生命周期升级不设置 `event_kind`，内容权重加分丢失

**文件：** [memory_service.py:553-568](../systems/memory/memory_service.py#L553-L568)

`_apply_compression_lifecycle` 的升级 INSERT 语句不包含 `event_kind` 列，新行该字段为 NULL。`compute_dynamic_weight` 对 NULL event_kind 不加 content_bonus（最高 +0.15）。而 Tier1→Tier2 bridge 会从子事件推导 event_kind（majority vote）。升级后的记忆丧失了内容重要性信号。

**修复方向：** 从被升级的源记忆继承 `event_kind`，或通过 LLM 重新分类。

---

### M5. `_LOCAL_SIMILARITY_BOOST = 3.0` 放大弱语义噪声

**文件：** [semantic_index.py:35](../systems/memory/semantic_index.py#L35)

本地 CharNgramEmbedder 原始余弦值范围约 0-0.33。放大 3.0 后，仅共享 2 个字符的弱匹配（raw cos 0.12 → boosted 0.36）刚好超过 recall.py 的 `min_similarity=0.35` 阈值。Recall 公式中 semantic 权重达 0.30-0.34，可能让表面共享子串的无关记忆混入候选。

**修复方向：** 降低 boost 到 2.0-2.5，或提高 `min_similarity` 阈值；或对低相似度区间使用非线性映射。

---

### M6. `_dynamic_weight` 存在两份独立实现

**文件：** [memory_service.py:312-343](../systems/memory/memory_service.py#L312-L343) (`compute_dynamic_weight`) vs [recall.py:1761-1784](../systems/memory/recall.py#L1761-L1784) (`_dynamic_weight`)

两者实现相同逻辑但分开维护。`memory_service.py` 版本额外支持 `hidden` 参数（返回 0.0）。未来修改 content_bonus 权重或 citation_bonus 公式需要两处同步。

**修复方向：** 将 `recall.py` 的 `_dynamic_weight` 改为从 `memory_service` 导入 `compute_dynamic_weight`，或提取到共享模块。

---

### M7. Feedback 评分调整永久生效，无时间衰减

**文件：** [recall.py:1738-1757](../systems/memory/recall.py#L1738-L1757)

feedback 的评分调整无时间衰减、无恢复机制。一次误点 `incorrect`（-0.50）或 2 次 `irrelevant`（共 -0.50）足以将任何记忆压到接近 0。负面反馈权重极不对称：1 次 `incorrect` (-0.50) 需要 7 次 `relevant` (+0.56) 才能对冲。

**修复方向：** 添加时间衰减（如 90 天半衰期），或按用户/会话去重 feedback，或降低负面权重。

---

### M8. 生命周期升级忽略 LLM 健康状态

**文件：** [memory_service.py:526-541](../systems/memory/memory_service.py#L526-L541)

`_build_compression_pipeline` 对 LLM down 有显式降级处理（切换到 heuristic），但 `_apply_compression_lifecycle` 无条件调用 `_llm_escalate_summary`。失败时静默回退到机械前缀（如 `[Arc] 原标题` + "自动升级，非LLM重摘要"）。

**修复方向：** 升级前检查 `self._llm_healthy`，LLM 不可用时跳过升级周期或直接使用机械降级。

---

### M9. `_purge_expired_memories` 跨所有 scope 无过滤

**文件：** [memory_service.py:782-787](../systems/memory/memory_service.py#L782-L787)

DELETE 语句无 `owner_id`/`workspace_id` 过滤。一个 scope 的清理周期可能删除其他 scope 的待清理条目。

**修复方向：** 添加 `owner_id`/`workspace_id` 过滤，或按 scope 逐个清理。

---

### M10. 五条维护规则独立事务，部分失败导致不一致

**文件：** [memory_service.py:1546-1575](../systems/memory/memory_service.py#L1546-L1575)

每条规则独立打开连接并提交。如果 `lifecycle_escalation`（规则 4）成功 supersede 50 条旧记忆但 `purge_expired`（规则 5）中途异常，系统处于旧条目已标记 superseded 但未清理的状态。

**修复方向：** 将 escalation 和 purge 合并到同一事务中，或在 purge 失败时在下个周期重试（当前行为如此但无明确保障）。

---

### M11. 压缩升级的 `created_at` 为 NULL 直至下个维护周期

**文件：** [memory_service.py:3539-3572](../systems/memory/memory_service.py#L3539-L3572)（remember）、[memory_service.py:553-568](../systems/memory/memory_service.py#L553-L568)（escalation）、[tier1_to_tier2_bridge.py:163-241](../systems/memory/tier1_to_tier2_bridge.py#L163-L241)（bridge）

三个写入路径都不在 INSERT 时设置 `created_at`。Bridge 和 escalation 有后续 backfill（`UPDATE SET created_at = compressed_at WHERE created_at IS NULL`），但 `remember` API 没有。手动记住的记忆在 backfill 前 `created_at = NULL`。双时态查询使用 `COALESCE(created_at, compressed_at)` 所以读取不受影响，但语义区分（事务时间 vs 更新时间）在此期间丢失。

**修复方向：** 在所有 INSERT 时显式设置 `created_at = now`。

---

## 🔵 Low

### L1. `source_memory_exists` 使用 GLOBAL_SCOPE_ID 但同模块其他查询不用

[promotion.py:215-223](../systems/memory/promotion.py#L215-L223) 使用 `(owner_id = X OR owner_id = '*')` 查找源 memory，但 `list_candidates`、`consent`、`revoke` 只用精确 scope。全局 scope 的 promotion 记录将可被创建但不可被管理。

### L2. `compressed_memories` 主键不含 scope

[database.py:305](../systems/memory/database.py#L305) — `memory_id TEXT PRIMARY KEY`，`INSERT OR REPLACE` 在跨 scope 碰撞时会覆盖。所有 memory_id 生成函数都编码了 scope 信息，但无 DB 级别保护。

### L3. Profile tombstone 迁移无事务包裹

[database.py:512-534](../systems/memory/database.py#L512-L534) — CREATE → INSERT SELECT → DROP → RENAME 模式，中途崩溃可能丢失数据。

### L4. COMPANION 域无出站提升路径

[promotion.py:90-94](../systems/memory/promotion.py#L90-L94) — `COMPANION → {}` 为空集。两条提升路径都汇聚到 COMPANION，但数据无法通过受治理机制流出。COMPANION 是设计上的数据终点。

### L5. EXECUTION 与 GOVERNOR 权限相同但 promotion 能力不同

两者在 domain.py 中权限完全一致（读/写 `evolution`），但只有 GOVERNOR 在 `_PROMOTION_MANAGERS` 中。

### L6. 三个 Promotion 模型默认 actor 不一致

- `MemoryPromotionCandidateCreate.memory_actor` → `GOVERNOR`
- `MemoryPromotionConsent.memory_actor` → `GOVERNOR`
- `MemoryPromotionRevoke.memory_actor` → `MEMORY_MAINTENANCE`

### L7. Background maintenance `batch_size=50` 硬编码

[maintenance.py:102](../systems/memory/maintenance.py#L102) — `run_tier2_bridge_cycle` 硬编码 `batch_size=50`，忽略 `Tier2CompressRequest.batch_size` 默认值 100。

### L8. `_turn_row_to_dict` 依赖 SELECT 显式列顺序

[memory_service.py:394-408](../systems/memory/memory_service.py#L394-L408) — 函数假设 SELECT 的列顺序，而非按列名取值。任一调用方改变 SELECT 列顺序就会导致字段错位。

### L9. `recent_conversation` 意图下 `_lexical_score` 返回常量 0.25

[recall.py:1451-1452](../systems/memory/recall.py#L1451-L1452) — 丢弃所有查询词，只按 recency 排序。最近对话中的噪音和信号无法区分。

### L10. Tier1 与 Tier2 的 lexical 权重不对称

[recall.py:864](../systems/memory/recall.py#L864) — Tier1 非语义路径 lexical 权重 0.76，Tier2 仅 0.62。原始对话容易压倒精心整理的结构化记忆。

### L11. `_LEVEL_MAX_AGE_DAYS` 和 `_LEVEL_WEIGHT` 无交叉验证

[memory_service.py:459-468](../systems/memory/memory_service.py#L459-L468) — 两个数据结构独立定义，无启动时校验。若一方被修改而另一方未同步，静默产生不一致行为。

### L12. 质量审计表缺少 `owner_id`/`workspace_id` 列

[tier1_to_tier2_bridge.py:741-776](../systems/memory/tier1_to_tier2_bridge.py#L741-L776) — `compression_quality_audit` 表有 `memory_domain` 但无 scope 列，审计记录无法按 owner/workspace 过滤。

### L13. FTS5 DELETE 触发器不带 scope 过滤

[lexical_index.py:64-66](../systems/memory/lexical_index.py#L64-L66) 等 — 所有 FTS DELETE 触发器只用 `memory_id = OLD.xxx_id` 匹配，不检查 scope。当前安全仅因主键全局唯一。

### L14. 生命周期升级 scope 仅从 `turns[0]` 推导

[tier1_to_tier2_bridge.py:813-818](../systems/memory/tier1_to_tier2_bridge.py#L813-L818) — 如果 bridge 以 `owner_id=None` 创建且批次包含混合 scope 的 turns（不太可能但代码路径存在），只有第一条 turn 的 scope 会被传播。

### L15. Identity 体验与 bridge 的 scope 来源独立推导

[tier1_to_tier2_bridge.py:721-727](../systems/memory/tier1_to_tier2_bridge.py#L721-L727) vs [tier1_to_tier2_bridge.py:813-818](../systems/memory/tier1_to_tier2_bridge.py#L813-L818) — 审计和提交从 `turns[0]` 独立推导 scope，存在漂移风险（当前一致因 WHERE 过滤）。

---

## 修复优先级建议

| 优先级 | 问题 | 理由 |
|--------|------|------|
| 🔴 P0 | C1 列索引错位 | 数据正确性 — 迁移DB返回错误数据 |
| 🔴 P0 | C2 FTS5 丢弃短 CJK | 召回质量 — 中文搜索基本失效 |
| 🟠 P1 | H4 Identity 误判 | 召回完全错误 — 常见中文名触发 |
| 🟠 P1 | H3 temporal_fit 错排 | 排序错误 — 窗口外记忆优于窗口内 |
| 🟠 P1 | H1 升级无质量门禁 | 数据质量 — LLM 幻觉静默持久化 |
| 🟠 P1 | H2 无限重试 | 成本/稳定性 — LLM 费用无限消耗 |
| 🟡 P2 | M5 语义噪声放大 | 召回精度 |
| 🟡 P2 | M4 event_kind 丢失 | 排序信号缺失 |
| 🟡 P2 | M7 feedback 无衰减 | 用户反馈机制失效 |
| 🟡 P2 | M9 跨 scope purge | 多用户隔离 |
| 🟡 P2 | M11 created_at NULL | 双时态语义 |
| 🔵 P3 | 其余 15 项 | 代码卫生/长期维护 |

---

## 审查方法说明

本报告通过以下方式生成：

1. **权限/Scope 代理** — 审查 `domain.py`、`scope.py`、`promotion.py` 的权限矩阵、scope 过滤一致性、GLOBAL_SCOPE_ID 使用模式
2. **数据流/压缩生命周期代理** — 审查 `tier1_to_tier2_bridge.py`、`maintenance.py`、`memory_service.py` 中 Tier1→Tier2 的数据传递、压缩质量门禁、生命周期升级逻辑
3. **召回排序代理** — 审查 `recall.py`、`semantic_index.py` 中五个候选源的评分公式、语义放大因子、去重逻辑、feedback 调整、时间感知排序
4. **Schema 一致性代理** — 审查 `database.py` 与各查询文件中 SQL 语句的列名/顺序一致性、触发器正确性、主键/唯一约束
5. **独立交叉分析** — 补充 FTS5 短词过滤、两处 `_dynamic_weight` 重复、identity 误判等未覆盖的交叉关注点
