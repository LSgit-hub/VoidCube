# VoidCube 服务化系统架构基线

## 1. 文档定位

本文档是 VoidCube 当前架构的最高优先级基线，用于约束后续实现、重构和文档整理。

当其他文档与本文冲突时，以本文为准；其他文档只能对本文定义的核心架构做组件级展开、运行说明、阶段路线或历史论证，不能重新定义 VoidCube 的主架构。

本文只回答 5 个问题：

- 哪些核心组件存在
- 每个组件负责什么
- 组件之间如何协作
- 整个架构到底在服务谁、升级谁
- 哪些职责必须收口，哪些能力暂时不做

详细协议、操作步骤和阶段计划已移至代码内文档和架构基线。本文件为单一真实源（single source of truth）。

参考：

- Mem 子系统文档：`Mem/docs/`
- 技能文档：`skills/`
- 系统代码即文档：`systems/`、`agent/`、`tools/`

## 2. 核心结论

VoidCube 不是“CLI 本地模式 + 服务模式”二选一项目，也不是一个普通 CLI Agent 外挂几项后台能力。

VoidCube 的目标形态是：

**一个以 CLI 为用户入口、以内部网关为神经中枢、以 Mem 为长期记忆与治理灵魂、以监督者为内生认知核心与治理输出中枢及任务列表管理者、以 Agent 为学习任务与身体升级执行体、以执行器为身体切换机械执行面、以可替换子 Agent 为主要升级对象的单机多进程母体系统。**

从用户视角看：

- 用户只直接感知 CLI
- 用户真正使用的是当前活跃 Agent

从系统视角看：

- VoidCube 是母体
- 双身体槽位中的两个 Agent 是母体持续培养、验证、切换的两个子 Agent
- 母体不会把自己整体暴露给用户
- 母体只会把通过治理验证的当前活跃子 Agent 交给用户使用

因此，这套架构共同服务的核心对象是：

**Agent 的持续改进、验证、替换与回滚。**

当前阶段主要需要持续升级的对象是子Agent中的替身，而不是优先要求 CLI、网关、Mem、监督者或执行器先自我升级。后者是支撑系统，目标是让 Agent 能稳定变好。

## 3. 核心组件

### 3.1 CLI

CLI 是 VoidCube 面向用户的唯一标准入口。

CLI 负责：

- 接收用户输入
- 展示 Agent 输出
- 提供手动配置与管理入口
- 触发技能、工具、服务管理等用户可见操作

CLI 不负责：

- 保存长期身份真相
- 判断身体切换是否放行
- 承担内部网关职责
- 直接成为身体治理执行器

CLI 可以启动、管理或观察内部服务，但这不意味着存在另一套“本地模式”。CLI 是用户门面，不是内部神经中枢。

涉及身体切换、升级、回滚的 CLI 命令只属于测试、验收、排障或应急恢复入口。它们不能成为正式自进化路径的主触发者。正式路径必须由 Mem / 监督者基于长期记忆、学习证据、协议状态、对用户使用状态的软感知和风险约束作出裁决，再由执行器消费裁决执行。

CLI 运维入口应直接依赖 gateway / executor 标准面；当某条旧 fallback 不再是必需能力时，应优先删除，而不是继续保留“更安全的 fallback”。如果某个残留兼容面被新实现替代，同一轮修改应尽量同时删除旧路径、旧参数、旧提示和旧测试，避免双路径继续生长。

### 3.2 内部网关

内部网关是 VoidCube 的内部神经中枢。

网关负责：

- 服务注册与发现
- 内部消息路由
- 活跃 Agent 选择
- 用户任务与自提升任务分类
- 活动事实记录
- 统一鉴权与追踪
- 身体切换相关内部入口统一暴露

网关不是用户人机入口；用户入口仍然是 CLI。

网关应成为系统活动事实源，至少维护：

- `last_user_request_at`
- `last_agent_work_at`
- `last_memory_task_at`
- `last_self_learning_activity_at`
- `last_self_evolution_plan_at`
- `last_self_evolution_execute_at`
- `last_self_evolution_activity_at`

后续所有“是否空闲”“是否允许自提升任务执行”的判断，应优先依据网关活动事实，而不是由各进程各自推断。

### 3.3 Agent 实例

Agent 是 VoidCube 中唯一的 API-A 执行体，负责用户任务、学习任务和身体升级（代码编辑）。Agent 是当前阶段的主要升级对象。

Agent 负责：

- 执行用户任务（常规模式）
- 调用工具、使用躯体/学习模型推理
- 通过网关访问长期记忆（Mem）
- 维护临时会话态、上下文压缩和短期工作记忆
- **在 AUTO / Governor Mode 下**：通过网关主动遍历监督者的任务列表，拉取并执行监督者已放行的自主任务，包括探索式学习任务（`self_learning`）以及学习后触发的替身改进任务（`body_improvement`）
- **身体升级（Git 替身基础上改进代码）**：根据学习成果，在 Git worktree 中 shell 槽位替身 Agent 的现有代码基础上进行改进——Agent 通过 Git 了解替身代码结构和自身短板，结合学习到的新知识编辑替身代码。升级完成后将 diff、commit 和进展描述提交到 Mem 长期记忆

Agent 不负责：

- 保存长期身份真相（属于 Mem）
- 独占长期规划状态
- 未经监督者裁决自行触发身体切换
- 自行裁决学习任务是否执行（任务由监督者内生驱动产出并放入任务列表）
- 执行身体切换的机械流程（由执行器执行）

Agent 可以拥有临时记忆，但这不等于 Mem 长期记忆。所有需要跨会话延续、需要成为身份真相、治理依据或演化历史的内容——包括学习成果、身体升级进展——都必须进入 Mem，由 Mem 的记忆管理者模型完成长期压缩、整理、归档与解释。

**关于子代理（sub-agent）**：子代理是 Agent 执行任务时的一项自主能力。Agent 在执行复杂任务（无论学习任务还是用户任务）时，可以自主决定是否使用子代理来辅助完成。子代理不是学习任务的强制模式，也不是被禁止的能力——它是 Agent 工具链的一部分，由 Agent 根据任务复杂度自行判断使用与否。

从母体-子体角度看：

- 当前 `active` Agent 是正在服务用户的子 Agent，也是执行学习任务和身体升级的执行体
- 另一槽位中的 `shell` Agent 是正在被培养的替身——Agent 在 Git worktree 中基于替身现有代码进行改进
- `candidate/probe` 是升级完成后等待验证的替身状态
- 候选子 Agent 只有通过 probe 和监督者裁决，才由执行器切换为新的 active Agent

每个子 Agent 应尽量拥有独立的：

- `worktree`
- `runtime`
- `logs`
- `meta`

这不是目录洁癖，而是母体能够培养、验证、切换、回滚子 Agent 的前提。

### 3.4 Mem 长期记忆系统

Mem 是 VoidCube 的长期记忆与治理灵魂，使用 API-B。

**LLM-First 原则**：Mem 的核心价值在于智能——充分利用已配置的记忆模型（API-B）来理解内容。LLM 参与和不参与有明确边界：

**LLM 参与（需要内容理解）**：

- 对话→事件提取（`LLMEventExtractionBackend` 语义理解 vs 关键词正则）
- 场景/弧线/纪元摘要（`LLMScholarBackend` 自然语言生成 vs 模板填充）
- 升级重摘要（`_llm_escalate_summary()` 逐级提升抽象层次）
- 清退终审（`_llm_purge_review()` 判断历史价值防误删）
- 内生驱动认知判断与治理建议（LM 参与证据解释、学习方向形成、治理模糊裁决与受限提案生成）
- 治理模糊裁决（`LLMGovernorReasoner` 评估证据、发现盲点）
- 语义搜索（LLM Embedding 余弦相似度）

**程序执行（不需要智能）**：

- Tier 1 存取（SQLite CRUD）、衰减公式（`score *= 0.99`）、权重计算（`compute_dynamic_weight()` 数学公式）
- Pin/Hide（布尔标记）、访问/引用计数（SQL UPDATE）、升级触发（年龄比较）
- 最终 DELETE（`WHERE status='purged' AND age>90d` 纯 SQL）
- 防自撞并发护栏判断（时间戳减法）、任务审批（状态机）、身体切换（机械流程）

**当前实现校正**：

- 当前代码里，监督者的智能成分主要集中在 `_llm_generate_learning_topics()`、`_llm_generate_improvement_direction()` 和 `_llm_review_diff()` 这类“主题生成 / 改进方向生成 / diff 质量审查”点上
- 任务列表真正的生命周期治理（是否放行、延后、取消、超时失败、状态流转）目前仍以确定性规则和状态机为主
- 因此，当前实现可以称为“LLM 辅助的监督者”，还不能准确称为“LM 已全面接管任务列表治理”

这条校正很重要：**目标架构可以是 LM 驱动监督者，但基线必须区分“当前已实现”与“目标要演进到的形态”。**

LLM Provider 启动时验证一次连通性（`GET /llm/health`），状态随 `GET /compressed/rules-status` 暴露。无 LLM 时降级路径明确记录告警日志。

Mem 负责：

- 长期记忆写入与检索
- **智能记忆压缩**（LLM 语义理解，非关键词正则）
- 记忆衰减
- **智能记忆总结**（LLM 逐级重摘要，非模板填充）
- 关联发现
- 身份连续性维护
- 演化历史与治理记录保存
- **LLM 健康监控**（降级状态可观测、可告警）

Mem 不替代 Agent 的临时上下文管理；Agent 临时记忆服务短期工作，Mem 承担长期真相。

Mem 的记忆架构采用**短长期双层设计**，从根本上解决"压缩即遗忘"的问题：

### 3.4.1 双层记忆架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Mem 双层记忆架构                               │
│                                                                 │
│   Tier 1：短期会话存储（SQLite）                                   │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ · 最近 30 天内所有会话内容完整保存，不做任何压缩            │   │
│   │ · 按 session → turn 树形目录组织，带完整时间轴索引         │   │
│   │ · 支持按时间点/时间段/会话/主题 精确检索原始对话            │   │
│   │ · relevance_score 指数衰减，但原始内容永久可读             │   │
│   │ · 每个 turn 有唯一 turn_id，作为上层引用的锚点             │   │
│   └──────────────────────────┬──────────────────────────────┘   │
│                              │                                   │
│   Tier 2：长期编年史记忆（Mem Pipeline）                          │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ · 超过 30 天的会话通过 ChroniclePipeline 压缩为结构化记忆 │   │
│   │ · Event → Scene → Arc → Epoch 四级金字塔                  │   │
│   │ · 每个 Event 通过 source_turns 反向引用 Tier 1 原始 turn  │   │
│   │ · ProfileMemory 从原始对话中提取偏好/约束/定义/事实       │   │
│   │ · 压缩后的原始 turn 可从 Tier 1 删除，但保留摘要可追溯    │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   关键原则：                                                      │
│   - 30 天内：保留完整，不做压缩（"先记住，再总结"）              │
│   - 30 天后：压缩入 Mem Pipeline，但保留 Tier 1 的 turn_id 引用  │
│   - 衰减而非删除：不重要内容先降 relevance，再考虑压缩           │
│   - 可追溯：从 Arc/Scene/Event 可沿 source_turns 回到原始对话   │
└─────────────────────────────────────────────────────────────────┘
```

**Tier 1 — 短期会话存储（SQLite）**：

- **数据模型**：`sessions` 表 + `turns` 表 + `turns_archive` 表。session 为根节点，turns 为叶子节点，形成会话树形目录
- **保留窗口**：默认 30 天。窗口内的所有会话内容完整保存，不做摘要、不合并、不删除
- **时间轴索引**：`idx_turns_timestamp` + `idx_turns_session`，支持按时间点/时间段/会话快速检索原始对话
- **衰减机制**：relevance\_score 按指数衰减（默认每天 ×0.99），低于阈值（默认 0.1）的 turn 标记为可压缩候选，但内容不自动删除
- **与现有 memory\_service 的关系**：直接扩展 `systems/memory/memory_service.py` 的 SQLite schema，增加 sessions/turns/turns\_archive 三张表，复用现有的 FastAPI 路由、decay loop、gateway 注册等基础设施

**Tier 2 — 长期编年史记忆（Mem Pipeline）**：

- **触发时机**：turn 超过 30 天保留窗口 OR Tier 1 turns 表行数超过阈值（默认 10000 条）
- **压缩流程**：选中的 turns → TranscriptTurn 序列 → ChroniclePipeline.ingest() → Event/Scene/Arc/Epoch + ProfileMemory
- **反向引用**：每个 Event 的 `source_turns` 字段记录原始 turn\_id 列表，Scene/Arc 通过 `evidence_refs` 可逐层追溯到原始对话
- **压缩后处理**：压缩完成的 turns 从 `turns` 表移至 `turns_archive` 表（保留 turn\_id + 时间锚点 + 摘要，原始内容可选删除）
- **与现有 Mem Pipeline 完全兼容**：Tier 2 就是现有的 ChroniclePipeline + MemoryMaintenanceEngine，不需要修改任何 Mem 内部逻辑

**双层协作流程**：

```text
用户对话 → Gateway 记录 → Tier 1 SQLite (sessions + turns, 完整保留)

每天定时检查 (memory_service decay loop):
  ├── turns 超过 30 天 → 标记为压缩候选
  ├── 批量取出候选 turns → 转换为 TranscriptTurn 序列
  ├── ChroniclePipeline.ingest(turns) → 产出 Event/Scene/Arc/Epoch
  ├── 新产出的结构化记忆存入 mem_state.json（或现有持久化路径）
  ├── 原始 turns 移至 turns_archive（保留 turn_id + 时间锚点 + 摘要）
  └── 更新 Tier 2 ↔ Tier 1 的 source_turns 反向引用

查询时:
  ├── 30 天内 → 直接查 Tier 1 SQLite，返回原始对话全文
  ├── 30 天外 → 先查 Tier 2 (Arc/Scene/Event 摘要)，再沿 source_turns 回 Tier 1 archive 查原文摘要
  ├── 主题演化 → Tier 2 MemoryQueryEngine.theme_evolution()
  └── 证据追溯 → MemoryQueryEngine.evidence_trace(target_id) 沿 child_ids 递归展开
```

**与现有系统的复用关系**（避免重复造轮子）：

| 现有组件                                                       | 复用方式                                                  |
| ---------------------------------------------------------- | ----------------------------------------------------- |
| `systems/memory/memory_service.py`                         | 扩展 SQLite schema，增加 sessions/turns/turns\_archive 三张表 |
| `Mem/src/memai/pipeline.py` (ChroniclePipeline)            | 直接作为 Tier 2 压缩引擎，无需修改                                 |
| `Mem/src/memai/schema.py` (TranscriptTurn)                 | 作为 Tier 1 → Tier 2 的数据转换格式                            |
| `Mem/src/memai/query.py` (MemoryQueryEngine)               | 扩展 source\_turns 回查 Tier 1 的能力                        |
| `Mem/src/memai/repository.py` (MemoryStateRepository)      | 增量更新机制不变                                              |
| `Mem/src/memai/maintenance.py` (MemoryMaintenanceEngine)   | 四级结构化压缩不变                                             |
| `Mem/src/memai/governance.py` + `governance_repository.py` | 压缩事件记录为治理审计日志                                         |
| `plugins/memory/mem/governor_bridge.py`                    | 治理桥接不变                                                |

### 3.4.2 记忆压缩双层体系（更新）

在双层架构下，Mem 的压缩分两个阶段运作：

- **Tier 1 衰减管理**（memory\_service）：turns 在 30 天保留窗口内完整保留。超过 30 天后，先降 relevance\_score（指数衰减），再标记为压缩候选。**仅当 Tier 2 已生成对应的结构化记忆（Event/Scene）后，才将原始 turn 移至 archive 表。不压缩、不合并原始对话文本——只做时间窗口管理和衰减标记。**
- **Tier 2 编年史压缩**（ChroniclePipeline）：将超过保留窗口的 turns 批量送入 ChroniclePipeline，**LLM 优先 + 启发式降级**。当 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 环境变量存在时，使用 `LLMEventExtractionBackend`（LLM 理解语义提取事件）和 `LLMScholarBackend`（LLM 生成场景/弧线/纪元摘要）。无 API 凭据时自动降级为 `HeuristicEventExtractionBackend`（关键词正则匹配）和 `HeuristicScholarBackend`（模板填充）。**LLM 压缩才能真正理解内容语义——区分"决定重构架构"和"嗯好的"，而不是仅靠关键词匹配。** 压缩不可逆，但通过 source\_turns 保留反向引用链路。
- **Tier 2 结构化四级压缩**（MemoryMaintenanceEngine）：对 Event→Scene→Arc→Epoch 四层对象做分层压缩与替代（supersede），超期 Scene（>30天）压缩入父 Arc，超期 Arc（>180天）压缩入父 Epoch，超期 Epoch（>365天）进一步压缩。默认使用 LLMScholarBackend（API-B）生成自然压缩摘要，无 API 凭据时自动降级到 HeuristicScholarBackend。**已接入运行时**：Governor Mode 下通过内生驱动→任务队列触发，Memory Mode 下每 3600s 自动执行。
- **压缩结果写回 SQLite**（`compressed_memories` 表）：Tier 2 压缩产出的 Event/Scene/Arc/Epoch **不仅存在于 Mem Pipeline 内存和 mem\_state.json 中，同时写回 SQLite 的** **`compressed_memories`** **表**。每条记录带 `source_turns`（反向引用原始 turn\_id）、`parent_id`（层级归属）、`compression_level`（压缩等级）、`status`（active/superseded/purged）、`weight`（查询权重）。这使得 SQLite 成为 Tier 1（原始会话）+ Tier 2（压缩记忆）的统一查询入口，不再需要分别访问两个存储系统。
- **压缩等级递进与最终清退**（生命周期管理，LLM 全程参与）：`compressed_memories` 中的条目按时间自动逐级升档，**每级升级由 LLM 生成更高抽象层次的摘要**（非机械贴标签），最终清退前由 **LLM 终审**防误删：

```text
Level 0: Event   (<30天)   weight=1.00  ← 刚从 turns 压缩来
         │ 30天后自动升级 → 旧 Event 标记 superseded
Level 1: Scene   (<180天)  weight=0.70  ← 覆盖替换 Level 0
         │ 180天后自动升级 → 旧 Scene 标记 superseded
Level 2: Arc     (<365天)  weight=0.40  ← 覆盖替换 Level 1
         │ 365天后自动升级 → 旧 Arc 标记 superseded
Level 3: Epoch   (<730天)  weight=0.20  ← 覆盖替换 Level 2
         │ 730天后自动升级 → 旧 Epoch 标记 superseded
Level 4: Final   (>730天)  weight=0.05  ← 查询权重极低
         │ 90天后 → 程序直接 DELETE（纯年龄判断，不需要 LLM）
         ▼
      彻底清退
```

每级升级时，旧条目不删除而是标记 `status='superseded'` 并通过 `superseded_by` 指向替代者，保留完整审计链。

**规则执行机制（双路径冗余）**：五条压缩规则通过两条独立路径执行，确保 Memory Mode 和 Governor Mode 下规则始终生效：

```text
路径 1: memory_service 自主循环 (_compression_loop, 每 3600s)
  └── _run_all_rules_internal()
        ├── 1. tier1_decay          (turns relevance 指数衰减)
        ├── 2. tier2_bridge         (过期 turns → ChroniclePipeline → compressed_memories)
        ├── 3. lifecycle_escalation  (Event→Scene→Arc→Epoch→Final 逐级升档)
        └── 4. purge_expired        (purged 条目审计期满后 DELETE)

路径 2: Supervisor 触发 (Memory Mode structured_maintenance_loop, 每 3600s)
  └── facade.trigger_memory_compression()
        └── POST /compressed/run-all-rules  (调用 memory_service 同一套规则)
              └── 同上四步，幂等执行
```

双路径保证：memory\_service 进程启动后自主执行（不依赖 Supervisor 是否启动），Supervisor 触发作为冗余确保规则一定被调用。两次执行完全幂等——`_tier2_bridge_cycle` 只处理 `compressed_to_tier2=0` 的 turns，`_apply_compression_lifecycle` 只处理超过年龄阈值的条目。

**可观测性**：`GET /compressed/rules-status` 返回每条规则的最后执行时间和累计执行次数。Supervisor UI 的 `tier1_stats` 面板包含 Tier 1 + Tier 2 的统计摘要。

### 3.4.3 五维内容感知权重模型

v1 的结构主义权重（W = f(类型, 年龄)）无法区分"我决定重构架构"和"嗯好的"。为此引入五维内容感知权重，将查询排序从**纯结构驱动**升级为**结构+内容+行为**融合驱动。

**权重公式**：

```text
W_dynamic = clamp(W_base(level) + content_bonus + access_bonus + citation_bonus, 0.0, 1.0)
W_final   = 1.0  if pinned
W_final   = 0.0  if hidden
W_final   = W_dynamic  otherwise
```

**五个维度**：

| 维度                       | 信号源                         | 计算公式                                                                                                                                  | 最大加成    | 更新时机                                     |
| ------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------- | ---------------------------------------- |
| **W\_base** 结构基础         | `compression_level` (0-4)   | 硬编码：Event=1.0, Scene=0.7, Arc=0.4, Epoch=0.2, Final=0.05                                                                              | —       | 创建时 + 升级时                                |
| **content\_bonus** 内容重要性 | `event_kind` (Mem 提取)       | `_CONTENT_IMPORTANCE_BONUS`: decision=+0.15, correction/shift=+0.12, completion/conflict=+0.08, blocker=+0.06, progress=+0.04, None=0 | +0.15   | 压缩写回时                                    |
| **access\_bonus** 访问频率   | `access_count` (每次查询递增)     | `min(log(access+1)/log(101), 1.0) × 0.10` — 100 次访问后饱和                                                                                | +0.10   | 每次查询命中时                                  |
| **citation\_bonus** 引用次数 | `citation_count` (被升级替代时递增) | `min(citation/5, 1.0) × 0.10` — 5 次引用后饱和                                                                                              | +0.10   | 生命周期升级时                                  |
| **pinned/hidden** 用户反馈   | `pinned`, `hidden` (API 设置) | pinned → W=1.0 锁定，hidden → W=0.0 排除                                                                                                   | ±1.0 覆盖 | `POST /compressed/{id}/pin\|hide\|unpin` |

**实际效果示例**：

```text
"我决定重构架构"  (decision)
  W = 1.0(base) + 0.15(content) + 0.02(access) + 0.06(cite) = 1.0(clamped)
  → 始终排在最前面

"嗯好的"  (progress)
  W = 1.0(base) + 0.04(content) + 0.01(access) + 0.00(cite) = 1.0(clamped, 但内容分低)
  → 在同类同级中排序靠后

一个被反复查询且被3个Arc引用的 decision Event:
  W = 1.0 + 0.15 + 0.07(50次访问) + 0.06(3次引用) = 1.0(clamped, 真正"重要")
  → 即使年龄超过30天升到Scene级(W_base=0.7)，总分仍有0.98，不会被淘汰

一个从未被查询、从未被引用的 progress Event (30天后升级到Scene):
  W = 0.7(base) + 0.04(content) + 0.00(access) + 0.00(cite) = 0.74
  → 在查询结果中自然沉底
```

**查询默认行为**：`search_compressed` 只返回 `status='active'` 且 `hidden=0` 的条目，按 `dynamic_weight DESC` 排序。高权重的新近重要决策始终优先展示，低权重的琐碎历史自然沉底。通过 `include_superseded=true` 可查看完整历史版本链。

**语义搜索（Dimension 5 补充）**：`POST /compressed/semantic-search` 通过 LLM Embedding 或哈希伪嵌入计算余弦相似度，作为关键词搜索的补充。当 LLM 可用时生成语义向量，不可用时退化为确定性 n-gram 哈希嵌入，保证搜索始终可用。

**关键改进**：旧设计中，SQLite 中的对话条目直接做摘要合并（扁平压缩），原始文本不可恢复，且权重完全由年龄和类型硬编码。新设计中：(1) 30 天内的原始对话完整保留在 Tier 1；(2) 超过 30 天才送入 Tier 2 压缩，压缩结果写回 SQLite 的 `compressed_memories` 表；(3) 压缩条目按五维权重模型排序——重要的决策自动保活，频繁访问的记忆自动提升，被多次引用的节点获得加成，用户可钉住/隐藏任意条目；(4) 压缩条目随时间逐级升档、权重衰减、最终清退，既控制了无限膨胀，又保留了完整审计链；(5) 从 Arc 摘要可沿 `source_turns` → `event_ids` →原始 turn 一路追溯到原始对话。

在当前基线中，记忆管理者与监督者共用同一条 API-B 能力链。二者不是两套互相割裂的灵魂系统，而是同一长期记忆与治理能力在不同运行模式、不同权限上下文下的两种身份：

- 记忆管理者：负责长期记忆管理、压缩、整理、总结
- 监督者：全天候负责规划、裁决、放行、推迟、取消或暂停自提升任务（按认知层对用户状态的软感知择机，不受时间窗口限制）

API-B 必须独立配置的原因是：

- Agent 的工作模型主要用于任务执行与工具推理
- Mem 的模型能力主要用于长期记忆管理与治理裁决
- 长期身份与治理判断不能完全复用 Agent 的短期工作模型心智

API-A / API-B 的运行边界还必须补充两条硬约束：

- **API-A 的运行时模型配置是活跃 Agent 执行的唯一权威来源**。用户请求体中附带的 `model` 字段不能覆盖已解析出的活跃 runtime model
- **API-B（Mem / Supervisor）绝不能回环到 Gateway 的 API-A chat 面**。`memory.llm.base_url` 必须指向真实的记忆模型提供方，而不是 `gateway /v1/chat/completions`；否则会把 API-B 的记忆/治理调用错误打回 API-A 路由，造成模型错位、上下文错位甚至 502

### 3.5 学习任务框架

学习任务不是内生驱动的本体，而是当前治理输出中的一种创造类兼容投影。当前阶段，这类投影主要表现为探索式研究任务，并作为身体改造进化的证据来源。学习任务不是由独立的"自学系统"服务执行，而是由 API-A 的活跃 Agent 在 AUTO / Governor Mode 下直接执行。学习之后，如果监督者判断证据积累达到阈值，还会继续产出 `body_improvement` 任务，由同一个 API-A Agent 在 shell 槽位执行代码改进。

当前进入任务列表的创造类兼容任务投影主要包括：

- **无依赖探索学习任务**（`self_learning`）：在缺少历史学习上下文、替身代码历史或明确改进线索时，先产出探索式学习任务，例如搜索、阅读、整理外部技术资料，建立后续进化方向的知识底座
- **基于当前替身代码基座的学习任务**（`self_learning`）：围绕当前 shell 槽位代码本体开展代码分析、实验验证、差距评估，理解当前结构、质量、缺陷与可改进方向
- **替身改进任务**（`body_improvement`）：学习成果累积质量 >= 阈值（默认 60 分）时产生。Agent 在 shell 槽位 worktree 中编辑代码（白名单目录内，≤5 文件），完成后提交改进报告供监督者审查评分。方向生成采用三层降级：LLM 智能分析 → 改进历史延伸 → Git diff 启发

学习任务的完整链路：

```text
监督者 (API-B, 内生驱动)
  → 产出自主任务（探索式学习任务 + 视证据累积触发的替身改进任务）
  → 放入任务列表（监督者管理的任务队列）

Agent (API-A, AUTO 模式下)
  → 通过网关遍历任务列表
  → 拉取并执行已放行任务（可自主决定是否使用子代理辅助）
  → 学习成果 / 改进进展写入 Mem 长期记忆

监督者 (API-B, 整理记忆后)
  → 在内生驱动下判断替身进展情况
  → 决定是否允许身体切换
```

学习任务不负责：

- 直接修改 active Agent 代码（身体升级由 Agent 根据学习成果在 shell 槽位编辑）
- 直接触发身体切换（切换只能由执行器执行）
- 绕过监督者裁决

**关键纠正**：不存在独立的"自学系统"运行服务。学习任务的执行者是活跃 Agent（API-A），任务的生产者是监督者（API-B），学习成果的存储位置是 Mem。当前代码中 `systems/self_learning/` 的 `service.py` 等运行服务是过渡实现，应逐步收口：学习任务的生产归监督者，执行归 Agent，存储归 Mem。

### 3.6 监督者

监督者是 Mem 在治理窗口内的提权身份，使用 API-B。监督者管理一个任务列表（即”任务管理器”），Agent 在 AUTO / Governor Mode 下遍历该列表执行监督者已放行的自主任务，其中包括 `self_learning` 和 `body_improvement`。

监督者负责：

- 读取长期记忆、学习结论与系统状态
- **内生驱动**：基于长期记忆、活动事实、身体状态和治理状态持续做认知评估，形成治理输出；其中创造类兼容投影会表现为探索式学习任务与替身改进任务
- **管理任务列表**：将内生驱动产出的学习/改进任务放入任务列表，并对任务做放行、推迟、取消、失败清理、优先级调整和生命周期治理
- **整理记忆**：定期整理 Mem 中长期记忆，识别学习成果和替身进展
- **健康值评估**（新增）：接收 Agent 提交的替身改进报告，LLM 审查代码 diff 质量 + evolution_boundary 合规检查 + probe 通过率 → 计算健康值 score_delta（范围 [-20, 30]），累加至 `BodySlotMeta.health_score`（0-100，含时间衰减）
- **判断身体切换**：当 shell 健康值超过 active 健康值时产生"建议切换"事件，Governor 独立审查后批准或否决。健康值达标只是建议，Governor 保有否决权（程序前置门），真正激活替身还需用户同意（目标语义，见 §7.5）
- 对 probe、切换、回滚、回收等关键动作输出治理裁决

监督者不负责：

- 直接拉代码、跑升级流水线
- 直接启停 Agent 进程
- 直接执行蓝绿切换（由执行器执行）
- 直接执行学习任务（由 Agent 在 AUTO 模式下执行）
- 直接编辑替身代码（由 Agent 根据学习成果执行身体升级）
- 直接向 Agent 推送任务（Agent 在 AUTO 模式下主动遍历任务列表）
- 承担长期机械执行器角色

监督者的内生驱动器：

监督者的内生驱动器不应再被理解为单纯的“候选任务生成器”，而应被理解为**认知核心 + 治理输出中枢**。它依然保留“延续、真实、创造”三类核心价值观，但这些价值观首先进入的是感知、世界模型、need、intent、meta-governance 等认知层；**候选任务只是其中一条兼容输出投影**。当前实现已经接入 `context layers + cognition charter + evidence packet + alignment feedback` 这条 LM 参与判断链，但程序仍负责输入裁剪、协议约束、风险护栏、一致性校验和最终落地边界。无 LLM 时自动降级为确定性文本提取或历史启发式生成。

当前 runtime 前台已经优先围绕主判断层组织，而不是围绕任务候选组织。主视图应以 `cognition_state` 为中心：

- `judgement_core`
- `attention_agenda`
- `uncertainty_ledger`
- `observation_program`
- `meta_governance`

`proposal_cognition` 在当前基线里应被理解为**辅助观察与追踪层**，不是第二套并列主结构。它只应保留 `lm_trace`、`assessment_trace`、`cognitive_evolution_trace`、`self_iteration_focus` 以及少量状态/计数型纠偏信号；最新 LM generation context 也应保持 dominant/count 口径，不得重新长回“顶层 LM 原始状态包 + 大量兼容摘要/历史条目并排外露”的旧形态。

下一步目标不是把系统重新拉回“候选中心”，而是继续让 LM 参与**治理任务列表本身**。

也就是说，LM 参与下的监督者目标不是不停地产生任务扔进列表，而是结合长期记忆、最近任务结果、队列状态和用户服务优先级，去决定：

- 该不该新增任务
- 哪些任务应放行
- 哪些任务应推迟
- 哪些任务应取消或清退
- 哪些任务应合并、降权或重新排序
- 哪些陈旧 `approved/running` 任务应失败、重排或彻底退休

- **延续**：维护长期记忆、演化谱系、队列健康和服务连续性 → 产出记忆维护任务、队列卫生任务
- **真实**：把错误、不确定性、证据缺口转成复核或学习任务 → 产出错误复核任务、证据验证任务
- **创造**：在空闲容量中提出受边界约束的学习和改进方向 → 先产出 `self_learning` 学习分支，证据成熟后再产出 `body_improvement` 改进分支

其中，创造类候选进入任务列表供 Agent 在 AUTO / Governor Mode 下遍历执行，但会分成两个层次：

- **`self_learning` 学习分支**：先建立认知与证据，再决定是否进入改进
- **`body_improvement` 改进分支**：只在学习证据积累达标后才出现，负责把学习结果落实为替身代码改进

其中，`self_learning` 又细分为两类：

- **无依赖探索学习任务**：在没有历史学习上下文、没有替身代码历史，或暂时无法建立明确改进线索时，先做开放式探索，解决"下一步应该学什么"的问题
- **基于当前替身代码基座的学习任务**：围绕当前 shell worktree 代码本体做代码分析、实验验证、结构理解与差距评估，解决"当前自己是什么状态、该朝哪里改"的问题

典型 `self_learning` 内容包括：

- **技术情报采集**：搜索、阅读外部技术资料，获取改进灵感
- **代码分析**：分析当前代码库结构、质量、可改进点
- **实验验证**：设计实验验证改进方案的可行性
- **差距评估**：对比当前能力与目标能力的差距

当学习成果累积质量达到阈值后，监督者才会把 **`body_improvement` 替身改进任务** 放入同一个任务列表，交给 Agent 在 shell 槽位 worktree 中执行代码改进。也就是说，任务列表承载的是"监督者放行给 Agent 执行的自主任务"，既包括学习，也包括学习后的改进，而不只是一类纯研究任务。

其他类型任务（记忆维护、队列卫生、错误复核等）由监督者内部机制或记忆维护系统直接处理，不进入 Agent 任务列表。

**现状与目标的区分**：

- **当前实现**：任务列表治理仍主要由 `evaluate_idle_window()`、`review_self_evolution_tasks()`、`decide_self_evolution_task()` 和 `SelfEvolutionTaskQueue.update_status()` 的规则、阈值和合法状态迁移控制
- **目标设计**：让 LM 先对“整个列表应该怎么管”给出结构化治理建议，再由确定性状态机验证是否允许落地

因此，正确的方向不是取消规则，而是做成“双层治理”：

- **上层 LM 治理**：做合并、放行、推迟、拒绝、退休、优先级重排、陈旧任务复审
- **下层程序护栏**：做状态机、防自撞并发护栏、边界限制、最终状态写入、超时失败和审计落盘

监督者**不**生产身体切换任务——身体切换不由任务队列驱动。身体切换的触发路径是：

```text
监督者内生驱动（治理输出中的创造类兼容投影）→ 产出学习任务 → 放入任务列表
  → Agent 执行学习任务 → 学习成果写入 Mem
    → Agent 根据成果编辑 shell 替身代码（身体升级）→ 进展写入 Mem
      → 监督者整理记忆 → 内生驱动判断替身进展
        → Governor 裁决是否建议切换（保有否决权）
          → 用户同意激活（目标语义，待实现，见 §7.5）
            → 执行器执行身体切换机械流程
```

内生驱动器不能直接执行任务，不能编辑代码，不能执行身体切换。它解决的是”该主动想到什么”，不是”可以不经审查做什么”。

监督者有两种运行模式，由 CLI 用户显式控制：

- **Memory Mode（默认）**：仅运行 health\_check 和结构化记忆维护循环。内生驱动器不启动。系统只做记忆管理，不派生任务。
- **Governor Mode（`/auto`** **激活）**：启动内生驱动器，按配置周期（默认 300s）运行认知评估与治理输出；其中只有适合投影为 Agent 自主执行任务的创造类输出，才会进入任务列表，当前主要是探索式学习任务（`self_learning`）以及达到阈值后产生的替身改进任务（`body_improvement`）。CLI `/auto-q` 退出回到 Memory Mode。

这里还要强调一点：**Governor Mode 的关键不只是“自动产任务”，而是“让监督者在治理窗口内接管任务列表管理责任”。** 如果只有产出而缺少治理，那么系统仍然只是程序循环，不是最初设计里的“监督者身份”。

正式身体切换不由任务队列驱动，而是由监督者在整理记忆后内生判断替身进展情况，形成"建议切换"裁决。

**身体切换的用户同意门（目标语义，待实现）**：替身的代码改进与 probe 验证仍由监督者/Agent 全天候自主进行（改的是替身槽代码，不影响用户使用），但**真正激活新替身（activate_slot）这最后一步只能由用户同意后执行**。监督者/Governor 的裁决产出"建议切换"，系统据此置 `awaiting_user_consent` 并在监督者 Web 监控页暴露确认入口；用户同意后才由执行器激活。Governor 的独立审查（否决权）作为同意之前的程序前置门，与用户同意门叠加，而非替代。
> 现状（待收口）：当前代码中 body_upgrade 流水线在 probe 通过后自动串接 switch_request，Governor 仅依据 probe_passed 自动批准、执行器直接 activate_slot，全链路无用户同意环节。详见 §7.5 与边界铁律第 13 条的"目标语义"标注。

### 3.7 执行器

执行器**只**负责身体切换的机械流程。执行器不做学习任务，不做身体升级（代码编辑），不做治理判断。

执行器只负责身体切换流程：

- 准备子 Agent 槽位
- 标记 `candidate`
- 执行 probe（结构化健康检查）
- 启停目标 Agent 进程
- 推进状态迁移（shell → candidate → probe → active → retired）
- 执行切换（将 candidate 切换为 active）
- 执行回滚（将 active 回退到上一个稳定版本）
- 回收旧体
- 将执行结果写回 Mem

执行器不负责：

- 判断是否应该升级（监督者判断）
- 判断候选体是否可信（监督者判断）
- 解释长期记忆（Mem / 监督者负责）
- 替代监督者做治理裁决
- 执行学习任务（Agent 负责）
- 编辑替身代码 / 身体升级（Agent 负责）

这条边界必须保持稳定：

**监督者只判断身体切换是否放行，执行器只执行身体切换机械流程，Agent 负责执行学习任务和身体升级（代码编辑）。**

### 3.8 任务列表

任务列表（即"任务管理器"）不是独立的架构组件，而是由监督者直接管理的一个任务队列。监督者会把治理输出中适合交由 Agent 执行的兼容任务投影放入此列表，Agent 在 AUTO / Governor Mode 下通过网关主动遍历此列表并执行。

任务列表的定位：

- **持有者**：监督者（属于 `systems/supervisor/` 的一部分，对应 `SelfEvolutionTaskQueue`）
- **生产者**：监督者治理输出中的兼容任务投影层（由内生驱动认知判断后落入任务列表）
- **消费者**：Agent（API-A，在 AUTO / Governor Mode 下遍历并执行）
- **内容**：包含 `self_learning` 与 `body_improvement` 两类 Agent 自主任务；不包含 `body_switch` / `body_upgrade` 这类身体切换机械任务
- **治理者**：监督者；未来应由“LM 先给出结构化治理意见 + 程序状态机最终把关”共同完成

任务列表不是独立服务，不需要独立进程、独立端口或独立注册。它是监督者模块内部的数据结构，通过网关暴露给 Agent 访问。

当前代码中 `systems/supervisor/task_queue.py` 的 `SelfEvolutionTaskQueue` 已经是任务列表的正式实现骨架，现阶段仍需继续完善的是：

- 在保持现有 pull 路径不回退的前提下，继续清理残留 push 兼容认知，避免后续会话再把旧路径当主逻辑
- 在现有 `planned/approved/running/completed/failed/paused/cancelled` 生命周期基础上，补强“陈旧任务复审、合并、退休、重排”的治理语义
- 为未来 LM 任务治理输出预留结构化动作层，而不是继续堆更多分支兼容逻辑

## 4. 总原则

### 4.1 用户服务优先

任何自学习、自提升、自愈、自进化行为都不能抢占用户服务链路。

当用户请求到达时，系统优先保证当前 active Agent 可用。内部自提升任务必须让位于用户任务。

### 4.2 网关中心化

长期存在的内部组件之间，应通过网关或受控协议协作。不得让 Agent、Mem、监督者、执行器之间形成难以追踪的私有旁路。

CLI 是用户入口；网关是内部组件入口。二者不能混为一谈。

### 4.3 双 API 最小配置

系统保留两组模型调用身份：

- API-A：供 Agent 使用，用于任务执行、工具调用、学习任务执行、身体升级（代码编辑）
- API-B：供 Mem 与监督者使用，用于长期记忆管理、总结规划、治理裁决

角色差异优先通过提示词、权限、调用入口、任务上下文和协议约束区分，不为每个子系统无限扩展模型栈。

### 4.4 Agent 无长期状态

Agent 是可替换执行体，不是长期身份载体。

Agent 可以持有短期工作态，但长期身份、长期记忆、治理历史、演化谱系、任务队列和裁决依据都必须落到 Mem 或明确的持久任务存储中。

### 4.5 学习与执行分离

学习任务由监督者内生驱动产出，由 Agent 在 AUTO / Governor Mode 下执行。学习之后触发的 `body_improvement` 任务同样由 Agent 执行；身体切换则由执行器执行。

三个动作分属三个角色：

- **监督者**：产出 Agent 自主任务，管理任务列表，判断身体切换时机
- **Agent**：执行学习任务与 `body_improvement` 任务，编辑替身代码（身体升级），提交进展到 Mem
- **执行器**：只执行身体切换机械流程

不存在独立的"自学系统"运行服务。交由 Agent 执行的自主任务，其生产者是监督者，执行者是 Agent，成果与进展的存储位置是 Mem。

### 4.6 身体切换必须可验证、可回滚

任何候选子 Agent 切换为 active 前，都必须经过受控验证。

正式状态机、切换协议、观察窗口和回滚条件由组件文档定义：

- [body-lifecycle.md](./body-lifecycle.md)
- [switch-protocol.md](./switch-protocol.md)

本文只规定总原则：

- 不能跳过 probe
- 不能无记录切换
- 不能切换后立即销毁旧体
- 不能让未通过治理的候选体承接正式用户流量
- 不能把手动 CLI 切换作为正式自进化路径

### 4.7 Git 作为演化谱系与回滚底座

Git 可以作为子 Agent 自进化的重要基础设施，但它不是治理者本身。

Git 适合承担：

- 候选体构建来源记录
- worktree 隔离
- 变更 diff 审查
- commit / branch / tag 谱系
- probe 前后的代码快照
- 回滚到已知稳定版本
- 将学习结论、执行结果与代码变更关联起来

Git 不应承担：

- 判断候选体是否可信
- 替代 Mem 保存长期身份真相
- 替代监督者做切换裁决
- 作为唯一运行状态来源

推荐语义是：

- `active` 对应已通过治理的稳定 commit 或 tag
- `candidate` 对应受控 worktree / branch
- `probe` 对应带测试报告与学习证据的候选 commit
- `retired` 对应可回滚的前一稳定 commit

Mem 记录“为什么演化”和“是否允许演化”；Git 记录“具体改了什么”和“如何回到过去”。

## 5. 运行形态

当前目标运行形态是：

- 单主机
- Windows 或 Linux
- 多进程
- 无容器前提
- CLI 作为用户入口
- 网关作为内部通信中枢

推荐启动顺序：

1. Mem / 记忆服务
2. 内部网关
3. 当前 active Agent
4. 执行器或看门狗

监督者默认不是独立于 Mem 的第二套灵魂系统。它可以在实现上被拆成独立服务，但架构身份仍属于 API-B / Mem 治理能力链。

执行器可以是独立进程、生命周期程序或看门狗组合，但它必须保持“消费裁决并执行动作”的边界。

## 6. 执行时机与让路治理

监督者**全天候自动执行**自提升任务，不再受时间窗口约束。这之所以安全，是因为自进化由**互不影响的子代理**执行、进化对象是**替身槽位的代码**（而非用户正在交互的主代理与 CLI），因此监督者的后台执行不打扰用户的前端使用。用户使用状态由**认知层软感知**，作为降权/择机信号，而不是硬性闸门。

> 历史背景：早期基线把自动执行限制在每日 `00:00-06:00` 执行窗口，并以"连续无用户请求"作为执行前提。在双泳道隔离（gateway agent scene 按 `supervisor_task` / `user_chat` 分槽，见《CLI展示与gateway双槽设计》）与"自进化只改替身代码"两项前提成立后，时间窗口与"等用户空闲"的硬闸门已**彻底移除**。

### 6.1 全天候执行与软感知

- 用户服务始终优先：用户在前端 CLI 的交互走主代理，与监督者的后台自进化子代理互不覆盖、互不抢占。
- 监督者可随时规划与执行任务（self_learning / memory_maintenance / self_evolution 全类型，无时间段限制）。
- **认知层软感知用户状态**：`active_sessions`（= gateway 会话缓存大小，用户普通 CLI 在主循环周期注册会话）进入内生驱动的感知快照，作为候选 utility 与优先级的**降权信号**——用户活跃时自进化倾向降低、择机让路，但**不被阻断**。
- 仍保留的是**防自撞并发护栏**（memory / self_learning / self_evolution / agent 各自的 in-flight idle 判定），它防的是"对同一子系统重复并发派发"，与"等用户"无关。

> **活动信号拓扑（2026-06 核实，重要）**：gateway 的用户代理端点（`/v1/chat/completions`、`/v1/agent/query`）已废弃并返回 410，用户真实 CLI 直连 LLM provider、不经 gateway。因此 `last_user_request_at` 在实时系统**无活 feeder**，旧的"等用户空闲"硬门本就形同虚设——这也是全天候改造删除该门低风险的依据。`last_agent_work_at` 的实时 feeder 只有监督者任务自身（AUTO 拉取开始 + 任务回写裁决），所以 `has_agent_idle` 等 idle 护栏只反映监督者自身在途工作、不耦合用户活动。对用户状态的感知由 `active_sessions` 这一独立信号承担（软适配），与 idle 护栏解耦。

### 6.2 执行触发方式

- **全天候自动**：监督者按内生驱动节律自动规划与执行，无时间窗口约束。
- **手动触发**：用户仍可明确触发 `auto` 立即执行。

### 6.3 抢占与让路

用户请求出现时，依赖隔离而非中断：

- 监督者的自进化子代理与用户主代理在不同执行体/泳道，用户请求不需要中断后台自进化。
- 认知层据用户活跃度软性下调自进化倾向（择机让路）。替身代码改进（body_improvement）与 probe 验证全天候自主进行；但**身体切换的最后一步 activate_slot 是用户同意门**（目标语义，见 §3.6 / §7.5），不自动发生。
- 网关始终保证用户服务优先级；后台执行不占用用户交互路径。

### 6.4 任务状态（监督者任务列表）

监督者管理的任务列表包含交由 Agent 执行的自主任务，至少包括探索式学习任务（`self_learning`）和替身改进任务（`body_improvement`）。身体切换不由任务队列驱动。

Agent 自主任务状态：

- `planned` — 监督者内生驱动产出，尚未放行
- `approved` — 监督者放行，等待 Agent 拉取执行
- `running` — Agent 已拉取，正在执行
- `completed` — Agent 执行成功，学习成果或改进进展已写入 Mem
- `failed` — Agent 执行失败
- `paused` — 用户请求到达，监督者暂停
- `cancelled` — 监督者取消

状态机（由监督者强制执行）：

```text
planned ──→ approved ──→ running ──→ completed
  │                         │
  │                         ├──→ failed
  │                         │
  └──→ paused              │
  │                         │
  └──→ cancelled           │
                           │
              (Agent 回报)  ┘
```

合法转换：

- `planned → approved`：监督者放行
- `planned → paused`：监督者暂停
- `planned → cancelled`：监督者取消
- `approved → running`：Agent 从任务列表拉取并开始执行
- `running → completed`：Agent 执行成功，学习成果或改进进展写入 Mem
- `running → failed`：Agent 执行失败，或监督者对长时间卡住的任务做正式失败清理
- `running → paused`：用户请求到达

禁止的转换：

- `planned → completed`（未经过 approved 和 running）
- `cancelled → *`（终态不可转换）

监督者输出裁决：

- 放行
- 推迟
- 取消
- 暂停
- 清退
- 退休
- 合并
- 重排优先级

其中，“清退 / 退休 / 合并 / 重排优先级”属于监督者的治理语义；真正落库时仍需映射为合法状态迁移、原因记录和审计日志，而不是绕过状态机直接改写任务真相。

### 6.5 单槽治理与定时队列

监督者治理的 UI 与运行时必须遵守“单槽执行、队列补位”的约束：

- **监督者执行卡**同一时刻只允许挂 1 个监督者任务；执行完成后任务回收消失，再从定时队列按先后顺序取下一个**非创造类**任务
- **Agent 执行卡**同一时刻只允许挂 1 个创造类任务（`self_learning` / `body_improvement`）；执行完成后任务回收消失，再从定时队列按先后顺序取下一个创造类任务
- **定时队列**不是创造类专属队列；它同时容纳创造类任务与监督者任务，只是由不同执行卡按类型各自取走
- **同一预设时间只能保留一个任务**；与定时队列重复的治理投影不得再次进入定时队列

定时队列的展示状态与执行状态分离：

- `预设时间`：还在等待到达预设时间或等待对应执行卡取走
- `执行中`：任务已经离开定时队列，进入监督者执行卡或 Agent 执行卡
- `已挂起` / `已顺延`：由监督者治理动作显式标注

## 7. 核心链路

### 7.1 用户服务链路

```text
用户 -> CLI -> 内部控制面 / 网关 -> active Agent -> 工具 / 记忆 -> CLI
```

CLI 是用户入口。实际任务执行由 active Agent 完成。长期记忆访问通过网关进入 Mem。

### 7.2 记忆链路

```text
Agent / 监督者 / 执行器 -> 网关 -> Mem
```

学习成果、身体升级进展、治理裁决、切换记录和演化历史必须进入 Mem。

### 7.3 学习任务链路（AUTO 模式）

```text
 监督者 (API-B, 内生驱动)
  → 将创造类治理输出投影为探索式学习任务
  → 放入任务列表

Agent (API-A, AUTO 模式下通过网关)
  → 遍历任务列表
  → 拉取并执行学习任务（Agent 自主决定是否使用子代理辅助）
  → 学习成果写入 Mem
```

Agent 主动拉取（pull），监督者不推送（push）。对 Agent 来说，任务列表中的主执行对象包括 `self_learning` 和后续的 `body_improvement`；其中学习任务可自主决定使用子代理辅助复杂任务。不存在独立的"自学系统"服务。

### 7.4 身体升级链路（内生驱动 → 学习 → 改进 → 健康值 → 切换）

**阶段 1：学习**

```text
监督者 (API-B, 内生驱动)
  → 产出 self_learning 任务
    ├─ 无依赖探索学习任务
    └─ 基于当前替身代码基座的学习任务
  → 放入任务列表

Agent (API-A, AUTO 模式下)
  → 拉取并执行学习任务（只读研究，不修改代码）
  → 学习成果写入 Mem 长期记忆
```

**阶段 2：替身改进**（学习成果累积质量 >= 60 分时触发）

```text
监督者 (API-B, 内生驱动)
  → 产出 body_improvement 任务（替身改进候选，三层方向降级）
  → payload 携带: worktree_path, editable_dirs, forbidden_patterns

Agent (API-A)
  → 拉取 body_improvement 任务 → 解析约束
  → 读 Mem（学习成果）+ Git diff(active↔shell) + 读 shell worktree 代码
  → 在 shell worktree 中编辑代码（白名单目录内，≤5 文件）
  → Git commit → 提交改进报告(commit_hash + diff + 学习引用) → POST /body/improvement-report
```

**阶段 3：健康值评分**

```text
监督者 (API-B)
  → 接收改进报告 → 多重验证:
      1. commit_hash 归属验证（属于 shell slot worktree）
      2. 白名单目录检查
      3. evolution_boundary 细粒度评分 (0-20)
      4. LLM 审查 diff 质量 (0-20)
      5. probe 通过率 (0-20，新替身用父 slot 历史平均)
      6. 学习成果新鲜度 (0-20)
      7. 同文件重复改进惩罚
  → 计算 score_delta = Σ(子分 × 权重) - penalty（范围 [-20, 30]）
  → 应用时间衰减（30天内不衰减，30-90天逐渐衰减）
  → 累加至 BodySlotMeta.health_score (0-100)
```

**阶段 4：建议切换**

```text
监督者 (API-B)
  → health_score >= active_health + 15 或 shell > active
  → 产生"建议切换"事件 → Governor 独立审查

Governor (API-B, 治理裁决)
  → 接收建议切换事件
  → 独立审查（可批准或否决，保有最终否决权）
  → 批准后 → 置 awaiting_user_consent（目标语义，待实现）

用户 (经监督者 Web 监控页)
  → 看到替身状况与进度 → 同意切换
  → 用户同意后 → 执行器执行 activate_slot
```

**健康值公式**：

```text
health_score = Σ(score_delta) - time_decay, [0, 100]

单次 score_delta =
    LLM_diff_quality   × 0.35  (0-20)
  + probe_pass_score   × 0.25  (0-20)
  + boundary_score     × 0.20  (0-20，细粒度非二元)
  + learning_freshness × 0.15  (0-20，含时间衰减)
  + stability_factor   × 0.05  (0-20)
  - file_repeat_penalty         (同文件第N次改: (N-1)×5)
```

**关键原则**：健康值达标是"建议"而非"自动"。Governor 保有否决权（程序前置门）；真正激活新替身（activate_slot）需**用户同意**（目标语义，待实现）。身体切换不由任务队列驱动。执行器只做切换的机械流程。

### 7.5 身体切换链路（Governor 裁决 + 用户同意 + 执行器执行）

```text
Governor (API-B)
  → 接收"建议切换"事件（health_score 达标触发）
  → 独立审查切换必要性（保有否决权，程序前置门）
  → 批准后 → 置 awaiting_user_consent（目标语义，待实现）

用户 (经监督者 Web 监控页)
  → 跟踪替身状况与进度 → 同意切换

执行器
  → 用户同意后消费裁决
  → 执行身体切换机械流程：
      shell → candidate → probe → active → retired
  → 执行结果写回 Mem
```

身体切换不由任务队列驱动。替身代码改进与 probe 验证由监督者/Agent 全天候自主进行；Governor 在收到健康值达标的建议切换事件后独立裁决（否决权为程序前置门）。**真正激活新替身（activate_slot）只能由用户同意后执行**（目标语义，待实现）——这是用户对"换身体"这一不可逆动作保有的最终控制权。执行器只做切换的机械流程，不做升级、不做学习、不做判断。

> 现状（待收口）：当前代码尚无用户同意环节——body_upgrade 流水线 probe 通过后自动串接 switch_request，Governor 依据 probe_passed 自动批准，执行器直接 activate_slot。落地时需：(a) Governor 批准后停在 awaiting_user_consent 而非自动 upgrade_executed；(b) 监督者 Web 页提供确认入口并展示替身状况与进度；(c) 切断 body_upgrade → switch 的自动串接。

## 8. 可观测性要求

核心链路必须支持统一追踪，至少包含：

- `trace_id`
- `task_type`
- `governance_task_type`
- `task_family`
- `execution_kind`
- `session_id`
- `source_service`
- `target_service`
- `decision_id`

其中：

- `task_type`
  - 只保留为 broad 原始/追踪分类，便于跨链路保留最粗粒度任务来源语义
- `governance_task_type`、`task_family`、`execution_kind`
  - 用于 runtime policy、idle-window、治理裁决、execution handoff 与写回语义

这些 canonical runtime 字段的归一化入口应统一落在 [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)；后续 queue、gateway activity、governor request、execution adapter、lifecycle writeback、Mem lineage 不应各自复制一套近似推导逻辑。

补充约束：

- 一旦某条 canonical runtime surface 已稳定，需在同一轮删除旧残留与重复镜像字段，避免 supervisor / executor / gateway 长期并存两套近义表达。
- formal execution handoff 中，broad `task_type` 可以保留在 formal contract、queue snapshot、trace lineage 里，但不应再作为 executor outward summary metadata 的重复主字段。

这些任务语义属于 activity / trace / execution 事实层，而不是长期服务注册身份层；服务注册 metadata 应优先表达稳定服务身份与路由信息。

`task_type` 至少区分：

- `user`
- `self_evolution`
- `memory_maintenance`

网关应成为统一追踪入口，避免各进程自行定义互不兼容的活动事实。

### 8.1 监督者监控 UI 设计原则

监督者的 Web UI（Supervisor Room）是 VoidCube 母体心跳的统一可观测窗口。所有任务——无论走哪条执行路径——都应在同一界面清晰可辨。

**核心设计原则**：

1. **标题保持不变**：页面标题固定为“义子的小屋”，不因治理结构升级而改名。
2. **任务卡固定分为 5 段，自上而下排列**：
   | 段位 | 名称 | 语义 |
   | --- | --- | --- |
   | **第 1 段** | 统计卡 | 展示总任务量、错误数、窗口状态，以及运行槽 / 编辑槽等治理概览 |
   | **第 2 段** | 监督者执行卡 | 只展示当前被监督者处理的 1 个非创造类任务；没有任务时显示休息态 |
   | **第 3 段** | Agent 执行卡 | 只展示当前被 API-A 处理的 1 个创造类任务 |
   | **第 4 段** | 定时队列卡 | 展示等待被取走的定时任务，既包含创造类，也包含监督者任务 |
   | **第 5 段** | 内生驱动治理投影列表 | 展示内生驱动每轮产出的治理投影列表，供监督者 LM 裁定是否进入定时队列 |
3. **统计卡必须反映运行槽与编辑槽**：不能只显示 `active_slot`。当 active 在 `slot-A` 而 Agent 正在编辑 `slot-B` 时，UI 必须能同时表达这两个槽位，避免把“正在编辑 shell 槽”误显示成“只存在 active 槽”。
4. **第 2、3 段是单槽，不是列表**：
   - 监督者执行卡只保留最先处理的 1 个监督者任务；完成后消失回收，再从第 4 段定时队列按先后顺序补位
   - Agent 执行卡只保留最先处理的 1 个创造类任务；完成后消失回收，再从第 4 段定时队列按先后顺序补位
5. **第 4 段定时队列必须混合展示两类任务**：
   - API-A 使用的创造类任务显示为**紫色**
   - 监督者处理的非创造类任务显示为**绿色**
   - “定时队列”不等于“创造类队列”；如果没有监督者任务，监督者执行卡就无任务可取，这与治理基线冲突
6. **第 5 段不是“只能放 1 个元素”的单卡**：它是“内生驱动生产的治理投影列表”。约束是“每次循环只产出 1 个新投影”，不是“整个列表 UI 只能展示 1 个”。投影列表中的任务不得与定时队列已有任务重复；若发生冲突，由监督者 LM 裁定。
7. **任务与事件分离**：任务面板展示的是“当前治理结构中的任务位置与状态”；时间线展示的是“刚刚发生了什么”。两者不应混淆。
8. **场景（scene）按报告者分域**：场景不是全局单值。每个运行实体只声明自己的"当前在做什么"——
   谁执行、谁上报。这与 §3.6 监督者职责一致（监督者只管理，不执行学习/身体升级/身体切换机械流程），
   也与 §3.4 "记忆管理者与监督者共用 API-B 能力链，但执行者是另一域" 一致。
   | 报告者                         | 域        | 合法 scene 值     | 含义                 |
   | --------------------------- | -------- | -------------- | ------------------ |
   | **监督者 (Supervisor, API-B)** | 治理身份     | `idle`         | 无活动                |
   | <br />                      | <br />   | `planning`     | 决策/批准/拒绝任务（管理任务列表） |
   | <br />                      | <br />   | `drive`        | 内生驱动：产出候选          |
   | <br />                      | <br />   | `memory`       | 直接触摸长期记忆（Mem 内部操作） |
   | <br />                      | <br />   | `maintenance`  | 记忆维护/队列卫生          |
   | <br />                      | <br />   | `dispatch`     | 发送执行请求到身体执行器       |
   | **Agent (API-A)**           | 学习/升级执行体 | `idle`         | 无活动                |
   | <br />                      | <br />   | `learning`     | 正在执行 `self_learning` 学习任务 |
   | <br />                      | <br />   | `code_editing` | 正在执行 `body_improvement` / 编辑替身代码 |
   | <br />                      | <br />   | `executing`    | 正在执行其他用户任务（直接对话响应） |
   | **执行器 (Executor)**          | 身体切换机械面  | `idle`         | 无活动                |
   | <br />                      | <br />   | `body_switch`  | 正在执行身体切换流程         |
   显式边界：
   - 监督者**永远不**上报 `learning` / `code_editing` / `executing` / `body_switch`（§3.6 边界）
   - Agent **永远不**上报 `body_switch`（§3.5 边界：身体切换由执行器执行）
   - 执行器**永远不**上报 `learning` / `code_editing`（§3.5 边界：学习由 Agent 执行）
   - 三个报告者的 scene 互不耦合——一个实体可以处于 `idle`，另一个处于 `learning`，互不影响
   旧"5 scene 全局"列表（`idle` / `drive` / `learning` / `body_switch` / `maintenance`）作废。
   任何代码、文档、UI 必须按上表的"报告者→scene"对应关系上报与展示。

   **监督者空闲语义补充**：
   - 如果第 2 段监督者执行卡没有任务，监督者 scene 必须是 `idle` / 休息态
   - 仅因为第 3 段存在创造类待执行任务，不得把监督者误显示为 `planning`
   - 监督者只有在自己真正持有非创造类任务、正在治理候选、或正在做记忆/维护动作时，才进入 `planning` / `drive` / `maintenance` 等 scene

   **三段式状态栏（CLI 状态展示）**：
   网关 `/admin/scenes` 端点聚合三域 scene；CLI `VoidCube status` 与 `VoidCube dashboard`
   在"Gateway Service"区块以三段式水平状态栏呈现：
   - `🧠 API-B (Supervisor) — <scene> — <title>`
   - `🤖 API-A (Agent) — <scene> — <task_id|—>`
   - `⚙️ Executor — <scene> — <title>`
     每段含 reachability 指示：✅ 节点可达 / ⚠️ 节点失联（默认 idle）。
     三段独立呈现，**不允许**把监督者的 `idle` 渲染成"学习中"，
     也不允许把 Agent 的 `learning` 错配到监督者段。
当前仓库中可以继续沿用并加强的基础包括：

- `systems/gateway/internal_gateway.py`
  - 服务注册、路由、活动事实、活跃身体同步
- `systems/memory/memory_service.py`
  - 记忆服务、压缩、总结、衰减与 API-B 入口
- `systems/body_registry.py`
  - 双槽位注册表、active 指针、槽位路径与观察窗口
- `systems/lifecycle.py`
  - 治理批准后的确定性状态迁移
- `systems/probe.py`
  - 结构化 probe 检查
- `systems/governor.py`
  - 治理裁决语义
- `systems/execution/`
  - 执行器与执行适配层
- `plugins/memory/mem/governor_bridge.py`
  - Mem 侧治理历史记录
- `systems/self_learning/`
  - 自学结论与建议事项的初级协议

仍需继续收口的方向包括：

- **子代理应收口为 Agent 自主能力**：当前学习任务的 `delegate_task` 是强制子代理路径。应收口为 Agent 根据任务复杂度自主决定是否使用子代理，而非学习任务的固定模式
- **清理** **`systems/self_learning/service.py`** **等独立运行服务**：学习任务的生产归监督者，执行归 Agent，存储归 Mem，不存在独立的"自学系统"运行服务
- **监督者监控 UI 重新设计**：当前 UI 任务面板只展示 5 条、不区分执行路径、身体切换不可见。按 §8.1 原则重新设计为按执行路径分组的全量任务视图
- **把任务列表治理升级为 LM + 状态机双层结构**：当前内生驱动和任务审批仍偏规则驱动，应补入 LM 对整表进行放行、推迟、清退、合并、重排、陈旧任务复审的结构化治理层

已完成收口的方向：

- **监督者模式切换**：Memory Mode / Governor Mode 现已通过 `/auto` 和 `/auto-q` CLI 命令显式控制
- **结构化四级记忆压缩**（Phase M5）：Event→Scene→Arc→Epoch 四级压缩管线已接入运行时
- **执行器收口为身体切换专用**：执行器已收缩为身体切换机械流程执行面，不做学习、不做升级
- **AUTO 改为 Agent pull 执行**：CLI `_poll_auto_mode_workflow()` 与网关 `/v1/tasks/{task_id}/decision` 已接入正式任务链路；活跃 Agent 现在会主动拉取并执行 `self_learning` / `body_improvement`
- **任务列表已纳入 `body_improvement` 与 `running` 生命周期**：API-A 可正式回报 `running/completed/failed`，监督者也可对卡住任务执行正式失败清理

## 10. 非目标

当前阶段不追求：

- 多主机分布式一致性
- 自动弹性扩缩容
- 容器编排体系
- 复杂多租户治理
- 多候选体并发竞争
- 完全自动代码自修复到生产发布

当前目标是先把单机多进程架构的职责边界做实，让“谁负责判断，谁负责执行，谁负责长期记忆，谁是主要升级对象”稳定下来。

## 11. 文档关系

本文是最高优先级基线。

其他文档定位如下：

- [architecture-integration.md](./architecture-integration.md)：组件接线、请求链路、部署说明
- [body-lifecycle.md](./body-lifecycle.md)：身体状态机
- [switch-protocol.md](./switch-protocol.md)：切换、审批、观察窗口、回滚协议
- [state-boundary.md](./state-boundary.md)：长期状态、运行状态、缓存状态的归属
- [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md)：第一阶段实验路线与验收
- [phase-2-implementation-plan.md](./phase-2-implementation-plan.md)：Phase 2 实现计划（架构基线对齐）
- [body-runtime-runbook.md](./body-runtime-runbook.md)：当前实现的操作与排障手册
- [architecture-conflicts-audit.md](./architecture-conflicts-audit.md)：当前实现与基线的偏差审计
- [voidcube架构可行性论证论文.md](./voidcube架构可行性论证论文.md)：架构可行性论证
- [phase1-core-loop-and-endogenous-drive.md](./phase1-core-loop-and-endogenous-drive.md)：Phase 1 核心闭环与内生驱动器运行机理，定义母体心跳、四重保障与完整运行循环

## 12. 一句话结论

VoidCube 的目标不是维护两套运行模式，而是建立一个单机多进程母体系统：用户通过 CLI 使用当前 active Agent，母体内部通过网关、Mem、监督者和执行器持续培养、验证、切换与回滚子 Agent。

在这个系统里，用户服务始终优先；长期记忆与治理属于 Mem；监督者只判断；执行器只执行；真正持续升级并最终交付给用户的主对象，是 Agent 本身。

## 13. 双核模型总览

### 13.1 双核定义

```
┌─────────────────────────────────────────────────────────────────┐
│                      VoidCube 母体                              │
│                                                                 │
│   API-B 核（灵魂/治理）              API-A 核（身体/执行）        │
│   ┌─────────────────────┐          ┌──────────────────────┐     │
│   │ Mem 长期记忆        │          │ Active Agent         │     │
│   │  · 四级压缩         │          │  · 用户任务执行       │     │
│   │  · 身份连续性       │          │  · 遍历任务列表       │     │
│   │  · 演化谱系         │          │  · 执行自主任务       │     │
│   │  · 学习成果存储     │          │  · 编辑替身代码       │     │
│   │                     │          │  · 提交进展到 Mem     │     │
│   │ 监督者 (Governor)   │          │                      │     │
│   │  · 内生驱动         │          │ Shell Agent (slot-B) │     │
│   │  · 管理任务列表     │  身体切换│  · 被编辑的替身       │     │
│   │  · 整理记忆         │◀────────│  · candidate/probe    │     │
│   │  · 判断身体切换     │  裁决    │  · 等待切换           │     │
│   └────────┬────────────┘          └──────────┬───────────┘     │
│            │                                  │                 │
│            │ 任务列表                         │ AUTO 模式        │
│            │ (监督者管理)                     │ 遍历任务列表     │
│            │                                  │                 │
│            ↓                                  ↓                 │
│   ┌─────────────────────┐          ┌──────────────────────┐     │
│   │ 执行器              │          │ Gateway              │     │
│   │  · 身体切换机械流程  │          │  · 路由              │     │
│   │  · probe/activate   │          │  · 服务注册          │     │
│   │  · rollback/recycle │          │  · 活动追踪          │     │
│   └─────────────────────┘          └──────────────────────┘     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

- **API-B 核**：Mem + 监督者，负责"记住自己是谁"、"当前局面是什么"、"该研究什么"、"哪些治理输出值得推动"和"该不该切换"。监督者管理任务列表，内生驱动先完成认知判断与治理输出选择，再把其中创造类兼容投影落成 `self_learning` 学习任务，证据成熟后再落成 `body_improvement` 改进任务。独立配置模型，不与 Agent 共享短期工作心智，也不能回环到 API-A chat 路由。
- **API-A 核**：Agent（活跃 + 候选双槽位），负责"做用户的事"、"学该学的东西"和"编辑替身代码"。可被替换，不持有长期真相。

### 13.2 API-B 核：Mem 三层角色

Mem 灵魂层的三个内部角色共享同一 API-B 能力链：

| 角色                               | 职责                                                | 约束                                                               |
| -------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------- |
| **Memory Core**（记忆核心）            | 长期记忆写入/检索、身份连续性维护、演化谱系保存、学习成果与身体升级进展存储            | 不能直接批准 probe/active/rollback                                     |
| **Governor Engine**（治理引擎）        | 内生驱动执行认知评估、形成治理输出、管理任务列表、整理记忆判断替身进展、裁决身体切换 | 当前实现前台已以 `judgement_core / attention_agenda / uncertainty_ledger / observation_program / meta_governance` 作为主判断结构；任务候选只保留为兼容投影，但内部仍存在一定 candidate-heavy 实现体量，程序继续负责输入裁剪、状态迁移、风险护栏与最终把关 |
| **Governance Audit Store**（审计存储） | 每条裁决的 decision\_id、观察窗口记录、回滚原因追溯                  | 不可篡改                                                             |

### 13.3 记忆压缩双层体系

| 层级                | 机制                                                                                               | 数据模型                                                 | 触发                       | 权重                   |
| ----------------- | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------- | ------------------------ | -------------------- |
| **Tier 0** 原始保留   | 30天内完整保留，不做任何压缩                                                                                  | SQLite `turns` 表                                     | Gateway 实时写入             | 1.00                 |
| **Tier 1** 衰减管理   | relevance\_score 指数衰减，标记压缩候选                                                                     | SQLite `turns` 表                                     | 运行时周期 loop               | 按 relevance          |
| **Tier 2a** 编年史压缩 | turns → ChroniclePipeline.ingest() → Event/Scene/Arc/Epoch **写回 SQLite** `compressed_memories` 表 | SQLite `compressed_memories` (L0 Event)              | turns 超30天 或 超10000条     | 1.00                 |
| **Tier 2b** 逐级升档  | Event(L0)→Scene(L1)→Arc(L2)→Epoch(L3)→Final(L4)，每级覆盖替代前级，旧条目标记 superseded                        | SQLite `compressed_memories` (status/superseded\_by) | 按年龄自动：30d→180d→365d→730d | 1.0→0.7→0.4→0.2→0.05 |
| **Tier 2c** 最终清退  | purged 条目审计保留 90 天后 DELETE                                                                       | SQLite `compressed_memories`                         | 自动周期 + 可手动               | 0.00                 |

**查询默认行为**：`search_compressed` 只返回 `status='active'` 且 `hidden=0` 的条目，按 `dynamic_weight DESC` 排序。dynamic\_weight 融合五个维度：W\_base(等级) + content\_bonus(内容重要性) + access\_bonus(访问频率) + citation\_bonus(引用次数)，被 pin 的条目锁定在 1.0，被 hide 的条目排除。权重公式详见 §3.4.3。

**语义搜索**：`POST /compressed/semantic-search` 通过 LLM Embedding 余弦相似度检索，LLM 不可用时自动降级为 n-gram 哈希伪嵌入。

### 13.4 监督者运行模式

| <br />             | Memory Mode（默认） | Governor Mode（/auto） |
| ------------------ | --------------- | -------------------- |
| health\_check loop | ✅               | ✅                    |
| 结构化记忆维护            | ✅ 自适应周期         | ✅                    |
| 内生驱动器（认知评估 + 治理输出） | ❌               | ✅ 每 300s             |
| 任务列表（Agent 遍历执行）   | ❌               | ✅                    |
| 整理记忆 + 判断身体切换      | ❌               | ✅                    |
| Gateway 用户请求       | ✅ 正常服务          | ❌ 返回 503             |
| CLI 输入             | ✅ 正常            | ❌ 仅 /auto-q          |

### 13.5 内生驱动器的核心价值观、主判断结构与兼容投影

监督者内生驱动器将"延续、真实、创造"三类核心价值观先映射为认知判断与治理动作。当前基线下，runtime 前台应先看主判断结构，再看兼容投影：

```text
cognition_state
  ├── judgement_core
  ├── attention_agenda
  ├── uncertainty_ledger
  ├── observation_program
  └── meta_governance

proposal_cognition（辅助观察与追踪层，不是第二套主系统）
  ├── lm_trace
  ├── assessment_trace
  ├── cognitive_evolution_trace
  └── self_iteration_focus

compatible projections
  ├── task_candidates
  └── secondary_task_shape_hint
```

也就是说，`task_candidates` 以及相关 `task_type` 语义只是当前实现中的兼容投影，不是内生驱动的本体定义。其真正判断主线仍然是下面这条：

```
Mem 中长期记忆 + 网关活动快照（7 个时间戳 + 错误/不确定性计数 + 活跃会话数）
  │
  ├── 认知判断 1: memory_maintenance_sweep（延续，维护长期记忆与演化谱系）
  ├── 认知判断 2: truthfulness:review_correction_signals（真实，复核错误与不确定性）
  ├── 认知判断 3: creativity
  │     ├── self_learning
  │     │     ├── 无依赖探索学习任务
  │     │     └── 基于当前替身代码基座的学习任务
  │     └── body_improvement
  └── 认知判断 4: queue_hygiene_review（延续，队列健康与服务连续性）
```

其中，认知判断 3（创造类）不是单一学习任务，而是分成两个连续分支：

- **`self_learning`**：先建立认知与证据，又分为
  - **无依赖探索学习任务**：在没有历史学习上下文、没有替身代码历史，或一时无法建立明确改进目标时，先进行探索式学习
  - **基于当前替身代码基座的学习任务**：围绕当前 shell 槽位代码本体开展代码分析、实验验证、差距评估，理解"现在的自己"与"下一步怎么学"
- **`body_improvement`**：只在学习证据达到阈值后出现，把学习结果落实为 shell worktree 上的实际代码改进

也就是说，进入任务列表供 Agent 在 AUTO 模式下遍历执行的，不只是单一的探索学习，而是"先学习、后改进"这条创造类兼容链路。其余判断优先在监督者内部完成；队列卫生、错误复核和候选清退本身，未来可以由 LM 监督者先提出治理动作，再交由程序护栏落地。

当前实现状态应明确区分为两层：

- 已经收敛的部分：runtime 前台主线已不再把 `top_priority_task_type` 这类字段当核心视图，主判断结构已经前移到 `cognition_state`
- 仍待继续收薄的部分：内部仍保留一定 `candidate-heavy` 评分、裁剪与兼容语义残留，因此后续演进仍应优先防止“兼容投影重新反客为主”

内生驱动器不产出身体切换任务。身体切换由监督者在整理记忆后内生判断替身进展，Governor 裁决建议切换（保有否决权），用户同意后由执行器执行激活（用户同意门为目标语义，待实现，见 §7.5）。

与治理 UI / 队列布局对应的基线约束：

- 创造类任务进入定时队列后，由第 3 段 Agent 执行卡按先后顺序单槽消费
- 非创造类监督者任务进入定时队列后，由第 2 段监督者执行卡按先后顺序单槽消费
- 内生驱动投影先进入第 5 段治理投影列表，只有适合执行的兼容任务投影，才经过监督者 LM 裁定后进入第 4 段定时队列
- 第 5 段治理投影列表与第 4 段定时队列不得出现重复任务，尤其不得在同一 `scheduled_for` 时间保留多个冲突任务

### 13.6 API-A 核：Agent 双槽位 + 学习与升级

**双槽位身体架构**：slot-A (active) 服务用户并执行监督者放行的自主任务，slot-B (shell) 是 Agent 根据学习成果编辑代码的替身。升级完成后标记为 candidate → probe，监督者判断通过后由执行器切换为 active。

**学习任务完整链路**：

```text
监督者 (API-B, 内生驱动)
  → 产出 `self_learning` 学习任务
    ├─ 无依赖探索学习任务
    └─ 基于当前替身代码基座的学习任务
  → 放入任务列表

Agent (API-A, AUTO 模式下通过网关)
  → 遍历任务列表
  → 拉取并执行 `self_learning` 任务（Agent 自主决定是否使用子代理辅助）
  → 学习成果写入 Mem
```

**身体升级完整链路**（四阶段）：

```text
阶段 1 — 学习:
  监督者内生驱动 → self_learning 任务 → Agent 执行 → 成果写入 Mem

阶段 2 — 替身改进 (学习质量 >= 60 分触发):
  监督者内生驱动 → body_improvement 任务 → Agent 编辑 shell worktree
    → Git commit → 提交改进报告(commit_hash + diff + 学习引用)

阶段 3 — 健康值评分:
  监督者 LLM 审查 → 五维评分(diff_quality+probe+boundary+freshness+stability)
    → score_delta → 累加至 BodySlotMeta.health_score (0-100)

阶段 4 — 建议切换:
  health_score >= active_health + 15 或 shell > active
    → "建议切换"事件 → Governor 独立审查 → 批准/否决
    → 批准后 → executor: shell→candidate→probe→active
```

**关键边界**：

- 学习任务由活跃 Agent 执行，Agent 可自主决定是否使用子代理辅助复杂任务
- 身体升级（代码编辑）由 Agent 在 Git 替身基础上执行，不由执行器代劳
- 身体切换只由执行器执行机械流程
- 健康值达标是"建议"而非"自动"，Governor 保有最终否决权（边界铁律第 13 条）
- 不存在独立的"自学系统"运行服务

### 13.7 不可妥协的边界铁律

| #  | 规则                      | 含义                                                    |
| -- | ----------------------- | ----------------------------------------------------- |
| 1  | **监督者只判断，执行器只切换**       | 监督者不拉代码、不编辑代码、不启停进程；执行器只做身体切换机械流程                     |
| 2  | **Agent 无长期状态**         | 长期记忆、身份真相、演化谱系只属于 Mem（API-B）                          |
| 3  | **Agent 执行自主任务，监督者管理任务** | `self_learning` 与 `body_improvement` 由监督者产出并放入任务列表，Agent 在 AUTO / Governor Mode 下遍历执行 |
| 4  | **不能跳过 probe**          | 候选体未经 probe 不得成为 active                               |
| 5  | **不能无记录切换**             | 每次切换必须有完整 governance trail                            |
| 6  | **不能切换后立即销毁旧体**         | 观察窗口是回滚保护层                                            |
| 7  | **API-A ≠ API-B**       | Agent 工作模型心智用于"做事和学习"，Mem 模型心智用于"记住自己是谁"。API-B 不得回环进入 Gateway 的 API-A chat 路由，API-A 活跃 runtime model 也不得被请求体 `model` 任意覆盖 |
| 8  | **子代理是 Agent 的自主能力**    | 子代理不是被禁止的能力，也不是学习任务的强制模式。Agent 根据任务复杂度自主决定是否使用子代理辅助执行 |
| 9  | **用户服务绝对优先**            | 自进化行为不能抢占用户链路。Governor Mode 显式激活后才允许                  |
| 10 | **内生驱动器只输出治理投影，不直接执行** | 不直接执行，不编辑代码，不执行切换。四类治理投影各走各的处置路径                    |
| 11 | **Agent 编辑替身代码，执行器只切换** | 身体升级（代码编辑）由 Agent 执行，身体切换机械流程由执行器执行                   |
| 12 | **身体切换不由任务队列驱动**        | 监督者整理记忆后内生判断替身进展，Governor 裁决建议切换，用户同意后交由执行器执行激活（用户同意门为目标语义，见 §7.5） |
| 13 | **健康值达标是建议；切换激活需用户同意** | shell 健康值超过 active 后产生"建议切换"事件，Governor 独立审查（否决权为程序前置门）。真正激活新替身（activate_slot）只能由用户同意后执行——健康值/Governor 都不是自动切换触发器（用户同意门为目标语义，待实现，见 §7.5） |
| 14 | **LM 治理不能绕过程序护栏**        | 即使未来由 LM 参与任务列表治理，最终状态迁移、防自撞并发护栏、边界检查和审计落盘仍必须由确定性程序负责 |
