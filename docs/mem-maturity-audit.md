# Mem Maturity Audit

## 1. 文档定位

本文是 Mem 接回 VoidCube 主链路前的成熟度审计。

它回答三个问题：

1. Mem 当前已经有什么。
2. Mem 距离 VoidCube 长期记忆与治理灵魂还缺什么。
3. 接回 VoidCube 自进化主链路前，必须满足哪些验收标准。

相关契约见 [mem-integration-contract.md](./mem-integration-contract.md)。

## 2. 总体判断

MemAI 已经具备一个相当完整的“时间优先记忆系统”雏形：

- `Event -> Scene -> Arc -> Epoch` 分层 schema
- 时间归一化
- 事件抽取
- scene / arc / epoch 聚合
- 压缩与 revision 设计
- query interface
- query planner
- benchmark fixtures
- provider compatibility fixtures
- soul-layer 文档

但它距离真正成为 VoidCube 的“长期记忆与治理灵魂”还差一个关键层：

**治理事件记忆层。**

目前 Mem 更偏向长期交互历史和项目叙事记忆；VoidCube 自进化还需要它稳定记录、索引、检索和复用治理事件，例如：

- body candidate review
- probe pass / fail
- body switch approval
- watch-window rollback
- boundary_defer
- execution outcome
- failure sample reuse

## 3. 已具备能力

| 能力 | 当前依据 | 判断 |
| --- | --- | --- |
| 时间优先 schema | [Mem/docs/02-schema-v1.md](../Mem/docs/02-schema-v1.md) | 已有清晰分层模型。 |
| 系统宪法 | [Mem/docs/01-system-constitution.md](../Mem/docs/01-system-constitution.md) | 已定义 evidence-first、time-first、revision-over-concealment。 |
| 查询接口 | [Mem/docs/05-query-interface.md](../Mem/docs/05-query-interface.md) | 已有 point/range/theme/active/evidence 查询形态。 |
| 查询规划 | [Mem/docs/09-query-planner.md](../Mem/docs/09-query-planner.md) | 已有 intent、step、answer strategy 和 uncertainty 设计。 |
| Soul layer 定位 | [Mem/docs/soul-layer.md](../Mem/docs/soul-layer.md) | 已把 Mem 定位为 VoidCube 的 memory / identity / governance organ。 |
| 测试与 benchmark 基础 | `Mem/tests/`、`Mem/benchmarks/` | 已有 fixtures 和 provider contract 思路。 |
| VoidCube 过渡写回 | [plugins/memory/mem/governor_bridge.py](../plugins/memory/mem/governor_bridge.py) | 已有轻量 governor history、`evolution_lineage`、`evolution_boundary`、`boundary_defer`。 |
| 治理事件模型 | [Mem/src/memai/governance.py](../Mem/src/memai/governance.py)、[Mem/docs/10-governance-event-schema.md](../Mem/docs/10-governance-event-schema.md) | 已建立 v0.1 dataclass 模型与基础序列化测试。 |
| 治理事件存储 | [Mem/src/memai/governance_repository.py](../Mem/src/memai/governance_repository.py) | 已有 append-only JSONL、按治理字段查询、按事件 ID 幂等追加的最小实现。 |
| 失败样本检索 | [Mem/src/memai/governance_repository.py](../Mem/src/memai/governance_repository.py) | 已能按 changed files、failure type、similarity keys 返回相似失败样本、匹配依据和 risk flags。 |
| 治理证据摘要 | [Mem/src/memai/governance_repository.py](../Mem/src/memai/governance_repository.py) | 已有确定性 evidence summary，可返回相关事件、risk flags、建议 posture 和 confidence。 |
| LLM provider 适配 | [Mem/src/memai/llm_client.py](../Mem/src/memai/llm_client.py)、[Mem/README.md](../Mem/README.md) | 已支持 OpenAI-compatible client、provider profiles、环境变量和 prompt packs。 |
| CLI 模型配置接线 | [Mem/src/memai/model_config.py](../Mem/src/memai/model_config.py)、[VoidCube_cli/api_config.py](../VoidCube_cli/api_config.py) | VoidCube CLI 作为配置入口，配置落到 `memory.llm.*`，Mem 负责解析并允许命令行参数覆盖。 |
| 多角色模型配置 | [Mem/src/memai/model_config.py](../Mem/src/memai/model_config.py) | 已支持 `memory.llm.roles.extraction / summarization / governance_summary / governance_reasoner / embedding` 的角色覆盖。 |

## 4. 主要缺口

### 4.1 治理事件 schema 还未正式并入 MemAI

当前 `Mem/docs/02-schema-v1.md` 主要描述 `Event / Scene / Arc / Epoch`。

VoidCube 还需要一类专门的治理事件对象：

```json
{
  "id": "gov_xxx",
  "type": "governance_event",
  "event_type": "boundary_defer",
  "task_id": "task-123",
  "body_id": "slot-B",
  "decision": "defer",
  "reason": "...",
  "git_lineage": {},
  "probe_report_ref": "...",
  "evolution_boundary": {},
  "risk_level": "medium",
  "evidence_refs": [],
  "created_at": "..."
}
```

### 4.2 治理事件索引仍是最小实现

当前已具备 JSONL 级别的最小查询能力：

- `event_type`
- `decision`
- `task_id`
- `body_id`
- `candidate_commit`
- `rollback_commit`
- `changed_file`
- `violation`
- `failure_type`
- `similarity_key`

但它还不是正式索引系统。后续仍需要：

- 时间范围查询
- source_actor 查询
- 大文件场景下的索引加速
- 损坏日志恢复
- 更严格的并发写入策略

### 4.3 失败样本复用仍是最小实现

当前已经能初步回答：

- 这个候选体改动是否和历史失败样本相似？
- 某个文件是否反复导致越界？
- 某类 probe failure 是否反复出现？

当前仍不能完整回答：

- 某个 body slot 是否有异常失败模式？
- 多个弱信号组合后是否应升高风险？
- 历史失败是否已经被后续成功修复抵消？

### 4.4 治理摘要能力不足

supervisor 需要的是紧凑证据摘要，不是原始日志。

Mem 应能生成：

```json
{
  "summary": "Similar boundary violation occurred 3 times.",
  "relevant_events": ["gov_1", "gov_2"],
  "risk_flags": ["repeated_mother_path_change"],
  "recommendation": "defer",
  "confidence": 0.86
}
```

### 4.5 写入可靠性还只是 best-effort

当前轻量写回可以失败不阻断 review，这是合理过渡。

但正式 Mem 接回前，需要：

- 写入失败记录
- 重试队列
- 幂等事件 ID
- 最终一致性检查
- 可观测告警

### 4.6 与 VoidCube 主链路的 API 边界还不够正式

未来应有稳定接口：

- `record_governance_event`
- `query_governance_events`
- `summarize_governance_context`
- `record_self_learning_conclusion`
- `query_failure_samples`

当前 `MemGovernorBridge` 可以继续作为过渡适配器，但不应成为最终接口形态。

### 4.7 Mem 模型配置接线仍需继续完善

Mem 当前已有 LLM 使用能力：

- `OpenAICompatibleLLMClient`
- provider capability profiles
- provider profile JSON 文件
- provider 行为环境变量
- prompt pack registry
- provider transport contract tests

当前已明确配置边界：

- CLI 是用户配置 Mem 大模型的入口。
- `memory.provider` 只表示记忆插件 provider，例如 `mem`。
- `memory.llm.provider` / `memory.llm.model` 表示 Mem 使用的大模型 provider / model。
- Mem 通过 `MemModelConfig` 解析 CLI 保存的配置。
- Mem 通过 `MemModelConfigSet` 解析角色覆盖。
- Mem CLI 的显式参数仍可覆盖保存配置，方便测试和临时实验。

当前已支持这些角色：

- `extraction`
- `summarization`
- `governance_summary`
- `governance_reasoner`
- `embedding`

这些配置可以继续由 CLI 设置，但语义和解析应属于 Mem 自身，不应散落在 supervisor、executor 或临时参数拼接里。

### 4.8 Mem 打包结构已经初步收口

当前实际 Python 包位于：

```text
Mem/src/memai/
```

`Mem/pyproject.toml` 使用标准 `src` 布局，根仓库 `pyproject.toml` 也已把 `Mem/src` 纳入包发现范围。

当前规则：

- MemAI 源码归属于 `Mem/src/memai/`。
- VoidCube 侧使用 `import memai`，不再使用 `agent.memai`。
- 根测试通过 `pytest.ini` 将 `Mem/src` 加入 `pythonpath`。
- `plugins/memory/mem` 暂保留一个轻量路径引导，兼容未安装 Mem 包的开发运行。

后续如果要把 Mem 完全作为独立发布模块，还需要明确版本发布与根项目依赖策略。

## 5. 接回 VoidCube 前的最低验收标准

| 编号 | 验收项 | 必须满足 |
| --- | --- | --- |
| M-01 | governance_event schema | 能表达 approve / defer / reject / rollback / boundary_defer / probe_failure / execution_outcome。 |
| M-02 | 持久化 | 已有最小 JSONL 落盘与事件 ID 幂等追加；正式接回前仍需写入失败处理。 |
| M-03 | 查询索引 | 已能按 task_id、body_id、event_type、commit、changed_files、violations 等字段查询；正式接回前仍需时间范围和大规模索引策略。 |
| M-04 | 失败样本检索 | 已能给定候选 changed_files / failure_type / similarity_keys 返回相似失败样本；后续需加入 body slot、时间衰减和修复抵消。 |
| M-05 | 证据摘要 | 已有确定性最小摘要；后续需加入更丰富的证据解释和可选模型辅助。 |
| M-06 | 写入降级 | 写入失败不阻断 executor，但必须进入 retry / failure log。 |
| M-07 | 测试夹具 | 至少覆盖成功切换、边界违规、probe 失败、watch-window rollback、重复失败样本。 |
| M-08 | VoidCube 适配层 | `MemGovernorBridge` 能切到正式 Mem 接口而不改变 supervisor 主链路语义。 |
| M-09 | 打包结构 | Mem 的 package root、pyproject、测试路径和 VoidCube 适配导入路径一致。 |
| M-10 | 模型配置 | CLI 配置入口、`memory.llm.*`、`memory.llm.roles.*` 已完成最小实现；后续需把更多 Mem 子系统实际接到对应角色。 |

## 6. 推荐实施阶段

### Phase M1: 治理事件 schema

目标：

- 在 MemAI 中新增 governance event 模型。
- 将当前 `boundary_defer`、`evolution_lineage` 字段规范化。
- 建立事件类型枚举。

交付：

- schema 文档
- pydantic/dataclass 模型
- 基础序列化测试

### Phase M2: 治理事件存储与索引

目标：

- 支持 append-only governance event log。
- 支持按关键字段查询。
- 支持幂等写入。

交付：

- repository 接口
- index 查询测试
- migration / compatibility notes

### Phase M3: 失败样本查询

目标：

- 输入 candidate changed_files / event_type。
- 返回相似失败样本。
- 给出 risk_flags。

交付：

- `query_failure_samples`
- fixtures
- ranking tests

### Phase M4: supervisor evidence summary

目标：

- 输入 self-evolution candidate。
- 输出 supervisor 可用的治理摘要。

交付：

- `summarize_governance_context`
- boundary/probe/watch-window 三类摘要测试

### Phase M5: 接回 VoidCube

目标：

- 用正式 Mem 接口替换或增强 `MemGovernorBridge`。
- 保持 supervisor / executor 主链路语义不变。

交付：

- adapter
- integration tests
- rollback/fallback policy

## 7. 当前不做的事

为了避免跑偏，当前不应做：

- 让 Mem 直接执行 body switch
- 让 self-learning 绕过 supervisor 触发 executor
- 因 Mem 未完善而改变主链路架构
- 把所有对话原文无差别塞进长期治理记忆
- 把模型生成的解释当成无证据治理事实

## 8. 下一步建议

Phase M1 和 Phase M2 已有最小闭环：

- [Mem/src/memai/governance.py](../Mem/src/memai/governance.py)
- [Mem/src/memai/governance_repository.py](../Mem/src/memai/governance_repository.py)
- [Mem/docs/10-governance-event-schema.md](../Mem/docs/10-governance-event-schema.md)
- `Mem/tests/test_governance_event_schema.py`
- `Mem/tests/test_governance_event_repository.py`

Phase M3 已有第一块最小实现：

- `GovernanceFailureSampleQuery`
- `GovernanceFailureSample`
- `GovernanceEventRepository.query_failure_samples`
- changed files / failure type / similarity keys 匹配测试

Phase M4 已有最小确定性实现：

- `GovernanceEvidenceSummary`
- `GovernanceEventRepository.summarize_governance_context`
- 相似失败样本压缩为 summary / relevant_event_ids / risk_flags / recommendation / confidence

下一步建议继续完善 Phase M5 前的模型配置接入项：

**把更多 Mem 子系统接到角色模型。**

当前 `extraction` 已接入角色解析。后续应让 summarization、governance summary、optional governance reasoner 和 embedding 分别使用自己的角色配置。

## 9. 一句话结论

Mem 已经具备时间记忆系统基础，但要成为 VoidCube 的长期记忆与治理灵魂，必须补齐治理事件 schema、索引、失败样本复用和 supervisor 证据摘要。主链路方向不变：完善 Mem，而不是让 VoidCube 绕开 Mem。
