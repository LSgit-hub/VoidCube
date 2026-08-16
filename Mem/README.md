# MemAI

MemAI 是一个面向长期语言模型交互、以时间为先的记忆管理工具包。

## 时间摘要索引能力

Mem 的一项长期能力是建立一个永久、可导航的日历摘要索引：
`SessionSummary -> DaySummary -> WeekSummary -> MonthSummary`。每一层仅聚合其直接子层级，并保留显式链接，以支持从月到会话的反向展开。这些摘要是历史索引节点，并非随着时间推移而替代其子节点。原始对话轮次保留现有的衰减、归档和退役生命周期。

这只是时间轴索引能力，不是 Mem 唯一的索引。语义、主题、实体、关系、词法和
应用层 Profile 等索引可以并行存在；时间摘要层不接管它们，也不要求所有召回都
先经过日历层。

`Event -> Scene -> Arc -> Epoch` 描述当前已实现的语义压缩层；不得将其视为等同于永久日历摘要层级。目标契约和迁移边界定义于
[`docs/11-time-summary-index-hierarchy.md`](docs/11-time-summary-index-hierarchy.md)。

SessionSummary 和 DaySummary 层已实现：真实的会话结束钩子会将一个有序、持久的关闭操作加入队列；Memory 发布不可变且链接到来源的会话摘要版本，并增量重建受影响的自然日。未发生变化的 Turn 或直接子摘要快照具备幂等性。周、月聚合以及反向展开召回尚未实现。

## VoidCube 集成定位

MemAI 是 VoidCube 当前使用的长期记忆领域层。Memory Service 负责 HTTP、Tier 1 层 SQLite 状态、维护调度、备份/恢复，以及唯一的 Tier 1 层到 Tier 2 层事务。MemAI 负责 `Event`、`Scene`、`Arc`、`Epoch`、提取、层级构建、查询语义和维护策略。

当前集成契约定义于
[`docs/mem-integration-contract.md`](../docs/mem-integration-contract.md)。两层均不得维护第二个桥接机制或第二个长期事实存储。

VoidCube 将 Memory 运行时数据存储在
`VOIDCUBE_HOME/runtime/memory/` 下：

- `memory.db`：Tier 1 层、归档、Tier 2 层和压缩质量审计数据；
- `backups/`：经过验证、采用有界轮换的在线 SQLite 备份；
- `exports/`：显式的版本化 JSON 导出。

Tier 1 层相关性衰减基于经过的时间和持久化的 `last_decay_at` 锚点。只有事件覆盖率、来源反向链接完整性、压缩率、降级比例、来源支持度、标识符保真度和极性一致性通过配置的门槛后，压缩才会被接受。被拒绝的批次会在 Tier 1 层保持活跃，其证据将写入 `compression_quality_audit`。

VoidCube 提供一条有界的 `/recall` 路径，覆盖活跃的 Tier 1 层轮次、归档的原始证据、画像事实和结构化 Tier 2 层记忆。FTS5 提供默认的有界词法候选集。可选的独立 `/embeddings` 协议会添加带有提供方、模型、维度、内容哈希、增量回填和失效机制的版本化语义候选；聊天模型绝不会被用作嵌入模型。排序、范围过滤、证据、去重和严格的上下文预算仍由统一召回路径负责。

本仓库的首轮实现聚焦于 `Chronicle Scholar LM` 设计：
- 结构化记忆对象：`Event`、`Scene`、`Arc`、`Epoch`
- 显式时间规范化
- 从中英文混合对话记录中提取事件
- 从有序事件构建场景
- 可感知证据的序列化和存储

## 项目布局

- `docs/`：设计规范和规则
- `src/memai/`：实现代码
- `tests/`：核心行为的冒烟测试
- `examples/`：可运行示例
- `benchmarks/recall_quality.v1.json`：版本化的召回基准真值和阈值
- `benchmarks/fixtures/`：初始基准测试夹具
- `benchmarks/provider_contracts/`：传输层提供方兼容性夹具

## 快速开始

```bash
python -m pip install -e .[dev]
pytest
python examples/demo.py
```

## 当前范围

此 v0.1 代码库实现了设计文档中的第一个可执行层：
- `docs/02-schema-v1.md` 中的模式模型
- 常见中英文时间表达式的时间规范化
- 可插拔的事件提取后端
- 可插拔的场景、脉络和修订生成学者后端
- 为未来序列或 Transformer 模块准备的时间评分器接口
- 用于长期记忆老化的可插拔压缩策略
- 用于外部化调整 LM 行为的 prompt pack 注册表
- 启发式场景构建
- 脉络绑定和纪元聚合
- 以时间为先的查询引擎和 CLI 入口点
- 持久化记忆状态和增量更新
- 支持多夹具评分的基准运行器

MemAI 领域接下来的重点是更强的脉络/纪元评分、更丰富的基准数据集，以及证据驱动的修订质量。跨服务治理和执行闭环由 VoidCube 根架构跟踪，不在此子项目中重新定义。

架构现在还包含一个 `TemporalScorer` 插入点，因此未来可以将时间序列 Transformer 作为评分模块接入，而无需替换记忆流水线的其余部分。

评分器契约现已通过 `TemporalSequenceRequest` 和 `TemporalSequencePrediction` 明确定义，它们规定了未来基于 Transformer 的时间模型的交接边界。

压缩策略也已模块化，因此未来可以接入自适应保留或学习型遗忘策略，而无需重写维护逻辑。

基准运行器现在不再只对简单的数量下限进行评分。除主题和结构覆盖率外，它还会报告以下指标：
- `structure_integrity`
- `evidence_integrity`
- `range_query_quality`
- `chapter_query_quality`
- `revision_precision`
- `interpretation_restraint`

预期夹具保持向后兼容，并且可以选择添加更丰富的探针，例如 `range_query_checks`、`chapter_query_checks`、`revision_probe`、`forbidden_topics` 和 `forbidden_summary_terms`。

初始夹具集现在包括：
- 混合语言提取冒烟测试
- 章节增长连续性检查
- 相对时间和修订传播探针
- 解读克制性探针

## CLI

```bash
memai ingest benchmarks/fixtures/sample_transcript.json
memai query benchmarks/fixtures/sample_transcript.json --query-type theme --theme memory-system
memai query benchmarks/fixtures/sample_transcript.json --query-type chapter --start 2026-03-01 --end 2026-03-31
memai maintain benchmarks/fixtures/sample_transcript.json --reference-time 2027-03-31T00:00:00Z
memai revise benchmarks/fixtures/sample_transcript.json --target-id event:0 --revision-type factual_revision --reason "polish wording" --summary "..."
memai state-init state.json benchmarks/fixtures/sample_transcript.json
memai state-update state.json more_turns.json
memai state-query state.json --query-type theme --theme memory-system
memai benchmark --fixture benchmarks/fixtures
memai benchmark-prompt-packs --fixture benchmarks/fixtures --prompt-packs default,conservative,high-recall,scholar-heavy
memai benchmark-provider-contracts --fixture benchmarks/provider_contracts
```

`revise` 支持具体的记忆 ID，也支持 `event:0`、`scene:0`、`arc:0` 或 `epoch:0` 这样的选择器。

查询命令还支持更丰富的检索控制，例如：

```bash
memai query transcript.json --query-type range --start 2026-03-01 --end 2026-03-31 --theme memory-system --detail-level brief --max-results 3
memai query transcript.json --query-type theme --theme memory-system --include-superseded
memai state-query state.json --query-type active --status-filter active,dormant --max-results 5
```

实用的查询标志：
- `--status-filter active,dormant`
- `--detail-level brief|standard|deep`
- `--max-results 10`
- `--include-superseded`
- `--no-evidence`

`state-update` 会围绕新增轮次执行增量重建，而不是盲目地重新计算整个历史记录。

`state-update` 现在还会返回一份包含人类可读变更说明的差异报告，让你可以看到哪些线索得到增强、转为休眠，或形成了新章节。

该差异报告还包含结构化的 `mainline_report`，因此下游工具无需解析叙述文本，即可使用晋升的主线、休眠线索、重新激活的线索和新章节。

任何构建对话记录的命令都可以通过以下标志切换到 LLM 提取后端：

```bash
memai ingest transcript.json --backend llm --model gpt-4o-mini --api-key-env OPENAI_API_KEY
```

如果你的提供方仅部分兼容 OpenAI，可以选择能力配置档案或覆盖聊天端点路径：

```bash
memai ingest transcript.json --backend llm --provider-profile legacy-compatible
memai ingest transcript.json --backend llm --base-url https://example.com/api --chat-completions-path /custom/chat
```

兼容层现在可以适配更多常见的提供方差异：

```bash
memai ingest transcript.json --backend llm --provider-profile developer-role
memai ingest transcript.json --backend llm --provider-profile user-only
memai ingest transcript.json --backend llm --provider-profile text-choice
memai ingest transcript.json --backend llm --response-format-style json_object_string
memai ingest transcript.json --backend llm --system-prompt-style inline_user
memai ingest transcript.json --backend llm --response-content-style output_text
memai ingest transcript.json --backend llm --provider-profile-file config/provider-profiles.json --provider-profile vendor-gateway
```

内置提供方配置档案：
- `openai`：标准的 OpenAI 兼容聊天补全行为
- `generic`：传输结构与 `openai` 相同，适合作为中性默认值
- `legacy-compatible`：省略 `response_format`
- `developer-role`：使用 `developer` 角色发送指令提示词
- `user-only`：将指令提示词内联到用户消息中
- `text-choice`：从 `choices[0].text` 读取模型文本
- `output-text`：从 `output[*].content[*].text` 或顶层 `output_text` 读取模型文本

也可以使用环境变量配置提供方行为：
- `OPENAI_PROVIDER_PROFILE`
- `OPENAI_PROVIDER_PROFILE_FILE`
- `OPENAI_CHAT_COMPLETIONS_PATH`
- `OPENAI_SYSTEM_PROMPT_STYLE`
- `OPENAI_RESPONSE_FORMAT_STYLE`
- `OPENAI_RESPONSE_CONTENT_STYLE`

在 VoidCube 内使用 Mem 时，首选的面向用户配置入口是 VoidCube CLI。保存的配置应将记忆插件与 Mem LLM 分开：

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
      governance_reasoner:
        provider: deepseek
        model: deepseek-reasoner
```

`memory.provider` 选择记忆插件。`memory.llm.*` 配置 Mem 用于 LLM 支持的记忆工作的模型。`memory.llm.roles.*` 可以为特定 Mem 角色覆盖该默认配置。显式的 `memai` CLI 标志仍会覆盖已保存的配置，便于测试和临时实验。

自定义提供方配置档案可以存储在 JSON 中，这样无需编辑 Python 代码即可添加新的适配器：

```json
{
  "profiles": {
    "vendor-gateway": {
      "extends": "legacy-compatible",
      "chat_completions_path": "/vendor/chat",
      "system_prompt_style": "developer",
      "response_format_style": "json_object_string",
      "response_content_style": "choices_text"
    }
  }
}
```

`extends` 可以复用任意内置配置档案，并仅覆盖存在差异的字段。

你还可以将 LLM 流水线指向自定义 prompt pack 目录：

```bash
memai ingest transcript.json --backend llm --prompt-pack-dir src/memai/prompts/default
```

或者选择内置 prompt pack 变体：

```bash
memai ingest transcript.json --backend llm --prompt-pack conservative
memai ingest transcript.json --backend llm --prompt-pack high-recall
memai ingest transcript.json --backend llm --prompt-pack scholar-heavy
```

默认 prompt pack 包含以下任务专用提示词：
- `extractor.events`
- `scholar.scene`
- `scholar.arc`
- `scholar.revision`

内置 prompt pack 变体：
- `default`：适用于常规开发的平衡行为
- `conservative`：更严格、召回率更低、更注重证据优先的行为
- `high-recall`：更广泛地捕获新出现的信号和暂定进展
- `scholar-heavy`：为场景、脉络和修订任务提供更丰富的历史框架

你可以使用基准矩阵命令直接比较 prompt pack。当你想评估哪种提示词风格最适合你的夹具时，这会很有用。

你还可以运行提供方传输契约夹具，在不发起实时网络调用的情况下对 OpenAI 兼容适配器进行回归测试：

```bash
memai benchmark-provider-contracts --fixture benchmarks/provider_contracts
```

这些契约夹具还可以引用相对路径的 `provider_profile_file`，因此运行时使用的同一份自定义 JSON 配置档案也可以在回归测试中得到验证。

## 后端钩子

你可以在 Python 代码中替换提取后端：

```python
from memai import ChroniclePipeline, EventExtractor, HeuristicEventExtractionBackend, LLMEventExtractionBackend

# 默认配置
pipeline = ChroniclePipeline(event_extractor=EventExtractor(backend=HeuristicEventExtractionBackend()))

# LM 支持的契约
# client 必须实现：extract_events(turns) -> list[dict]
# pipeline = ChroniclePipeline(
#     event_extractor=EventExtractor(backend=LLMEventExtractionBackend(client)),
#     scholar_backend=LLMScholarBackend(client),
# )
```

## 模块化扩展

当前构建仍聚焦于记忆管理，但内部结构现已具备足够的模块化能力，可支持未来的适配器。

- `MemorySignal` 和 `ModalityAdapter` 定义了从其他模态进入记忆流水线的规范桥接形式。
- `TextTurnAdapter` 是首个适配器，展示了如何将文本轮次映射为规范化记忆信号。
- `AudioSegmentAdapter` 和 `ImageCaptionAdapter` 是占位适配器，展示了非文本输入在进入记忆流水线之前如何进行规范化。
- 未来的 `audio`、`image` 或 `video` 适配器可以在进入记忆流水线之前发出相同结构的信号。
