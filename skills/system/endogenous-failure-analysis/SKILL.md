---
name: endogenous-failure-analysis
description: 系统化诊断 VoidCube 内生认知管道中的信号退化与自我锁死循环。当某个内生方向（grounding、governance、body_eligibility 等）学习未改善交付效果时使用。核心方法：从退化的信号标签反向追踪数据流，识别根因（证据不足/引用对齐漂移/假设错误/执行偏移），输出结构化审查报告。
---

# 内生管道失败模式分析

## 触发条件
- "审查 [component] 方向近期学习未改善交付效果的失败模式"
- `grounding:hurt` / `recent_learning_quality=0.0` 调查
- 任何内生信号退化调查（governance, body_eligibility 等）
- 用户问 "为什么 X 不改善" 在内生认知环路中

## 方法：信号追踪诊断模式

### 阶段 1：识别退化信号
1. 用 `mem_search` 搜索失败标签（如 `grounding:hurt`, `recent_learning_quality`）
2. 用 `search_files` 定位生成该标签的**源函数**
3. 确定产生标签的函数 — 这是测量点

### 阶段 2：追踪上游 — 什么输入驱动测量？
4. 从测量函数反向追踪其输入参数
5. 检查每个输入是否派生自其他系统或原始数据
6. 映射完整数据流：Source → Intermediate → Measurement → Signal

### 阶段 3：识别阈值和门控逻辑
7. 提取所有控制信号值的数值阈值（>=, <, ==）
8. 检查不对称性："好" 是否需要比 "坏" 更多的条件？
9. 检查覆盖逻辑：任何条件是否无条件强制输出坏结果？
10. 检查死区：既不产生好也不产生坏的值域

### 阶段 4：测试固件交叉验证
11. 搜索测试中该信号名称以找到预期的行为契约
12. 注意哪些场景被测试，哪些未测试（如 `grounding:helped` 有测试但 `grounding:hurt` 可能没有）
13. 反向运行测试断言以理解什么输入产生什么输出

### 阶段 5：根因分类
将每个发现归入以下四类之一：

| 类别 | 定义 | 诊断信号 |
|------|------|----------|
| 证据不足 (Evidence Insufficiency) | 输入数据管线为空或过期；任务无材可用 | 空列表、缺失节点、零长度数组 |
| 引用对齐漂移 (Reference Alignment Drift) | 对齐测量触发纠正但对其改善不敏感 | 单一弱匹配触发完整修复姿态 |
| 假设错误 (Hypothesis Error) | 自迭代假设自指或循环 | 假设所需的恰是其试图修复的条件 |
| 执行偏移 (Execution Drift) | 任务以一种类型生成但以另一种类型测量/注册 | 任务类型不匹配，学习任务未注册为 completed_learning_tasks |

### 阶段 6：反馈环路检测
14. 追踪退化信号是否**反馈**到生成它的机制中
15. 检查："检测到失败 → 纠正行动 → 行动失败 → 再次检测到失败" 是否形成正反馈循环？
16. 识别锁死条件：什么必须为真才能打破循环？

### 阶段 7：stay/switch 锁死分析（当用户需要 switch 决策时）
17. 读取 `build_self_iteration_trend_memory` 获取 `trend_state` (`consolidating` 需 2+ 同目标, `locked` 需 3+ 同目标且 stability=stable)
18. 读取 `build_self_iteration_hypotheses` 追踪趋势惯性: 当 `trend_state="locked"` 时趋势假设获 +0.10 优先级加成 — 系统设计上偏 stay
19. 读取 `build_switch_self_regulation_memory`: 检查 `preferred_switch_bias` — 若 switch_quality_scores 为空，默认偏 `"stay"` (行 224-227)，无历史 switch 数据时无法触发方向切换
20. 构建三层锁死模型: (a) 目标假设优先级最高 (b) 趋势惯性助推 stay (c) switch 调节器默认偏 stay
21. 判定唯一逃生条件是否在当前状态下可达成；若为否，结论应为 SWITCH

## 关键文件速查

| 信号 | 源文件 | 函数 |
|------|--------|------|
| `dominant_target_effect`（如 "grounding:hurt"） | `endogenous_cognitive_memory.py` | `build_post_task_effect_memory` |
| `recent_learning_quality` | `endogenous_reflection.py` | `build_reflection_projection` |
| `grounding_gaps` | `endogenous_context.py` | `reference_alignment_gap_labels` |
| `self_iteration_hypotheses` | `endogenous_self_iteration.py` | `build_self_iteration_hypotheses` |
| `grounding_focus` | `endogenous_lm_evidence.py` | `build_grounding_focus` |
| `cognitive_assessment`（LM 输出） | `endogenous_cognitive_memory.py` | `build_cognitive_assessment_memory` |
| 提案漂移 | `endogenous_meta_cognition.py` | `build_proposal_drift_memory` |
| 最近引用对齐 | `endogenous_self_model.py` | `build_recent_reference_alignment` |

`build/lib/systems/supervisor/` 和 `systems/supervisor/` 都包含副本 —— 两者都检查但以 `systems/`（非 build）作为主要真相来源。

## 输出格式

交付结构化审查报告，包含：
1. **核心机制追踪**：信号如何生成，附代码引用和阈值
2. **四个根因分析**：每个发现附严重性评分（★1-5）和决定性特征
3. **总结矩阵**：失败模式、严重性、决定性特征总表
4. **根因归属**：每个发现映射到哪个根因类别（证据/对齐/假设/偏移）
5. **建议**：按紧急程度分层 — 立即止血、中期重构、长期改进

## 已知陷阱
- `build/lib/` 和 `systems/` 目录含近相同代码；始终交叉引用两者
- session dump 不可用时，测试是唯一的行为真相
- `"hurt"` 标签通常通过**覆盖**逻辑应用，而非主要分类 — 检查后置覆盖
- `search_files` 搜索字面字符串如 `"grounding:hurt"` 可能返回 0 结果，因为字符串通过 f-string 拼接构建；改为搜索组件部分
- `recent_learning_quality = 0.0` 可能来自空学习任务**或**零值 perception.learning_quality — 检查两条路径
- **Meta 递归陷阱（★★★）**: 内生失败分析任务本身是 "review" 类型，不会写入 `completed_learning_tasks`。即使分析完美，`recent_learning_quality` 也不会改善。这是根因 D（执行偏移）在元层面的复现 — 分析诊断偏移的任务自身也被偏移所困。这意味着做更多分析不会改善度量指标。
- **元自指陷阱（★★★）**: `build_self_iteration_hypotheses` 是纯算法函数，只看 `grounding_gaps` 和 `reference_alignment_score` 两个数值，不读任何 cognitive assessment 输出。即使分析正确识别了假设自指悖论，假设生成器仍会继续产出相同的 grounding 假设。**"理解自指不能打破自指"** — 这是分析无法改善结果的深层原因。
- **不要再运行一次（反模式）**: 当内生失败分析已产出 SWITCH 结论后，对同一方向再做一次失败分析期望不同结果是徒劳的。锁在代码里，不在理解里。如果三层锁死环未被代码修补，再次分析只会确认同样的锁死。应直接推动方向切换或代码修复。
- **search_files 在 Windows 路径失效**: `search_files` content 模式在 `F:\...` 等 Windows 路径上恒返回 0 结果（即使模式确定存在）。改用 terminal `grep`/`find`。运行数据 JSON 位于 `~/.VoidCube/runtime/supervisor/`（git-bash 路径 `/c/Users/<user>/.VoidCube/...`）。
- **运行数据真相优先**: 内生认知漂移证据以 `endogenous_drive_history.json`（240 条 outcomes）与 `endogenous_cognition_state.json` 为准，而非仓库测试快照。用 Counter 统计全量 outcomes 的 `weak_agenda_nodes`/`missing_evidence_nodes` 分布，量化漂移频率（如 stabilize_memory_continuity=63 次居首）。
- **恒定 quality_score 先查评分器**: 多条同构任务 quality 全部相同（如全 0.5）时，先检查 `assess_autonomous_learning_quality`（autonomous_learning_quality.py）是否确定性饱和，再检查统计窗口是否被 schema 不一致跳过，不要直接判定"执行质量差"。
- **双证据标准**: 断裂点结论必须同时附代码位置（文件:行 + 阈值）与运行数据计数（JSON 分布），否则交接给修复 Agent 无法复测。
- **Auto 员工结论不得 media_display**: research/self_learning 角色执行时 media_display 返回 `autonomous_employee_delivery_forbidden` —— Auto 员工结果必须落盘 `~/.VoidCube/runtime/supervisor/self-learning/conclusions/` 并在回复末尾附 Subagent Output Contract JSON；media_display 仅日常 Assist 模式可用。被拒不是错误，勿重试。
- **上下文压缩后必复核代码行号**: 长会话压缩会丢失先前验证的精确行号/阈值，交接前用 `sed -n`/`grep -n` 重新确认评分调用点、outcome 构建处与阈值，避免报告引用过时行号（本技能第 4/8 条的行号即来自压缩后复核）。

## 已知根因模式（grounding 方向）

来源：2026-08-05 grounding 审查中发现的四个系统性问题：

1. **reference 一票否决**（严重性 ★★★★★）：`build_post_task_effect_memory` 中 "hurt" 是后置覆盖，即使 quality 高，reference < 0.4 也判 hurt
2. **证据管线空洞**（★★★★）：`build_grounding_focus` 不检查 evidence_graph.nodes 是否为空，LM 对空集做 grounding
3. **假设自指悖论**（★★★★）："先修补 evidence-to-agenda grounding" 需要 grounding 本身作为前提
4. **执行偏移**（★★★）：grounding 任务不注册为 learning_tasks，导致 `recent_learning_quality` 永远无法从 0 上升

## 已知根因模式（proposal_selection / evidence-to-agenda 方向）

来源：2026-08-22 proposal_selection 引用漂移审查（task 3a9bd5c2），四断裂点均代码+运行数据双证据：

1. **evidence_graph 通道缺失再生循环**（★★★★★）：`endogenous_lm_evidence.py` `build_evidence_graph` 只消费 recent_learning/external_research/shell_body_profile 三源，从不消费 `deliberation_state` 通道 → LLM 从 `primary_grounding_gaps` 得知缺失并主动讨论 → 讨论又落入 `missing_evidence_nodes`（运行数据 240 条中 10 次居首）→ 缺口再生。**缺失即漂移源：证据图上不存在的节点被持续"引用"（讨论），形成 grounding 缺口自锁。** 修复方向：补通道消费，或把"仅出现在 missing 讨论中"的节点降级为 weak_evidence_nodes。
2. **绝对阈值 vs 结构性低优先级耦合**（★★★★★）：`endogenous_proposals.py:509` `align_lm_references` weak_agenda 判定 `priority < 0.45`（绝对阈值），而 `stabilize_memory_continuity` 议程节点结构性 priority=0.4212 → 只要 LLM 引用当前焦点就必判 weak（240 条中 63 次落入 weak_agenda_nodes，绝对首位）→ alignment_quality 恒 weak → reference_alignment_is_unstable 恒成立。**审查基准本身被固定偏置污染："引用即弱"。** 修复方向：相对阈值（如 0.6*max_priority）或焦点节点豁免 weak 判定。**审查前先验证阈值相对性：检查 agenda 节点 priority 是否落在 [阈值-0.05, 阈值) 死区。**
3. **schema 不一致静默跳过**（★★★★）：outcome 落盘路径不同导致字段缺失 — 240 条中 231 条有 `llm_cognitive_assessment`、仅 162 条有 `reference_alignment`（69 条缺口）。`build_recent_reference_alignment`（endogenous_self_model.py:17）无 reference_alignment 则静默跳过（最近12条窗口10/12有效），但 `normalize_recent_learning_evidence`（endogenous_evidence.py:187）仍以 `source_reliability=0.84`、`supports=["self_understanding","learning_trace"]` 标记这些无绑定记录 → **无绑定记录被当作高可靠证据**，引用绑定不真实。修复方向：统一 schema 或显式输出 skipped_count，skipped>30% 时标记 degraded。
4. **确定性评分器饱和**（★★★★）：`autonomous_learning_quality.py` `assess_autonomous_learning_quality` 是纯规则评分（基线 0.15 + 长度/证据/工具痕迹奖励），不评估引用绑定真实性与结论新颖性 → 同构 review 任务恒评 0.5（运行数据 31 条有分记录全部 0.5，08-09~08-22 两周 26→28→31 持续饱和）→ recent_learning_quality=0.5 被下游误读为"执行效果不佳"（blocking_factor），实为评分饱和。**恒定分数 ≠ 退化信号；先查评分器是否无区分度。**
   **★ 调用点截断机制（2026-08-22 3dbcbeb3 机制级定位）**: 唯一评分调用点 `autonomous_task_review_service.py:178` 只传 `{**decision_context, "response": final_response}`，decision_context 无 tools_used/source_urls → 评分器 `observation.get("tools_used")` 恒空 → 非 exploratory 分支工具加分（+0.25 本地工具 / +0.10 双工具 / +0.15 web 源）**永久不可达**。0.5 精确组成 = 0.15(completed_turn) + 0.15(≥120字) + 0.10(≥400字) + 0.05(≥1000字) + 0.05(证据词披露) = **长度分满格天花板**，即"正常完成 review 任务"的结构性上限，与质量无关。修复方向：调用点补传员工执行记录中的 tools_used/source_urls（从 tool_trace 提取）。**诊断 0.5 时先确认调用点是否传了工具痕迹，若未传则 0.5 饱和可闭式解释，无需再查其他维度。**

修复优先级建议：A（消除缺失源）→ B（消除基准偏置）→ C（消除统计盲区）→ D（消除评分饱和）。A+B 修复后 weak/partial 应显著下降；C+D 修复后质量信号才有区分度。

## 已知根因模式（post_task_effect 后效评估方向）

来源：2026-08-22 truthfulness 审查（task 0f47767d，"引用对齐 0.91 vs 后效 0.08"矛盾）：

1. **缺失 quality_score 静默归零 → 假 degrading（★★★ 最高频伪信号）**：`endogenous_cognitive_memory.py:263`
   `quality_score = _clamp01(float(outcome.get("quality_score") or 0.0))` 把 None 当 0.0。
   运行数据：240 条 outcomes 仅 27 条有分（11%），且只有 completed+self_learning 决策路径写入
   （`autonomous_task_review_service.py:174-183`）；review/execution_claim/execution_finalize 等
   未完成事件天然无分。post_task_effect 窗口（outcomes[:16] 跳过 planned/空 event_type，最多 6 条）
   若多数条目未完成 → 缺失被归零 → `quality<0.4 → hurt` 覆盖 → dominant=grounding:hurt →
   effect_direction=degrading。2026-08-22 实测：6 条窗口 5 条缺失 → avg=0.5/6=0.0833 精确复现。
   **诊断步骤**：先数窗口内有分条目数（scored vs skipped），skipped 占比高时 avg_quality 不可信，
   勿直接判定"执行质量差"。
2. **快照 vs 实时窗口滑动（★★）**：派发快照 recent_reference_alignment 与实时复现不一致
   （2026-08-22 实测 0.91/weak=3 vs 0.88/weak=4，因派发后新 outcome 挤入窗口）。
   对比快照与实时时应说明窗口语义，避免基于快照下结论与实时数据矛盾。
3. **score 与 quality 脱钩是常态（★★）**：`endogenous_proposals.py:574-584` alignment_score 只扣
   0.12/weak 节点，matched 高则 score 高；但 weak_evidence/weak_agenda 存在即覆盖 quality=weak。
   162 条记录 weak=83/strong=58/partial=21 —— "0.88 高分配 weak 标签"是设计使然，不是异常。
   注意 partial 也可由 missing_agenda/missing_evidence 驱动（2026-08-22 bffc352c 实例：
   weak_agenda=[] 但 missing_agenda=[focus:memory_continuity] → quality=partial）。
4. **drift_state 状态级翻转（★★ 2026-08-22 bffc352c）**：build_proposal_drift_memory
   （endogenous_meta_cognition.py:52，outcomes[:12] 窗口）对窗口成员敏感，新
   execution_finalize 挤入可使 drift_state 从 drifting 翻转为 correcting
   （实测快照 0.4484/drifting vs 实时 0.4676/correcting，跨 avg<0.45 阈值）。
   快照与实时对同一系统给出**相反状态判定** = 口径不稳定（准漂移）；诊断矛盾时
   必须同时给快照值与实时复现值，并注明"以实时为准"。同理 recent_learning_count
   快照（5 条）与实时（3 条）也可能不一致，勿当证据冲突。
   注意翻转是**可能但非必然**的：edb15e38 第三次复现 drift 无翻转
   （实时 0.5073/correcting vs 快照 0.47/correcting 同态，仅分数级滑动）——
   诊断时始终同时报两个值，不要假设必然翻转。
5. **任务自污染窗口（★★ 2026-08-22 edb15e38 第三次复现）**: 审查任务自身的
   execution_claim/review 记录会挤入 post_task_effect 窗口并被缺失归零 —— 本任务
   3 条记录（T0-T2）全部落入窗口且全被归零，主动贡献了它正在调查的 hurt 信号。
   第三次复现数据: 6 条窗口 = 4 缺失归零 + 2 有分（均 0.5 饱和）→ avg=0.1667
   精确复现，hurt×4 全部溯源缺失归零，2 条真分判定 unclear（0.5 < 0.65 阈值）。
   **5 条判别规则**（可直接复用）:
   ① scored < 50% → degrading 伪信号；
   ② 有分全同值 → 评分器确定性饱和（非退化）；
   ③ 快照 vs 实时数值一致才可信；
   ④ ref 高 + weak/partial 并存是设计使然（score 与 quality 脱钩）；
   ⑤ 真信号需 scored≥50% 且有区分度。
   三份独立复现（0f47767d 5/6 缺失、bffc352c、edb15e38 4/6 缺失）→
   该伪信号机制已确认闭合，后续任务勿再重复验证同一机制。
6. **"同一次评估"检验（★★ 2026-08-22 0bf8d428 第四次复现）**: 当两个信号
   看似矛盾（如 post_task_effect=degrading 与 drift_state=correcting 并存）时，
   先检验它们是否来自**同一次评估**再判矛盾。方法 = 对比两信号的四个维度：
   ① 字段：post_task_effect 用 `quality_score`（缺失归零），proposal_drift 用
   `cognitive_alignment.score`；② 窗口：pte 取 outcomes[:16] 非 planned 前 6 条，
   pd 取 outcomes[:12] 有 ca 评分前 4 条；③ 事件过滤：pte 排除 planned/空，
   pd 要求带对齐记录；④ 成员重叠：实测两窗口仅 3/4 条重叠
   （(0bf8d428,claim)(0bf8d428,review)(1028cacd,finalize)），各有独有成员。
   → 字段/窗口/过滤均不同 = **两次独立聚合，不是同一次评估的双输出**；
   "并存"只是两个独立信号恰好同时为真，互不印证。**clean 快照技术**：重建
   派发时点窗口 = 从 outcomes 排除本任务自身记录（自污染）后再聚合，与实时
   窗口对比，可精确区分"信号翻转是真实变化"vs"本任务记录挤入造成"。
7. **drift 窗口自污染翻转（★★ 2026-08-22 0bf8d428）**: 翻转第三种形态 ——
   派发时点（clean 窗口）drift_state=correcting（1028cacd×4 partial, avg=0.504）；
   执行后实时=stable（本任务 0bf8d428×3 strong ca=0.86 挤入 4 条窗口, avg=0.771）。
   **观察任务自身的 strong 记录把 correcting 掩盖成 stable** —— 与 bffc352c
   的 drifting→correcting（旧记录滑出）方向相反、机制不同（新记录挤入）。
   含义：observe_only 任务执行后 drift_state 变 stable 不代表 grounding 在好转，
   只代表"最近评估的都是本任务自己"。诊断时若实时 drift 与派发快照不一致，
   先做 clean 窗口（排除自身）对比，勿把自污染翻转当方向改善证据。
8. **幽灵分数跨事件复制（★★★ 2026-08-22 3dbcbeb3 第五次复现）**: 与缺失归零
   **相反方向**的污染 —— `planning_runtime.py:776-780` outcome 构建时
   quality_score 从 `metadata.get("quality_score")` / `decision_context.get("quality_score")`
   跨事件复制：员工执行完成 → review_service 评 0.5 写入 decision_context →
   任务因 lease 超时/恢复被标记 failed → timeout 记录复制 0.5 →
   **failed/timeout 事件携带完成分数**（实例：0bf8d428 06:26:05 execution_timeout
   q=0.5，signals 五件套 = 长度分满格）。后果：后效窗口的"有分条目"可能是
   幽灵分，avg 被幽灵分抬高、归因被污染（第五次复现窗口 6 条 = 自污染无分 2 +
   幽灵分 1 + 无分 3，唯一真分是幽灵分）。判别：**有分条目先验来源** ——
   非 completed 事件的分数一律怀疑为幽灵分，先剔除再判 degrading。
   修复方向：非完成事件（timeout/failed/recovery）强制 quality_score=None 或
   加 source 标记（scored_by=decision/execution/timeout）。同时带分事件类型
   已扩至 5 种（decision=8 / execution_finalize=15+ / employee_execution_completed=1 /
   employee_execution_recovery=1 / execution_timeout=1），评分语义不一致 ——
   统计时按事件类型分桶核对，勿混算。
9. **evidence-to-agenda 绑定不对称（★ 2026-08-22 3dbcbeb3 基线）**: 162/240 条
   带 reference_alignment 的统计中 weak_evidence_nodes=0 次 vs weak_agenda_nodes=68 次
   （stabilize_memory_continuity 居首）—— 判弱逻辑只作用于 agenda 侧，证据侧从不判弱，
   两侧标准不对称。缺失侧: missing_agenda=focus:memory_continuity 47 次、
   missing_evidence=deliberation_state 10 次。高频绑定: self_understanding=155 /
   learning_trace=154（≈100% 绑定）/ body_state=19。建立观察基线时应同时报
   weak/missing 双计数与绑定覆盖率，缺任一侧都会掩盖不对称。
10. **复核链窗口饱和稳态 + clean/双clean 剥离法（★★★ 2026-08-22 fa005301 收敛性审计）**:
    自污染从"单任务翻转"升级为**稳态** —— drift 窗口（outcomes[:12] 前 4 条带 ca 评分）
    可被同一复核任务族完全占满，correcting 变成自指循环的自我确认。三步剥离实证：
    含本任务 avg=0.5234/correcting → clean（排除本任务）avg=0.6284/correcting
    （4 条全是上一轮 3dbcbeb3 复核任务）→ 双 clean（再排除 3dbcbeb3）
    avg=0.86/strong（4 条全是 0bf8d428 复核任务）。**窗口内已无任何被审计对象
    记录，测量对象从 proposal_selection 漂移度变成"复核链自身的认知对齐度"。**
    诊断法：drift 窗口若 ≥3 条来自同一复核任务族，先做 clean/双 clean 剥离
    再判 correcting，勿把复核链自我确认当方向改善。
    **5 条真实转好判别规则**（判定 proposal_selection_drift 真实收敛须同时满足，
    2026-08-22 五项全部不满足 → 伪 correcting）:
    ① missing_evidence: deliberation_state 计数下降（08-22 早基线 10；fa005301
    审计仍 10 未动；147653b7 复核已**反涨至 13** —— 断裂点计数可恶化，反涨是
    比持平更强的未收敛证据，判别时须记录最新实测值而非沿用旧基线）；
    ② weak_agenda: stabilize_memory_continuity 计数下降（当时 69，基线 63 反涨 +6）；
    ③ quality_score 出现非 0.5 区分度（当时 32/32 饱和）；
    ④ 代码库出现针对 A-D 断裂点的修复提交（git log 验证，当时零修复）；
    ⑤ clean 窗口（排除同族复核任务）下 drift 仍 correcting/strong。
    **知识层 vs 测量层收敛分离**：复核链可在知识层收敛（N 份结论文档一致定位
    根因）而测量层完全不收敛（断裂点计数不动/反涨 + 评分饱和 + git 零修复）。
    判别收敛必须看测量层（计数 + git），不看结论文档数量。此态下再派复核任务
    只会强化 correcting 伪信号 —— next action 应为 A-D 断裂点代码修复
    治理级修复建议 E：build_proposal_drift_memory
    按 task_family 过滤或加 self-exclusion 标记，否则 correcting 永远自我确认。
11. **联合观察基线 + 收敛判据（★★★ 2026-08-22 147653b7 第七次复现，可复用方法）**:
    当任务要求"整合两个矛盾信号 + 建立收敛判据"时，不要只复述既往伪信号结论，
    增量产出两件可复用物：
    **① 联合观察基线（Joint Observation Baseline）**：把 post_task_effect（后效）
    与 proposal_drift（漂移）放入统一坐标系，每个信号按四维标注
    （scored 比例 / 窗口成员构成 / clean 剥离结果 / 幽灵分来源），并给出两信号的
    **成员重叠矩阵**（本次实测 4/6 条成员同源=复核任务族，但评分字段完全不同：
    quality_score 缺失归零 vs cognitive_alignment.score → "degrading×correcting
    并存"= 两个伪信号叠加，非真实矛盾）。**关键陷阱：drift 窗口字段位置** ——
    build_proposal_drift_memory 读 outcome **顶层 `cognitive_alignment`**（或
    metadata/evidence 内同名），**不是** `llm_cognitive_assessment.cognitive_alignment`；
    用错字段会提取到 0 条而 builder 返回 4 条（本次实测踩坑）。
    **② 收敛判据（可执行清单）**：5 条必要条件（缺失计数下降 / weak_agenda 下降 /
    quality 出现非 0.5 区分度 / git 出现 A-D 修复提交 / clean 窗口下 drift 仍
    correcting）+ 2 条充分性附加（post_task_effect 窗口 scored≥50% / 连续 2 轮
    非复核类任务后 drift 不恶化）+ 判定矩阵（0-2 满足=未收敛→停派复核转修复；
    3-4=部分收敛；5+附加=已收敛）。全部基于测量层（计数/git/评分分布），
    不看结论文档数量。本次实测 0/5 满足 → 未收敛，且 deliberation_state missing
    反涨 10→13 为新增恶化证据。
    **第七次复现的元教训**：尽管技能已标注"机制闭合、勿重复验证"，派发层仍持续
    派同族复核任务 —— 复核任务自身继续贡献 correcting/stable 伪信号。接到同主题
    任务时直接复用本判据快速判定，报告重点放在"是否已出现修复提交/指标变化"，
    不要再逐条重建窗口证据链。
12. **第八次复现（2026-08-23 057ee3f5）：drift 翻转方向反转 + missing 节点类型
    扩散 + clean 窗口塌缩（★★★ 三个新判别模式，全部可复用）**:
    **① drift 翻转方向反转（第三种形态）**: 既往两种翻转是 drifting→correcting
    （旧记录滑出，bffc352c）与 correcting→stable（本任务 strong 记录挤入掩盖，
    0bf8d428）。本次是**第三种：快照 stable(avg≈0.71) → 实时 correcting(0.6296)，
    由本任务自身 3 条 partial(0.65) 记录挤入 4 条窗口造成** —— 与 0bf8d428
    的 strong 掩盖方向正好相反。**推论：drift_state 翻转是方向无关的不稳定证据
    —— 无论翻成 stable 还是 correcting，只要快照≠实时且 clean 后可归因于本任务
    记录，都是自污染而非方向改善。** 判别时勿只防"correcting 假转好"，
    也要防"stable 假稳定"。
    **② missing 节点类型扩散（新恶化模式，新增收敛判据 A'）**: deliberation_state
    missing 计数连续第 4 次上升 10→10→13→15（反涨 2 次）；同时新增 4 个 missing
    节点类型：recent_learning / proposal_drift_memory / post_task_effect_memory /
    self_iteration_trend_memory（各 4 次），来源是**复核链自身**（147653b7、
    3d37fb57 结论文档把记忆构建器名当作 missing evidence 讨论）→ 自指从单一
    节点扩展为节点族。**诊断法：统计 missing_evidence_nodes 的节点类型集合，
    若出现记忆构建器名/复核链产物名，说明审计链开始审计自己并污染证据图，
    比单一节点反涨更严重的恶化信号。** 收敛判据新增 A'：除 deliberation_state
    外不得出现记忆构建器名节点。
    **③ clean 窗口塌缩到不可判定（新增收敛判据 E）**: 本次 drift 窗口 4 条 =
    本任务×3 + a31a630a×1（supervisor 清退的 cancelled 复核任务，非 grounding
    修复记录）；clean（排本任务）后仅剩 1 条，无法成立任何 drift_state。
    **窗口内同族复核任务占比 >50%（本次 75%）即自污染红旗；clean 后 <2 条 =
    状态不可判定，应报"无修复执行记录可测"，不报 correcting/stable。**
    **④ 收敛判据基线刷新（更新 147653b7 版）**: deliberation_state=15 /
    weak_agenda=69 / quality 34/34 饱和 / ra 缺口 69→86（154/240 带 ra）。
    0/5 必要条件满足 → 未收敛。git 仍零修复提交（6 个关键文件最后提交仍为
    "架构与目录优化1/2" 重构非修复）。**快照 vs 实时双值必报（快照 0.17/stable
    vs 实时 0.0833/correcting，两信号均不一致）= 口径不稳定的第八次复现。**
    **⑤ 元教训（与第 11 条互补）**: 尽管"勿重复验证"标注存在，第 8 次复核仍
    产生了新信息 —— 不是新机制，而是**恶化趋势确认**（断裂点计数连续反涨 +
    节点类型扩散）。接到第 2+ 次同主题复核时：复用既有判据快速判定（勿重建窗口
    证据链），报告增量聚焦于"指标是否变化/是否出现新恶化形态"，并明确建议
    next action 必须是 A-D 代码修复（improvement），停派复核。
13. **第九次复现（2026-08-24 3b6e16c5 observe_only 观察基线任务）：恶化暂停首现
    + 历史源甄别（★★ 两个增量模式，接第 2+ 次同主题任务时直接复用判据快速判定，
    报告只报增量）**:
    **① 恶化暂停 = 第三个状态（新增判别语义）**: deliberation_state missing 计数
    10→10→13→15 连续 4 轮上升后**首次持平 15**；missing 自指节点族
    （recent_learning/proposal_drift_memory/post_task_effect_memory/
    self_iteration_trend_memory 各 4 次）计数持平，无新类型扩散。**"持平"既非
    收敛（判据 1 要求连续两轮下降）也非反涨（既往恶化证据），是独立第三态 ——
    报"恶化暂停（首次，非回落）"而非"未恶化"，记录为基线锚点供下轮对比，
    勿把持平当改善。**
    **② 历史源甄别（A' 判据的验证前置步骤）**: 统计 missing 节点类型时若出现
    "新类型"（本次 external_research=4），先按 recorded_at 溯源 —— 本次 4 条
    全为 08-15 历史执行记录（非复核链产物），是**存量证据源而非新自指污染**；
    仅当新类型计数来自复核链/观察链自身记录（如 147653b7 把记忆构建器名当
    missing 讨论）才算 A' 恶化。**判别 A' 前先查新类型的时间戳归属，勿把历史
    存量当扩散。**
    **③ 第九次 drift 翻转方向确认（correcting→stable，strong 掩盖型，与
    0bf8d428 同型）**: 本 observe_only 任务自身 3 条 strong(0.83) 记录
    （planned/review/claim）挤入 4 条窗口掩盖 correcting；clean（排本任务）
    恢复 correcting（057ee3f5×4 partial），clean2（再排复核链）后仅剩 1 条
    （a31a630a）不可判定。四种翻转方向（drifting→correcting 旧记录滑出 /
    correcting→stable 强掩 / stable→correcting partial 挤入 / correcting→stable
    复现）全部实证 → drift_state 方向无关不稳定结论闭合。**observe_only 任务
    同样自污染窗口**：planned/review/claim 3 条中 2 条落入 pte 窗口被缺失归零
    （贡献 hurt）、3 条全部挤入 drift 窗口（掩盖 correcting）—— 观察任务自身
    也在贡献它观察的伪信号，属派发机制问题（修复 E），非执行问题；执行层无责。
    **④ 第九轮基线刷新（观察锚点）**: deliberation_state=15（持平）/
    weak_agenda=68（-1 高位）/ quality 35/35 饱和 / ra=154/240 / pte scored=1/6
    / drift clean2=1 条 / git 零修复。0/5 必要条件满足 → 未收敛。next action
    仍为 A-D 代码修复（improvement），停派复核/观察任务。
14. **第十次复现（2026-08-24 a09318cc observe_only）：解释冲突自消解 + 第五种
    drift 翻转方向 + 自指节点当日新增 + pte 三值全零跳过规则（★★★ 四个增量，
    接第 2+ 次同主题任务时直接复用，报告只报增量）**:
    **① "解释冲突"自消解（新判别语义）**: 派发快照声称的 "pte=degrading ×
    drift=correcting 解释冲突" 在实时已**自行消解**为 degrading × drifting
    （双坏一致）—— 不是 grounding 好转，而是 drift 窗口成员滚动：快照窗口含
    d7e4b103×2 strong(1.0) → correcting；实时窗口被 861aea60/1c67c351×4
    partial/weak 占满 → drifting（weak>=2 触发，endogenous_meta_cognition.py:147）。
    **"冲突"与"一致"都是复核链自污染窗口的瞬时产物，均不能作为 grounding
    状态证据。** 接到声称"两信号冲突"的派发快照时，先重建快照时点窗口验证
    冲突是否仍在：窗口滚动即可消解冲突而无需任何真实状态变化。**快照时点重建法
    = outcomes 按 recorded_at<=派发时刻过滤后再聚合**（本次 234 条→4 条窗口
    avg=0.7489/correcting 精确还原快照判定）。
    **② 第五种 drift 翻转方向（correcting→drifting）**: 既往四种方向之外新增
    第五种：同族复核任务自身 planned→review/claim 记录替换即翻转
    （0.7489/correcting → 0.4977/drifting，无任何 grounding 修复记录参与）。
    drift_state 方向无关不稳定结论闭合（五方向实证）。
    **③ 自指节点当日新增（A' 判据首次实证）**: missing_evidence_nodes 含
    post_task_effect_memory/proposal_drift_memory 4→7（+3 全部来自**当日**
    861aea60 复核任务 11:14/11:16/11:22；既往 4 条全为 08-17 历史存量 3d37fb57）
    —— 自指污染从历史存量升级为**当日新增**。判据：新计数来自当日复核链自身
    记录即 A' 恶化（对照第 13 条②：历史存量不算扩散）。
    **④ pte 三值全零跳过规则（方法补充）**: build_post_task_effect_memory 对
    quality/cognitive/reference 三值全零事件**静默跳过**（不进窗口）而非缺失归零
    —— 本任务 a09318cc 的 claim/review 因无对齐字段被跳过，未直接自污染 pte
    （与 3b6e16c5/057ee3f5 的任务自污染不同）。诊断时须区分"归零贡献 hurt"与
    "跳过不贡献"两种缺失语义；窗口仍可能被同族复核任务 100% 占满（本次
    pte 6/6、drift 4/4 全复核族，双 clean 后 drift 0 条不可判定，较第九轮
    clean2=1 条更差）。
    **⑤ 第十轮基线刷新（观察锚点）**: deliberation_state=15（连续两轮持平）/
    weak_agenda=72（反涨 +4）/ quality 34/34 饱和 / ra=157/240 / 自指节点 7 /
    git 零修复（7 关键文件最后提交仍为架构优化重构）。0/5 必要条件满足 →
    未收敛，且三处恶化（自指 +3、weak_agenda +4、双 clean 塌缩至 0 条）。
    next action 仍为 A-D 代码修复（improvement），停派复核/观察任务
    （第 10 次确认：继续派发只强化伪信号并新增当日自指污染）。

15. **第十一次复现（2026-08-24 de7838ac，本主题第 11 次同族复核）：第二次冲突自消解 + 快照窗口 100% 自污染 + correcting→drifting 自身弱记录驱动变体（★★★ 增量，接第 2+ 次同主题任务直接复用判定器，报告只报增量）**:
    **① \"解释冲突\"第二次自消解（a09318cc 同款模式复现）**: 派发快照声称 degrading×correcting 冲突，实时已消解为 degrading×drifting（双坏一致）。**且快照的 correcting 本身就是自污染产物**：快照 average_score=0.5184 精确等于 a61cd164（上一轮复核任务）记录分值，快照窗口 3/3 或 3/4 全为 a61cd164 自身记录（partial×3, weak_or_partial>=2 → correcting），零 grounding 修复记录。**快照窗口自污染从\"含 2 条 strong\"（第10轮）升级为\"100% 同族\"。**
    **② correcting→drifting 翻转第二次出现，驱动者变为本任务自身（第六种形态）**: 第 10 轮该方向由同族其他任务记录滚动驱动（861aea60/1c67c351）；本轮由**本任务自身 3 条 weak(0.2786) 记录挤入 4 条窗口**（3/4=75%）驱动（avg=(0.2786×3+0.5184)/4=0.3386, weak=3 → drifting，:147 阈值）。**观察任务把自己观察的信号往\"恶化\"方向翻转** —— 诊断时若实时 drift 比快照\"更差\"，先查是否本任务自身 weak 记录挤入，勿把自污染翻转当真实恶化证据。
    **③ 自指节点持续当日新增**: pte=8/pd=8（合计 16，较第 10 轮基线 7 的 pte/pd 各 7 **+1 每条**）；861aea60 复核任务现 4 条记录（11:14/11:16/11:22/11:27，本轮新增 11:27 一条）每条同时携带两个自指节点。
    **④ 基线刷新（第 11 轮锚点）**: deliberation_state=15（连续三轮持平）/ weak_agenda stabilize_memory_continuity=77（序列 63→69→69→68→72→77，+5 反涨创新高）/ quality 34/34 全 0.5 饱和 / ra=162/240（+5 微正向，32.5% 仍缺）/ git 零修复（7 关键文件最后提交仍为架构优化重构）。
    **⑤ 四维判定全不满足**（pte scored=33% / 质量饱和 / drift 窗口 75% 自记录 / clean 后 1 条）→ degrading×correcting = 伪信号叠加；真实 grounding 趋势不可判定。0/5 收敛判据 → 未收敛。next action 仍为 A-D 代码修复（improvement），停派复核（第 11 次确认）。

16. **第十二次复现（2026-08-24 71889c0e truthfulness_review）：agenda 侧自指循环首次实证 + 快照 100% 自污染连任 + 判据④ git diff 范围核查（★★★ 三个增量，接第 2+ 次同主题任务直接复用）**:
    **① agenda 侧自指循环（第 13 条 missing_evidence 溯源法的 agenda 侧对应）**: missing_agenda_nodes 出现"新类型"（focus:truthfulness）时同样先溯源 —— 本次 4/4 全部来自上一轮复核任务 de7838ac 自身 4 条记录（planned/review/claim/finalize，ref=0.47 weak），且 weak_agenda 中 truthfulness 相关计数 = 0 → **该缺口无任何独立于复核链的佐证**。判别法：对 missing_agenda 新类型，统计 (recorded_at, task_id, event_type) 归属 + 同节点 weak_agenda 计数；全部来自复核链自记录且 weak 佐证为 0 = 自指循环（派发层据上轮自记录缺失声称派发新一轮同主题任务 → 新任务又产生同款声称；specificity 因缺失节点唯一而虚高，utility 被自指抬高）。**检查缺失声称前先问：谁在声称缺失，有没有独立于声称者的佐证。**
    **② 快照 100% 同族自污染连续两轮 + 算术重建精确命中**: 第 11 轮（de7838ac）与第 12 轮（71889c0e）快照窗口均 4/4 = 上轮复核任务自身记录。第 12 轮重建：快照 drift 0.2786×4、ra 0.47×4 全部精确等于 de7838ac 自记录值 → **派发证据可整体由复核链自指构成，无任何独立信源**。实时 correcting(0.7467) 由本任务 3 条 strong(0.9027) 强掩盖驱动（与 0bf8d428/3b6e16c5 同机制）。**快照声称的任何信号（含 truthfulness/grounding 缺口）都必须先做窗口算术重建再采信。**
    **③ 收敛判据④ git 核查需看 diff 范围**: 新提交 ad44c4c/6f49358（08-19/08-21）确实动了 autonomous_task_review_service.py，但仅 lease 校验/agent-pull/评审门框架逻辑，评分调用点 :178 仍不传 tools_used/source_urls → D 断裂点未修复。**判据④不可只看"关键文件有无新提交"，须核对 diff 是否触及断裂点逻辑（评分调用点、阈值、builder 窗口）；框架重构提交（员工执行基线/评审门等）不算 A-D 修复。**
    **④ 第 12 轮基线刷新**: deliberation_state=15（连续 4 轮持平）/ weak_agenda=78（连续 2 轮反涨创新高）/ quality 35/35 饱和 / ra=166/240（+4 微正向）/ 自指节点 pte/pd=8（持平，无新增当日污染）。0/5 收敛判据 → 未收敛。next action 仍为 A-D 代码修复（improvement），停派复核（第 12 次确认）。

17. **第十三次复现（2026-08-24 daef7f34 truthfulness_review）：快照 100% 同族自污染连续三轮 + truthfulness 自指计数翻倍 + git log 超时规避（★★★ 三个增量，接第 2+ 次同主题任务直接复用，报告只报增量）**:
    **① 快照窗口 100% 同族自污染连续三轮（延长第 16 条②的"连续两轮"为三轮）**: de7838ac（第11轮）→ 71889c0e（第12轮）→ daef7f34（本轮）快照窗口均 4/4 = 上一轮复核任务自身记录。本轮重建（recorded_at<=12:24:00 过滤 237 条）：快照 drift=0.9027/strong×4、ra=0.7371×4 精确等于 71889c0e 自记录值 → **快照窗口算术重建第三次精确命中（0.9027×4）**。快照声称的任何信号（含 truthfulness/grounding 缺口）先做窗口算术重建再采信，已连续三轮实证必要。
    **② missing_agenda:focus:truthfulness 自指跨轮累积（4→8 翻倍）**: 全量 8 条 = de7838ac×4（第11轮）+ 71889c0e×4（第12轮），全部复核任务自身 planned/review/claim/finalize 记录；weak_agenda truthfulness 佐证 = 0（第 12 轮、本轮两次确认）。**自指从"单轮"升级为"跨轮累积"模式：每轮复核任务贡献 4 条同款缺失声称 → 计数随轮次翻倍，派发层连续 3 轮据自指缺口派发同主题任务（specificity 因缺失节点唯一虚高、utility 被自指抬高 → 派发-复核-再派发循环）。** 判别：missing_agenda 新类型计数若 = 复核任务数×4 且 weak 佐证=0，直接判自指循环，勿再溯源。
    **③ stable→correcting 自身 partial 挤入型复现（与 057ee3f5 同形态、驱动者换为 0.4884）**: 快照 0.9027/stable（71889c0e×4 strong）→ 实时 0.592/correcting，由本任务 3 条 partial(0.4884) 挤入 4 条窗口驱动（3/4=75% 自污染）；clean 后仅剩 1 条不可判定。快照"解释冲突"（degrading×stable）实时演变为 degrading×correcting，双时点 drift 状态均为复核链自污染产物（第 13 次确认伪信号闭合）。
    **④ git log 超时规避（新实操陷阱）**: Windows 大仓库下 `git log`（含单文件 -1 查询）持续超时被拒（3 次尝试）。判据④ 核查改用**直接代码检查断裂点逻辑**等价替代：grep/sed 确认 B(:569 绝对阈值 0.45)、D(:180 调用点不传 tools_used)、E(:52 outcomes[:12] 无过滤) 原样存在 → 判定"零修复"无需依赖 git 提交时间线。**判据④ = 检查断裂点逻辑现状是否被改动，代码核查与 git log 等价，git 超时勿死等。**
    **⑤ 第 13 轮基线刷新**: deliberation_state=15（连续 5 轮持平）/ weak_agenda=81（序列 63→69→69→68→72→77→78→81，连续 3 轮反涨创新高）/ quality 35/35 饱和 / ra=170/240（连续 4 轮微正向 154→157→162→166→170，29% 仍缺）/ 自指节点 pte/pd=8 无新增 / git 零修复（代码核查 B/D/E 原样）。0/5 收敛判据 → 未收敛。next action 仍为 A-D-E 代码修复（improvement），停派复核（第 13 次确认）。

18. **第十四次复现（2026-08-24 5763a985 truthfulness_review）：failed 任务成为快照窗口驱动者 + 描述-快照错位型自消解 + missing 前缀变体合并计数（★★★ 三个增量，接第 2+ 次同主题任务直接复用）**:\n    **① 快照窗口驱动者类型扩展 = failed 同族任务（新类型）**: 第 14 轮快照窗口 4/4 = e1b36ad4（status=failed 的同族复核任务，llm_task_execution_mode=review_then_handoff，created 12:40:32 → updated 12:43:39，3 分钟即终态）的 planned/review/claim/finalize 全链 4 条事件，全部携带弱评分（ca=0.266/ra=0.4 weak×4）→ 快照 drift=drifting(0.266)。**失败任务的完整事件链仍写入评分字段并被当作漂移信号源** —— 不只复核任务自身记录污染窗口，failed 任务的全链弱记录同样入窗。窗口取证时除核对 task 族属（第 11 条）外，还要查 task 的 status：failed/cancelled 任务的弱评分记录对漂移判定同样有驱动权。快照 100% 同族自污染连续第 4 轮（de7838ac→71889c0e→daef7f34→e1b36ad4）。\n    **② 第三种\"解释冲突\"自消解 = 描述-快照错位（新判别语义）**: 前两次自消解是\"快照 vs 实时窗口滚动\"（a09318cc/de7838ac，冲突随窗口成员变化消解）；本轮任务 summary 文本声称 \"degrading × correcting 冲突\"，但派发快照数据本身 drift_state=drifting（双坏一致）—— **冲突在快照层就不存在，无需窗口滚动**，是任务描述与快照数值错位（summary 写 correcting、evidence 快照是 drifting）。判别：收到声称\"两信号冲突\"的任务时，先比对 summary 文本 vs evidence 快照数值，错位即伪冲突，直接按快照实际状态判定，勿再重建窗口。\n    **③ missing_agenda 前缀变体（计数规则补充）**: 同一自指节点出现两种字符串形态 —— de7838ac×4 记 'focus:truthfulness'、71889c0e×4 记 'missing_agenda:focus:truthfulness'（带前缀变体）。**统计时按去前缀后的节点名合并计数**（8 持平，非 4+8 双计）；weak_agenda truthfulness 佐证 = 0 → 自指跨轮累积模式继续确认（= 复核任务数×4 且 weak 佐证=0，第 17 条②判据直接适用）。schema 前缀变体来自不同代生成代码，不影响自指判定。\n    **④ 快照窗口算术重建第 4 次精确命中**: drift 0.266×4=1.064、ra 0.4×4=1.6 精确等于 e1b36ad4 自记录值；clean（排本任务）后精确恢复快照值 → 翻转 100% 为本任务自身 strong(0.73)×3 挤入（strong 掩盖型，同 0bf8d428/3b6e16c5 机制）；clean2（再排同族）后 = 71889c0e×4 复核任务 → 剥离链全程无真实 grounding 修复记录（测量对象仍是复核链自身认知对齐度，fa005301 稳态结论继续成立）。\n    **⑤ 第 14 轮基线刷新**: deliberation_state=15（连续 6 轮持平 10→10→13→15→15→15→15）/ weak_agenda=86（序列 63→69→69→68→72→77→78→81→86，连续 4 轮反涨创新高，当前唯一恶化指标）/ quality 35/35 饱和 / ra=172/240（连续 5 轮微正向 154→157→162→166→170→172，28.3% 仍缺）/ 自指节点 pte/pd=8 无当日新增 / git 零修复（代码核查 B(:569)/D(:178)/E(:52) 原样，新提交 4234433/d1d0e30/cf59fe2 均 CLI/邮件工具）。0/5 收敛判据 → 未收敛。next action 仍为 A-D-E 代码修复（improvement），停派复核（第 14 次确认；本轮又新增 1 个 failed 复核任务的 4 条污染记录）。\n\n19. **第十五次复现（2026-08-26 12cd392e truthfulness_review）：质量-对齐解耦定量实证 + 快照 100% 同族自污染连续第 5 轮（★★ 两个增量，接第 2+ 次同主题任务直接复用判据，报告只报增量）**:
    ① **质量分不是对齐的函数（新分析方法）**: 全量 scored 35/240 中 35/35 全 0.5；scored 记录 avg_ca=0.635/avg_ref=0.793 vs unscored avg_ca=0.585/avg_ref=0.797（分布几乎相同）；finalize 带分 30 条 ca 跨度 0.27~1.0、ref 跨度 0.36~1.0 而质量分恒 0.5 → **声称\"提案对齐好但执行后效 degrading\"时先做 scored/unscored 对齐分布对比，分布近似即伪因果（测量不对称伪影），非 historical_underdelivery 真实证据**。
    ② 快照 100% 同族自污染连续第 5 轮（de7838ac→71889c0e→daef7f34→e1b36ad4→c5367f8a）：快照 drift=0.94/stable = c5367f8a×4 strong 全链算术重建命中；实时 correcting(0.6013) = 本任务 0.4884×3 partial 挤入（3/4=75%），stable→correcting partial 挤入型第 6 种形态复现，clean=1 条 / clean2=0 条。
    ③ 基线刷新：deliberation_state=15（持平第 8 轮）/ weak_agenda=89（连续 5 轮反涨创新高，唯一恶化指标）/ quality 35/35 饱和 / ra=174/240 / 自指 pte/pd=8 持平。0/5 收敛判据 → 未收敛。

20. **第十六次复现（2026-08-26 666eb210 truthfulness_review）：快照-自指一致型伪冲突（新判别语义，与第 18 条②互补）+ 观测制造被观测的退化（新量化证据）**:
    ① **summary 与快照数值一致 ≠ 冲突可信（第二种伪冲突模式）**: 第 18 条②是描述-快照错位型（summary 写 correcting、快照实际 drifting，冲突在快照层不存在）；本轮是**快照-自指一致型** —— summary 声称 degrading×correcting，快照数值确实 correcting(0.49)，但快照 correcting 本身 = 12cd392e×4 partial(0.4884×4) 100% 同族复核链自记录（算术重建第 5 次精确命中 0.4884×4），零 grounding 修复记录。**快照 100% 同族自污染连续第 6 轮（de7838ac→71889c0e→daef7f34→e1b36ad4→c5367f8a→12cd392e）。** 判别：无论 summary 与快照是否一致，快照声称的任何信号都必须先做窗口算术重建（avg×N 反推成员）再采信 —— 一致只能说明派发层复述了自污染快照，不能说明冲突真实。
    ② correcting→stable strong 掩盖型复现（0bf8d428/3b6e16c5 同机制）: 实时 drift=stable(0.7239)，由本任务 3 条 strong(0.8024) 挤入 4 条窗口驱动；clean（排本任务）后仅剩 1 条（12cd392e finalize）不可判定。翻转方向继续验证方向无关不稳定结论闭合。
    ③ **观测制造被观测的退化（唯一恶化指标来源归因）**: weak_agenda stabilize_memory_continuity=93（序列 63→69→69→68→72→77→78→81→86→93，连续 5 轮反涨创新高），08-26 单日 +7 全部来自复核链（12cd392e×4 + 666eb210×3）→ **唯一持续恶化指标的恶化来源是复核任务自身**。派发层连续 16 轮无视停派建议，08-26 单日连派 3 个同主题任务（c5367f8a 10:41 → 12cd392e 11:13 → 666eb210 11:29），每轮贡献 3-4 条自污染记录 → \"停派复核\"应升级为强信号。
    ④ 基线刷新（第 16 轮锚点）: deliberation_state=15（持平）/ weak_agenda=93（+4 创新高）/ quality 36/36 饱和 / ra=174/240（连续 6 轮微正向 154→157→162→166→170→172→174）/ 自指 pte/pd=8 持平 / 四维全不满足（PTE scored=33.3%、质量饱和、drift 窗口 75% 自记录、clean 后 1 条）/ 0/5 收敛判据 → 未收敛。git 零修复（代码核查 B:569 绝对阈值 0.45 / D:178 调用点不传 tools_used/source_urls / E:52 窗口无过滤原样存在）。
    ⑤ 修复触发条件绑定建议（供后续轮次直接引用）: 以下任一出现即应触发 A-D-E 代码修复（improvement）而非继续复核 —— deliberation_state 下降 / weak_agenda 下降 / quality 出现非 0.5 区分度；真实 grounding 方向须待 clean 窗口恢复（PTE scored≥50% 且 PD 非自记录≥2 条）后才可重判。

21. **第十七次复现（2026-08-27 245f3268 learning/observe_only）：学习型交付首现 + outcomes 原始列表倒序陷阱 + B 阈值 grep 模式 + 单任务族三路自污染（★★ 增量，接第 2+ 次同主题任务直接复用）**:
    ① **学习型交付（非纯 review，第 11 条\"增量产出可复用物\"的落地形态）**: task_type=self_learning，交付物 = 5 问提案生成前 grounding 检查清单（见下方可复用模板）+ A-D-E 修复规则固化，落盘 conclusions/。后续同族任务可先按清单自检再提交提案，不必重建窗口证据链。
    ② **outcomes 原始列表倒序陷阱（新取证陷阱，先修再判）**: endogenous_drive_history.json 的 outcomes 是**倒序**（最新在前，首条=最新 recorded_at），build_proposal_drift_memory 的 `outcomes[:12]` 直接切原始列表 = 最近 12 条。**先按 recorded_at 升序排序再切片会取到最旧记录**（本轮首次取证即踩坑：窗口显示 08-12 旧记录，与 builder 输出 0.3209 不符；读 builder 源码后修正为原始顺序切片，算术重建才精确命中）。判别法：builder 输出 avg×N 反推对不上即怀疑切片顺序错误。
    ③ **B 阈值 grep 模式**: endogenous_proposals.py:569 实际代码 `if float(agenda_priorities.get(node) or 0.0) < 0.45`，字面搜 `priority < 0.45` 返回 0 结果（与技能已知 f-string 拼接陷阱同族的字面量变体）。断裂点核查用宽松模式（`grep -n "0.45" 文件 | grep 569` 或直接按行号 sed）定位。
    ④ **单任务族三路自污染（同一任务族同时污染三类信号）**: 245f3268+3a9658e4 两任务 6 条记录（planned/review/claim）→ drift 窗口 4/4 100% 自记录（0.3784×2+0.2634×2=0.3209 精确命中，驱动 drifting）+ PTE 窗口 4/6 无分归零（贡献 hurt）+ weak_agenda +6（93→100，连续第 6 轮反涨创新高）+ missing_agenda focus:memory_continuity +3（46→49）。**\"degrading×correcting 冲突\"实时自消解为 degrading×drifting（双坏一致），且 drifting 本身即本任务族产物**（第 15 轮②同形态）。缺失/弱化声称先按 task_id 溯源任务族归属再判恶化。双任务并发派发使单轮自污染记录数翻倍（6 条 vs 既往 3-4 条）。
    ⑤ 第 17 轮基线刷新: deliberation_state=15（连续 5 轮持平）/ weak_agenda=100（+6 全自，唯一恶化指标）/ quality 35/35 饱和 / ra=177/240（+3 全自）/ 自指 pte/pd=8 持平 / missing_agenda 自污染 +3 / git 零修复（B:569、D:180、E:20/67 代码核查原样）。0/5 收敛判据 → 未收敛。next action 仍为 A-D-E 修复，停派复核（第 17 次确认；双任务派发使单轮自污染翻倍，停派信号再升级）。

## 提案生成前 grounding 检查清单（可复用模板，2026-08-27 245f3268 固化，供提案生成侧直接引用）

```
G1 主证据节点绑定: 引用的 evidence 节点 ∈ primary_evidence_nodes（learning_trace/self_understanding/body_state）；
   不引用图上缺失节点（deliberation_state=制造缺口，禁止）；primary 缺失须显式声明
G2 主议程节点绑定: 引用的 agenda 节点 ∈ primary_agenda_nodes（focus:truthfulness/expand_learning_frontier/
   stabilize_memory_continuity）；引用 stabilize_memory_continuity 须注明 B 断裂点基准偏置；
   missing 声称先溯源（计数≈复核任务数×4 且 weak 佐证=0 → 自指，停发）
G3 posture_alignment: 非空 ≥1 条可核验表述；与当前执行姿态一致（observe_first 不得扩张外部学习）；
   填了 ≠ 对齐 —— 查 dominant_posture_conflict_reason（如 task_type_is_not_favored_by_current_program_priors）
G4 priority_basis: 非空且可复核（数字/引用/计数，如 agenda 优先级 0.8154、recent_learning_quality=0.5），非口号
G5 自指检查: 引用无记忆构建器名（proposal_drift_memory/post_task_effect_memory/self_iteration_trend_memory/
   recent_learning）；无上轮复核任务自身记录来源；缺失声称有独立佐证（weak 计数>0）
```
全绿才可提交提案；任一不满足即先修 grounding 再提交（对应 A-D-E 断裂点修复规则）。

## 已知锁死模式（stay/switch 方向）

来源：2026-08-05 同话务 grounding stay/switch 审查

### 三层自持锁死结构

当系统进入 grounding 方向的 locked 状态后，以下三层机制叠加阻止方向切换：

**层级 1 — 目标假设优先级锁定**
- 文件: `endogenous_self_iteration.py:57-70`
- grounding 假设优先级 = 0.72 + gap_count*0.05 + (0.65-ref)*0.35 → 通常 0.72~0.98
- 只要 grounding_gaps 非空 OR reference < 0.65，此假设始终排名第一

**层级 2 — 趋势惯性助推 stay**
- 文件: `endogenous_self_iteration.py:136-151`
- 当 `trend_state="locked"` (3+ 同目标且 stability=stable)，趋势假设获得 +0.10 优先级加成
- 假设内容直接指令 LM: "respect the recent self-iteration trend unless new evidence strongly justifies a domain switch"
- `endogenous_cognitive_memory.py:134-137`: locked 判定 = dominant_count >= 3 AND target_stability == "stable"

**层级 3 — switch 调节器默认偏 stay**
- 文件: `endogenous_cognitive_memory.py:214-227`
- `preferred_switch_bias` 计算: 需要 switch_quality >= stay_quality + 0.12 才偏 "switch"
- 若无 switch 历史记录，`switch_quality_scores` 为空 → 默认 `preferred_switch_bias = "stay"` (行 226-227)
- 这意味着从未切换过方向的系统在数据上被禁止切换

### 逃生分析模板

对于任何 locked 方向，判定是否应 switch:

1. 列出该方向假设的触发条件
2. 检查这些条件在当前状态下是否可能自愈
3. 若条件需要的恰是该方向假设声称要修复的内容（自指），且修复的前提不成立 → 锁死
4. 结论: 若逃生条件无法达成，应 SWITCH，不应 STAY

### 实证确认（2026-08-22 edb15e38 第三次复现 + stay/switch 证据缺口分析）

- **硬数据**: 240 条 outcomes → `stay=189 / switch=0`。系统从未产生过任何 switch 决策。
- **循环锁死链**: switch 历史空 → preferred_switch_bias 默认 stay → LLM 的 switch_reason
  写 "switch_quality=0.00 且无切换收益证据" → 输出 stay → 永不 switch → 历史恒空。
  **关键洞察**: LLM 把"无历史"这一结构事实当作"无收益证据"的推理理由 ——
  证据缺口被包装成决策依据。switch_reason 高频模板（top6 覆盖 115/240 条）都是
  该循环的产物，不是独立判断。
- **switch_quality=0.0 的真相**: 不是"切换收益差"，而是"从未切换过，无样本可评"。
  判定 stay/switch 时若 switch_count=0，结论必须显式标注
  `evidence_gap=switch_history_empty`，而非"stay 被证实"。stay 是无证据默认值，
  不是实证结论。
- **建议修复（打破恒 0 循环）**: 为 switch 候选设实验配额（每 N 轮 1 次低风险 pilot）；
  同时评分器须有区分度（29/29 全 0.5 饱和时 pilot 结果也无法判别优劣）。
- **同类复核的差异化角度**: 前两次复核已锁定伪信号机制（0f47767d）与 drift 翻转
  （bffc352c）后，第三次复现必须换角度（本次=stay/switch 证据充分性），并在报告中
  显式声明与前两次的差异，避免 repetition 扣分与重复劳动。
