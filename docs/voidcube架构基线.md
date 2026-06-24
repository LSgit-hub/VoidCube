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

**一个以 CLI 为用户入口、以内部网关为神经中枢、以 Mem 为长期记忆与治理灵魂、以监督者为内生驱动与任务列表管理者、以 Agent 为学习任务与身体升级执行体、以执行器为身体切换机械执行面、以可替换子 Agent 为主要升级对象的单机多进程母体系统。**

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

当前阶段主要需要持续升级的对象是子 Agent 本身，而不是优先要求 CLI、网关、Mem、监督者或执行器先自我升级。后者是支撑系统，目标是让 Agent 能稳定变好。

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

涉及身体切换、升级、回滚的 CLI 命令只属于测试、验收、排障或应急恢复入口。它们不能成为正式自进化路径的主触发者。正式路径必须由 Mem / 监督者基于长期记忆、学习证据、协议状态、空闲窗口和风险约束作出裁决，再由执行器消费裁决执行。

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
- **在 AUTO 模式下**：通过网关主动遍历监督者的任务列表，拉取并执行探索式学习任务
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

**LLM-First 原则**：Mem 的核心价值在于智能——充分利用大模型的理解能力来压缩总结、判断问题、给出学习方向。LLM 是 Mem 的核心引擎，不是可选插件。**需要智能的环节**（压缩提取、场景/弧线/纪元摘要、升级重摘要、内生驱动学习主题）由 LLM 驱动。**不需要智能的环节**（Tier 1 字节存取、衰减公式、清退删除、时间阈值判断）直接用程序执行。LLM Provider 已作为第一公民配置，启动时自动验证连通性，周期健康检查（每 5 个压缩周期），状态通过 `GET /llm/health` 和 `GET /compressed/rules-status` 暴露。无 LLM 时系统进入显式降级模式并记录告警日志。详见 [mem-llm-first-redesign.md](mem-llm-first-redesign.md)。

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
- **衰减机制**：relevance_score 按指数衰减（默认每天 ×0.99），低于阈值（默认 0.1）的 turn 标记为可压缩候选，但内容不自动删除
- **与现有 memory_service 的关系**：直接扩展 `systems/memory/memory_service.py` 的 SQLite schema，增加 sessions/turns/turns_archive 三张表，复用现有的 FastAPI 路由、decay loop、gateway 注册等基础设施

**Tier 2 — 长期编年史记忆（Mem Pipeline）**：

- **触发时机**：turn 超过 30 天保留窗口 OR Tier 1 turns 表行数超过阈值（默认 10000 条）
- **压缩流程**：选中的 turns → TranscriptTurn 序列 → ChroniclePipeline.ingest() → Event/Scene/Arc/Epoch + ProfileMemory
- **反向引用**：每个 Event 的 `source_turns` 字段记录原始 turn_id 列表，Scene/Arc 通过 `evidence_refs` 可逐层追溯到原始对话
- **压缩后处理**：压缩完成的 turns 从 `turns` 表移至 `turns_archive` 表（保留 turn_id + 时间锚点 + 摘要，原始内容可选删除）
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

| 现有组件 | 复用方式 |
|---------|---------|
| `systems/memory/memory_service.py` | 扩展 SQLite schema，增加 sessions/turns/turns_archive 三张表 |
| `Mem/src/memai/pipeline.py` (ChroniclePipeline) | 直接作为 Tier 2 压缩引擎，无需修改 |
| `Mem/src/memai/schema.py` (TranscriptTurn) | 作为 Tier 1 → Tier 2 的数据转换格式 |
| `Mem/src/memai/query.py` (MemoryQueryEngine) | 扩展 source_turns 回查 Tier 1 的能力 |
| `Mem/src/memai/repository.py` (MemoryStateRepository) | 增量更新机制不变 |
| `Mem/src/memai/maintenance.py` (MemoryMaintenanceEngine) | 四级结构化压缩不变 |
| `Mem/src/memai/governance.py` + `governance_repository.py` | 压缩事件记录为治理审计日志 |
| `plugins/memory/mem/governor_bridge.py` | 治理桥接不变 |

### 3.4.2 记忆压缩双层体系（更新）

在双层架构下，Mem 的压缩分两个阶段运作：

- **Tier 1 衰减管理**（memory_service）：turns 在 30 天保留窗口内完整保留。超过 30 天后，先降 relevance_score（指数衰减），再标记为压缩候选。**仅当 Tier 2 已生成对应的结构化记忆（Event/Scene）后，才将原始 turn 移至 archive 表。不压缩、不合并原始对话文本——只做时间窗口管理和衰减标记。**

- **Tier 2 编年史压缩**（ChroniclePipeline）：将超过保留窗口的 turns 批量送入 ChroniclePipeline，**LLM 优先 + 启发式降级**。当 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 环境变量存在时，使用 `LLMEventExtractionBackend`（LLM 理解语义提取事件）和 `LLMScholarBackend`（LLM 生成场景/弧线/纪元摘要）。无 API 凭据时自动降级为 `HeuristicEventExtractionBackend`（关键词正则匹配）和 `HeuristicScholarBackend`（模板填充）。**LLM 压缩才能真正理解内容语义——区分"决定重构架构"和"嗯好的"，而不是仅靠关键词匹配。** 压缩不可逆，但通过 source_turns 保留反向引用链路。

- **Tier 2 结构化四级压缩**（MemoryMaintenanceEngine）：对 Event→Scene→Arc→Epoch 四层对象做分层压缩与替代（supersede），超期 Scene（>30天）压缩入父 Arc，超期 Arc（>180天）压缩入父 Epoch，超期 Epoch（>365天）进一步压缩。默认使用 LLMScholarBackend（API-B）生成自然压缩摘要，无 API 凭据时自动降级到 HeuristicScholarBackend。**已接入运行时**：Governor Mode 下通过内生驱动→任务队列触发，Memory Mode 下每 3600s 自动执行。

- **压缩结果写回 SQLite**（`compressed_memories` 表）：Tier 2 压缩产出的 Event/Scene/Arc/Epoch **不仅存在于 Mem Pipeline 内存和 mem_state.json 中，同时写回 SQLite 的 `compressed_memories` 表**。每条记录带 `source_turns`（反向引用原始 turn_id）、`parent_id`（层级归属）、`compression_level`（压缩等级）、`status`（active/superseded/purged）、`weight`（查询权重）。这使得 SQLite 成为 Tier 1（原始会话）+ Tier 2（压缩记忆）的统一查询入口，不再需要分别访问两个存储系统。

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

双路径保证：memory_service 进程启动后自主执行（不依赖 Supervisor 是否启动），Supervisor 触发作为冗余确保规则一定被调用。两次执行完全幂等——`_tier2_bridge_cycle` 只处理 `compressed_to_tier2=0` 的 turns，`_apply_compression_lifecycle` 只处理超过年龄阈值的条目。

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

| 维度 | 信号源 | 计算公式 | 最大加成 | 更新时机 |
|------|--------|---------|---------|---------|
| **W_base** 结构基础 | `compression_level` (0-4) | 硬编码：Event=1.0, Scene=0.7, Arc=0.4, Epoch=0.2, Final=0.05 | — | 创建时 + 升级时 |
| **content_bonus** 内容重要性 | `event_kind` (Mem 提取) | `_CONTENT_IMPORTANCE_BONUS`: decision=+0.15, correction/shift=+0.12, completion/conflict=+0.08, blocker=+0.06, progress=+0.04, None=0 | +0.15 | 压缩写回时 |
| **access_bonus** 访问频率 | `access_count` (每次查询递增) | `min(log(access+1)/log(101), 1.0) × 0.10` — 100 次访问后饱和 | +0.10 | 每次查询命中时 |
| **citation_bonus** 引用次数 | `citation_count` (被升级替代时递增) | `min(citation/5, 1.0) × 0.10` — 5 次引用后饱和 | +0.10 | 生命周期升级时 |
| **pinned/hidden** 用户反馈 | `pinned`, `hidden` (API 设置) | pinned → W=1.0 锁定，hidden → W=0.0 排除 | ±1.0 覆盖 | `POST /compressed/{id}/pin\|hide\|unpin` |

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

在当前基线中，记忆管理者与监督者共用同一条 API-B 能力链。二者不是两套互相割裂的灵魂系统，而是同一长期记忆与治理能力在不同时间窗口、不同权限上下文下的两种身份：

- 记忆管理者：负责长期记忆管理、压缩、整理、总结
- 监督者：在执行窗口内负责规划、裁决、放行、推迟、取消或暂停自提升任务

API-B 必须独立配置的原因是：

- Agent 的工作模型主要用于任务执行与工具推理
- Mem 的模型能力主要用于长期记忆管理与治理裁决
- 长期身份与治理判断不能完全复用 Agent 的短期工作模型心智

### 3.5 学习任务框架

学习任务是监督者内生驱动器产出的探索式研究任务，是身体改造进化的证据来源。学习任务不是由独立的"自学系统"服务执行，而是由 API-A 的活跃 Agent 在 AUTO 模式下直接执行。

学习任务的类型（由监督者内生驱动产出）：

- **技术情报采集**：搜索、阅读、整理外部技术资料
- **实验验证**：设计并执行代码实验，验证改进方案的可行性
- **代码分析**：分析当前代码库的结构、质量、缺陷
- **差距评估**：对比当前能力与目标能力，识别改进方向

学习任务的完整链路：

```text
监督者 (API-B, 内生驱动)
  → 产出探索式学习任务
  → 放入任务列表（监督者管理的任务队列）

Agent (API-A, AUTO 模式下)
  → 通过网关遍历任务列表
  → 拉取并执行学习任务（可自主决定是否使用子代理辅助）
  → 学习成果写入 Mem 长期记忆

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

监督者是 Mem 在治理窗口内的提权身份，使用 API-B。监督者管理一个任务列表（即”任务管理器”），Agent 在 AUTO 模式下遍历该列表执行学习任务。

监督者负责：

- 读取长期记忆、学习结论与系统状态
- **内生驱动**：基于核心价值观、活动事实和空闲窗口派生探索式学习任务（身体改造进化的依据）
- **管理任务列表**：将内生驱动产出的学习任务放入任务列表，由监督者统一管理
- **整理记忆**：定期整理 Mem 中长期记忆，识别学习成果和替身进展
- **判断身体切换**：在内生驱动下，根据替身进展情况和学习证据，判断是否允许执行身体切换
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

监督者的内生驱动器保持原有的三类核心价值观驱动能力，把”延续、真实、创造”映射为可审计的候选任务。**内生驱动器默认使用确定性规则引擎（计数器 + 时间戳判断），当 LLM API 凭据可用时，创造力候选（探索式学习任务）通过 LLM 分析压缩记忆上下文生成智能学习主题——而非简单截取最近文本的前 80 个字符。LLM 生成的主题具有更高的 utility 评分（0.72 vs 0.68），并带 `llm_generated: true` 标记。**无 LLM 时自动降级为确定性文本提取。

- **延续**：维护长期记忆、演化谱系、队列健康和服务连续性 → 产出记忆维护任务、队列卫生任务
- **真实**：把错误、不确定性、证据缺口转成复核或学习任务 → 产出错误复核任务、证据验证任务
- **创造**：在空闲容量中提出受边界约束的学习和改进方向 → 产出探索式学习任务（LLM 优先）

其中，**探索式学习任务**（创造类）进入任务列表供 Agent 在 AUTO 模式下遍历执行，包括：

- **技术情报采集**：搜索、阅读外部技术资料，获取改进灵感
- **代码分析**：分析当前代码库结构、质量、可改进点
- **实验验证**：设计实验验证改进方案的可行性
- **差距评估**：对比当前能力与目标能力的差距

其他类型任务（记忆维护、队列卫生、错误复核等）由监督者内部机制或记忆维护系统直接处理，不进入 Agent 任务列表。

监督者**不**生产身体切换任务——身体切换不由任务队列驱动。身体切换的触发路径是：

```text
监督者内生驱动（创造类）→ 产出学习任务 → 放入任务列表
  → Agent 执行学习任务 → 学习成果写入 Mem
    → Agent 根据成果编辑 shell 替身代码（身体升级）→ 进展写入 Mem
      → 监督者整理记忆 → 内生驱动判断替身进展
        → 裁决是否允许身体切换
          → 执行器执行身体切换机械流程
```

内生驱动器不能直接执行任务，不能编辑代码，不能执行身体切换。它解决的是”该主动想到什么”，不是”可以不经审查做什么”。

监督者有两种运行模式，由 CLI 用户显式控制：

- **Memory Mode（默认）**：仅运行 health_check 和结构化记忆维护循环。内生驱动器不启动。系统只做记忆管理，不派生任务。
- **Governor Mode（`/auto` 激活）**：启动内生驱动器，按配置周期（默认 300s）产出全部四类候选任务。其中探索式学习任务放入任务列表，Agent 通过网关主动遍历执行。CLI `/auto-q` 退出回到 Memory Mode。

正式身体切换不由任务队列驱动，而是由监督者在整理记忆后内生判断替身进展情况，直接裁决并交由执行器执行，不是用户日常手动操作。

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

任务列表（即"任务管理器"）不是独立的架构组件，而是由监督者直接管理的一个任务队列。监督者的内生驱动器将探索式学习任务放入此列表，Agent 在 AUTO 模式下通过网关主动遍历此列表并执行学习任务。

任务列表的定位：

- **持有者**：监督者（属于 `systems/supervisor/` 的一部分，对应 `SelfEvolutionTaskQueue`）
- **生产者**：监督者内生驱动器（产出探索式学习任务）
- **消费者**：Agent（API-A，在 AUTO 模式下遍历并执行）
- **内容**：仅包含探索式学习任务（身体改造进化的依据），不包含身体切换任务

任务列表不是独立服务，不需要独立进程、独立端口或独立注册。它是监督者模块内部的数据结构，通过网关暴露给 Agent 访问。

当前代码中 `systems/supervisor/task_queue.py` 的 `SelfEvolutionTaskQueue` 就是这个任务列表的雏形。需要修正的是：
- 任务列表中不应包含 body_upgrade/body_switch 任务（身体切换不由任务队列驱动）
- Agent 应从任务列表主动拉取（pull），而不是监督者推送（push）
- 当前的 push 路径（supervisor → gateway governance_task_proxy → agent）应废弃

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

学习任务由监督者内生驱动产出，由 Agent 在 AUTO 模式下执行。身体升级（代码编辑）由 Agent 根据学习成果在 shell 槽位执行。身体切换由执行器执行。

三个动作分属三个角色：
- **监督者**：产出学习任务，管理任务列表，判断身体切换时机
- **Agent**：执行学习任务，编辑替身代码（身体升级），提交进展到 Mem
- **执行器**：只执行身体切换机械流程

不存在独立的"自学系统"运行服务。学习任务的执行者是 Agent，任务的生产者是监督者，成果的存储位置是 Mem。

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

## 6. 时间治理

自提升任务必须受到时间窗口和空闲条件约束。

### 6.1 日常时段

默认日常服务时段内：

- 用户服务优先
- 监督者可以规划任务
- Agent 可以产出学习结论
- 自提升任务可以进入队列
- 不应直接执行高风险升级或切换

### 6.2 执行窗口

建议执行窗口为每日 `00:00-06:00`。

真正允许放行的最早条件不是简单到 `00:00`，而是：

- 当前位于执行窗口内
- 最近连续 10 分钟无用户请求
- 最近连续 10 分钟无冲突工作流
- 系统健康状态允许
- 队列任务未取消、未过期

因此，最早放行通常发生在 `00:10` 或之后。

### 6.3 抢占规则

执行窗口期间一旦出现用户请求：

- 监督者应将相关自提升任务裁决为 `paused` 或后续可恢复状态
- 执行器应停止或安全中断可中断动作
- 网关恢复用户服务优先级
- 未完成任务等待下一次执行窗口重新裁决

### 6.4 任务状态（监督者任务列表）

监督者管理的任务列表仅包含探索式学习任务。身体切换不由任务队列驱动。

学习任务状态：

- `planned` — 监督者内生驱动产出，尚未放行
- `approved` — 监督者放行，等待 Agent 拉取执行
- `running` — Agent 已拉取，正在执行
- `completed` — Agent 执行成功，学习成果已写入 Mem
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
- `running → completed`：Agent 执行成功，学习成果写入 Mem
- `running → failed`：Agent 执行失败
- `running → paused`：用户请求到达

禁止的转换：
- `planned → completed`（未经过 approved 和 running）
- `cancelled → *`（终态不可转换）

监督者输出裁决：

- 放行
- 推迟
- 取消
- 暂停

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
  → 产出探索式学习任务
  → 放入任务列表

Agent (API-A, AUTO 模式下通过网关)
  → 遍历任务列表
  → 拉取并执行学习任务（Agent 自主决定是否使用子代理辅助）
  → 学习成果写入 Mem
```

Agent 主动拉取（pull），监督者不推送（push）。学习任务的执行者是 Agent，Agent 可自主决定使用子代理辅助复杂任务。不存在独立的"自学系统"服务。

### 7.4 身体升级链路（Agent 在 Git 替身基础上改进代码）

```text
Agent (API-A)
  → 从 Mem 读取学习成果
  → 通过 Git 了解 shell 槽位替身 Agent 的代码结构和自身短板
  → 在 Git worktree 中基于替身现有代码进行改进（编辑、commit）
  → 将 diff、commit 和进展描述提交到 Mem 长期记忆
```

身体升级不是凭空编写代码，而是在 Git 替身现有代码基础上的持续改进。Git 负责记录"改了什么、从哪来、如何回滚"，Mem 负责记录"为什么改、学到了什么"。

### 7.5 身体切换链路（监督者判断 + 执行器执行）

```text
监督者 (API-B)
  → 整理 Mem 中长期记忆
  → 内生驱动判断替身进展情况
  → 裁决是否允许身体切换

执行器
  → 消费裁决
  → 执行身体切换机械流程：
      shell → prepare → candidate → probe → active → retired
  → 执行结果写回 Mem
```

身体切换不由任务队列驱动。监督者在整理记忆后内生判断替身进展，直接裁决并交由执行器执行。执行器只做切换的机械流程，不做升级、不做学习、不做判断。


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

1. **所有任务统一展示**：监督者内生驱动四类候选、任务列表中的学习任务、身体切换任务——全部在同一任务面板中可见。当前 UI 仅展示前 5 条任务（`slice(0,5)`），远远不够。

2. **执行路径一目了然**：每条任务必须标注其执行路径，让观察者能区分：
   - **学习任务**：内生驱动（创造类）→ 任务列表 → Agent pull 执行
   - **身体切换**：监督者裁决 → 执行器执行
   - **记忆维护**：内生驱动（延续类）→ 内部机制直接执行
   - **错误复核**：内生驱动（真实类）→ 内部机制直接执行
   - **队列卫生**：内生驱动（延续类）→ 内部机制直接执行

3. **任务全生命周期可追踪**：每个任务从产生到终态的过程清晰展示，包括 `planned → approved → running → completed/failed` 的每个状态转变时刻。

4. **分类视图，非混乱堆叠**：任务按执行路径分组展示，不是简单的时间线堆砌。推荐分为以下面板：

   | 面板 | 内容 | 执行路径 |
   |------|------|---------|
   | **学习任务** | 任务列表中 Agent 待执行/执行中/已完成的学习任务 | 监督者 → 任务列表 → Agent |
   | **身体切换** | 当前正在进行的身体切换状态（prepare/candidate/probe/active） | 监督者 → 执行器 |
   | **内生驱动** | 四类候选任务的最近产出记录 | 监督者内生驱动 → 各处置路径 |
   | **记忆维护** | 记忆压缩、队列卫生等内部维护任务 | 监督者 → 内部机制 |

5. **当前场景（scene）保留但精简**：当前 UI 的 5 个场景（memory/learning/planning/execution/idle）过度简化了系统状态。改为基于实际任务活动驱动的场景切换：
   - `idle`：无任何活动
   - `drive`：内生驱动正在评估产出候选
   - `learning`：Agent 正在执行学习任务
   - `body_switch`：执行器正在执行身体切换
   - `maintenance`：记忆维护/队列卫生

6. **任务与事件分离**：任务面板展示的是"待完成、正在做、已完成"的任务状态；时间线展示的是"刚刚发生了什么"的事件流。两者不应混淆。

**当前 UI 的问题**：
- 任务面板只展示 5 条，不区分执行路径
- `scene` 表达过于粗糙（5 个静态场景无法表达系统真实状态）
- 指标面板只显示 Queued/Approved/Errors/Exec Win 四个数字，无法了解任务全貌
- 身体切换状态在 UI 中不可见
- 内生驱动四类候选与任务列表学习任务混在一起，无法区分

**改造方向**：保留现有 UI 的 Room 设计语言（CSS、布局、SSE 流），将任务面板从"最近 5 条队列"升级为"按执行路径分组的全量任务视图"，补齐身体切换状态面板，补齐内生驱动候选面板。

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

- **废弃 push 模式**：当前监督者通过 `governance_task_proxy` 直接 push 任务到 Agent，应改为 Agent 在 AUTO 模式下主动遍历监督者任务列表（pull 模式）
- **子代理应收口为 Agent 自主能力**：当前学习任务的 `delegate_task` 是强制子代理路径。应收口为 Agent 根据任务复杂度自主决定是否使用子代理，而非学习任务的固定模式
- **激活 CLI `_poll_auto_mode_workflow()`**：当前为显式 no-op，应改为在 AUTO 模式下遍历监督者任务列表并执行学习任务
- **清理 `SelfEvolutionTaskQueue` 内容**：任务列表应仅包含学习任务，移除 body_upgrade/body_switch 任务类型（身体切换不由任务队列驱动）
- **补齐 `running` 状态**：当前 `SelfEvolutionTaskStatus` 只有 `planned/deferred/approved/paused/cancelled/completed/failed`，缺少 `running`
- **清理 `systems/self_learning/service.py` 等独立运行服务**：学习任务的生产归监督者，执行归 Agent，存储归 Mem，不存在独立的"自学系统"运行服务
- **监督者监控 UI 重新设计**：当前 UI 任务面板只展示 5 条、不区分执行路径、身体切换不可见。按 §8.1 原则重新设计为按执行路径分组的全量任务视图

已完成收口的方向：

- **监督者模式切换**：Memory Mode / Governor Mode 现已通过 `/auto` 和 `/auto-q` CLI 命令显式控制
- **结构化四级记忆压缩**（Phase M5）：Event→Scene→Arc→Epoch 四级压缩管线已接入运行时
- **执行器收口为身体切换专用**：执行器已收缩为身体切换机械流程执行面，不做学习、不做升级

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
│   │  · 演化谱系         │          │  · 执行学习任务       │     │
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

- **API-B 核**：Mem + 监督者，负责"记住自己是谁"、"该研究什么"和"该不该切换"。监督者管理任务列表，内生驱动产出探索式学习任务。独立配置模型，不与 Agent 共享短期工作心智。
- **API-A 核**：Agent（活跃 + 候选双槽位），负责"做用户的事"、"学该学的东西"和"编辑替身代码"。可被替换，不持有长期真相。

### 13.2 API-B 核：Mem 三层角色

Mem 灵魂层的三个内部角色共享同一 API-B 能力链：

| 角色 | 职责 | 约束 |
|------|------|------|
| **Memory Core**（记忆核心） | 长期记忆写入/检索、身份连续性维护、演化谱系保存、学习成果与身体升级进展存储 | 不能直接批准 probe/active/rollback |
| **Governor Engine**（治理引擎） | 内生驱动产出学习任务（LLM 优先生成智能主题）、管理任务列表、整理记忆判断替身进展、裁决身体切换 | 确定性协议为主，模糊决策时咨询 LLM 推理器（`LLMGovernorReasoner`），LLM 仅为咨询角色不覆盖确定裁决 |
| **Governance Audit Store**（审计存储） | 每条裁决的 decision_id、观察窗口记录、回滚原因追溯 | 不可篡改 |

### 13.3 记忆压缩双层体系

| 层级 | 机制 | 数据模型 | 触发 | 权重 |
|------|------|---------|------|------|
| **Tier 0** 原始保留 | 30天内完整保留，不做任何压缩 | SQLite `turns` 表 | Gateway 实时写入 | 1.00 |
| **Tier 1** 衰减管理 | relevance_score 指数衰减，标记压缩候选 | SQLite `turns` 表 | 运行时周期 loop | 按 relevance |
| **Tier 2a** 编年史压缩 | turns → ChroniclePipeline.ingest() → Event/Scene/Arc/Epoch **写回 SQLite** `compressed_memories` 表 | SQLite `compressed_memories` (L0 Event) | turns 超30天 或 超10000条 | 1.00 |
| **Tier 2b** 逐级升档 | Event(L0)→Scene(L1)→Arc(L2)→Epoch(L3)→Final(L4)，每级覆盖替代前级，旧条目标记 superseded | SQLite `compressed_memories` (status/superseded_by) | 按年龄自动：30d→180d→365d→730d | 1.0→0.7→0.4→0.2→0.05 |
| **Tier 2c** 最终清退 | purged 条目审计保留 90 天后 DELETE | SQLite `compressed_memories` | 自动周期 + 可手动 | 0.00 |

**查询默认行为**：`search_compressed` 只返回 `status='active'` 且 `hidden=0` 的条目，按 `dynamic_weight DESC` 排序。dynamic_weight 融合五个维度：W_base(等级) + content_bonus(内容重要性) + access_bonus(访问频率) + citation_bonus(引用次数)，被 pin 的条目锁定在 1.0，被 hide 的条目排除。权重公式详见 §3.4.3。

**语义搜索**：`POST /compressed/semantic-search` 通过 LLM Embedding 余弦相似度检索，LLM 不可用时自动降级为 n-gram 哈希伪嵌入。

### 13.4 监督者运行模式

| | Memory Mode（默认） | Governor Mode（/auto） |
|---|---|---|
| health_check loop | ✅ | ✅ |
| 结构化记忆维护 | ✅ 自适应周期 | ✅ |
| 内生驱动器（产出学习任务） | ❌ | ✅ 每 300s |
| 任务列表（Agent 遍历执行） | ❌ | ✅ |
| 整理记忆 + 判断身体切换 | ❌ | ✅ |
| Gateway 用户请求 | ✅ 正常服务 | ❌ 返回 503 |
| CLI 输入 | ✅ 正常 | ❌ 仅 /auto-q |

### 13.5 内生驱动器四类候选

监督者内生驱动器将"延续、真实、创造"三类核心价值观映射为四类可审计候选任务：

```
Mem 中长期记忆 + 网关活动快照（7 个时间戳 + 错误/不确定性计数 + 活跃会话数）
  │
  ├── 候选 1: memory_maintenance_sweep（延续，维护长期记忆与演化谱系）
  ├── 候选 2: truthfulness:review_correction_signals（真实，复核错误与不确定性）
  ├── 候选 3: creativity:idle_learning_thread（创造，探索式学习任务）
  └── 候选 4: queue_hygiene_review（延续，队列健康与服务连续性）
```

其中，候选 3（创造类探索式学习任务）进入任务列表供 Agent 在 AUTO 模式下遍历执行。其余候选由监督者内部机制或记忆维护系统直接处理。

内生驱动器不产出身体切换任务。身体切换由监督者在整理记忆后内生判断替身进展，直接裁决交由执行器执行。

### 13.6 API-A 核：Agent 双槽位 + 学习与升级

**双槽位身体架构**：slot-A (active) 服务用户并执行学习任务，slot-B (shell) 是 Agent 根据学习成果编辑代码的替身。升级完成后标记为 candidate → probe，监督者判断通过后由执行器切换为 active。

**学习任务完整链路**：

```text
监督者 (API-B, 内生驱动)
  → 产出探索式学习任务
  → 放入任务列表

Agent (API-A, AUTO 模式下通过网关)
  → 遍历任务列表
  → 拉取并执行学习任务（Agent 自主决定是否使用子代理辅助）
  → 学习成果写入 Mem
```

**身体升级完整链路**：

```text
Agent (API-A)
  → 从 Mem 读取学习成果
  → 通过 Git 了解 shell 槽位替身代码结构和自身短板
  → 在 Git worktree 中基于替身现有代码进行改进（编辑、commit）
  → 将 diff、commit 和进展描述提交到 Mem

监督者 (API-B)
  → 整理记忆
  → 内生驱动判断替身进展
  → 裁决是否允许身体切换

执行器
  → shell → prepare → candidate → probe
  → 监督者最终确认
  → activate → 旧 active retired
  → 执行结果写回 Mem
```

**关键边界**：
- 学习任务由活跃 Agent 执行，Agent 可自主决定是否使用子代理辅助复杂任务
- 身体升级（代码编辑）由 Agent 在 Git 替身基础上执行，不由执行器代劳
- 身体切换只由执行器执行机械流程
- 不存在独立的"自学系统"运行服务

### 13.7 不可妥协的边界铁律

| # | 规则 | 含义 |
|---|------|------|
| 1 | **监督者只判断，执行器只切换** | 监督者不拉代码、不编辑代码、不启停进程；执行器只做身体切换机械流程 |
| 2 | **Agent 无长期状态** | 长期记忆、身份真相、演化谱系只属于 Mem（API-B） |
| 3 | **Agent 执行学习，监督者管理任务** | 学习任务由监督者内生驱动产出并放入任务列表，Agent 在 AUTO 模式下遍历执行 |
| 4 | **不能跳过 probe** | 候选体未经 probe 不得成为 active |
| 5 | **不能无记录切换** | 每次切换必须有完整 governance trail |
| 6 | **不能切换后立即销毁旧体** | 观察窗口是回滚保护层 |
| 7 | **API-A ≠ API-B** | Agent 工作模型心智用于"做事和学习"，Mem 模型心智用于"记住自己是谁" |
| 8 | **子代理是 Agent 的自主能力** | 子代理不是被禁止的能力，也不是学习任务的强制模式。Agent 根据任务复杂度自主决定是否使用子代理辅助执行 |
| 9 | **用户服务绝对优先** | 自进化行为不能抢占用户链路。Governor Mode 显式激活后才允许 |
| 10 | **内生驱动器只派生候选，不直接执行** | 不直接执行，不编辑代码，不执行切换。四类候选各走各的处置路径 |
| 11 | **Agent 编辑替身代码，执行器只切换** | 身体升级（代码编辑）由 Agent 执行，身体切换机械流程由执行器执行 |
| 12 | **身体切换不由任务队列驱动** | 监督者整理记忆后内生判断替身进展，直接裁决交由执行器执行 |

