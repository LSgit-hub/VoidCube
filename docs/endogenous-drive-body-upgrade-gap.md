# 内生驱动与替身改进链路分析（修正版）

> **2026-07 语义对齐说明**：本文保留“内生驱动 → 学习 → 替身改进 → 健康评分”的 gap 分析价值，但需按最新基线理解执行门控：监督者目标语义为全天候运行，旧时间窗口和“等用户空闲”硬门已移除；`active_sessions` 只作为认知层软感知/降权信号，不再是生成或执行的硬条件；`/auto` 开关只是当前自主链路的临时启停门控，不限制主 CLI 输入，也不阻断用户与主 Agent 交互。替身改进和 probe 可全天候自主进行，但真正 `activate_slot` 必须停在用户同意门（目标语义，待实现），不能写成 Governor 批准后自动切换。Web 小屋也只承担 API-B 动作、状态、反馈与任务的只读观测，不再应被理解成旧队列管理台或执行控制台。
>
> **2026-07-08 现状补记**：本文中的部分早期 gap 已被当前实现追上。`body_improvement` 现已进入正式 `execution_kind`、可被 API-A 自主执行面拉取执行，并已进入 Supervisor 的链路观测与 Web 小屋替身升级红点提示。当前更真实的缺口，已经从“有没有 `body_improvement` 这条链”转向“学习成果如何稳定驱动定向改进、如何自动形成建议切换、以及用户同意门如何落地”。

## 1. 当前内生驱动产生的任务

内生驱动器 `EndogenousDriveEngine._candidate_stream()` 当前围绕四类核心候选生成任务，并按 utility 降序参与后续治理：

| # | stable_key | 标题 | utility | 类型 | 约束 |
|---|-----------|------|---------|------|------|
| 1 | `continuity:memory_maintenance_sweep` | Maintain long-term memory continuity | 0.92 | memory_maintenance | 无 |
| 2 | `truthfulness:review_correction_signals` | Review recent uncertainty and correction signals | 0.65~0.95 | self_learning | learn_only |
| 3 | `creativity:idle_learning:{hash}` | Research: {topic} | 0.58~0.72 | self_learning | **learn_only, must_not_modify_active_body** |
| 4 | `continuity:governance_hygiene_review` | Review autonomous-chain governance hygiene | 0.52 | general_self_evolution | must_not_execute_without_review |

**关键发现**：全部 4 种候选都与替身代码编辑无关。创造力候选（唯一可能导向代码改进的任务）明确禁止编辑身体：

```python
constraints={
    "execution_policy": "learn_only",
    "must_not_modify_active_body": True,
}
```

## 2. 创造力候选的三层降级

```
active_sessions 软感知 + self_learning eligible + 防自撞护栏通过
  │
  ├── Tier 1: LLM 智能分析 (utility=0.72)
  │     _llm_generate_learning_topics()
  │     → 拉取压缩记忆 + Gateway 活动元数据
  │     → LLM 生成具体学习方向
  │
  ├── Tier 2: 压缩记忆提取 (utility=0.65)
  │     _mem_extract_learning_topics()
  │     → 直接用 Arc 标题作为学习主题
  │     → 不需要 LLM
  │
  ├── Tier 3: 活动元数据截取 (utility=0.58)
  │     _extract_learning_topic()
  │     → 截取 user_request.text 前 80 字符
  │
  └── Tier 4: 静态兜底 (utility=0.58)
        "Explore one unresolved learning thread"
```

触发条件：
- `active_sessions` 仅作为用户状态软感知/降权信号，不再要求等于 0
- `self_learning` 家族 `eligible_for_planning == True`
- 防自撞并发护栏通过（不与同类在途任务重复派发）
- 每周期最多产生 2 个创造力候选

## 3. 替身改进的当前实现与缺口

当前替身改进已经有两条不同层级的实现路径，不能再混写成“只有手动触发”：

### 3.1 已接上的自主链路路径

```text
监督者内生驱动
  → 形成 body_improvement 候选 / 转交任务
  → 进入自主链路治理在途
  → API-A 自主执行面 pull 任务
  → 在 shell worktree 中编辑替身代码
  → 提交改进结果 / 报告 / 回写
  → Mem / Supervisor 再读取并继续判断
```

这条链已经接上了以下能力：

- `body_improvement` 已是正式 `execution_kind`
- API-A 自主执行面可以拉取并执行
- Web 小屋替身卡片 / 树形图已能对对应节点亮红点
- Supervisor 闭环观测里已能看到转交、执行、回写和再读取

### 3.2 仍保留的身体生命周期路径

```text
POST /body/upgrade/execute
  → BodyUpgradeExecutionAdapter.execute_body_upgrade()
    ├── prepare_slot_workspace()   # 准备 Git worktree
    ├── mark_candidate()           # 标记 candidate
    ├── health_review_request      # 审查 / probe 前置门
    ├── 执行 probe                 # 技术健康检查
    ├── switch_request             # 切换审批入口（旧程序路径）
    └── execute switch             # active ↔ retired
```

这条路径更偏**身体生命周期 / probe / activate_slot**，不是“学习证据如何形成替身改进任务”的主问题。它当前最大的语义缺口也不是“能不能切”，而是：

- Governor 批准后仍应先停在 `awaiting_user_consent`
- 真正 `activate_slot` 需要用户同意门
- 不应再被写成“Governor 批准后自动切换”

## 4. 架构基线要求的链路

架构基线 §7.3-7.4 定义的身体升级链路：

```
监督者内生驱动（创造类）
  → 产出学习任务 → 进入自主链路治理在途
    → Agent 执行学习任务 → 学习成果写入 Mem
      → Agent 读取学习成果
        → 通过 Git 了解 shell 替身代码结构
          → 在 Git worktree 中编辑替身代码
            → 提交 diff/commit/进展描述到 Mem
              → 监督者整理记忆 → 判断替身进展
                → 裁决是否建议身体切换
                  → 用户同意后
                    → 执行器执行身体切换
```

**架构约束（§3.8 / §7.5）**：身体切换不由自主链路治理在途直接驱动。Governor 保有否决权，但健康值/Governor 批准都只是“建议切换”的程序前置门；真正 `activate_slot` 需用户同意（目标语义，待实现）。

## 5. 差距分析

### 已实现的部分

| 步骤 | 状态 | 说明 |
|------|------|------|
| 内生驱动产出学习任务 | ✅ | 创造力候选 (只读研究) |
| Agent 执行学习任务 | ✅ | Agent 通过 `/v1/tasks` 拉取 |
| 学习成果写入 Mem | ✅ | Agent 完成任务后写入 |
| 执行器执行身体切换 | ✅ | `BodyUpgradeExecutionAdapter` |
| Governor 裁决切换 | ✅（程序前置门） | `GovernorDecisionEngine` 保有否决权；但目标语义下 activate 仍需用户同意 |
| Mem 存储所有记忆 | ✅ | 双层记忆架构 |

### 缺失的环节

| # | 缺失环节 | 描述 |
|---|---------|------|
| **1** | 学习证据→定向改进 | `body_improvement` 已存在，但“哪些学习结果足以推成具体改进、改哪个结构节点、为什么现在改”这层映射还不够稳定 |
| **2** | 学习成果→替身改进的触发 | Agent 完成学习任务后，仍缺一条稳定、细粒度的“读 Mem 学习结论 → 锁定替身结构节点 → 生成定向改进任务”桥 |
| **3** | 替身改进候选质量 | 监督者已具备 `body_improvement` 候选与转交能力；当前不足主要是证据累积、候选质量与触发条件还不够稳定，容易继续偏向“只读研究” |
| **4** | 改进范围约束统一 | 白名单目录、禁止模式、文件数上限与 boundary 检查已存在；当前缺口在于让这组边界成为唯一正式入口，并和审查/回写链保持一致 |
| **5** | 改进→建议切换的自动化 | 从“Agent 提交改进结果”到“Supervisor / Governor 形成建议切换”之间仍缺自动桥接；从建议切换到 activate 还缺用户同意门 |
| **6** | 健康值时间衰减 | 健康值只增不减，无法反映代码腐化 |
| **7** | 改进回滚机制 | 破坏性改进后无回滚路径 |

### 关键断裂点

```
当前实际链路:
  内生驱动 → self_learning / body_improvement 候选
    → API-B 治理在途 → API-A 拉取执行 → 写入 Mem
    → Supervisor 继续观察与再读取
    → 在“学习成果如何稳定推成定向改进 / 如何形成建议切换”这里仍断链 ✗

架构基线要求:
  内生驱动 → 学习任务 → Agent 学习 → Mem
    → Agent 读 Mem + Git diff → 编辑替身 → 提交改进报告
    → Supervisor LLM 审查 → 健康值评分 → 建议切换
    → Governor 裁决（保有否决权）→ awaiting_user_consent
    → 用户同意后 → probe / activate_slot → 新 active body
```

## 6. 待讨论的设计问题

1. **替身改进任务类型**：扩展现有 `self_learning`（加 `can_modify_shell_body` 约束），新增 `body_improvement` execution_kind

2. **触发时机**：监督者检测 Mem 中有足够学习证据后，生成 `body_improvement` 任务（方案 A，推荐）

3. **改进边界**：Agent 编辑替身代码时的约束
   - 只能编辑 shell 槽位的 worktree（白名单目录）
   - 需要 Git commit + diff 记录（commit_hash 验证）
   - 需要通过 evolution_boundary 检查（细粒度评分）

4. **改进→建议切换的自动化程度**：
   - **半自动**（推荐）：Agent 编辑 → 提交 → Supervisor LLM 评分 → health_score 达标 → 产生"建议切换"事件 → Governor 审查（保有否决权）→ `awaiting_user_consent` → 用户同意后 activate

5. **学习证据的质量门槛**：基于学习成果的**累积质量评分**，而非数量

## 7. 设计方案（修正版）

### 7.1 总体思路

```
内生驱动 → 创造力候选(学习) → Agent 执行 → 写入 Mem
   │
   │ 学习成果累积质量 >= 阈值 + shell 槽位存在
   ▼
内生驱动 → body_improvement 候选(编辑代码) → Agent 拉取执行
   │
   ├── 读 Mem 学习成果 + Git diff(active↔shell)
   ├── 在 shell worktree 编辑代码（白名单目录内，≤5 文件）→ commit
   └── 提交改进报告( diff + 描述 + 学习引用 ) → Supervisor
        │
        ▼
Supervisor LLM 审查 → 健康值评分（细粒度）→ 累加至 BodySlotMeta
        │
        │ health_score >= active_health + DELTA_THRESHOLD
        ▼
产生"建议切换"事件 → Governor 审查（保有否决权）
        │
        ▼（Governor 批准后）
awaiting_user_consent → 用户同意后 → probe（技术健康检查）→ activate_slot → 新 active body
```

**Governor 权力边界**：`health_score` 达标只是"建议触发"，不是"自动切换"。Governor 接收"建议切换"事件后，进行独立审查，可批准或否决；批准后也必须停在用户同意门，不能直接 activate。

### 7.2 健康值评分公式（修正版）

```
健康值 = Σ(每次改进评分) - 时间衰减，范围 [0, 100]

单次改进评分 = 五项子分 × 权重:

  diff_quality          × 0.35   LLM 评估代码改动的实质性（0-20）
  + probe_pass_score    × 0.25   替身当前 probe 检查分数（0-20，新替身用父 slot 历史平均）
  + boundary_score      × 0.20   演化边界合规（0-20，细粒度评分）
  + learning_link_score × 0.15   改进是否引用了 Mem 中的学习成果（0-20，含新鲜度因子）
  + stability_factor    × 0.05   替身稳定运行时长因子（0-20）
  - file_penalty                  同一文件重复改进惩罚

单次 score_delta 范围：[-20, 30]
```

### 7.3 健康值时间衰减机制（新增）

```
健康值每日衰减率 = max(0, (30 - days_since_last_improvement) / 120) × 2
→ 0~30 天内不衰减
→ 30~90 天逐渐衰减，每天最多扣 2 分
→ 90 天后稳定每天扣 1 分

衰减公式：
health_score = max(0, health_score - daily_decay)
```

### 7.4 内生驱动新增第 5 种候选（修正版）

在 `_candidate_stream()` 中，creativity 候选之后插入，**带完整降级路径**：

```python
learning_quality = self._calculate_learning_quality_score()
shell_slot = self._execution_facade.body_registry.get_shell_slot()

if (learning_quality >= self.config.body_improvement_min_quality
    and shell_slot is not None
    and shell_slot.state != "improving"
    and self._passes_self_collision_guards()):

    improvement = self._generate_improvement_direction(
        mem_context    = recent_learning_findings,
        git_diff       = diff_active_vs_shell,
        learning_quality = learning_quality,
    )
    if improvement:
        candidates.append(EndogenousTaskCandidate(
            stable_key    = f"body_improvement:{hash(improvement['title'])}",
            title         = f"Improve shell body: {improvement['title']}",
            summary       = improvement['summary'],
            priority      = "high" if learning_quality >= 80 else "normal",
            governance_task_type = "self_learning",
            task_family   = "self_learning",
            execution_kind = "body_improvement",
            value_tags    = ["creativity", "continuity"],
            utility       = 0.85 if learning_quality >= 80 else 0.72,
            constraints   = {
                "execution_policy": "improve_shell_body",
                "target_slot": "shell",
                "target_slot_id": shell_slot.slot_id,
                "worktree_path": shell_slot.worktree_path,
                "must_commit": True,
                "evolution_boundary_check": True,
                "max_files_changed": 5,
                "editable_dirs": ["skills/", "tools/", "agent/", "prompts/"],
                "forbidden_patterns": [
                    "**/credential*", "**/.env*", "systems/**",
                ],
            },
            evidence = {
                "learning_quality": learning_quality,
                "shell_slot_id": shell_slot.slot_id,
                "git_diff_summary": diff_summary,
            },
        ))
```

#### 改进方向生成的三层降级

```
learning_quality >= threshold
  │
  ├── Tier 1: LLM 智能分析 (utility=0.85)
  │     _llm_generate_improvement_direction()
  │     → 分析学习成果 + Git diff + 现有改进历史
  │     → 生成具体改进方向
  │
  ├── Tier 2: 改进历史延伸 (utility=0.75)
  │     _extend_recent_improvement()
  │     → 基于最近一次改进报告的延伸建议
  │     → 不需要 LLM
  │
  └── Tier 3: Git diff 启发 (utility=0.65)
        _diff_based_suggestion()
        → 基于 active↔shell 的 diff 生成文件级改进建议
```

#### 阈值条件汇总（修正版）

| 条件 | 值 | 理由 |
|------|-----|------|
| **学习质量下限** | >= 60 | 基于学习成果质量评分，而非数量 |
| 学习质量提升优先级 | >= 80 → priority="high" | 高质量学习更值得改进 |
| shell 槽位存在 | slot-B 非空 | 没有替身就不生成改进任务 |
| shell 槽位状态 | != "improving" | 并发改进隔离保护 |
| body_improvement eligible | 防自撞护栏 + 软让路策略 | 不与同类在途任务重复派发；用户活跃只降权/择机，不硬阻断 |
| 最大文件改动数 | 5 | 防止一次改动过大 |
| **健康值切换阈值** | >= active + 15 | **相对阈值**，自适应不同 Agent |

### 7.5 Agent 执行替身改进的步骤（修正版）

Agent 拉取到 `execution_kind="body_improvement"` 任务后：

```
1. 解析任务约束 → 获取 worktree_path、editable_dirs、forbidden_patterns
2. 读 Mem → 获取最近的学习成果摘要
3. 读 Git → diff active..shell 了解当前差异
4. 读 shell worktree → 了解替身代码结构
5. 基于 1+2+3 → 决定改进目标文件（必须在 editable_dirs 内）
6. 在 shell worktree 中编辑代码（≤5 个文件，不匹配 forbidden_patterns）
7. Git commit → 记录 diff（必须在 shell slot 分支上）
8. 写改进报告到 Mem:
     - commit_hash（必须属于 shell slot worktree）
     - diff_summary
     - changed_files（必须在 editable_dirs 内）
     - learning_refs（引用的学习成果 ID + 时间戳）
     - improvement_description（为什么这样改、预期效果）
9. 通过 Gateway 通知 Supervisor 审查 → POST /body/improvement-report
10. 任务状态变为 "awaiting_review"（等待审查结果）
```

### 7.6 任务状态流转（新增）

```
created → running → awaiting_review → completed / failed / retry
                                    ↖
                    (审查失败且可重试)

- awaiting_review: Agent 已提交改进报告，等待 Supervisor 审查
- completed: Supervisor 审查通过，score_delta 已累加
- failed: 审查失败（空改进/边界违规/提交验证失败）
- retry: 可重试（如网络超时，最多重试 3 次）
```

### 7.7 待确认的前置问题

| # | 问题 | 当前状态 |
|---|------|---------|
| 1 | Agent 有 Git 工具吗？ | 需确认 `tools/` 下是否有 git 相关工具 |
| 2 | Agent 可以读写任意文件路径吗？ | 需确认文件编辑工具是否支持指定 worktree 路径 |
| 3 | shell worktree 路径 Agent 可见吗？ | `BodyRegistry` 已管理路径，任务 payload 携带 |
| 4 | `_calculate_learning_quality_score()` 存在吗？ | 需要在 planning_runtime 中新增（替代数量计数） |
| 5 | evolution_boundary 规则定义好了吗？ | `systems/evolution_boundary.py` 已有基础实现，需扩展细粒度评分 |

## 8. 补充设计（闭环完整方案）

### 8.1 闭环全链路（修正版）

```
① 内生驱动 → 创造力候选 → Agent 执行 → Mem
        │
        │ _calculate_learning_quality_score() >= 60  [新增]
        ▼
② 内生驱动 → body_improvement 候选（带降级路径）[新增第5种候选]
        │  payload: {execution_policy, worktree_path, editable_dirs, forbidden_patterns}
        ▼
③ Agent 拉取 → 解析约束 → 读 Mem + Git diff  [新增: 任务携带白名单]
        ▼
④ Agent 编辑 shell worktree（白名单内，≤5 files）→ git commit  [需确认Agent工具体]
        ▼
⑤ Agent 提交改进报告（commit_hash 需验证）[新增: POST /body/improvement-report]
        ▼
⑥ Supervisor 审查（多重验证 + LLM 评分）[新增: _review_body_improvement()]
        │  ├── commit_hash 验证（属于 shell slot worktree）
        │  ├── 白名单目录检查
        │  ├── evolution_boundary 细粒度评分
        │  └── LLM diff 质量评估
        ▼
⑦ 健康值评分 → 累加 + 时间衰减 [新增: 完整评分逻辑]
        ▼
⑧ health_score >= active_health + 15 → 产生"建议切换"事件 [修正: 相对阈值]
        ▼
⑨ Governor 审查（保有否决权）→ 批准/否决 [修正: 明确权力边界]
        ▼（批准后）
⑩ awaiting_user_consent → 用户同意后 → probe / activate_slot → active [目标语义；待实现用户同意门]
```

### 8.2 新增数据结构（修正版）

**BodySlotMeta 新增字段**：

```python
class BodySlotMeta(BaseModel):
    # ... 现有字段 ...
    
    # ── 替身健康值（新增）──
    health_score: float = 0.0          # 累加健康值 0-100，含时间衰减
    health_history: list[dict] = []    # [{score_delta, reason, reviewed_at, reviewer, commit_hash}]
    improvement_count: int = 0         # 累计改进次数
    last_improvement_at: str | None = None
    previous_healthy_commit: str | None = None  # 上一次健康的 commit（用于回滚）
    decay_applied_at: str | None = None          # 上次应用衰减的时间
```

**改进报告 Schema**（修正版）：

```python
class BodyImprovementReport(BaseModel):
    """Agent 提交的替身改进报告"""
    slot_id: str                       # 哪个槽位
    task_id: str                       # 关联的任务 ID
    commit_hash: str                   # Git commit（Supervisor 会验证归属）
    branch_name: str                   # 所属分支（必须是 shell slot 分支）
    diff_summary: str                  # 改动摘要（Agent 自述）
    changed_files: list[str]           # 改了哪些文件（相对路径）
    learning_refs: list[dict] = []     # [{mem_id, timestamp, relevance}] 引用的学习成果
    improvement_description: str       # 为什么这样改、预期效果
    executed_at: str                   # ISO timestamp
```

**学习质量评分 Schema**（新增）：

```python
class LearningQualityScore(BaseModel):
    """学习成果累积质量评分"""
    total_score: float                 # 综合质量 0-100
    recent_count: int                  # 最近 30 天完成的学习任务数
    avg_quality: float                 # 平均质量评分
    freshness_bonus: float             # 新鲜度加成（0-20）
    correction_signal_count: int       # 关联的错误修正信号数
```

### 8.3 新增 API 端点

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/body/improvement-report` | Agent 提交改进报告 |
| GET | `/body/{slot_id}/health` | 查询指定槽位的健康值 |
| GET | `/body/{slot_id}/health/history` | 查询健康值历史 |
| POST | `/body/{slot_id}/health/reset` | 重置健康值（管理员） |
| POST | `/body/{slot_id}/rollback` | 回滚到上一次健康的 commit（管理员） |

### 8.4 新增方法（修正版）

**`_calculate_learning_quality_score()`** — planning_runtime.py：

```python
def _calculate_learning_quality_score(self) -> float:
    """计算学习成果累积质量评分（替代简单的数量计数）"""
    recent_tasks = self._autonomous_chain_store.list_tasks(
        status="completed",
        task_family="self_learning",
        time_window_days=30,
    )
    
    if not recent_tasks:
        return 0.0
    
    total_quality = 0.0
    correction_signals = 0
    
    for task in recent_tasks:
        quality = task.metadata.get("learning_quality", 50.0)
        age_days = (datetime.now(timezone.utc) - task.completed_at).days
        freshness_factor = max(0, 1 - age_days / 90)
        
        total_quality += quality * freshness_factor
        
        if task.metadata.get("correction_signal", False):
            correction_signals += 1
    
    avg_quality = total_quality / len(recent_tasks)
    freshness_bonus = min(20, correction_signals * 5)
    
    return min(100, avg_quality + freshness_bonus)
```

**`_review_body_improvement()`** — planning_runtime.py（修正版）：

```python
async def _review_body_improvement(self, report: BodyImprovementReport):
    """审查替身改进 → 健康值评分（含多重验证）"""
    
    # 1. 空改进检测
    if not report.changed_files or not report.commit_hash:
        self._update_task_status(report.task_id, "failed", reason="empty_improvement")
        return {"score_delta": 0, "reject_reason": "empty_improvement"}
    
    # 2. commit_hash 验证（完整性保护）
    registry = self._execution_facade.body_registry.load_registry()
    slot_meta = registry.load_slot_meta(report.slot_id)
    
    if not self._verify_commit_ownership(
        report.commit_hash, 
        report.slot_id, 
        report.branch_name
    ):
        self._update_task_status(report.task_id, "failed", reason="invalid_commit")
        return {"score_delta": 0, "reject_reason": "invalid_commit"}
    
    # 3. 白名单目录检查（安全边界）
    forbidden_patterns = self.config.body_improvement_forbidden_patterns
    for file_path in report.changed_files:
        if self._matches_forbidden_pattern(file_path, forbidden_patterns):
            self._update_task_status(report.task_id, "failed", reason="forbidden_file")
            return {"score_delta": 0, "reject_reason": "forbidden_file"}
    
    # 4. 演化边界检查（细粒度评分，0-20）
    boundary = self._classify_evolution_changes(report.changed_files)
    boundary_score = boundary.score  # 细粒度评分，非二元
    
    # 5. 同一文件重复改进检测（边际递减）
    file_penalty = self._calc_file_repeat_penalty(report.slot_id, report.changed_files)
    
    # 6. 学习成果新鲜度检查
    learning_freshness = self._calc_learning_freshness(report.learning_refs)
    
    # 7. LLM 审查 diff 质量
    diff_text = self._get_git_diff(report.slot_id, report.commit_hash)
    llm_score = await self._llm_review_diff(
        diff_text, report.diff_summary, report.learning_refs)
    
    # 8. probe 通过率（新替身用父 slot 历史平均）
    probe_score = self._get_probe_score(report.slot_id, slot_meta)
    
    # 9. 综合评分（修正：移除自相矛盾的 self_confidence）
    score_delta = (
        llm_score * 0.35                # LLM 评估代码质量（0-20）
        + probe_score * 0.25            # probe 分数（0-20）
        + boundary_score * 0.20         # 边界合规（0-20）
        + learning_freshness * 0.15     # 学习引用新鲜度（0-20）
        + self._calc_stability_factor(report.slot_id) * 0.05  # 稳定因子（0-20）
        - file_penalty                  # 重复文件惩罚
    )
    score_delta = max(-20, min(30, score_delta))  # 单次范围 [-20, 30]
    
    # 10. 应用时间衰减
    score_delta = self._apply_health_decay(report.slot_id, score_delta, slot_meta)
    
    # 11. 累加健康值
    slot_meta.health_score = max(0, min(100, slot_meta.health_score + score_delta))
    slot_meta.health_history.append({
        "score_delta": score_delta,
        "task_id": report.task_id,
        "commit_hash": report.commit_hash,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reason": f"LLM={llm_score:.1f}, probe={probe_score:.1f}, boundary={boundary_score:.1f}",
    })
    slot_meta.improvement_count += 1
    slot_meta.last_improvement_at = datetime.now(timezone.utc).isoformat()
    slot_meta.previous_healthy_commit = report.commit_hash  # 记录健康 commit
    
    # 12. 更新任务状态
    if score_delta >= 0:
        self._update_task_status(report.task_id, "completed")
    else:
        self._update_task_status(report.task_id, "completed", reason="negative_score")
    
    # 13. 达到相对阈值或超过 active → 产生"建议切换"事件（不是自动 activate）
    active_slot = registry.get_active_slot()
    if (active_slot is not None
        and (slot_meta.health_score > active_slot.health_score
             or slot_meta.health_score >= active_slot.health_score + 15)):
        
        await self._emit_switch_suggestion_event(report.slot_id)
    
    registry.save_slot_meta(report.slot_id, slot_meta)
    
    return {
        "score_delta": score_delta,
        "health_score": slot_meta.health_score,
        "improvement_count": slot_meta.improvement_count,
    }
```

**`_verify_commit_ownership()`** — planning_runtime.py（新增，完整性保护）：

```python
def _verify_commit_ownership(self, commit_hash: str, slot_id: str, branch_name: str) -> bool:
    """验证 commit_hash 是否属于指定 slot 的 worktree 和分支"""
    slot = self._execution_facade.body_registry.get_slot(slot_id)
    if slot is None:
        return False
    
    commits = self._git_client.list_commits(slot.worktree_path, branch_name)
    return commit_hash in [c["hash"] for c in commits]
```

**`_apply_health_decay()`** — planning_runtime.py（新增，时间衰减）：

```python
def _apply_health_decay(self, slot_id: str, score_delta: float, slot_meta: BodySlotMeta) -> float:
    """应用健康值时间衰减"""
    if slot_meta.last_improvement_at is None:
        return score_delta
    
    last_improvement = datetime.fromisoformat(slot_meta.last_improvement_at)
    days_since = (datetime.now(timezone.utc) - last_improvement).days
    
    if days_since <= 30:
        return score_delta
    
    daily_decay = min(2.0, (days_since - 30) / 60 * 2.0)
    decay_amount = daily_decay * (days_since - 30)
    
    return score_delta - decay_amount
```

**`_get_probe_score()`** — planning_runtime.py（修正，新替身初始值）：

```python
def _get_probe_score(self, slot_id: str, slot_meta: BodySlotMeta) -> float:
    """获取 probe 分数，新替身用父 slot 历史平均"""
    if slot_meta.probe_result is not None:
        return self._calc_probe_pass_score(slot_meta.probe_result)
    
    parent_id = slot_meta.parent_slot_id
    if parent_id is not None:
        parent_meta = self._execution_facade.body_registry.load_slot_meta(parent_id)
        if parent_meta.health_history:
            avg_probe = sum(h.get("probe_score", 10) for h in parent_meta.health_history) / len(parent_meta.health_history)
            return min(20, avg_probe)
    
    return 10.0  # 默认初始值
```

**`_calc_learning_freshness()`** — planning_runtime.py（新增，新鲜度衡量）：

```python
def _calc_learning_freshness(self, learning_refs: list[dict]) -> float:
    """计算学习成果新鲜度分数（0-20）"""
    if not learning_refs:
        return 0.0
    
    total_freshness = 0.0
    now = datetime.now(timezone.utc)
    
    for ref in learning_refs:
        ref_time = datetime.fromisoformat(ref.get("timestamp", ""))
        days_old = (now - ref_time).days
        freshness = max(0, 1 - days_old / 90) * 20
        total_freshness += freshness * ref.get("relevance", 1.0)
    
    return min(20, total_freshness / len(learning_refs))
```

**`_emit_switch_suggestion_event()`** — planning_runtime.py（新增，事件驱动）：

```python
async def _emit_switch_suggestion_event(self, slot_id: str):
    """产生"建议切换"事件，Governor 接收后独立审查；批准后仍需用户同意"""
    event = SwitchSuggestionEvent(
        slot_id=slot_id,
        health_score=self._execution_facade.body_registry.load_slot_meta(slot_id).health_score,
        suggested_at=datetime.now(timezone.utc).isoformat(),
        reason="health_score_threshold_reached",
    )
    await self._governor_engine.receive_event(event)
```

### 8.5 边界条件处理（修正版）

| 场景 | 处理规则 |
|------|---------|
| **空改进** (diff 为空) | 直接拒绝，score_delta = 0，任务状态 failed |
| **commit_hash 验证失败** | 拒绝，任务状态 failed（防止提交伪造 commit） |
| **修改禁止文件** | 拒绝，任务状态 failed（白名单目录保护） |
| **破坏性改进** (probe 全部失败) | score_delta 保底 -20，健康值大幅扣减 |
| **同文件反复改** | `_calc_file_repeat_penalty()` 检测：同一文件第 N 次改 → penalty = (N-1) × 5 |
| **改进停滞** | 任务 status=running 超过 2 小时 → auto-failed（已有超时机制） |
| **切换失败回滚** | 回滚到 `previous_healthy_commit`，健康值降为 active slot 的健康值 |
| **竞争条件** | `body_improvement` 候选生成前检查 slot.state != "improving" |
| **用户未同意切换** | 停留在 `awaiting_user_consent`，继续展示替身健康与风险，不自动 activate |
| **健康值上限** | max=100，超过不累加 |
| **健康值下限** | min=0，不出现负数 |
| **时间衰减** | 30 天内不衰减，30-90 天逐渐衰减，90 天后稳定衰减 |
| **手动重置** | `POST /body/{slot_id}/health/reset` 管理员可重置 |
| **手动回滚** | `POST /body/{slot_id}/rollback` 管理员可回滚到上次健康 commit |
| **Governor 否决** | "建议切换"事件被否决后，健康值保留，但需等待下次改进周期 |

### 8.6 LLM 审查 diff 的实现（修正版）

```python
async def _llm_review_diff(self, diff_text: str, description: str, learning_refs: list[dict]) -> float:
    """LLM 评估代码改动质量，返回 0-20 的分数"""
    
    learning_context = []
    for ref in learning_refs:
        mem_item = await self._mem_client.get_item(ref["mem_id"])
        learning_context.append({
            "id": ref["mem_id"],
            "summary": mem_item.summary if mem_item else "",
            "relevance": ref.get("relevance", 1.0),
        })
    
    prompt = (
        f"评估以下替身 Agent 的代码改进质量（0-20分）。\n\n"
        f"【改进描述】{description}\n"
        f"【引用的学习成果】{json.dumps(learning_context, ensure_ascii=False)}\n"
        f"【代码 Diff】\n{diff_text[:4000]}\n\n"
        f"评分维度（每项 0-5 分）：\n"
        f"1. 改动实质性：是否为非格式化/非注释的实际改进\n"
        f"2. 学习支撑：改动是否有学习成果支撑\n"
        f"3. 合理范围：是否为非破坏性变更\n"
        f"4. 代码质量：改动是否提升代码质量\n"
        f"输出JSON: {{\"score\": 0-20, \"reason\": \"...\", \"dimension_scores\": [0-5, 0-5, 0-5, 0-5]}}"
    )
    
    result = self._llm_client.complete_json(
        system_prompt="你是代码审查专家。客观评估代码改进质量。",
        user_payload={"task": prompt},
        task="scholar.revision",
    )
    
    if isinstance(result, dict) and "score" in result:
        return float(result["score"])
    
    return 10.0  # 默认分数
```

### 8.7 evolution_boundary 细粒度评分（新增）

```python
class EvolutionBoundaryResult(BaseModel):
    """演化边界检查结果（细粒度评分）"""
    ok: bool
    score: float  # 0-20，细粒度评分
    violations: list[str]
    warnings: list[str]

def classify_agent_evolution_changes(changed_files: list[str]) -> EvolutionBoundaryResult:
    """分类 Agent 演化变更，返回细粒度评分"""
    EDGE_DIRS = {"skills/", "tools/", "agent/", "prompts/", "config/"}
    WARN_DIRS = {"systems/", "core/"}
    FORBIDDEN_DIRS = {"security/", "auth/", "secrets/"}
    
    violations = []
    warnings = []
    score = 20.0
    
    for file in changed_files:
        file_lower = file.lower()
        
        if any(file_lower.startswith(d) for d in FORBIDDEN_DIRS):
            violations.append(f"FORBIDDEN: {file}")
            score -= 10.0
        
        elif any(file_lower.startswith(d) for d in WARN_DIRS):
            warnings.append(f"WARN: {file}")
            score -= 3.0
        
        elif not any(file_lower.startswith(d) for d in EDGE_DIRS):
            warnings.append(f"UNVERIFIED: {file}")
            score -= 2.0
    
    score = max(0, score)
    
    return EvolutionBoundaryResult(
        ok=len(violations) == 0,
        score=score,
        violations=violations,
        warnings=warnings,
    )
```

### 8.8 健康值可视化（修正版）

Supervisor UI `/ui/state` 新增 `body_health` 字段：

```json
{
  "body_health": {
    "slot-A": {
      "health_score": 85,
      "improvement_count": 0,
      "status": "active",
      "last_improvement_at": null
    },
    "slot-B": {
      "health_score": 62,
      "improvement_count": 4,
      "status": "improving",
      "last_improvement_at": "2026-06-24T10:30:00Z",
      "decay_pending": 3.5,
      "switch_suggested": false
    }
  }
}
```

CLI 状态栏在替身槽位显示健康值进度条：`[slot-B ██████░░░░ 62% ↻4]`

### 8.9 改进回滚机制（新增）

```python
async def _rollback_to_healthy_commit(self, slot_id: str):
    """回滚到上一次健康的 commit"""
    registry = self._execution_facade.body_registry.load_registry()
    slot_meta = registry.load_slot_meta(slot_id)
    
    if slot_meta.previous_healthy_commit is None:
        raise ValueError("No healthy commit to rollback to")
    
    slot = registry.get_slot(slot_id)
    await self._git_client.reset(slot.worktree_path, slot_meta.previous_healthy_commit)
    
    slot_meta.health_score *= 0.7  # 惩罚：健康值打 7 折
    slot_meta.health_history.append({
        "score_delta": -slot_meta.health_score * 0.3,
        "reason": "rollback_to_healthy_commit",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    
    registry.save_slot_meta(slot_id, slot_meta)
```

### 8.10 实现优先级（修正版）

| 优先级 | 任务 | 依赖 |
|--------|------|------|
| **P0** | BodySlotMeta 新增 health_score + history + previous_healthy_commit | 无 |
| **P0** | POST /body/improvement-report 端点（含 commit_hash 验证） | P0 |
| **P0** | `_calculate_learning_quality_score()`（替代数量计数） | 无 |
| **P0** | evolution_boundary 细粒度评分（0-20） | 无 |
| **P0** | shell slot 白名单目录 + 禁止模式 | 无 |
| **P1** | 内生驱动第 5 种候选 body_improvement（带降级路径） | P0 |
| **P1** | `_review_body_improvement()` 完整逻辑 | P0 |
| **P1** | `_llm_review_diff()` | P1 |
| **P1** | 健康值时间衰减机制 | P0 |
| **P2** | 相对阈值触发 + "建议切换"事件 | P1 |
| **P2** | Governor 事件处理（接收建议切换事件） | P2 |
| **P2** | `awaiting_user_consent` 用户同意门（阻断自动 activate） | P2 |
| **P2** | 改进回滚机制 | P0 |
| **P2** | 学习成果新鲜度衡量 | P0 |
| **P3** | UI 健康值可视化 | P0 |
| **P3** | CLI 进度条显示 | P2 |

## 9. 关键修正点总结

| # | 原问题 | 修正方案 |
|---|--------|---------|
| 1 | Governor 裁决路径被绕过 | 改为事件驱动：健康值达标 → 产生"建议切换"事件 → Governor 独立审查（保有否决权）→ 用户同意后 activate |
| 2 | LLM 审查与 Governor 审查重复 | LLM 评分是策略层面（健康值），probe 是技术层面，两者互补 |
| 3 | 健康值无时间衰减 | 新增时间衰减机制：30 天内不衰减，之后逐渐衰减 |
| 4 | 学习次数阈值 10 无依据 | 改为基于学习成果质量评分（`_calculate_learning_quality_score`） |
| 5 | body_improvement 无降级路径 | 新增三层降级：LLM → 改进历史延伸 → Git diff 启发 |
| 6 | 改进报告提交后任务状态未定义 | 新增 `awaiting_review` 状态，审查后更新为 `completed`/`failed` |
| 7 | evolution_boundary 二元判断 | 改为细粒度评分（0-20），边界模糊时有中间分数 |
| 8 | shell slot 无白名单 | 新增 `editable_dirs` 和 `forbidden_patterns` 约束 |
| 9 | commit_hash 无验证 | 新增 `_verify_commit_ownership()` 验证归属 |
| 10 | self_confidence 权重自相矛盾 | 删除 self_confidence 作为独立权重项 |
| 11 | 新替身 probe_pass_rate=0 | 新替身用父 slot 历史平均，或默认 10 分 |
| 12 | 固定阈值 85 | 改为相对阈值：`health_score >= active_health + 15` |
| 13 | 并发改进无隔离 | 候选生成前检查 slot.state != "improving" |
| 14 | 改进回滚机制缺失 | 新增 `previous_healthy_commit` 字段 + 回滚方法 |
| 15 | 学习成果无新鲜度衡量 | 新增 `_calc_learning_freshness()` 考虑时间衰减 |
| 16 | 建议切换后缺用户同意门 | 新增 `awaiting_user_consent`，Governor 批准后仍不得自动 activate |

## 10. 安全边界清单

| 边界类型 | 保护措施 |
|---------|---------|
| **文件访问** | 白名单目录 + 禁止模式 + 运行时检查 |
| **代码完整性** | commit_hash 验证 + 分支归属验证 |
| **架构一致性** | Governor 保有否决权，真正 activate 需用户同意 |
| **质量控制** | LLM 审查 + probe 检查 + 边界评分 |
| **时间衰减** | 健康值自动衰减，防止过时改进长期生效 |
| **回滚能力** | previous_healthy_commit + 一键回滚 |

## 11. 实现注意事项

以下 5 个点方案正确，但实施时需注意补齐底层依赖：

### 11.1 相对阈值的边界兜底

```
若 active_health = 90，阈值 = 105，但健康值上限 100
→ shell 永远达不到。需要兜底规则：

if shell.health_score > active.health_score:
    # 即使差值 < 15，只要超过 active 就建议切换
    await _emit_switch_suggestion_event(slot_id)
elif shell.health_score >= active.health_score + 15:
    # 正常相对阈值路径
    await _emit_switch_suggestion_event(slot_id)
```

### 11.2 Governor 事件接收接口

`GovernorDecisionEngine` 当前只有 `evaluate(request)` 方法，没有 `receive_event()`。两种实现方式：

- **方案 A（推荐）**：`_emit_switch_suggestion_event` 构造一个 `GovernorRequest(event_type="switch_suggestion")`，调用现有 `evaluate()` 方法
- **方案 B**：新增 `GovernorDecisionEngine.receive_event()` 方法

方案 A 复用现有接口，改动最小。

### 11.3 `parent_slot_id` 字段

`BodySlotMeta` 当前没有 `parent_slot_id`。`_get_probe_score()` 中需要它来获取父 slot 的历史平均 probe 分。替代方案：

- 在 `materialize_slot_workspace()` 时设置 `meta.materialized_from = source_slot_id`
- 或通过 `registry.active_slot` 作为 parent 的隐式引用（shell 从 active materialize）

### 11.4 `slot.state = "improving"` 状态

当前 `BodyState = Literal["shell", "candidate", "probe", "active", "retired"]`，不含 `"improving"`。

- **方案 A（推荐）**：不扩展状态机，改用 `BodySlotMeta.metadata["improving"] = True` 标记
- **方案 B**：扩展 `BodyState` 增加 `"improving"` 状态，需同步更新状态转换表

方案 A 改动最小，且不干扰现有状态机。

### 11.5 三个不存在的底层依赖

| 引用 | 所在方法 | 替代实现 |
|------|---------|---------|
| `self._git_client` | `_verify_commit_ownership` | 使用 `BodyRegistry._git_head_for_path(worktree)` 或 `subprocess.run(["git","log","--format=%H"])` |
| `self._git_client` | `_rollback_to_healthy_commit` | 使用 `subprocess.run(["git","reset","--hard",commit_hash])` 在 worktree 中执行 |
| `self._mem_client.get_item()` | `_llm_review_diff` | 使用 HTTP GET `{memory_service}/compressed/{mem_id}` |
| `list_tasks(status, task_family, time_window)` | `_calculate_learning_quality_score` | 扩展 `AutonomousChainStore.list_tasks()` 增加过滤参数，或先获取全部再手动过滤 |

这些在 P0 阶段一次性补齐。

### 11.6 `runtime_task_profile` 扩展

当前 `normalize_runtime_task_family()` 已补入 `body_improvement` 映射：

```python
# runtime_task_profile.py normalize_runtime_task_family()
if normalized in {"body_upgrade", "body_improvement"}:
    return "body_upgrade"
if normalized == "body_switch":
    return "body_switch"
```

这样 `body_improvement` 任务会走 `body_upgrade` 的执行路径，复用现有的 `BodyUpgradeExecutionAdapter`；`body_switch` 保持独立 task family，避免身体改进与身体切换在治理、审计和用户同意门上混成同一类任务。

### 11.7 `BodySlotMeta` 字段补充

当前 `BodySlotMeta`（[body_registry.py:47-79](file:///f:/My_code/Traecode/VoidCube/systems/body_registry.py#L47-L79)）缺少方案中定义的健康值相关字段：

```python
class BodySlotMeta(BaseModel):
    # ... 现有字段 ...
    
    # ── 替身健康值（新增）──
    health_score: float = 0.0                     # 累加健康值 0-100
    health_history: list[dict] = Field(default_factory=list)  # 健康值变更历史
    improvement_count: int = 0                    # 累计改进次数
    last_improvement_at: Optional[str] = None     # 上次改进时间（ISO）
    previous_healthy_commit: Optional[str] = None  # 上次健康的 commit（用于回滚）
    decay_applied_at: Optional[str] = None         # 上次应用衰减的时间（ISO）
```

### 11.8 `AutonomousChainTaskStatus` 扩展

当前任务状态（[autonomous_chain_store.py:17](file:///f:/My_code/Traecode/VoidCube/systems/supervisor/autonomous_chain_store.py#L17)）缺少方案中需要的状态：

```python
AutonomousChainTaskStatus = Literal[
    "planned", "deferred", "approved", "running", "paused",
    "cancelled", "completed", "failed", "awaiting_review", "retry"
]
```

- `awaiting_review`: Agent 已提交改进报告，等待 Supervisor 审查
- `retry`: 审查失败且可重试（如网络超时，最多重试 3 次）

### 11.9 时间衰减调用时机调整

方案原设计在 `_review_body_improvement()` 的第 10 步调用 `_apply_health_decay()`，但时间衰减应该是**周期性**的，不是每次改进时才计算。

**调整后**：在审查开始时先应用累积的时间衰减，再计算 score_delta：

```python
async def _review_body_improvement(self, report: BodyImprovementReport):
    # 0. 先应用累积的时间衰减（自上次衰减以来的总衰减）
    registry = self._execution_facade.body_registry.load_registry()
    slot_meta = registry.load_slot_meta(report.slot_id)
    await self._apply_cumulative_decay(slot_meta)
    
    # 1. 空改进检测
    # ...
```

**累积衰减方法**：

```python
def _apply_cumulative_decay(self, slot_meta: BodySlotMeta):
    """应用自上次衰减以来的累积时间衰减"""
    if slot_meta.decay_applied_at is None:
        slot_meta.decay_applied_at = datetime.now(timezone.utc).isoformat()
        return
    
    last_decay = datetime.fromisoformat(slot_meta.decay_applied_at)
    now = datetime.now(timezone.utc)
    days_since_decay = (now - last_decay).days
    
    if days_since_decay <= 0:
        return
    
    # 计算总衰减量
    if slot_meta.last_improvement_at is None:
        days_since_improvement = days_since_decay
    else:
        last_improvement = datetime.fromisoformat(slot_meta.last_improvement_at)
        days_since_improvement = (now - last_improvement).days
    
    if days_since_improvement <= 30:
        total_decay = 0.0
    else:
        daily_decay = min(2.0, (days_since_improvement - 30) / 60 * 2.0)
        total_decay = daily_decay * min(days_since_decay, days_since_improvement - 30)
    
    slot_meta.health_score = max(0, slot_meta.health_score - total_decay)
    slot_meta.decay_applied_at = now.isoformat()
    
    if total_decay > 0:
        slot_meta.health_history.append({
            "score_delta": -total_decay,
            "reason": "time_decay",
            "reviewed_at": now.isoformat(),
        })
```

**调用时机**：除了改进审查时，还应在：
- 内生驱动循环开始时（`_run_endogenous_drive_cycle`）
- 健康值查询时（`GET /body/{slot_id}/health`）

### 11.10 辅助方法清单

`_review_body_improvement()` 依赖以下未定义的辅助方法，需在 `planning_runtime.py` 中实现：

| 方法 | 用途 | 依赖 |
|------|------|------|
| `_update_task_status(task_id, status, reason)` | 更新任务状态 | `AutonomousChainStore` |
| `_classify_evolution_changes(changed_files)` | 演化边界细粒度评分（0-20） | `evolution_boundary.py` |
| `_calc_file_repeat_penalty(slot_id, changed_files)` | 同一文件重复改进惩罚 | `BodySlotMeta.health_history` |
| `_calc_learning_freshness(learning_refs)` | 学习成果新鲜度分数（0-20） | 无 |
| `_get_git_diff(slot_id, commit_hash)` | 获取指定 commit 的 diff | `subprocess.run()` + worktree_path |
| `_matches_forbidden_pattern(file_path, patterns)` | 文件路径是否匹配禁止模式 | `fnmatch` |
| `_calc_stability_factor(slot_id)` | 替身稳定运行时长因子（0-20） | `BodySlotMeta` |
| `_llm_review_diff(diff_text, description, learning_refs)` | LLM 评估代码改动质量 | LLM client |
| `_get_probe_score(slot_id, slot_meta)` | 获取 probe 分数（新替身用父 slot 平均） | `BodySlotMeta.last_probe_result` |
| `_apply_cumulative_decay(slot_meta)` | 应用累积时间衰减 | `BodySlotMeta` |
| `_emit_switch_suggestion_event(slot_id)` | 发送"建议切换"事件给 Governor；批准后进入用户同意门 | `GovernorDecisionEngine.evaluate()` + `awaiting_user_consent` |

### 11.11 `BodyRegistryManager` 方法补充

当前 `BodyRegistryManager`（[body_registry.py:112](file:///f:/My_code/Traecode/VoidCube/systems/body_registry.py#L112)）缺少方案中依赖的方法：

```python
def get_shell_slot(self) -> Optional[BodySlotMeta]:
    """获取 shell 槽位的元数据"""
    registry = self.load_registry()
    if registry.shell_slot:
        return self.load_slot_meta(registry.shell_slot)
    return None

def get_active_slot(self) -> Optional[BodySlotMeta]:
    """获取 active 槽位的元数据"""
    registry = self.load_registry()
    if registry.active_slot:
        return self.load_slot_meta(registry.active_slot)
    return None
```

### 11.12 API 端点注册

新增的 API 端点需要在 [systems/execution/service.py](file:///f:/My_code/Traecode/VoidCube/systems/execution/service.py) 中注册，参考现有 `/body/upgrade/execute` 的注册方式：

| 方法 | 路径 | 处理函数 |
|------|------|---------|
| POST | `/body/improvement-report` | `facade.submit_body_improvement_report()` |
| GET | `/body/{slot_id}/health` | `facade.get_slot_health(slot_id)` |
| GET | `/body/{slot_id}/health/history` | `facade.get_slot_health_history(slot_id)` |
| POST | `/body/{slot_id}/health/reset` | `facade.reset_slot_health(slot_id)` |
| POST | `/body/{slot_id}/rollback` | `facade.rollback_slot(slot_id)` |

---

*文档版本：修正版 v1.0 | 最后更新：2026-06-25*

