# 治理事件模式 v0.1

## 1. 目的

本文定义 MemAI 的第一版治理事件模式。

MemAI 已经将长期记忆建模为：

```text
Event -> Scene -> Arc -> Epoch
```

VoidCube 还需要一个并行的治理记忆层，用于记录决策、失败、回滚和自我演化证据，而不必把这些记录塞进普通的生命历史事件中。

新的对象类型是：

```text
governance_event
```

## 2. 在 VoidCube 中的位置

在 VoidCube 中，治理事件是灵魂侧的审计记录。

它们支持：

- 主体候选评审
- 探测批准或失败
- 活跃主体切换
- 观察窗口通过 / 回滚
- 自我演化任务的批准 / 延期 / 拒绝
- 边界违规
- 执行结果
- 失败样本复用

它们不执行动作，而是为 Mem / 监督者治理保留证据。

## 3. 核心形态

```json
{
  "id": "gov_001",
  "type": "governance_event",
  "event_type": "boundary_defer",
  "task_id": "task-123",
  "body_id": "slot-B",
  "source_actor": "supervisor",
  "decision": "defer",
  "reason": "候选主体更改了子代理边界之外的文件。",
  "risk_level": "medium",
  "confidence": 0.92,
  "git_lineage": {},
  "probe_report_ref": null,
  "evolution_boundary": {},
  "execution_result": null,
  "failure_signature": {},
  "evidence_refs": [],
  "related_event_ids": [],
  "created_at": "2026-05-26T00:00:00Z"
}
```

## 4. 必填字段

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `id` | string | yes | 稳定的治理事件 ID。 |
| `type` | string | yes | 必须为 `governance_event`。 |
| `event_type` | string | yes | 受控的事件类别。 |
| `source_actor` | string | yes | 治理记录的产生者。 |
| `decision` | string | yes | 治理决策或结果。 |
| `reason` | string | yes | 人类可读、以证据为界的理由。 |
| `created_at` | ISO datetime | yes | 事件创建时间。 |

## 5. 可选但推荐的字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `task_id` | string or null | 自我演化任务 ID。 |
| `body_id` | string or null | 主体槽位或子代理身份。 |
| `risk_level` | string | `low`、`medium`、`high` 或 `critical`。 |
| `confidence` | number | 对决策/证据质量的置信度。 |
| `git_lineage` | object | 分支、提交、差异、回滚以及被更改的文件。 |
| `probe_report_ref` | string or null | 探测报告指针。 |
| `evolution_boundary` | object | 代理 / 母系统边界分类。 |
| `execution_result` | object or null | 执行器结果摘要。 |
| `failure_signature` | object | 用于复用的规范化失败指纹。 |
| `evidence_refs` | string[] | 对来源报告、日志、记忆对象或提交的引用。 |
| `related_event_ids` | string[] | 指向先前治理事件的链接。 |

## 6. 事件类型

初始受控集合：

```text
candidate_review
probe_approval
probe_failure
switch_approval
switch_rejection
watch_window_pass
watch_window_rollback
boundary_defer
self_evolution_approval
self_evolution_defer
self_evolution_cancel
execution_outcome
rollback_outcome
memory_maintenance
```

## 7. 决策

初始受控集合：

```text
approve
approve_with_watch
defer
reject
cancel
pause
rollback_required
completed
failed
record_only
```

## 8. 风险等级

```text
low
medium
high
critical
unknown
```

规则：

- `boundary_defer` 至少为 `medium`，除非违规仅为信息性的。
- `watch_window_rollback` 至少为 `high`。
- `probe_failure` 的风险取决于失败的检查项。
- 当干净地完成时，`execution_outcome` 可为 `low`。

## 9. Git 谱系形态

涉及代码演进的治理事件应包含：

```json
{
  "source_branch": "main",
  "source_commit": "aaa111",
  "candidate_branch": "evolution/task-123",
  "candidate_commit": "bbb222",
  "active_ref": "body/slot-A",
  "rollback_ref": "body/slot-A",
  "rollback_commit": "aaa111",
  "diff_summary": "改进代理运行时行为。",
  "changed_files": ["agent/stream_handler.py"]
}
```

主体自我演化的最低要求：

- `candidate_commit`
- `rollback_commit`
- `changed_files`

## 10. 演化边界形态

涉及边界感知的事件应包含：

```json
{
  "ok": false,
  "changed_files": [
    "agent/stream_handler.py",
    "systems/body_registry.py"
  ],
  "allowed_files": ["agent/stream_handler.py"],
  "forbidden_files": ["systems/body_registry.py"],
  "unknown_files": [],
  "violations": ["systems/body_registry.py"]
}
```

规则：

- `ok=false` 通常应阻止正式的主体交接。
- `violations` 应被索引，以便失败样本复用。
- 边界记录是证据，不是执行命令。

## 11. 失败签名

`failure_signature` 字段用于复用和相似性搜索。

建议的形态：

```json
{
  "failure_type": "boundary_violation",
  "primary_paths": ["systems/body_registry.py"],
  "probe_checks": [],
  "risk_flags": ["mother_system_path_in_body_candidate"],
  "similarity_keys": [
    "boundary_violation:systems/body_registry.py",
    "body_candidate:mixed_agent_and_mother_paths"
  ]
}
```

常见的 `failure_type` 值：

```text
boundary_violation
probe_failure
watch_window_failure
execution_failure
rollback_failure
insufficient_evidence
```

## 12. 索引要求

Mem 最终应按以下字段为治理事件建立索引：

- `id`
- `event_type`
- `decision`
- `task_id`
- `body_id`
- `source_actor`
- `git_lineage.candidate_commit`
- `git_lineage.rollback_commit`
- `git_lineage.changed_files`
- `evolution_boundary.violations`
- `failure_signature.failure_type`
- `failure_signature.similarity_keys`
- `created_at`

## 13. 与事件 / 场景 / 脉络 / 纪元的关系

`governance_event` 不是时间线记忆的替代。

建议的关系：

- 治理事件是原子性的审计记录。
- 重要的治理事件日后可被摘要进普通的 `Event` / `Scene` / `Arc` 记忆。
- 即使摘要压缩之后，原始的治理事件历史也必须保持可用于审计。

示例：

```text
governance_event: boundary_defer
  -> 之后被摘要进
Event: 候选主体因母系统路径违规而被拒绝
```

## 14. 最小 v0.1 合规性

若实现能够做到以下各项，即符合治理事件 v0.1 合规性：

- 创建有效的 `governance_event`
- 在不丢失字段的情况下对其进行序列化和反序列化
- 表示 `boundary_defer`
- 表示 `execution_outcome`
- 表示 `watch_window_rollback`
- 暴露被更改文件和违规项以供索引

## 15. 最小仓库

当前的 v0.1 仓库刻意保持小巧且仅追加。

包位置：

```text
Mem/src/memai/
```

文件：

```text
Mem/src/memai/governance.py
Mem/src/memai/governance_repository.py
```

`GovernanceEventRepository` 支持：

- 仅追加的 JSONL 持久化
- 按事件 ID 幂等追加
- 列出所有事件
- 列出最近 N 条事件
- 按 `event_type` 查询
- 按 `decision` 查询
- 按 `task_id` 查询
- 按 `body_id` 查询
- 按 `candidate_commit` 查询
- 按 `rollback_commit` 查询
- 按 `changed_file` 查询
- 按 `violation` 查询
- 按 `failure_type` 查询
- 按 `similarity_key` 查询
- 按被更改文件、失败类型和相似性键检索排序后的失败样本

该仓库是 Mem 侧的治理记忆原语，不执行切换、回滚、升级或生命周期转移。

## 16. 失败样本检索

`GovernanceFailureSampleQuery` 支持：

- `changed_files`
- 可选的 `failure_type`
- 可选的 `similarity_keys`
- 可选的结果 `limit`

`GovernanceFailureSample` 返回：

- 匹配到的治理事件
- 简单的相关度评分
- 匹配到的文件
- 匹配到的相似性键
- 从失败签名复制的风险标志

当前评分刻意保持简单：

- 匹配到被更改文件或主路径会提高相关度
- 匹配到相似性键会更强烈地提高相关度
- 明确的失败类型过滤会缩小样本集

这对于早期的监督者证据收集已经足够，但还不是成熟的风险模型。

## 17. 监督者证据摘要

`GovernanceEvidenceSummary` 提供了第一个面向监督者的压缩层。

它返回：

- 紧凑的自然语言摘要
- 相关的治理事件 ID
- 规范化的风险标志
- 建议的治理姿态，如 `defer`、`approve_with_watch` 或 `record_only`
- 置信度评分
- 底层的失败样本

当前实现是确定性的，且刻意保持保守，不需要调用模型。

## 18. 模型配置边界

VoidCube CLI 是配置 Mem 的 LLM 的用户侧入口。

规范配置路径为：

```yaml
memory:
  provider: mem
  llm:
    provider: openrouter
    model: google/gemini-2.5-flash
    api_key_env: OPENROUTER_API_KEY
    base_url: https://openrouter.ai/api/v1
    provider_profile: openai
    roles:
      extraction:
        model: google/gemini-2.5-flash
      governance_summary:
        model: google/gemini-2.5-flash
      governance_reasoner:
        provider: deepseek
        model: deepseek-reasoner
```

规则：

- `memory.provider` 标识外部记忆插件，如 `mem`。
- `memory.llm.provider` 标识 Mem 使用的模型提供方。
- Mem 通过 `MemModelConfig` 解析此配置块。
- Mem 可通过 `MemModelConfigSet` 解析按角色区分的覆盖配置。
- 明确的 Mem CLI 标志仍会覆盖已保存的配置，以用于测试和实验。
- 已退役的 `memory.model` / 插件级 `memory.provider` 字段不是 LLM 配置。
- 已保存的 VoidCube 配置必须使用 `memory.llm.*` 来选择 Mem / API-B 的模型。

## 19. 下一步实施

在更多 Mem 子系统中使用按角色区分的模型配置：

- 使用 LLM 后端时，抽取已经会解析 `extraction` 角色
- 学者 / 摘要应解析 `summarization`
- 治理证据摘要应解析 `governance_summary`
- 可选的模型辅助治理应解析 `governance_reasoner`
- 嵌入 / 相似性搜索使用 `memory.semantic_recall.*` 和独立的
  `/embeddings` 协议；它不得通过聊天模型角色来解析

提供方配置和提示词包已经存在，但它们还不是完整的模型配置系统。
