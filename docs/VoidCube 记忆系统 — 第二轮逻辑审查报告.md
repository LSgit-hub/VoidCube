# VoidCube 记忆系统 — 第二轮逻辑审查最终复核

> 原始审查日期：2026-08-05  
> 最终复核日期：2026-08-05  
> 本文只描述当前实现；旧的“仍存在”和“新增问题”快照已移除，避免后续会话误用。

---

## 一、最终结论

原报告列出的 12 条当前问题中：

| 分类 | 数量 | 结论 |
|------|------|------|
| 真实缺陷 | 8 | 已全部修复并有定向测试 |
| 重复问题 | 2 | 分别与 `created_at` 和生命周期质量门禁重复 |
| 设计边界 | 2 | `GLOBAL_SCOPE_ID` 可见性与 COMPANION 单向推广均为安全策略，不应修改 |

没有证据支持“当前 Mem LLM 被策略拦截是唯一根因”。历史日志中策略拦截、密钥不可用和远端鉴权失败曾分别发生，但当前运行配置必须通过 `/llm/health` 和结构化解析状态判断，不能互相推导。

---

## 二、12 条问题逐项复核

| # | 原编号 | 原主张 | 真实性 | 最终处理 |
|---|--------|--------|--------|----------|
| 1 | NC1/S8 | 生命周期质量拒绝会无限调用 LLM | 属实 | 增加 `lifecycle_retry_count/retry_after/last_error`，最多 3 次并指数退避 |
| 2 | NC2 | 桥接质量门禁不适合抽象到抽象升级 | 属实 | 新增独立 `lifecycle_policy.py`，使用可配置的 0.15 source support 与 0.8 identifier fidelity |
| 3 | NH3/S6 | Bridge INSERT 未显式写 `created_at` | 属实 | event/scene/arc/epoch 四条写入路径均显式设置 |
| 4 | NH4 | Bridge 与 lifecycle 的 `event_kind` 不一致 | 属实 | 高层记忆统一继承子层多数 `event_kind` |
| 5 | S1 | 时间窗口外点候选仍获得 temporal boost | 属实 | 窗口外明确返回 `0.0` |
| 6 | S2 | 本地 embedding 线性乘 3 放大噪声 | 属实 | 改为非线性 `20 * raw^2` 并增加阈值边界测试 |
| 7 | S3 | `source_memory_exists` 接受全局 scope 是隔离漏洞 | 不属实 | 全局源允许被本地 scope 引用，但 candidate/ref 始终写入请求者私有 scope；新增契约测试 |
| 8 | S7 | 生命周期 0.35 阈值硬编码 | 属实 | 生命周期阈值已独立配置化，不再复用 bridge 的 0.35 |
| 9 | S4 | COMPANION 没有出站推广路径 | 不属实 | 这是隐私边界：agent/evolution 可经用户同意投影到 companion，companion 私密记忆不得外流 |
| 10 | S5 | Tombstone 迁移无事务边界 | 属实 | 使用 SQLite `SAVEPOINT` 包裹完整迁移并支持回滚 |
| 11 | — | Bridge `created_at` 回退脆弱 | 重复 | 与 #3 相同，已修复 |
| 12 | — | 生命周期标识符绝对门禁误拒绝 | 重复 | 与 #2 相同，已改为比例式 fidelity |

---

## 三、上一轮修复保留情况

以下原有修复在当前代码中仍然有效：

| 项目 | 当前证据 |
|------|----------|
| `_cmem_row_to_dict` 列错位 | `_CMEM_COLUMNS` 显式列清单与按名称索引转换 |
| 两字 CJK FTS 召回 | 两字词使用 `LIKE` fallback，短于两字才丢弃 |
| Bridge 质量拒绝无限重试 | 最多 3 次、指数退避、终止状态 `compressed_to_tier2=-1` |
| 身份查询误判 | 功能性动词过滤覆盖“查看星子的配置”“星子昨天做了什么” |
| 动态权重重复实现 | 统一由 `ranking_policy.compute_dynamic_weight` 提供 |
| Feedback 永久影响 | 使用 90 天指数衰减 |
| Entity graph 全局重建误用 | 默认 scope 为 `None`，只有显式请求才全局重建 |
| Scope 与 domain 隔离 | owner/workspace/domain 均参与查询和写入条件 |

---

## 四、补充诊断四项复核

### 1. “Mem LLM 当前被项目策略拦截”

当前不成立。解析器现区分：

- `policy_blocked`
- `api_key_unavailable`
- `client_initialization_failed`
- `ready`

项目策略仍必须拒绝仓库明确退役的集成，不应为修复 Memory 而绕过。当前 `deepseek-v4-flash` 的真实健康状态以 `/llm/health` 为准。

### 2. “`memories=0` 证明 Tier 2 无数据”

不成立。Canonical 长期记忆表是：

- `compressed_memories`
- `profile_memories`

旧 `memories` 表不参与写入或召回。新增迁移会在备份后删除空的旧表；如果旧表含数据则保留并告警，绝不自动丢弃。

### 3. “409 一定由 workspace 默认值漂移造成”

证据不足。当前 scope 定义明确区分：

- 服务默认 workspace：`default`
- 交互 CLI workspace：`VoidCube`

复用同一 `session_id` 但提交不同 owner/workspace/domain 时，409 是隔离保护。它不能仅凭状态码被判定为 daemon 加载了旧常量。

### 4. “全部为 event 说明生命周期瘫痪”

不成立。原数据最旧记录未达到旧的 30 天 Event→Scene 阈值，因此没有升级候选。启发式管道离线样例也能产生 event/scene/arc/epoch，不能把 LLM 不可用等同于 0 事件。

---

## 五、时序策略改进

原 30/180/365/730 天策略对当前项目节奏过于保守，已改为可配置策略：

| 阶段 | 默认阈值 |
|------|----------|
| Tier 1 → Event | 7 天 |
| Event → Scene | 14 天 |
| Scene → Arc | 60 天 |
| Arc → Epoch | 180 天 |
| Epoch → Final | 365 天 |
| Final 清理复核 | 90 天 |

维护循环仍每小时做轻量检查；昂贵的 lifecycle 聚合使用持久化的 7 天 cadence：

- 首次运行或错过周期后立即补偿；
- 成功后 7 天内跳过；
- 失败不进入周级冷却，下一个小时可重试；
- 数据库 lease 防止并发执行重复调用 LLM；
- 手动 `run-all-rules` 保留强制执行语义。

所有时序均可通过 `MemoryServiceConfig` 或对应 `MEMORY_*` 环境变量覆盖。

---

## 六、最终验收要求

完成状态必须同时满足：

1. Memory/Mem 定向测试通过；
2. 架构、Gateway、退役集成和打包契约通过；
3. 真实数据库在线备份且 `integrity_check=ok`；
4. 服务重启后 Gateway registration 和 Mem LLM health 正常；
5. 空旧 `memories` 表已安全清理；
6. 实际规则运行能看到 7 天 Tier 1 候选和持久化 lifecycle cadence 状态。

最终运行结果应记录在本节之后，不得用旧测试结果替代本次修改后的验证。
