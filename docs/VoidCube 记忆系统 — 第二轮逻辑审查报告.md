# VoidCube 记忆系统 — 第二轮逻辑审查报告

> 审查日期：2026-08-05
>
> 基于上一轮 32 个问题的修复验证 + 新引入问题审查

---

## 一、修复状态总览

| 状态 | 数量 | 说明 |
|------|------|------|
| ✅ 已修复 | 16 | 上一轮 Critical/High 大部分已修复 + 多个 Medium |
| ⚠️ 仍存在 | 8 | 5 个原始问题 + 3 个由本次修复新引入 |
| 🔴 新增 | 4 | 本次修复引入的新 Critical/High 问题 |

---

## 二、已修复确认 ✅

| # | 原始编号 | 问题 | 修复方式 |
|---|---------|------|---------|
| 1 | C1 | `_cmem_row_to_dict` 迁移DB列索引错位 | 改为按列名访问 + `_CMEM_COLUMNS` 显式列名常量，所有 `SELECT *` 替换为 `SELECT {_CMEM_COLUMNS}` |
| 2 | C2 | FTS5 丢弃 2-字符 CJK 词 | `len(term) < 3` → `< 2`，2-字符词使用 `LIKE '%term%'` 回退搜索 |
| 3 | H1 | 生命周期升级无质量门禁 | 新增 `_source_support` + `_identifiers` 双重检查（M11→已升级为 NM1/NM2） |
| 4 | H2 | 桥接质量门禁拒绝后无限重试 | `_MAX_QUALITY_RETRIES = 3` + 指数退避 (`2^N` 小时) + 永久标记 (`compressed_to_tier2 = -1`) |
| 5 | H4 | "星子" Identity Query 误判 | 从 `_IDENTITY_TOPIC_MARKERS` 移除，改为多条件判断（长度≤15 + 身份疑问词 + 无功能性动词） |
| 6 | M4 | 生命周期升级不设置 event_kind | 升级 INSERT 现在从源行继承 `event_kind` |
| 7 | M6 | `_dynamic_weight` 重复定义 | 提取到 `ranking_policy.py` 的 `compute_dynamic_weight`，recall 和 memory_service 均导入 |
| 8 | M7 | Feedback 无时间衰减 | `decay = math.exp(-age_days / 90.0)`，90 天半衰期 |
| 9 | M11 | created_at NULL | escalation 升级 INSERT 显式设置 `created_at = now` |
| 10 | — | entity_graph 默认 GLOBAL_SCOPE_ID | `rebuild_entity_graph` 默认参数改为 `None` |
| 11 | — | escalation 质量门禁统计 | `_apply_compression_lifecycle` 返回 `quality_rejected` 计数 |
| 12 | — | `_CMEM_COLUMNS` 与 `_CMEM_COLUMN_INDEX` 对齐 | 32 列顺序一致，按名称映射索引 |
| 13 | — | remember API created_at | INSERT 显式设置 `created_at = now` |
| 14 | — | recall.py 身份查询分类 | 新增功能性动词过滤（"配置"、"做了"、"查看" 等不触发 identity） |
| 15 | — | 桥接 retry 机制 | `compression_retry_count` + `compression_retry_after` 列 + `_record_quality_rejection` |
| 16 | — | escalation 升级 SELECT 增加 event_kind | `SELECT ... event_kind ...` 用于质量门禁和继承 |

---

## 三、仍存在的问题 ⚠️

### S1. `_temporal_fit_score` 窗口外点候选仍高于窗口内跨段候选

**文件：** [recall.py:1822-1838](../systems/memory/recall.py#L1822-L1838)

点候选在窗口外时 cap 在 `min(0.35, ...)`。对于 30 天窗口内仅有 1 天重叠的跨段候选，得分仅为 `1/30 ≈ 0.033`。**0.35 >> 0.033**，窗口内部分覆盖的跨段记忆仍被窗口外的无关点记忆超越。需降低上限或使其与 `query_seconds` 成比例。

### S2. `_LOCAL_SIMILARITY_BOOST = 3.0` 仍存在

**文件：** [semantic_index.py:35](../systems/memory/semantic_index.py#L35)

线性放大 3.0 仍将底层噪声（raw cos < 0.12）映射到刚好超过 0.35 阈值的值，使表面共享字符的无关记忆通过语义通道混入候选。

### S3. `source_memory_exists` 使用 GLOBAL_SCOPE_ID，同模块其他查询不用

**文件：** [promotion.py:218-231](../systems/memory/promotion.py#L218-L231) vs [promotion.py:348](../systems/memory/promotion.py#L348) 等处

`source_memory_exists` 仍使用 `(owner_id = X OR owner_id = '*')` 模式，但 `list_candidates`、`consent`、`revoke` 等只用精确 scope 匹配。不一致性未消除。

### S4. COMPANION 域仍无出站提升路径

**文件：** [promotion.py:97](../systems/memory/promotion.py#L97)

`MemoryDomain.COMPANION: frozenset()` 仍为空。COMPANION 在所有合法路径中为数据汇点。是否是设计意图待确认，但结构上仍无出站路径。

### S5. Profile tombstone 迁移仍无事务包裹

**文件：** [database.py:508-547](../systems/memory/database.py#L508-L547)

CREATE → INSERT SELECT → DROP → RENAME 序列仍无 SAVEPOINT 或显式事务边界。每个 DDL 在 SQLite 中自动提交，中途崩溃可使数据库处于不一致状态。

### S6. 桥接 INSERT 仍省略 `created_at`

**文件：** [tier1_to_tier2_bridge.py:164-242](../systems/memory/tier1_to_tier2_bridge.py#L164-L242)

四个桥接 INSERT（event/scene/arc/epoch）均未显式设置 `created_at`，完全依赖回退 UPDATE。与 escalation（显式设置）和 remember API（显式设置）不一致（原 L3→升级为 NH3）。

### S7. Escalation 质量门禁 0.35 阈值硬编码

**文件：** [memory_service.py:537](../systems/memory/memory_service.py#L537)

`_source_support < 0.35` 硬编码，不可配置。桥接通过 `tier2_min_source_support` 配置项暴露，但 escalation 无对应配置项（原 M6→确认仍存在）。

### S8. Escalation 拒绝后无限 LLM 重试无退避

**文件：** [memory_service.py:535-544](../systems/memory/memory_service.py#L535-L544)

质量门禁拒绝后直接 `continue`，不更新源记忆的 `compressed_at`，下个周期（每小时）重新选择、重新调用 LLM。无重试计数器或退避策略（新发现，详见 NC1）。

---

## 四、本次修复引入的新问题 🔴

### NC1 (Critical). Escalation 质量拒绝无重试上限 — 无限 LLM 重调用

**文件：** [memory_service.py:535-544](../systems/memory/memory_service.py#L535-L544)

```python
if _source_support(escalated_summary, source_text) < 0.35 or unsupported_identifiers:
    quality_rejected += 1
    continue  # ← 仅跳过，不更新 compressed_at
```

源记忆的 `compressed_at` 保持不变，`status` 保持 `'active'`。SELECT 查询要求 `status = 'active' AND compressed_at < cutoff`，因此同一个源行在下个周期（每 3600 秒）再次满足条件。LLM 调用在质量检查**之前**（第 525 行），所以每次重试都会消耗 LLM 费用。缺少桥接已实现的 `_MAX_QUALITY_RETRIES` + 指数退避等价机制。

**修复方向：** 为 escalation 路径添加 retry 计数列（或复用 `citation_count`），实现类似桥接的重试限制和退避逻辑。

---

### NC2 (Critical). `_source_support`/`_identifiers` 在 escalation 语义上不适用 — 将导致误拒绝

**文件：** [memory_service.py:535-537](../systems/memory/memory_service.py#L535-L537)

```python
source_text = f"{title} {summary}"  # 旧摘要（已压缩、抽象、短）
unsupported_identifiers = _identifiers(escalated_summary) - _identifiers(source_text)
if _source_support(escalated_summary, source_text) < 0.35 or unsupported_identifiers:
```

`_source_support` 和 `_identifiers` 是为桥接设计的——桥接中 `source_text` 是**原始对话 turns**（长、token 丰富、包含用户原话），检查 LLM 摘要是否忠实于原始对话是合理的。

但在 escalation 场景中：
- `source_text` = 旧 `title + summary`，已经是压缩过的、抽象的、短文本
- 新 escalation 的目的是**进一步提升抽象层次**（Event→Scene→Arc→Epoch）
- **Token 重叠率**：更高层次的抽象自然会使用不同的、更宏观的语言。50-token 旧摘要与 60-token 新摘要可能仅共享 15 token，得分 0.25，低于 0.35 阈值，即使完全忠实
- **标识符检查**：LLM 可能合法地引入在旧摘要中隐式存在但在高层次变得显式的实体名、日期或引用。`_identifiers` 是绝对门禁——任何新标识符 = 拒绝

这两个函数在 escalation 上下文中将导致**合法的升级被错误拒绝**，因为比较的是抽象 vs 抽象，而非桥接的摘要 vs 原始对话。

**修复方向：** 为 escalation 设计专用的质量检查，或大幅降低阈值（e.g., 0.15），或使用语义相似度（嵌入）而非 token 重叠来检查抽象一致性。

---

### NH3 (High). 桥接 INSERT 省略 `created_at` — 与其他路径不一致

**文件：** [tier1_to_tier2_bridge.py:164-242](../systems/memory/tier1_to_tier2_bridge.py#L164-L242)

四类 INSERT（event/scene/arc/epoch）均省略 `created_at`。代码依赖回退 UPDATE（第 255-258 行）：
```sql
UPDATE compressed_memories SET created_at = compressed_at WHERE created_at IS NULL
```

Escalation 和 remember API 已在 INSERT 中显式设置 `created_at`。桥接的间接方式脆弱：回退 UPDATE 若被移除、条件化或遗漏，桥接插入的行将产生 NULL `created_at`。

**修复方向：** 在桥接的四类 INSERT 中显式添加 `created_at` 列和值。

---

### NH4 (High). `event_kind` 在 escalation 和 bridge 之间不一致 — arc/epoch 级别

**文件：** [memory_service.py:570](../systems/memory/memory_service.py#L570) vs [tier1_to_tier2_bridge.py:222](../systems/memory/tier1_to_tier2_bridge.py#L222)、[tier1_to_tier2_bridge.py:240](../systems/memory/tier1_to_tier2_bridge.py#L240)

- **桥接 arc**：显式设为 `None`
- **桥接 epoch**：显式设为 `None`
- **Escalation**：从源行继承 `event_kind`，arc 和 epoch 均继承源值

同一底层数据通过不同生成路径产生不同 `event_kind`。下游消费者（排序、筛选、UI）将因生成路径不同而看到不一致的数据。如果 arcs/epochs 不应携带 `event_kind`（如 bridge 所示），escalation 路径应在 level ≥ 2 时设为 `None`。

**修复方向：** 统一行为——两端均继承、或两端均设为 None。若选择继承（更合理），同步修改 bridge。

---

## 五、完整问题清单

| # | 严重度 | 文件 | 问题 | 状态 |
|---|--------|------|------|------|
| 1 | 🔴 Critical | memory_service.py:535-544 | Escalation 质量拒绝无限 LLM 重试 | 🆕 新增 |
| 2 | 🔴 Critical | memory_service.py:535-537 | Escalation 质量函数语义不匹配（抽象→抽象） | 🆕 新增 |
| 3 | 🟠 High | tier1_to_tier2_bridge.py:164-242 | 桥接 INSERT 省略 `created_at` | 🆕 新增 |
| 4 | 🟠 High | escalation vs bridge | `event_kind` 不一致（继承 vs None） | 🆕 新增 |
| 5 | 🟠 High | recall.py:1822-1838 | `_temporal_fit_score` 窗口外点候选超出窗口内跨段候选 | ⚠️ 仍存在 |
| 6 | 🟡 Medium | semantic_index.py:35 | `_LOCAL_SIMILARITY_BOOST = 3.0` 放大噪声 | ⚠️ 仍存在 |
| 7 | 🟡 Medium | promotion.py:218-231 vs 348 | `source_memory_exists` GLOBAL_SCOPE_ID 不一致 | ⚠️ 仍存在 |
| 8 | 🟡 Medium | memory_service.py:537 | Escalation 质量阈值 0.35 硬编码 | ⚠️ 仍存在 |
| 9 | 🔵 Low | promotion.py:97 | COMPANION 无出站提升路径 | ⚠️ 仍存在 |
| 10 | 🔵 Low | database.py:508-547 | Tombstone 迁移无事务包裹 | ⚠️ 仍存在 |
| 11 | 🔵 Low | tier1_to_tier2_bridge.py:164-242 | 桥接 created_at 脆弱回退 | ⚠️ 仍存在 |
| 12 | 🔵 Low | escalation vs bridge | `unsupported_identifiers` 绝对门禁 vs 桥接比率 | ⚠️ 仍存在 |

---

## 六、修复优先级

| 优先级 | 问题 | 理由 |
|--------|------|------|
| 🔴 P0 | NC1 + NC2 escalation 质量门禁 | 无限 LLM 费用 + 误拒绝合法升级 |
| 🟠 P1 | NH3/NH4 created_at/event_kind 不一致 | 数据完整性 + 下游一致性 |
| 🟠 P1 | S1 temporal_fit_score | 排序正确性 |
| 🟡 P2 | S2 _LOCAL_SIMILARITY_BOOST | 召回精度 |
| 🟡 P2 | S3 source_memory_exists scope | 多用户隔离 |
| 🟡 P2 | S6/S7 escalation 硬编码阈值 | 可运维性 |
| 🔵 P3 | S4/S5 COMPANION + tombstone | 长期架构卫生 |

---

## 七、与上一轮对比

| 指标 | 上一轮 | 本轮 |
|------|--------|------|
| 总问题 | 32 | 12 |
| Critical | 2 | 2（新） |
| High | 4 | 2（新）+ 1（留存） |
| Medium | 11 | 5（留存） |
| Low | 15 | 4（留存） |
| 已修复 | — | 16 个 |

**净效果：** 本轮质量门禁修复解决了上轮最多的 Critical/High 问题，但同时引入 2 个新的 Critical（无限 LLM 重试 + 语义不匹配的质量函数），这些需要在下一轮修复中优先处理。
