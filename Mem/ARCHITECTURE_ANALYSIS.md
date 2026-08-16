# Mem 模块架构分析报告

## 1. 概述

`Mem` 模块（`memai`）是面向 AI 智能体的**基于编年史的记忆流水线**。它将原始对话转录转换为结构化、可查询且可维护的长期记忆。该系统遵循受叙事编年史理论启发的分层架构：回合 → 事件 → 场景 → 脉络 → 纪元。

**规模（2026-07-19）**：27 个源文件（约 9,086 行）、18 个测试文件（约 2,571 行），108 个测试通过。

---

## 2. 核心架构层

### 第 1 层：原始输入（TranscriptTurn）
- **模式**：`TranscriptTurn` — turn_id、speaker、text、timestamp
- **输入格式**：JSON 文件、dict、直接构造
- **流水线入口**：`ChroniclePipeline.ingest()` 接受 `Sequence[TranscriptTurn]`

### 第 2 层：事件提取
- **模块**：`extraction.py`
- **关键类**：`EventExtractor` 使用 `TemporalNormalizer` 进行时间解析
- **后端**：
  - `HeuristicEventExtractionBackend` — 基于规则的提取（默认）
  - `LLMEventExtractionBackend` — LLM 辅助提取，并带有回退机制
- **输出**：包含 kind（decision/progress/blocker/shift/completion/conflict/correction）、topics、entities、importance、confidence、novelty、impact_scope 的 `Event` 对象

### 第 3 层：场景构建
- **模块**：`scene_builder.py`
- **聚类逻辑**：同一天且共享主题的事件，或时间间隔在 6 小时以内的事件
- **Scholar 集成**：`HeuristicScholarBackend.summarize_scene()` 或 `LLMScholarBackend`
- **输出**：包含目标、关键事件、转折点、未决问题的 `Scene` 对象

### 第 4 层：脉络绑定
- **模块**：`arc_binder.py`
- **聚类逻辑**：共享主题的场景，或时间间隔在 21 天以内的场景
- **时间评分**：`HeuristicTemporalScorer` 评估连续性、影响和目标一致性
- **分类**：分数 ≥0.70 → MAIN，≥0.40 → SIDE，<0.40 → UNDETERMINED
- **休眠检测**：距上一个场景超过 30 天 → DORMANT
- **输出**：包含状态机（EMERGING→ACTIVE→STALLED/DORMANT→RESOLVED）的 `Arc` 对象

### 第 5 层：纪元构建
- **模块**：`epoch_builder.py`
- **逻辑**：当时间跨度超过阈值时，将脉络分组为纪元级摘要
- **输出**：包含主题、主要脉络、章节变化、长期影响的 `Epoch` 对象

### 第 6 层：档案记忆提取
- **模块**：`extraction.py`（ProfileMemoryExtractor）
- **模式匹配**：从文本中提取偏好、约束、定义和事实
- **语言**：英语和中文（例如“指的是”“必须”“默认”）
- **冲突解决**：`normalize_profile_memories()` 检测同一 (subject, predicate) 对的矛盾值
- **确定性状态**：OBSERVED、INFERRED、PENDING_VERIFICATION、DISPUTED、CONFIRMED

---

## 3. 查询系统

### 查询引擎（`query.py`）
- 为所有记忆层提供完整的类 CRUD 接口
- 支持按状态、确定性、时间范围、主题、实体进行筛选
- 方法：`query_events`、`query_scenes`、`query_arcs`、`query_epochs`、`query_profiles`
- 证据追踪：`trace_evidence_for` 链接回源回合

### 查询规划器（`query_planner.py`）
- 意图分类：5 种模式：
  1. `explain_memory` → audit_first 策略
  2. `retrieve_stable_context` → stable_context_first 策略
  3. `trace_theme` → theme_first 策略
  4. `inspect_current_state` → state_first 策略
  5. 默认 → timeline_first 策略
- 通过 `TemporalNormalizer` 解析时间范围
- 传播不确定性标记

### 答案组装器（`answer_assembler.py`）
- 将查询结果转换为结构化答案
- 按策略提供专用组装方法（timeline_first、theme_first、state_first、audit_first、stable_context_first）
- **完整 i18n 支持**：所有答案部分均支持中文本地化
- 语言检测：存在中文字符 → "zh"，否则 → "en"
- 未知情况/不确定性报告

---

## 4. 治理系统

### 治理事件（`governance.py`）
- 事件类型：CANDIDATE_REVIEW、PROBE_APPROVAL、SWITCH_APPROVAL、SELF_EVOLUTION_*、EXECUTION_OUTCOME、ROLLBACK_OUTCOME、MEMORY_MAINTENANCE
- 决策：APPROVE、APPROVE_WITH_WATCH、DEFER、REJECT、CANCEL、PAUSE、ROLLBACK_REQUIRED、COMPLETED、FAILED、RECORD_ONLY
- 风险级别：LOW、MEDIUM、HIGH、CRITICAL、UNKNOWN
- 失败类型：BOUNDARY_VIOLATION、PROBE_FAILURE、WATCH_WINDOW_FAILURE、EXECUTION_FAILURE、ROLLBACK_FAILURE、INSUFFICIENT_EVIDENCE

### 治理仓储（`governance_repository.py`）
- 基于 JSONL 的只追加日志
- 失败样本相似度搜索（对哈希指纹计算余弦相似度）
- 为监督器决策汇总证据

### 流水线集成
- `ChroniclePipeline.record_governance_event()`、`query_governance_events()`、`query_failure_samples()`、`summarize_governance_context()`
- `GovernanceEventRepository` 的延迟初始化
- 默认路径：`~/.VoidCube/soul/mem_governance.jsonl`

---

## 5. LLM 集成

### LLM 客户端（`llm_client.py`）
- 兼容 OpenAI 的 API 抽象
- 线程安全的 JSON 补全（`safe_complete_json`）
- 解析提供商能力（仅 JSON 与聊天模型）
- 通过 `model_config.py` 配置模型，并与 VoidCube 集成
- 内置提供商配置：openai、deepseek、openrouter、ollama

### 协议（`llm_protocol.py`）
- 版本：`memai.llm.v1`
- 任务模式：extractor.events、scholar.scene、scholar.arc、scholar.revision
- 支持多种响应格式的负载构建/解包
- 灵活的响应解析：支持 result/output/response 键，以及去除协议包装的负载

### Scholar 后端（`scholar.py`）
- `HeuristicScholarBackend`：基于规则的场景/脉络分析（默认）
- `LLMScholarBackend`：LLM 增强，并带有启发式回退
- 清理层：`_coerce_text`、`_coerce_string_list`、`_coerce_float`、`_coerce_enum_value`

---

## 6. 维护与压缩

### 压缩策略（`compression_policy.py`）
- 包含 `decide()` 方法的 `CompressionPolicy` 协议
- `HeuristicCompressionPolicy`：基于规则的压缩决策
- 自适应：使用 `AdaptiveCompressionPolicyAdapter` + `AdaptiveCompressionClient` 实现 LLM 驱动的策略
- 操作：COMPRESS、ARCHIVE、PRUNE、RETAIN

### 维护引擎（`maintenance.py`）
- 修改后重建场景/脉络/纪元
- 包含完整审计轨迹的修订记录
- `MemoryMaintenanceEngine.apply_plan()` 和 `revise_by_id()`

### 差异引擎（`diffing.py`）
- `MemoryDiffEngine` 比较两个 `PipelineResult` 状态
- `MemoryDiffReport`：新增/移除的事件、场景、脉络、纪元和档案记忆
- 跟踪状态转换以及重要性/置信度变化

---

## 7. 基准测试系统（`benchmarking.py`）

### 多种基准测试类型：
1. **标准基准测试**：转录 + 预期 JSON → 流水线 → 指标比较
2. **规划器基准测试**：测试查询规划器的正确性
3. **提供商契约基准测试**：验证 LLM 提供商兼容性
4. **提示词包矩阵**：比较不同提示词包在各个测试装置上的表现

### 指标：
- 结构准确性（事件、场景、脉络是否匹配）
- 分类质量（脉络状态、状态）
- 时间精度
- 档案记忆提取质量

---

## 8. 数据模型（模式）

所有核心类型都继承自 `BaseMemoryUnit`，并包含以下公共字段：
- id、type、title、summary、timespan_start/end、time_precision
- importance、confidence、status、main_or_side
- topics、entities、evidence_refs、parent_ids、child_ids
- compression_level、timestamps（created/updated/last_reviewed）

**层级结构**：
```
TranscriptTurn → Event → Scene → Arc → Epoch
                         ↕
                   ProfileMemory（扁平键值三元组）
```

**枚举**：
- TimePrecision：EXACT、DAY、WEEK、MONTH、APPROX
- Status：ACTIVE、DORMANT、CLOSED、SUPERSEDED
- ArcState：EMERGING、ACTIVE、STALLED、DORMANT、RESOLVED
- EventKind：DECISION、PROGRESS、BLOCKER、SHIFT、COMPLETION、CONFLICT、CORRECTION
- ImpactScope：LOCAL、THREAD、ARC、EPOCH
- MainOrSide：MAIN、SIDE、UNDETERMINED
- MemoryKind：PREFERENCE、CONSTRAINT、DEFINITION、FACT
- CertaintyState：OBSERVED、INFERRED、PENDING_VERIFICATION、DISPUTED、CONFIRMED

---

## 9. 关键设计模式

1. **策略模式**：ScholarBackend（启发式/LLM）、TemporalScorer、CompressionPolicy、EventExtractionBackend
2. **流水线模式**：ChroniclePipeline 按固定顺序串联提取器/构建器
3. **协议/接口**：多个 Protocol（ScholarBackend、TemporalScorer、CompressionPolicy、ModalityAdapter）
4. **工厂模式**：通过构造函数注入流水线组件，并提供默认值
5. **观察者模式**：记录治理事件以形成审计轨迹
6. **构建器模式**：SceneBuilder、ArcBinder、EpochBuilder 分别实现 build() 方法
7. **双重分派**：AnswerAssembler 根据策略类型进行分派
8. **回退链**：Scholar 和提取后端中采用 LLM → 启发式回退

---

## 10. 时间归一化

`TemporalNormalizer` 同时处理英语和中文表达：
- 精确日期（ISO 格式）
- 相对日期：today/yesterday/tomorrow、今天/昨天/明天
- 之前/之后的天数：X days ago、X天前、in X days、X天后
- 周：this week/last week、本周/这周/上周、X weeks ago、X周前
- 月：this month/last month、本月/上个月、X months ago、X个月前
- 模糊时间：recently/lately、最近/前阵子/近期（→ 14 天窗口）

返回包含计算出的起止时间以及精度/置信度的 `TemporalSpan`。

---

## 11. 观察与优势

1. **架构分层良好**：清晰分离提取、构建、查询和治理
2. **双后端支持**：每个依赖 LLM 的组件都有启发式回退
3. **全面的 i18n**：全流程支持中文和英语（时间、提取、答案）
4. **治理优先设计**：内置审计轨迹、风险评估和回滚机制
5. **基于协议的 LLM 集成**：标准化 JSON 协议与灵活的响应解析
6. **基准测试驱动开发**：多种基准测试类型确保质量
7. **具备压缩感知能力**：内置用于记忆生命周期管理的策略
8. **108 个测试通过**：所有模块均有良好的测试覆盖

---

## 12. 潜在改进领域

1. **不支持向量嵌入**：所有查询均基于关键词/筛选；大型记忆存储将受益于语义搜索
2. **单线程流水线**：可以并行处理大型转录中的事件提取
3. **JSONL 治理存储**：没有索引，失败样本查询需要线性扫描
4. **启发式阈值硬编码**：场景聚类（6 小时、同一天）、脉络聚类（21 天）、休眠（30 天），都可以配置化
5. **不支持增量摄取**：每次都会重新处理所有回合
6. **模态支持有限**：AudioSegmentAdapter 和 ImageCaptionAdapter 已定义，但集成程度很低
7. **不支持分布式存储**：JSONFileMemoryStore 仅支持基于文件的存储
