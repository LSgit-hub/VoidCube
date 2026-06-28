# 内生驱动 LM 核心提示词设计

## 1. 当前新增了什么

当前内生驱动已经新增一层：

```text
程序感知 / 证据链汇聚
  -> evidence_packet
    -> LM 核心使命 prompt
      -> 结构化任务提案 proposals
        -> 程序校验 / 约束 / 映射
          -> EndogenousTaskCandidate
```

这意味着现在不再只是“程序自己硬编码所有任务”，而是开始允许：

- 程序负责汇聚证据
- LM 负责基于核心使命理解证据
- LM 返回结构化或类型化任务提案
- 程序负责最终边界、验证和落地

## 2. 代码位置

- Prompt builder:
  [systems/supervisor/endogenous_drive_prompts.py](/F:/My_code/Traecode/VoidCube/systems/supervisor/endogenous_drive_prompts.py)
- 配置项:
  [systems/supervisor/config_models.py](/F:/My_code/Traecode/VoidCube/systems/supervisor/config_models.py)
- 环境变量读取:
  [systems/config.py](/F:/My_code/Traecode/VoidCube/systems/config.py)
- 任务提案接入:
  [systems/supervisor/endogenous_drive.py](/F:/My_code/Traecode/VoidCube/systems/supervisor/endogenous_drive.py)

## 3. 当前配置方式

### 3.1 模型配置

LM 模型本身仍然来自 API-B / CLI `/api` 写入的：

- `memory.llm.*`
- `memory.llm.roles.governance_reasoner`

也就是说：

- 模型与 provider 配置仍然复用 Mem / Supervisor 共用的 LLM 配置源
- 任务提案默认建议使用 `governance_reasoner` role

### 3.2 Prompt 配置

当前主配置入口已经升级为一套 `cognition_charter` 结构，而不再只是单段 prompt。

主入口：

- `endogenous_drive_cognition_charter.core_mission`
- `endogenous_drive_cognition_charter.self_model_principles`
- `endogenous_drive_cognition_charter.evidence_policy`
- `endogenous_drive_cognition_charter.task_generation_policy`
- `endogenous_drive_cognition_charter.self_iteration_guardrails`
- `endogenous_drive_cognition_charter.cognitive_control_policy`

兼容入口仍保留，但更适合作为旧配置迁移来源：

- `endogenous_drive_lm_task_generation_enabled`
- `endogenous_drive_lm_task_max_candidates`
- `endogenous_drive_lm_task_model_role`
- `endogenous_drive_core_mission_prompt`
- `endogenous_drive_task_generation_principles`

同时支持环境变量覆盖：

- `SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_GENERATION_ENABLED`
- `SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_MAX_CANDIDATES`
- `SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_MODEL_ROLE`
- `SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_CORE_MISSION`
- `SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_SELF_MODEL_PRINCIPLES`
- `SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_EVIDENCE_POLICY`
- `SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_TASK_GENERATION_POLICY`
- `SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_SELF_ITERATION_GUARDRAILS`
- `SUPERVISOR_ENDOGENOUS_DRIVE_CORE_MISSION_PROMPT`
- `SUPERVISOR_ENDOGENOUS_DRIVE_TASK_GENERATION_PRINCIPLES`

其中 `...TASK_GENERATION_PRINCIPLES` 支持两种格式：

1. JSON 数组

```bash
SUPERVISOR_ENDOGENOUS_DRIVE_TASK_GENERATION_PRINCIPLES=["优先证据链","证据不足先观察"]
```

2. `||` 分隔

```bash
SUPERVISOR_ENDOGENOUS_DRIVE_TASK_GENERATION_PRINCIPLES=优先证据链||证据不足先观察
```

新的 `cognition_charter.*` 列表型环境变量也支持相同格式。

这一步的意义是把“LM 的角色设定”从一段容易漂移的自然语言说明，
提升成一套更稳定的内生认知宪章。

LM 当前看到的系统提示词结构已经不只是：

- 核心使命
- 一组泛化原则

而是更明确地区分为：

- 核心使命 `core_mission`
- 自我模型原则 `self_model_principles`
- 证据政策 `evidence_policy`
- 任务生成政策 `task_generation_policy`
- 自我迭代护栏 `self_iteration_guardrails`

这更符合“内生驱动是大脑核心中的核心”的设计方向，
因为它让 LM 在生成任务前，先明确：

- 我是谁
- 我应该如何理解自己
- 我应该如何使用证据
- 我应该如何产出任务
- 我绝对不能越过哪些边界

现在这套 charter 不再只服务于 LM prompt，也开始服务于程序自身的认知节律控制。

也就是说它已经分成两层：

- 面向 LM 的认知宪章
- 面向程序的认知控制策略

其中新增的 `cognitive_control_policy` 用来决定：

- 近期认知漂移到什么程度时，程序应主动提高 observation bias
- 最近 reference alignment 低到什么程度时，程序应主动抬高 truthfulness / throttle
- self-iteration readiness 低到什么程度时，程序应主动压低 learning expansion
- 弱证据通道 / self-understanding gaps 对认知收紧的放大步长是多少

这意味着当前的“核心使命”已经不只是：

- 告诉 LM 该如何生成任务

而是开始进一步约束：

- 程序什么时候该更谨慎
- 程序什么时候该先观察再扩张
- 程序什么时候该因为证据不足而主动收缩

这比单纯的 prompt 更接近“内生驱动是大脑核心中的核心”的设计。

现在这层程序侧认知控制又继续演化了一步：

- 不只是阈值和 boost
- 还支持语义化的认知姿态模板 `active_posture_profile`

当前默认内置了几类 posture profile：

- `balanced`
- `observe_first`
- `evidence_repair_first`
- `truthfulness_first`
- `conservative`

它们的意义不是替代全部细粒度阈值，而是在同一套底层规则上，快速切换“这颗大脑此刻更偏向哪种思维姿态”。

现在姿态选择又分成两种模式：

- `manual`
- `auto`

其中：

- `manual` 会固定使用 `active_posture_profile`
- `auto` 会让程序依据当前证据与自身状态自动选择 posture

当前 auto 模式主要参考：

- 当前 `active_sessions` 带来的服务压力
- 当前 `correction_signals`
- 最近 `reference_alignment` 的弱项数量
- 当前 `weak_or_missing_channels`
- 当前 `self_understanding_gaps`
- 最近 `proposal_drift_memory`
- 当前 `self_iteration_readiness_score`
- 当前 `dominant_constraint`

也就是说，姿态模板现在不只是“配置里的一种偏好”，而开始成为：

- 程序对自己当前应该如何思考的一阶判断

现在这层判断又进一步前推到了 LM 输入侧：

- 程序会先决定当前 `cognitive_posture`
- 然后把 posture 的 `name / selection_mode / selection_reason / multipliers`
  显式注入 `evidence_packet`
- 同时把当前 posture 的语义和任务排序要求写入 LM 的 system prompt
- LM 在生成结构化任务前，已经能知道“程序当前正在以什么认知姿态思考”

这意味着现在的链路更接近：

- 程序感知证据
- 程序先决定当前思维姿态
- LM 在该姿态下理解证据并生成结构化任务
- 程序再继续进行后续校验与约束

现在 LM 的结构化 proposal 也开始显式回填姿态遵循信息，例如：

- `posture_alignment`
- `priority_basis`

它们的意义是：

- 让 LM 说明“这个提案如何遵循当前 cognitive posture”
- 让程序在后续 materialize / scoring 时，不只看任务本身，还能看 LM 是否真的按当前姿态排序和解释了任务

例如：

- `observe_first` 会更早进入观察，放大 observation / throttle / learning suppression
- `evidence_repair_first` 会更重视引用稳定性和证据修复
- `truthfulness_first` 会更强地放大 truthfulness bias
- `conservative` 会整体更保守，优先压制扩张

因此现在的配置层开始更像：

- 核心使命：我为什么存在
- 证据政策：我如何理解依据
- 任务政策：我如何生成任务
- 认知控制策略：我在什么条件下收缩或扩张
- 认知姿态模板：我当前更像哪一种思维方式

## 4. 当前 evidence packet 包含什么

当前程序汇聚给 LM 的证据包大致包含：

- `perception`
- `world_model`
- `reflection`
- `adaptive_policy`
- `self_model_snapshot`
- `evidence_credibility_summary`
- `task_type_priors`
- `proposal_drift_memory`
- `needs`
- `intents`
- `signals`
- `memory_context`
- 最近学习标题
- 最近学习证据摘要
- 外部研究证据入口
- 当前队列中的学习 / body improvement 任务
- idle window 的 checks / idle_seconds
- shell slot 信息
- shell 身体结构画像
- 统一证据输入层 `evidence_channels`
- 研究摘要视图 `research_digest`
- 主题级关系摘要 `evidence_graph`
- 行动前认知摘要 `agenda_graph`

这意味着 LM 已经开始获得：

- 自身状态
- 治理状态
- 学习状态
- 长期记忆上下文
- 最近学习结论的证据摘要
- 来自系统外部、可配置注入的研究线索
- 当前 shell 身体的 worktree / 来源 / 结构就绪状态
- 当前约束条件
- 一个更接近正式认知输入层的统一证据组织结构
- 一份程序先行整理后的“当前自我理解摘要”
- 一份程序先行整理后的“证据可信度地图”
- 一份程序先行整理后的“任务类型先验倾向”
- 一份程序先行整理后的“跨轮次提案漂移记忆”

其中新增的 `shell_body_profile` 目前会尽量提供：

- `worktree_path`
- `body_version`
- `generation`
- `candidate_branch`
- `candidate_commit`
- `.body-origin.json` 中的来源信息
- 顶层目录画像
- 是否存在 `run_agent.py` / `config.yaml`

这一步的意义是让 LM 在决定“该继续观察、学习、复核还是提出改进”时，
不只看到抽象的 deliberation，也能看到“自己当前这具身体大概是什么样子”。

现在又新增了一层 `self_model_snapshot`。

它的定位不是替代原始证据，而是把程序已经看见的关键自我理解状态，
先压缩成一份更接近“自我模型摘要”的结构，再交给 LM。

它当前会综合：

- `reflection`
- `adaptive_policy`
- `shell_body_profile`
- 最近学习证据
- 外部研究证据
- 最近 `reference_alignment` 反馈
- `evidence_graph`
- `agenda_graph`

然后产出一份面向 LM 的程序侧摘要，包含例如：

- 当前主导约束
- 当前偏好 focus
- 当前 body profile 状态
- 当前学习产出状态
- 当前研究新鲜度
- 当前 self-iteration readiness 分数
- 当前 self-understanding gaps
- 当前 reference alignment 是否稳定
- 当前高优先 topic / unresolved gap / direction

这一步的意义是：

- 不再完全依赖 LM 自己从散乱字段临时拼出“我当前理解自己到了什么程度”
- 程序开始显式承担一部分“自我模型整理”的职责
- 为后续把内生驱动继续演化成真正的认知核心，提供一层可持续扩展的自我模型接口

现在又新增了两层程序侧判断：

- `evidence_credibility_summary`
- `task_type_priors`

其中：

- `evidence_credibility_summary` 会先总结哪些证据通道当前更可信、哪些更弱、有哪些 conflict flags，以及当前 reference alignment 大致有多稳定
- `task_type_priors` 会先根据 dominant constraint、preferred focus、self-understanding gaps、weak channels、agenda gaps 等信息，形成一份程序侧的 task type 倾向摘要

它不是替 LM 做最终决定，而是先把下面这类问题显式整理给 LM：

- 当前更像是应该先观察，还是先复核？
- 当前有没有足够条件支持 learning？
- 当前 improvement 是否仍然应该保持克制？
- 当前 maintenance 是否比探索更重要？

这一步的意义是：

- 程序不再只是“把证据扔给 LM”，而开始先做一层可审计的认知整理
- LM 的任务生成开始拥有更强的程序先验，而不只是自然语言即兴判断
- 为后续把 task_type 选择做成更稳定的认知策略层提供基础

现在又新增了一层 `proposal_drift_memory`。

它会从最近几轮 outcome 中读取 `cognitive_alignment`，总结：

- 最近 proposal 更常是 strong / partial / weak
- 最近平均 alignment score 大概是多少
- 最近是更像在 drifting，还是已经开始 correcting
- 最近最典型的跑偏理由是什么

它的意义在于：

- LM 不再只知道“当前这一轮证据够不够”
- LM 开始知道“我最近连续几轮是在往正确方向收敛，还是在重复犯同类偏差”
- 程序开始形成真正跨轮次的自我修正记忆，而不是只做单轮判断

现在这层记忆还会进一步反向影响程序侧 `task_type_priors`。

也就是说，如果最近几轮 proposal 持续表现为 drifting：

- 程序会先提高 `observation / review` 的先验权重
- 程序会先压低 `improvement` 的先验冲动
- 程序会把“先纠偏再推进”变成下一轮认知的一部分

这让内生驱动更像真正的认知闭环，而不只是：

- 看到证据
- 生成任务
- 记录结果

而是开始出现：

- 看到证据
- 生成任务
- 校验偏差
- 记住偏差
- 下一轮先根据偏差调整自己的任务倾向

当前还新增了一个统一输入层：

- `evidence_channels`
- `research_digest`
- `evidence_graph`
- `agenda_graph`

其中：

- `evidence_channels` 会把近期学习证据、shell 身体画像、外部研究证据、当前 deliberation 状态整理成 channel 列表
- `research_digest` 会把外部研究证据压缩成一个摘要视图，包括来源、主题和新鲜度提示
- `evidence_graph` 会把 item 级 `supports / contradicts` 汇总成主题节点与关系边
- `agenda_graph` 会把当前 focus、未解缺口、建议方向、活跃信号整理成行动前认知图谱摘要，并开始表达它们之间的关系边，以及 evidence -> gap 的支撑关系、direction -> task 语义映射
- 每个 channel 开始带有 `confidence`、`evidence_strength`、`conflict_flags`
- 每条 evidence item 开始带有 `confidence_score`、`novelty_score`、`source_reliability`、`supports`、`contradicts`

这让 LM 看到的开始更像“认知输入层”，而不只是一些散落字段。
也就是说，程序不仅把证据汇聚给 LM，还开始给出对证据质量的初步判断。

当前 `external_research_evidence` 是一个可配置入口，适合先接入：

- 最新研究摘要
- 架构笔记
- 外部理论线索
- 人工整理的前沿判断

它目前支持两种注入方式：

- 直接配置字符串条目
- 结构化 JSON 文件证据源

这意味着它已经不只是“几条 prompt 备注”，而开始具备正式证据源的形态。
当前仍然是通过 Supervisor 配置或环境变量注入，而不是运行时主动联网抓取。
这样先把“外部依据通道”接到认知核心，再决定后面是否接自动检索。

## 5. 当前允许 LM 返回的任务类型

当前为了安全，LM 只能返回这些 `candidate_kind`：

- `memory_maintenance`
- `truthfulness_review`
- `exploratory_learning`
- `shell_baseline_learning`
- `queue_hygiene_review`
- `body_improvement`

也就是说：

- LM 还不能随意发明全新任务类型
- 先让它在受限类型里学会“基于证据做判断”
- 程序继续负责边界控制

## 6. 当前 proposal 协议

当前 LM 不只是返回基础标题摘要，而是开始返回一个受控的 typed proposal。

每个 proposal 当前应包含：

- `title`
- `summary`
- `candidate_kind`
- `task_type`
- `rationale`
- `evidence_summary`
- `confidence`
- `risk_level`
- `evidence_level`
- `observation_required`
- `execution_mode`
- `blocking_factors`
- `referenced_evidence_nodes`
- `referenced_agenda_nodes`

其中：

- `task_type` 目前被限制为：`observation / review / learning / maintenance / improvement`
- `risk_level` 目前被限制为：`low / medium / high`
- `evidence_level` 目前被限制为：`weak / moderate / strong`
- `execution_mode` 目前被限制为：`observe_only / review_then_queue / guarded_execution`

程序会继续做二次约束：

- 如果 `risk_level=high` 或 `evidence_level=weak`
- 程序会生成额外的 `supervisor_advisory`
- 但不会直接覆盖 `llm_task_execution_mode` 这类 LM 原始认知字段

现在程序还会新增一层 `cognitive_alignment`。

这层不是传统意义上的下层门控，而是一次“LM 输出后的认知校验”。

它会把 LM proposal 再拿回来，对照当前程序侧已经整理好的：

- `task_type_priors`
- `evidence_credibility_summary`
- `self_model_snapshot`
- `reference_alignment`

然后判断这条 proposal 是否：

- 顺着当前程序判断的 task type 倾向
- 顺着当前证据可信度结构
- 顺着当前 self-understanding gaps
- 顺着当前引用对齐稳定性

它当前会输出例如：

- `score`
- `quality`
- `task_type_prior_score`
- `top_priority_task_type`
- `weak_or_missing_channels`
- `self_understanding_gaps`
- `reasons`
- `summary`

这一步的意义是：

- LM 不是“说了就算”，而是开始接受程序侧的认知一致性审视
- 程序开始形成“感知 -> 认知整理 -> LM 生成 -> 认知校验”的闭环
- 后续如果要继续演化成真正的内生大脑，这层会成为很重要的自我纠偏接口

这一步很关键，因为它把“LM 的认知判断”从纯文本标题提升成了可审计的结构化产物，
同时避免让下层门控建议反过来污染上层认知判断本身。
并且 proposal 开始显式说明：它主要引用了哪些证据节点、哪些 agenda 节点。
程序侧还会补充一层 `reference_alignment`，校验这些引用是否真的能在当前认知图谱中找到。
这层校验现在还会进一步区分：强匹配、弱匹配、部分匹配或漂移引用。

## 7. 当前实现的性质

这还是一个**第一阶段接入**，不是最终形态。

当前特点是：

- 默认关闭
- 开启后作为新分支补充到现有 candidate 生成链
- 现有规则链继续保留，作为兜底
- LM 提案会被程序再次映射成现有 `EndogenousTaskCandidate`
- LM 原始 typed judgement 与 supervisor advisory 开始分层保留

所以它更像：

> 证据驱动的 LM 任务提案层

而不是：

> 已经完全由 LM 接管内生驱动决策

## 8. 当前缺口

还没完全到位的地方主要有 4 个：

1. Prompt 配置还没有独立写回 CLI 普通配置向导
2. 虽然 proposal 已经 typed 化，但还没有形成独立的 supervisor-native 协议对象
3. evidence packet 已经有外部研究入口，但还没有形成统一的多来源研究证据协议
4. 规则链与 LM 链还没有做更细的冲突仲裁

## 9. 下一步建议

最值得继续做的是：

1. 把外部研究证据从配置/文件入口继续升级为统一的多来源证据协议
2. 给 proposal 增加程序侧仲裁层，而不是简单并入 candidate 列表
3. 把 typed proposal 提升为独立的 supervisor-native 协议对象
4. 把 prompt 配置正式接到 CLI 可编辑配置项里
