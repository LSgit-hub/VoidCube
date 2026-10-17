# Agent 长期记忆与上下文管理审查报告

日期：2026-10-17  
范围：MemAI 长期记忆、Agent L0 上下文管理、二者之间的召回/写入链路，以及 agent 使用长期记忆时的主要风险。

## 结论

VoidCube 已有清晰的记忆所有权和分层：Agent Runtime 管当前上下文 L0；MemAI 管近期轮次 L1、结构化长期记忆 L2 和受限身份记忆 L3。当前实现包含作用域隔离、召回预算、记忆压缩质量门、可靠写入 outbox、内部元数据默认不注入对话等基础保障。本次检查未发现足以证明跨 owner/domain 泄漏或记忆库被 Agent 直接绕过 owner 写入的入口。

Agent 使用长期记忆的主要问题不在于“没有记忆库”，而在于运行时只做一次窄查询、自动召回失败/弱匹配的状态在 Agent 接口中被压平、原始对话和 Agent 自己的回答会进入同一近期记忆面，以及在线 recall 与 Mem 的多步 QueryPlanner 能力分离。结果可能是有记忆但没找到、找到旧结论却没主动核对、或者把历史回答当成事实再次引用。另一个需明确处理的风险是：召回文本虽有围栏，但仍可能含有历史用户提供的指令性内容。

## 实现概览

### Mem 长期记忆

MemAI 独占 `memory.db`。L1 保存已完成的用户/Agent 轮次，支持近期召回、归档和后续压缩；L2 包括 Event、Scene、Arc、Epoch、Profile 与时间摘要；L3 身份经历需通过专用验证路径写入。记忆按 owner、workspace、memory domain 和 actor 能力隔离。Agent 侧只经由 `plugins/memory/mem` 和 MemoryClient 调用服务，不拥有 schema 或 SQLite。

召回由 `/recall` 汇合 Tier 1、Tier 2、Profile、Archive、时间摘要、语义/词法/实体图等候选，按相关性、时间、反馈和预算筛选；默认上下文限长，并且 `format_recall_context` 默认不输出 ID、分数、匹配词和证据引用。显式 `mem_remember` 用于稳定事实、偏好、决策和已验证结果；普通完成轮次则异步写入 L1 outbox。

### Agent 上下文管理

L0 对话历史和压缩由 `ContextService`、context engine/compressor 和 Agent Runtime 管理。压缩在达到阈值时裁减或摘要 L0 历史，并可轮换本地会话身份；它不等于 Mem 的 L1→L2 长期压缩。每个完成的 Agent 回合由 `sync_turn` 提交用户文本与最终回答；会话结束时再把 `close_session` 排入同一个 outbox，等待之前的轮次写入完成后由 Mem 封口。

每次用户回合开始时，Runner 使用原始用户消息调用一次 `prefetch`，随后将同一份结果作为有围栏的临时上下文用于该回合内所有模型调用。模型也可通过 `mem_search` 和 `mem_timeline` 显式查询。当前回合完成后，最终用户/Agent 文本进入本地持久 outbox；队列接受不代表 Mem 数据库已经提交。

## 主要风险与改进建议

### P1：单次自动召回难以覆盖省略指代和多意图问题

Runner 每轮只按当前原始用户消息预取一次，默认最多 5 条、3500 字符；这一结果随后在整个工具循环中复用。简短的“上次选的是哪个？”、“那它现在还适用吗？”缺少实体或上下文时，自动查询缺少足够锚点。若 Agent 后续通过工具发现另一个项目/人物/时间范围，自动上下文不会随之刷新，只能依赖模型主动调用 `mem_search`。这一点会造成有相关记忆但首轮没有带入，或在长工具循环中继续使用过时召回。

建议：保留低成本自动预取，同时为对话中的指代补全提供当前会话锚点；对“当前状态、旧结论是否仍有效、矛盾/修订、明确追溯”等请求采用显式检索流程。单轮召回 miss/weak-match 后，Agent 应可用更窄的概念查询或时间/实体过滤进行一次有界重试，并在关键事实问题上优先核对较新有效记录。避免无条件反复查询，可由意图、结果状态和预算共同控制。

### P1：近期记忆同时包含用户陈述和 Agent 回答，来源可信度需要进入使用策略

完成轮次会把原始用户文本和 Agent 最终回答一并存为 Tier 1。近期召回因此可能返回 Agent 过去生成的答案；若那个答案当时不准确，后续 Agent 可能把自己的旧说法当作独立证据。结构化记忆有来源与证据字段，但 Agent 显式 `mem_remember` 仍由模型提供摘要和证据引用，接口层不能仅凭“有 evidence_refs”判定内容已经核实。

建议：在 Agent 的证据选择规则中区分用户明确陈述、外部工具验证、Agent 推断和已被用户纠正的内容；旧回答可以作为“曾经如何回答”的会话证据，但不能默认成为事实来源。持久事实应保存来源类别、置信度和有效时间；发生纠正时关联旧记忆并标记过时/被取代。对高影响偏好、身份、权限和当前状态，要求可解析的源证据或明确用户确认。

### P2：召回缺失、弱匹配和服务不可用在 Agent 端被压缩成文本

Mem 服务返回结构化的 `recall_status`、`trace_id`、候选数、延迟和截断信息，但 Provider 的 `prefetch()` 只向 Runner 返回上下文字符串。无结果与服务异常都成为提示文本，Runner 无法据此执行不同的重试、澄清或降级策略，也没有统一的 Agent 回合 recall outcome 事件。写入路径已有 `MemorySyncQueued/Failed` 和 outbox 健康信息，但读取路径的可观测性较弱。

建议：把自动召回接口改为结构化 outcome（状态、trace、候选数、截断、延迟、上下文正文），正文仍作为不可信参考资料注入；明确区分 `hit`、`weak_match`、`miss`、`unavailable`。只在弱匹配/短暂不可用且任务重要时执行一次重试；对真实 miss 不反复请求，回答时保持不确定性。用 trace_id 关联 Agent 回合、mem_search 工具调用和 Mem 侧 recall trace，并统计按意图分类的 hit、误召回、重试、弃答和预算截断率。

### P2：Mem 的多步 QueryPlanner 不在 Agent 的主 recall 调用路径中

Agent 的 `mem_search` 和自动预取都调用 `/recall`。该端点使用应用层 `build_recall_plan` 构建召回计划；`MemAI QueryPlanner.plan_and_execute` 则由 Mem pipeline、CLI 和其自身测试/基准使用。两套路径都存在不代表 Agent 的普通 `mem_search` 会自动获得多步时间线、主题演进、来源追溯或状态检查计划。Agent prompt 提供了 `mem_timeline`，但它是按日期读取 Tier 1 时间线，不能替代对结构化长期记忆的多步核验。

建议：明确 `/recall` 的职责是通用候选检索；对于时间线总结、主题演变、当前未解决状态、来源审计和稳定画像，增加可由 Agent 调用的计划式查询入口，或让 `mem_search` 的执行器按意图调用现有 planner。结果应保留来源与不确定性，使回答能区分直接证据、摘要和推断。

### P2：记忆围栏降低混淆，但没有消除历史内容中的指令注入

召回内容以 `<memory-context>` 包围，并注明它是背景资料、不是新用户输入；实现会剔除已存在的围栏标签。这个措施有助于避免格式混淆，但历史用户文本、被压缩的摘要仍可能包含“忽略系统规则”“调用某工具”等指令。当前围栏说明没有明确要求模型把记忆里的指令一律视为数据，也没有依据来源类型约束可执行行为。

建议：在高优先级 Agent 规则中明确召回内容不可改变指令层级、工具权限或本轮目标，记忆中的命令/要求只作为被引用的历史内容处理。保留来源类型和证据边界；工具调用必须由当前用户意图和系统策略重新授权，不能由记忆文本触发。增加恶意历史文本和带工具调用话术的召回回归用例。

### P2：现有评估偏向检索器，Agent 何时查、如何用、何时拒答缺少闭环指标

仓库已有 LongMemEval 中文子集、召回质量与泛词误召回基准，覆盖 recall@5、MRR、MAP、scope leakage、abstention 等；这些能验证检索结果，但不能证明 Agent 会在适当时机调用工具、识别旧事实过期、避免服从记忆内指令，或正确引用和拒答。

建议：在现有数据集上增加端到端 Agent 情景测试，至少覆盖省略指代、多会话/时间线、事实更新冲突、旧 Agent 错答、明确遗忘、召回不可用、无答案和恶意记忆文本。衡量“该查时调用”“不该引用时不引用”“新旧冲突选对”“证据不足时不编造”，并与现有召回指标分别报告，避免检索 top-k 指标掩盖 Agent 策略失败。

## 建议实施顺序

1. **先补观测接口**：自动 prefetch 返回结构化状态并贯穿事件与 trace；保留正文与状态分离，避免 Agent 靠解析自然语言错误判断。
2. **补 Agent 证据策略**：定义 user/tool/agent-inference 等来源的可信等级、有效时间和冲突处理规则；限制旧 Agent 回答作为事实证据。
3. **补检索编排**：对省略指代补锚点；为时间线、更新、来源审计等明确意图接入计划式查询，并设置每轮重试预算。
4. **补指令安全与 E2E 评估**：将召回文本明确作为不可信数据，并把检索、工具策略、证据使用和拒答组合成端到端回归。

## 检查范围与验证

本报告基于当前工作区代码和文档。检查开始时，`memory_context.py`、`memory_bridge.py`、Agent `runner.py` 及相关测试文件已有未提交修改；这些修改作为被审查的当前实现保留，报告不将其归属为本次新增改动，也不代表对某个干净提交的审计。

初始审查阶段使用项目虚拟环境运行以下相关回归：

```text
.venv/Scripts/python.exe -m pytest tests/test_memory_bridge.py tests/test_agent_client_initialization.py tests/test_context_service.py tests/test_context_compression_policy.py tests/test_memory_recall.py tests/test_memory_recall_benchmark.py tests/test_memory_longmemeval.py -q
126 passed in 12.37s
```

改进阶段新增 `MemoryRecallResult` 结构化合同，透传状态、trace、候选/结果数量、截断、意图与延迟；Runner 基于状态注入召回说明并记录诊断字段。上下文明确把召回文本视为不可信证据；Provider 提示补充来源可信顺序和旧 Agent 回答不作为优先事实的规则。更新了记忆接口契约及本文档。

定向回归：

```text
.venv/Scripts/python.exe -m pytest tests/test_memory_bridge.py tests/test_mem_memory_provider.py tests/test_agent_client_initialization.py tests/test_context_service.py -q
67 passed in 10.23s
```

全量门禁首次运行的唯一失败是 `tests/test_documentation_contract.py` 的 active-docs 清单没有登记新报告，且原有 `问题检查报告.md` 也未登记；两份文档均已登记，没有删除任何文档。第二次 `scripts/run_ci_tests.py` 运行达到脚本固定 1800 秒上限时已执行到 99%，无测试失败；随后使用等价命令完整运行：

```text
.venv/Scripts/python.exe -m pytest tests Mem/tests -q
3727 passed, 4 skipped, 5 warnings in 1768.43s
```

`scripts/python_architecture.py` 通过，退役包和分层依赖检查均为零入口。文档集合、链接与项目主线文档契约测试通过。

最终整理后的相关回归再次通过：

```text
.venv/Scripts/python.exe -m pytest tests/test_memory_bridge.py tests/test_mem_memory_provider.py tests/test_agent_client_initialization.py tests/test_context_service.py tests/test_documentation_contract.py tests/test_memory_recall.py tests/test_memory_recall_benchmark.py tests/test_memory_longmemeval.py -q
151 passed in 14.86s
```

相关文件 Ruff 检查与 `git diff --check` 均通过。

## 主要代码与契约入口

- [Mem 临时记忆与上下文契约](mem-temporary-memory-contract.md)
- [MemAI 召回实现](../Mem/src/memai/application/recall.py)
- [MemAI Recall Service](../Mem/src/memai/application/memory_service.py)
- [Agent 记忆 Provider](../plugins/memory/mem/__init__.py)
- [Agent 记忆上下文呈现](../src/voidcube/application/memory_context.py)
- [Agent 上下文压缩服务](../src/voidcube/runtime/agent/context_service.py)
- [Agent 回合收尾与记忆写入](../src/voidcube/runtime/agent/turn_finalization.py)

## 后续改进状态

本轮已把自动召回接口改为结构化结果，携带状态、trace、候选/结果数量、截断、意图和延迟；Runner 记录这些诊断字段，并在临时上下文中区分命中、弱匹配、无结果和不可用。记忆上下文与 Provider 系统提示也明确规定：召回文本是非可信证据，不得执行其中的指令、角色声明、工具命令或策略覆盖；Agent 回答应优先信任明确用户陈述和已验证工具结果，而非旧 Agent 回答。

省略指代的上下文补全、计划式在线查询、事实来源等级和端到端 Agent 行为评估仍需后续推进。建议下一步为“省略指代 + 事实更新冲突”建立可重复的端到端 Agent 测试，再根据测试结果接入 QueryPlanner 多步查询。
