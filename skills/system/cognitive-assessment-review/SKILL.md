---
name: cognitive-assessment-review
description: 审查 VoidCube 内生认知评估记忆（cognitive assessment memory）的标准流程。当 self_learning 任务的学习分支为 cognitive_assessment_review、或需对比"任务派发快照"与"实时评估数据"提取漂移（drift）、或需复现 build_cognitive_assessment_memory 系列构建器时使用。
---

# 内生认知评估记忆审查 (cognitive_assessment_review)

## 触发条件
- self_learning 任务，evidence.learning_branch == "cognitive_assessment_review"
- 需要对比任务快照中的 cognitive_assessment_memory 与实时数据，提取"变化"
- 需要复现 trend / switch / post_task_effect / agenda 等关联记忆投影

## 核心数据与代码位置
- 仓库根：`F:/My_code/VScode_py/VoidCube`（已重构为 src/voidcube 布局；在仓库根搜
  `endogenous_cognitive_memory` 文件名会返回 0，模块在 src 下）
- 构建器：`src/voidcube/systems/supervisor/endogenous_cognitive_memory.py`
  - `build_cognitive_assessment_memory(ctx)` / `build_self_iteration_trend_memory(ctx)`
    / `build_switch_self_regulation_memory(ctx)` / `build_post_task_effect_memory(ctx)`
  - ctx 只需 `{'drive_history': d}`（整个 json 文件内容）
- 关联构建器注意跨文件（不在 endogenous_cognitive_memory.py）：
  - `build_proposal_drift_memory` → `endogenous_meta_cognition.py`
  - `build_recent_reference_alignment` → `endogenous_self_model.py`
  - 从 endogenous_cognitive_memory import 这两个会 ImportError，先 grep 定位再 import
- 数据文件：`C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json`
  - 运行时根由 `get_VoidCube_home()`（`voidcube.infrastructure.config.runtime_paths`）解析
  - 结构 keys：`updated_at` / `judgements` / `outcomes` / `strategy_memory`
  - outcomes 无独立时间戳，排序用 `recorded_at`

## 标准步骤

### 1. 复现评估记忆（关键：不用 execute_code）
execute_code 在本环境无沙箱后端会报错（`Code execution requires a sandbox backend`）。
一律用 terminal + python：
```bash
cd F:/My_code/VScode_py/VoidCube && python -c "
import json, sys
sys.path.insert(0, 'src')
from voidcube.systems.supervisor.endogenous_cognitive_memory import \
    build_cognitive_assessment_memory, build_self_iteration_trend_memory, \
    build_switch_self_regulation_memory, build_post_task_effect_memory
p = r'C:/Users/lishuo/.VoidCube/runtime/supervisor/endogenous_drive_history.json'
d = json.load(open(p, encoding='utf-8'))
ctx = {'drive_history': d}
for fn in (build_cognitive_assessment_memory, build_self_iteration_trend_memory,
           build_switch_self_regulation_memory, build_post_task_effect_memory):
    print('===', fn.__name__, '===')
    print(json.dumps(fn(ctx), ensure_ascii=False, indent=1))
"
```

### 2. 提取评估条目（verbatim 级证据，不要只复述聚合结果）
评估可能在 `o.get('llm_cognitive_assessment')` 或 `metadata` / `evidence` 中：
```python
a = o.get('llm_cognitive_assessment') or md.get('llm_cognitive_assessment') or ev.get('llm_cognitive_assessment')
if isinstance(a, dict) and a.get('current_judgement'):
    # 记录 judgement / why_not_improvement_now / self_iteration_target /
    # self_iteration_hypothesis / primary_grounding_gap / dominant_constraint
```
按 `recorded_at` 排序取最近 10 条，输出完整字段做时间线。

### 3. 收集策略记忆统计
`d['strategy_memory']` 下：`focus_stats`、`contextual_focus_stats`、
`agenda_topic_stats`。重点看：
- agenda 是否长期 dragging 且 resolved=0（未闭环议题）
- focus 的 judged/completed/failed/dragging 计数
- post_task_effect 的 `dominant_target_effect`（如 grounding:hurt）与 avg_quality

### 4. 对比快照 vs 实时，提取漂移
逐字段对比任务派发 evidence 中 `cognitive_assessment_memory` 与实时复现结果：
- self_iteration_target / hypothesis / primary_grounding_gap 变化 = 方向漂移（★★）
- dominant_constraint / why_not_improvement_now 变化 = 约束与理由演变（★）
- current_judgement 不变但计数变化 = 判断与目标解耦（判断滞后）

### 5. 写入学习笔记 + 交付
- 学习笔记写入：`C:/Users/lishuo/.VoidCube/runtime/supervisor/self-learning/conclusions/`
  命名 `<topic>-<date>.md`，包含变化对比表、时间线证据、关联投影、学习结论、下周期建议
- 报告用 media_display 推送 HTML 展板（title 含任务主题与日期）

## 陷阱
1. **execute_code 不可用**：直接 terminal python，勿浪费时间试 execute_code。
2. **数据不在仓库**：endogenous_drive_history.json 在用户主目录 .VoidCube 下；仓库内搜不到。
3. **self-learning/ 子目录全空且无代码消费方**：src 中唯一 self-learning 引用是
   `runtime_assemblers.py` 第 168 行 `canonical_root / "self-learning" / "mem_governance.jsonl"`；
   学习笔记是记录性落盘，供未来管线/人工读取。
4. **判断字段滞后**：current_judgement 常停留在旧焦点（如 memory_continuity），而
   self_iteration_target 已漂移（如 grounding）——这正是要提取的关键信号，不是矛盾。
5. **learn_only 约束**：任务约束为 learn_only 时只做调查+笔记落盘+展板交付，
   不得修改 src 代码或活动身体；所有 python 只读。
6. **post_task_effect 缺失值归零陷阱**：`build_post_task_effect_memory` 中
   `outcome.get("quality_score") or 0.0` 会把未评分的 outcome 当 0 分。解读前先统计窗口内
   scored vs skipped 条目数。实测（2026-08-24 第2阶段）：全量 240 条 scored=34（14.2%），
   且 **35/35 全部 quality_score=0.5（评分器饱和，无区分度）**；第3轮复核
   （2026-08-24 晚，任务 a61cd164）实测 PTE 窗口 6 条 scored=2/6=33%（两条 0.5），
   4 条 quality=None 被归零 → avg_quality=0.1667 → degrading；早期第2阶段实测窗口为
   scored=1/6=16.7%（avg_quality=0.0833）。窗口成员随时间滚动，scored 比例会变，
   但始终 <50% → 伪信号结论稳健。skipped 占比高时 avg_quality 不代表真实执行质量；
   系统评分器饱和时，执行侧显式质量标记（quality_score）独立于系统评分器，是唯一
   有区分度的信源。
7. **快照 vs 实时窗口滑动**：派发快照的 recent_reference_alignment 与实时复现可能因新
   outcome 挤入窗口而不一致（实测 0.91/weak=3 vs 0.88/weak=4）。对比时说明窗口语义，
   以实时复现为准，快照仅作派发时点参考。
8. **drift_state 状态级翻转（★★ 2026-08-22 bffc352c，2026-08-24 扩展）**：build_proposal_drift_memory
   （endogenous_meta_cognition.py:52）取 outcomes[:12] 窗口，新 execution_finalize 挤入
   可使 drift_state 从 drifting 翻转为 correcting（实测快照 0.4484/drifting vs 实时
   0.4676/correcting，跨 0.45 阈值）。窗口滑动不止分数级差异，可产生**相反状态判定**；
   报告矛盾时必须同时给出快照值与实时复现值并注明窗口成员。
   - **三态并存**：同一时点可同时存在 快照=stable、实时=drifting、clean=correcting
     三个状态（clean=排除自任务/归档任务记录后的窗口）。第2阶段实测：排除同族归档任务
     1c67c351 后窗口 avg=0.577/partial×2/correcting，与实时 drifting/0.4977 并存。
   - **双向翻转方向**：strong 掩盖型（高分记录把 drifting 掩盖成 stable）与 weak 挤入型
     （弱分派记录挤入把 correcting 翻成 drifting，ca=0.4184 weak 记录挤入实测）两个方向
     都见过；翻转方向计数按次累计，可多次同向。
   - **本任务自记录入窗翻转（★★ 2026-08-24 晚 a61cd164）**：第3轮复核实测快照
     drifting(0.4977, weak=2) → 实时 correcting(0.4934, weak=1/partial=3)，翻转 100%
     由**本任务自身 3 条记录**（planned/review/claim 全 partial 0.5184）入窗驱动，挤出
     861aea60×2 与 1c67c351 review。窗口 3/4 为本任务记录 = 自污染 >50% 红旗；剔除后仅
     剩 1 条 → 不可判定。**快照窗口算术重建法**：已知快照 avg 与候选分数时，用
     avg×N 组合反推窗口成员（实测 0.4977×4=1.9908 = 0.577+0.577+0.4184+0.4184，
     即 861aea60 finalize/claim + 1c67c351 finalize/review），可精确归因翻转来源。
     派发快照"双坏一致"（degrading×drifting）与实时"冲突"（degrading×correcting）
     都可能是伪影——报告矛盾前必须重建快照窗口。
   - **连续窗口滑动**：同一信号在不同时点复现即漂移——归档任务时点 vs 自己复现时点
     相隔 5 分钟，missing builder 名 6→7、weak_agenda 71→72、ra 覆盖 156→157、
     PTE 窗口第6成员被替换。单时点聚合信度是窗口伪影，必须做多时点对比。
9. **recent_learning 快照计数不一致**：任务派发快照声称的 recent_learning 条数（如
   "5 条 grounding/drift 记录 avg_confidence=0.868"）与实时 cognition_state 的
   recent_learning_count（实测 3）可能不一致，属派发/执行时点窗口差异，不是数据丢失，
   不要当作证据冲突。
10. **观察基线判别规则（可复用方法）**：判定"引用对齐 vs 后效矛盾"是否真信号——
   ① 后效窗口 scored<50% → degrading 判伪信号（缺失归零）；② 有分记录全同值 →
   评分器饱和非退化；③ 快照≠实时 → 以实时为准；④ ref 高+weak/partial 标签并存是
   设计使然（missing 驱动 partial）；⑤ 仅当 scored≥50% 且分数有区分度且
   deliberation_state 缺口归零，矛盾才可能是真信号。时间序列节点 T0 planned →
   T1 review → T2 claim → T3 finalize → T4/T5 后效窗口，逐节点记录绑定字段。
11. **窗口成员任务族属核对（必做，2026-08-24）**：把状态翻转/信度下降归因给"新任务"前，
    先在 `C:/Users/lishuo/.VoidCube/runtime/supervisor/autonomous_chain_store.json`
    递归 walk 查窗口内每个 task_id 的 summary/task_type/status，确认其任务族。第2阶段
    实测窗口 4 任务全部是 observation/maintenance/review 族（含本任务自身），无一执行
    grounding 修复 → 翻转 100% 是窗口组成驱动（自污染），与真实修复状态无关。若不核对，
    很容易把"同族自污染"误判为"真实恶化"。
12. **含质量标记观测的评分准则（可复用，2026-08-24）**：任务要求每次记录显式附加任务
    质量分时，用分项加总并显式分离两个维度：
    - **观测执行质量**：数据完整度（builder 复现数/值匹配）+0.3、观测覆盖（窗口取证+
      任务身份核对）+0.2、时点对比（≥2 时点）+0.1；第2阶段自评 quality_score=0.6。
    - **观测对象质量 ≠ 执行质量**：观测对象是伪信号源（如 degrading 实为缺失归零）不扣
      观测执行分，但必须显式标注"观测到伪信号"，否则下游会误读为执行质量差。
    - 与系统评分器区分：系统 34/34 全 0.5 饱和无区分度，执行侧显式标记是独立信源。
13. **missing_evidence_nodes 溯源法（观测污染观测，★★ 2026-08-24 晚 a61cd164）**：
    自指节点（post_task_effect_memory / proposal_drift_memory / recent_learning /
    self_iteration_trend_memory）被标 missing 的次数会随时间上涨，必须先溯源是谁在声称
    缺失，再判断是真数据缺口还是观测链自污染：
    ```python
    # 对每个 selfref 节点，按 (recorded_at[:16], task_id[:8], event_type) 分组
    # 实测：pte/proposal_drift missing 各 8 条 = 4 历史(08-17 3d37fb57)
    #       + 4 条上一轮 review 任务自身(08-24 861aea60 planned/review/claim/finalize)
    ```
    结论模式：**review 链任务自身的记录把这些自指节点标为 missing → 观测污染观测**，
    计数上涨不代表系统数据真实缺失。注意区分"本任务记录是否加剧"（a61cd164 的
    missing_evidence_nodes=[] → 本轮未加剧；861aea60 的 4 条 → 上轮加剧 4→8）。
    **agenda 侧对应（2026-08-24 71889c0e 实证）**：missing_agenda_nodes 出现新类型时
    同样先溯源 + 查独立佐证 —— focus:truthfulness 4/4 全部来自上一轮复核任务
    de7838ac 自身 4 条记录，且 weak_agenda 中 truthfulness 计数=0 → 自指循环，
    缺口无独立信源。判别：统计 (recorded_at, task_id, event_type) 归属 +
    同节点 weak_agenda 计数，全部自记录且 weak 佐证为 0 = 自指。
14. **统一 grounding 冲突基线（四维判定规则，可复用 ★★ 2026-08-24 晚）**：把第10条
    观察基线推广为可重复执行的判定器，专用于裁决"degrading × drift 是否构成真实
    grounding 冲突"：
    - 维度1 PTE 可判定性：窗口 scored_ratio >= 50% 才可判定（当前 33% → 不可判定）
    - 维度2 质量区分度：带分记录须存在非饱和值(≠0.5)（当前 35/35 饱和 → 无信息量）
    - 维度3 DRIFT 窗口纯净度：自记录占比 <= 50% 才可信（当前 75% → 污染）
    - 维度4 干净窗口规模：剔除自记录后须 >= 2 条（当前 1 条 → 不可判定）
    - 收敛条件（解除不可判定）：出现 >=2 条带非饱和 quality 的改善执行类任务
      finalize 记录，且窗口内 review 链记录占比 < 50%
    - 恶化触发条件（判定真实恶化）：PTE scored_ratio>=50% 且 avg_quality<0.45 且
      dominant_target=grounding:hurt，同时 DRIFT 干净窗口>=2 条且 drift_state 连续
      两轮同向
    - 四维全绿才允许把矛盾当作真信号；任一维不满足即判伪信号/不可判定，报告需
      逐维给出当前值与阈值。

15. **failed 任务窗口污染检查（2026-08-24 5763a985 第14轮实证）**: 漂移/后效窗口取证时，除核对窗口成员任务族属（第11条）外，**还要查成员 task 的 status** —— failed/cancelled 任务的完整事件链（planned/review/claim/finalize）仍写入弱评分（实测 e1b36ad4 status=failed、ca=0.266/ra=0.4 weak×4）并被当作漂移信号源。失败任务记录同样驱动快照 drifting，与\"上一轮复核任务自身记录\"是两种不同的污染来源，报告须区分。\n16. **描述-快照错位型伪冲突（2026-08-24 5763a985 实证，第三种自消解形态）**: 任务 summary 声称的\"冲突\"可能根本不存在于派发快照层 —— 实测 summary 写 degrading×correcting，但 evidence 快照本身 drift_state=drifting（双坏一致），是描述文本与快照数值错位，无需窗口滚动即自消解。**收到声称两信号冲突的任务时，先比对 summary 文本 vs evidence 快照数值；错位即伪冲突，按快照实际状态判定，勿重建窗口。**\n17. **missing_agenda 前缀变体合并计数（2026-08-24 实证）**: 同一自指节点可能以两种字符串形态出现（'focus:truthfulness' 与 'missing_agenda:focus:truthfulness'），来自不同代生成代码。**统计 missing_agenda 时按去前缀后的节点名合并计数**，否则会双计虚高或漏计；weak_agenda 佐证=0 且计数≈复核任务数×4 时直接判自指循环，勿再溯源。\n\n18. **快照-自指一致型伪冲突（2026-08-26 666eb210 第16轮实证，与第16条错位型互补）**: summary 与派发快照数值一致 ≠ 冲突可信 —— 实测 summary 声称 degrading×correcting、快照也确实 correcting(0.49)，但快照数值本身 = 上一轮复核任务 12cd392e×4 partial(0.4884×4) 100% 自记录（算术重建 avg×N 精确命中），零 grounding 修复记录。**无论 summary 与快照是否一致，快照声称的信号先做窗口算术重建（反推窗口成员）再采信**；一致只说明派发层复述了自污染快照。另：**质量-对齐解耦定量法** —— 声称\"对齐好但后效差\"时对比 scored vs unscored 对齐分布（实测 avg_ca 0.635 vs 0.585、avg_ref 0.793 vs 0.797 几乎相同 = 伪因果），勿当 historical_underdelivery 证据。快照 100% 同族自污染已连续 6 轮（de7838ac→71889c0e→daef7f34→e1b36ad4→c5367f8a→12cd392e）。\\n## 验证
- 4 个 builder 均成功返回且 entry_count/available 字段合理
- 提取的评估条目数与时间线连续（无跳档）
- 笔记文件已落盘（bytes_written > 0），media_display 返回 status=ok
