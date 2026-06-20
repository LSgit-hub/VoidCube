# VoidCube 自学习知识底座

## 自愈自进化系统的理论与工程基石

---

> **核心命题**：三元架构的自愈自进化系统需要一个"知识底座"来回答三个根本问题：
> **往哪进化？为什么这样进化？如何验证进化正确？**
>
> 没有知识底座的进化系统是无头苍蝇——它不知道什么是"更好"，只能盲目试错。
> 有了知识底座，进化系统才能做出**基于证据的、有方向的、可验证的**升级决策。

---

## 目录

1. [问题：盲目的自进化为什么危险](#一问题盲目的自进化为什么危险)
2. [解法：自学习系统作为知识底座](#二解法自学习系统作为知识底座)
3. [全景架构：知识驱动的自愈自进化闭环比](#三全景架构知识驱动的自愈自进化闭环比)
4. [知识底座核心设计：六阶段学习循环](#四知识底座核心设计六阶段学习循环)
5. [知识底座与自进化系统的深度耦合](#五知识底座与自进化系统的深度耦合)
6. [数据模型设计](#六数据模型设计)
7. [可持续迭代机制](#七可持续迭代机制)
8. [配置设计](#八配置设计)
9. [实施路线图](#九实施路线图)
10. [设计总结](#十设计总结)

---

## 一、问题：盲目的自进化为什么危险

VoidCube 的三元架构已经实现了一个精巧的自进化闭环：

```
身体轮换 → 候选构建 → 探针检查 → 治理审批 → 蓝绿切换 → 观察窗口 → 回滚或退役
```

这个闭环能**安全地执行**升级，但它缺少一个关键前提：**升级什么？为什么升级？**

### 当前系统的"盲区"

| 环节 | 当前能做的 | 缺少的 |
|------|-----------|--------|
| 升级决策 | 审批通过/拒绝 | **为什么这个升级是值得的？** |
| 补丁生成 | 分析自身代码 → 生成补丁 | **行业最佳实践是什么？有没有更好的方案？** |
| 技术选型 | 基于现有依赖 | **Python 有没有新特性可以替代？有没有更成熟的库？** |
| 架构改进 | 局部优化 | **前沿的 Agent 架构设计是什么？我们的方向对吗？** |
| 安全加固 | 已知漏洞修复 | **最新的安全威胁和防御实践是什么？** |

### 盲目进化的代价

```
没有知识底座的盲目进化：
┌────────────────────────────────────────────┐
│ Agent: "我看到一个更好的写法，让我升级自己"    │
│                                            │
│ → 基于局部优化做出全局改动                    │
│ → 引入了一个废弃的 API（行业已不推荐）          │
│ → 探针通过，治理审批通过                      │
│ → 上线后性能下降 30%                         │
│ → 回滚，浪费一次身体轮换机会                   │
└────────────────────────────────────────────┘

有知识底座的明智进化：
┌────────────────────────────────────────────┐
│ Agent: "我注意到有一个升级机会"               │
│                                            │
│ → 查询知识底座：这个技术被评估为多少分？         │
│ → 查询知识底座：有没有更新的替代方案？           │
│ → 查询知识底座：社区推荐的做法是什么？           │
│ → 基于多维证据做出升级提案                    │
│ → 治理引擎看到：置信度0.92，有3个权威来源支撑    │
│ → 审批通过，升级成功                         │
└────────────────────────────────────────────┘
```

---

## 二、解法：自学习系统作为知识底座

### 2.1 知识底座定义

知识底座是一个**持续自更新的技术知识生态系统**，它使 VoidCube Agent 拥有：
- **技术世界观**：知道当前技术生态中什么存在、什么过时、什么是趋势
- **判断力**：能评估一项技术的价值（多维评分）
- **记忆力**：跨会话持久化的知识图谱，随时间演化
- **前瞻性**：主动追踪关注领域的最新技术动态

### 2.2 知识底座与三元架构的关系

```
              ┌───────────────────────────────────┐
              │          VoidCube 三元架构          │
              │                                   │
              │  ┌─────────┐    ┌────────────┐   │
              │  │ 智力层   │    │  躯体层      │   │
              │  │ (LLM)   │    │ (Agent执行)  │   │
              │  └────┬────┘    └─────┬──────┘   │
              │       │               │           │
              │       │   ┌───────────┘           │
              │       │   │                       │
              │  ┌────▼───▼──────────────┐        │
              │  │      灵魂层 (MemAI)     │        │
              │  │  ← 身份连续性载体 →     │        │
              │  └───────────────────────┘        │
              │                                   │
              │  ┌───────────────────────────┐    │
              │  │   自学习知识底座 (NEW)      │    │
              │  │   - 技术知识图谱           │    │
              │  │   - 五维评估体系           │    │
              │  │   - 知识演化引擎           │    │
              │  │   - 学习策略优化           │    │
              │  └───────┬───────────────────┘    │
              │          │                        │
              │  ┌───────▼───────────────────┐    │
              │  │   自愈自进化系统 (现有)      │    │
              │  │   - 双槽蓝绿部署           │    │
              │  │   - 探针健康检查           │    │
              │  │   - 治理决策引擎           │    │
              │  │   - 观察窗口与回滚          │    │
              │  └───────────────────────────┘    │
              │                                   │
              └───────────────────────────────────┘
```

**知识底座不是替代自进化系统，而是为自进化系统提供"决策依据"。**

两者分工如下：

| 知识底座（新） | 自进化系统（现有） |
|--------------|-----------------|
| 回答 **WHAT**：应该升级什么？ | 执行 **HOW**：如何安全升级？ |
| 回答 **WHY**：为什么这个升级值得？ | 执行 **WHEN**：何时执行升级？ |
| 提供 **证据**：权威来源、社区共识 | 提供 **安全网**：探针、回滚、观察 |
| **方向**（知识驱动的目标选择） | **轨道**（受控的执行管道） |

---

## 三、全景架构：知识驱动的自愈自进化闭环比

### 3.1 完整闭环比

```
                        ┌──────────────────────────────────┐
                        │                                  │
              ┌─────────▼──────────┐                      │
              │   Phase A          │                      │
              │   知识发现与沉淀    │                      │
              │   (六阶段学习循环)  │                      │
              └─────────┬──────────┘                      │
                        │                                  │
                        │ 高价值技术知识、最佳实践、行业趋势   │
                        │                                  │
              ┌─────────▼──────────┐                      │
              │   Phase B          │                      │
              │   进化需求识别      │                      │
              │   知识底座查询      │                      │
              │   "根据最新知识，    │                      │
              │    我应该升级什么？" │                      │
              └─────────┬──────────┘                      │
                        │                                  │
                        │ 升级提案 + 知识证据                │
                        │                                  │
              ┌─────────▼──────────┐                      │
              │   Phase C          │                      │
              │   治理审批 (现有)   │                      │
              │   基于知识证据      │                      │
              │   做出 approve/     │                      │
              │   reject/rollback  │                      │
              └─────────┬──────────┘                      │
                        │                                  │
                        │ approve                         │
              ┌─────────▼──────────┐                      │
              │   Phase D          │                      │
              │   蓝绿部署 (现有)   │                      │
              │   探针检查 (现有)   │                      │
              └─────────┬──────────┘                      │
                        │                                  │
                        │ 部署结果                         │
              ┌─────────▼──────────┐                      │
              │   Phase E          │                      │
              │   效果验证与反馈    │                      │
              │   → 成功：更新知识  │                      │
              │     确信度         │                      │
              │   → 失败：标记矛盾  │                      │
              │     触发重新学习    │──────────────────────┘
              └────────────────────┘
```

### 3.2 关键数据流

```
互联网 (Web/GitHub/论文/文档)
    │
    ▼
┌─────────────────┐
│  自学习知识底座   │ ← 持续收集、评估、演化
│                 │
│  KnowledgeEntry │ ← 每条知识有：评分、确信度、来源、时效
│  KnowledgeGraph │ ← 技术间关系：替代、互补、依赖、竞争
│  TrackedTopics  │ ← 主动追踪的主题及其趋势
│  DecayModel     │ ← 知识半衰期、自动老化淘汰
└────────┬────────┘
         │
         │ 查询接口: query_relevant_knowledge(context)
         │
         ▼
┌─────────────────┐
│  自进化系统      │
│                 │
│  升级提案         │ ← 附带知识证据
│  {              │
│    "what": "...",│
│    "why": [...], │ ← 引用知识底座的 KnowledgeEntry
│    "evidence": [ │
│      {"knowledge_id": "k_xxx", "score": 85, "source": "..."}
│    ]            │
│  }              │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  治理引擎        │
│                 │
│  决策依据增强:    │
│  - 提案本身的技术合理性    │
│  - 知识底座证据的置信度    │
│  - 社区共识程度           │
│  - 风险/收益平衡           │
└────────┬────────┘
         │
    approve / reject / request_more_evidence
```

---

## 四、知识底座核心设计：六阶段学习循环

### 阶段全景

```
  ┌─────────────────────────────────────────────────────────┐
  │                                                         │
  │  ┌──────────┐   ┌──────────┐   ┌──────────┐            │
  │  │ Phase 0  │   │ Phase 1  │   │ Phase 2  │            │
  │  │ 触发调度 │──→│ 雷达扫描 │──→│ 提纯评估 │            │
  │  └──────────┘   └──────────┘   └────┬─────┘            │
  │       ↑                              │                  │
  │       │         ┌─────────────────────┘                  │
  │       │         ▼                                        │
  │  ┌────┴────┐   ┌──────────┐   ┌──────────┐             │
  │  │ Phase 5 │←──│ Phase 4  │←──│ Phase 3  │             │
  │  │ 元优化  │   │ 对比演化 │   │ 知识沉淀 │             │
  │  └────────┘   └──────────┘   └──────────┘             │
  │                                                         │
  │  ← 反馈闭环（优化下次的学习）                            │
  └─────────────────────────────────────────────────────────┘
```

---

### Phase 0: 触发与调度

**四种触发模式：**

| 触发类型 | 场景 | 示例 |
|---------|------|------|
| **手动触发** | 用户主动学习 | `/self-learning start --topic "AI agent 2026" --duration 45` |
| **定时触发** | 定期追踪已关注主题 | 每周一上午检查 "Python 新特性" |
| **事件触发** | 检测到重大更新 | 追踪的 GitHub 仓库发新版本 / arXiv 新论文 |
| **进化联动** | 自进化系统查询知识缺口 | "我要升级通信层，相关的最新实践是什么？" |

**调度器设计：**

```python
class LearningScheduler:
    """
    学习会话调度器

    管理四种触发来源，决定何时启动学习会话。
    """

    def __init__(self, repo: KnowledgeRepository):
        self.repo = repo
        self._last_session_time: datetime | None = None

    def should_start_session(self) -> LearningSession | None:
        """判断是否应该启动学习会话（按优先级依次检查）"""
        # 1. 手动触发 — 最高优先级，始终响应
        if self._has_pending_manual_request():
            return self._create_session(self._get_manual_request())

        # 2. 进化联动 — 自进化系统主动查询知识缺口
        if self._has_evolution_query():
            return self._create_session(self._get_evolution_query())

        # 3. 定时触发 — 检查 overdue 的追踪主题
        overdue = self.repo.get_overdue_topics()
        if overdue:
            return self._create_session(overdue[0])

        # 4. 事件触发 — GitHub trending / 论文 / 新闻
        triggered = self._check_event_triggers()
        if triggered:
            return self._create_session(triggered)

        return None

    def _check_event_triggers(self) -> TrackedTopic | None:
        """检查外部事件是否触发学习"""
        for topic in self.repo.get_active_topics():
            if topic.key_repos:
                # 检查追踪的仓库是否有新 release
                for repo in topic.key_repos:
                    if self._github_has_new_release(repo):
                        return topic
        return None
```

---

### Phase 1: 信息雷达扫描

**多源并行搜索矩阵：**

```
          ┌─────────────────────────────────────────┐
          │            信息雷达扫描                    │
          │                                         │
          │  ┌──────────┐  ┌──────────┐             │
          │  │ Web搜索   │  │ 社区讨论  │             │
          │  │ (3-5变体) │  │ HN/Reddit│             │
          │  └─────┬────┘  └─────┬────┘             │
          │        │             │                   │
          │  ┌─────▼────┐  ┌────▼─────┐             │
          │  │ GitHub   │  │ 官方文档  │             │
          │  │ 深度分析  │  │ /博客    │             │
          │  └─────┬────┘  └─────┬────┘             │
          │        │             │                   │
          │  ┌─────▼────┐  ┌────▼─────┐             │
          │  │ 学术论文  │  │ 项目自有  │             │
          │  │ (arXiv)  │  │ 知识图谱  │             │
          │  └──────────┘  │ (去重)   │             │
          │                └──────────┘             │
          └─────────────────────────────────────────┘
```

**GitHub 深度分析—不只是看星星：**

```python
@dataclass
class GitHubRepoInfo:
    """GitHub 仓库多维度分析结果"""
    # 基础指标
    repo_full_name: str
    stars: int
    forks: int
    description: str
    language: str
    license: str

    # 活跃度指标
    commit_velocity: float          # 近30天日均提交数
    issue_resolution_rate: float    # 近90天 Issue 解决率 (0-1)
    pr_merge_velocity: float        # 近30天 PR 平均合并时间(小时)
    contributor_retention: float    # 贡献者留存率 (0-1)

    # 质量指标
    bus_factor: int                 # 关键贡献者数量(<5 = 高风险)
    test_coverage_estimate: float   # 测试覆盖估计 (0-1)
    documentation_score: float      # 文档完整度 (0-1)
    dependency_health: float        # 依赖健康度 (0-1)

    # 趋势指标
    star_growth_rate: float         # 近6个月 Star 增长率
    new_contributor_trend: str      # "growing" | "stable" | "declining"
    community_engagement: float     # Discussions/Issues 参与度

    # 关联项目
    is_fork: bool
    parent_repo: str | None
    related_to_voidcube: float      # 与 VoidCube 项目相关性 (0-1)
```

**智能搜索变体生成：**

```python
def generate_search_variants(topic: TrackedTopic) -> list[str]:
    """为同一主题生成多个搜索变体以提高覆盖率"""
    base = topic.topic_name
    year = str(datetime.now().year)
    return [
        f"{base} {year} latest developments best practices",
        f"{base} state-of-the-art production deployment",
        f"{base} GitHub trending this month stars:>100",
        f"{base} breakthrough innovation recent months",
        f"{base} vs alternative comparison {year}",
        f"{base} migration guide upgrade deprecated",
        f"{base} common pitfalls anti-patterns production",
        f"{base} performance optimization techniques",
        f"awesome-{base.replace(' ', '-').lower()}",
    ]
```

---

### Phase 2: 信息提纯与评估

#### 2.1 五维评估体系

```
┌─────────────────────────────────────────────────────────┐
│                      技术价值评估模型                      │
├──────────────┬──────┬───────────────────────────────────┤
│     维度      │ 权重  │             评估标准               │
├──────────────┼──────┼───────────────────────────────────┤
│ 实用性        │ 30%  │ 能否立即应用到项目？解决什么痛点？    │
│ 前沿性        │ 20%  │ 是否代表发展方向？近期是否有突破？    │
│ 成熟度        │ 20%  │ 稳定性、社区规模、文档完善度？       │
│ 学习成本      │ 15%  │ 入门难度？与现有技术栈的兼容性？     │
│ 长期价值      │ 15%  │ 技术寿命预期？可迁移性？             │
├──────────────┼──────┼───────────────────────────────────┤
│              │ 100% │                                   │
└──────────────┴──────┴───────────────────────────────────┘
```

**增强维度（加权修正）：**

在基础五维之上，每条知识还受以下增强维度的加权修正：

| 增强维度 | 修正方式 | 说明 |
|---------|---------|------|
| **项目相关性** | 乘数 0.8-1.5 | 与 VoidCube 项目栈的关联度 |
| **新颖度** | 加分 0-10 | 与现有知识相比的新信息量 |
| **可操作性** | 加分 0-5 | 是否有明确的实践路径 |
| **社区信号** | 加分 0-5 | HN/Reddit/Twitter 讨论热度 |
| **时间新鲜度** | 衰减系数 | 越新的信息权重越高 |

#### 2.2 记忆分级标准

```
总分 ≥ 80:  ★★★ 核心知识 → 深度记忆 + 写入 MemAI ProfileMemory + 触发进化建议
总分 65-79: ★★  重要知识 → 详细笔记 + 知识图谱 + 备查
总分 45-64: ★   备选知识 → 简要记录 + 链接存档
总分 < 45:  ·   忽略      → 仅记录搜索轨迹用于优化搜索策略
```

#### 2.3 矛盾检测

当新知识与旧知识冲突时，不简单覆盖，而是标记为"待裁决"：

```python
class ContradictionDetector:
    """知识矛盾检测器"""

    THRESHOLD = 0.6  # 矛盾判定阈值

    def detect(
        self,
        new_entry: KnowledgeEntry,
        existing_entries: list[KnowledgeEntry]
    ) -> list[Conflict]:
        """检测新知识与旧知识的矛盾"""
        conflicts = []
        for existing in existing_entries:
            if self._same_domain_and_topic(new_entry, existing):
                contradiction_score = self._measure_contradiction(new_entry, existing)
                if contradiction_score >= self.THRESHOLD:
                    conflicts.append(Conflict(
                        entry_a_id=new_entry.id,
                        entry_b_id=existing.id,
                        score=contradiction_score,
                        resolution=self._suggest_resolution(new_entry, existing)
                    ))
        return conflicts

    def _suggest_resolution(self, new: KnowledgeEntry, old: KnowledgeEntry) -> str:
        """建议裁决策略"""
        # 新知识更新且来源更权威 → 倾向替代
        if new.discovered_at > old.discovered_at and new.sources_have_higher_authority:
            return "resolve_favor_new"
        # 旧知识经过验证 → 需要更多证据
        if old.certainty_state == "confirmed" and new.certainty_state == "observed":
            return "need_more_evidence"
        return "defer_to_human"
```

---

### Phase 3: 知识沉淀

知识以**三层结构**存储，确保冗余和可访问性：

```
┌────────────────────────────────────────────┐
│              知识沉淀三层架构                │
│                                            │
│  第一层：文件层                              │
│  .VoidCube/knowledge/                       │
│  ├── core/          (评分≥80的深度笔记)     │
│  ├── archive/       (评分65-79的备选笔记)   │
│  ├── references/    (评分<65的链接存档)     │
│  └── sessions/      (学习会话历史)         │
│                                            │
│  第二层：图谱层                              │
│  .VoidCube/knowledge_graph.db               │
│  → SQLite + FTS5全文搜索                    │
│  → 技术关系图谱 (替代/依赖/互补/竞争)         │
│  → 衰减状态追踪                              │
│                                            │
│  第三层：灵魂层                              │
│  MemAI ProfileMemory + Event + Scene + Arc  │
│  → 知识与 Agent 身份绑定                    │
│  → 跨会话/跨模型持久化                       │
│  → 参与记忆查询和上下文注入                  │
└────────────────────────────────────────────┘
```

```python
class KnowledgePrecipitator:
    """知识沉淀器 — 负责将评估通过的知识写入三层存储"""

    def precipitate(self, evaluation: EvaluationResult, session: LearningSession):
        entry = self._create_knowledge_entry(evaluation)

        if evaluation.total_score >= 65:
            # 第一层: 文件笔记
            self._write_markdown_note(entry)

            # 第二层: 知识图谱
            self.graph.insert_entry(entry)

        if evaluation.total_score >= 80:
            # 第三层: MemAI 灵魂层
            profile_memory = ProfileMemory.create(
                memory_kind=MemoryKind.FACT,
                subject=f"技术:{entry.title}",
                predicate="被评估为核心高价值技术",
                value=f"评分:{entry.total_score}|领域:{entry.domain}|层级:core",
                summary=entry.summary,
                confidence=evaluation.confidence,
                certainty_state=CertaintyState.OBSERVED,
                evidence_refs=[s.url for s in entry.sources],
            )
            self.memai_repo.save_profile_memory(profile_memory)
            entry.profile_memory_id = profile_memory.id

            # 同时记录学习事件
            event = Event.create(
                title=f"发现核心技术: {entry.title}",
                summary=f"[评分{entry.total_score:.0f}] {entry.summary[:200]}",
                timespan=TemporalSpan(
                    start=session.start_time,
                    end=utc_now(),
                    precision=TimePrecision.EXACT,
                    confidence=0.95,
                ),
                importance=entry.total_score / 100,
                confidence=entry.confidence,
                event_kind=EventKind.PROGRESS,
                impact_scope=ImpactScope.ARC,
                topics=[entry.domain, entry.title],
                entities=[],
                evidence_refs=entry.file_paths,
                source_turns=[],
            )
            self.memai_repo.save_event(event)

            # 触发进化建议
            self._maybe_trigger_evolution(entry)
```

---

### Phase 4: 知识对比与演化（存优去劣核心）

这是整个系统中最精妙的部分——知识不是堆积的，而是**演化的**。

```python
class KnowledgeEvolutionEngine:
    """知识演化引擎 — 存优去劣的核心"""

    def evolve(
        self,
        new_entries: list[KnowledgeEntry],
        session: LearningSession,
    ) -> EvolutionReport:
        report = EvolutionReport()

        for new_entry in new_entries:
            # Step 1: 在知识图谱中寻找相关旧知识
            related = self.graph.find_related(
                new_entry,
                radius=2,           # 2度关系半径
                min_similarity=0.3,  # 最低语义相似度
            )

            # Step 2: 用 LLM 判断每对关系的类型
            for old_entry in related:
                decision = self.classify_relationship(new_entry, old_entry)

                if decision == "SUPERSEDES":
                    self._apply_supersede(new_entry, old_entry, report)

                elif decision == "COMPLEMENTS":
                    self._apply_complement(new_entry, old_entry, report)

                elif decision == "CONFLICTS":
                    self._apply_conflict(new_entry, old_entry, report)

                elif decision == "DUPLICATE":
                    self._apply_merge(new_entry, old_entry, report)

            # Step 3: 如果没有找到相关旧知识 → 全新知识
            if not related:
                report.new_discoveries.append(new_entry)

        # Step 4: 执行衰减检查
        decayed = self.decay_model.check_and_prune()
        report.pruned_entries = decayed

        return report

    def classify_relationship(
        self, new: KnowledgeEntry, old: KnowledgeEntry
    ) -> str:
        """使用 LLM 判断新旧知识的关系类型"""
        prompt = f"""你是技术知识仲裁者。比较以下两条知识，判断关系。

【新知识】(发现于 {new.discovered_at.strftime('%Y-%m-%d')})
标题: {new.title}
摘要: {new.summary}
评分: {new.total_score}/100 (实用性{new.score_practicality} 前沿性{new.score_cutting_edge} 成熟度{new.score_maturity})

【旧知识】(发现于 {old.discovered_at.strftime('%Y-%m-%d')})
标题: {old.title}
摘要: {old.summary}
评分: {old.total_score}/100

请判断关系（单选）：
- SUPERSEDES:  新知识在技术先进性/准确性上明显超越旧知识，旧知识应被标记为被替代
- COMPLEMENTS: 新知识补充了旧知识缺失的细节或新视角，旧知识仍有效
- CONFLICTS:   新旧知识在关键结论上存在矛盾
- DUPLICATE:   讨论的是同一技术/同一结论，信息重复
- NO_RELATION: 两者无直接关联

只回答一个词。"""
        response = self.llm.complete(prompt, max_tokens=10)
        return response.strip().upper()
```

**五种演化动作详解：**

```
  新知识到来
      │
      ├── SUPERSEDES (替代)
      │   ├── 旧知识 status → "superseded"
      │   ├── 旧知识 superseded_by → 新知识ID
      │   ├── 新知识 supersedes → [旧知识ID]
      │   └── MemAI: 旧 ProfileMemory status → SUPERSEDED
      │
      ├── COMPLEMENTS (补充)
      │   ├── 旧知识 detailed_notes += 补充内容
      │   ├── 旧知识 score_long_term_value += 2
      │   ├── 双向关联: related_entries
      │   └── 旧知识 last_updated_at → now
      │
      ├── CONFLICTS (冲突)
      │   ├── 两者 certainty_state → DISPUTED
      │   ├── 创建 Conflict 记录加入裁决队列
      │   ├── 触发深度调查学习会话
      │   └── MemAI: conflict_refs 双向关联
      │
      ├── DUPLICATE (重复)
      │   ├── 比较来源权威度，保留更好的
      │   ├── 合并 sources、补充细节到保留条目
      │   └── 移除较差的条目
      │
      └── NO_RELATION (无关)
          └── 各自独立存在于知识图谱中
```

---

### Phase 5: 元学习与策略优化

Agent 从学习效果中学习如何更好地学习：

```python
class MetaLearningOptimizer:
    """元学习优化器 — 让学习策略本身不断进化"""

    def optimize(self, recent_sessions: list[LearningSession]) -> OptimizationReport:
        """分析历史会话，优化未来学习策略"""

        # 1. 搜索策略效果分析
        strategy_roi = self._compute_strategy_roi(recent_sessions)
        # 示例输出:
        #   "github_trending"   → 平均每查询产生 3.2 个高价值发现
        #   "web_generic"       → 平均每查询产生 1.1 个高价值发现
        #   "arxiv_paper"       → 平均每查询产生 0.4 个高价值发现

        # 2. 最佳学习时段分析
        best_hours = self._analyze_time_productivity(recent_sessions)

        # 3. 主题优先级重新校准
        for topic in self.tracked_topics:
            roi_metrics = self._compute_topic_roi(topic, recent_sessions)

            # 长期无产出的主题 → 降级
            if roi_metrics.days_since_last_high_value > 60:
                topic.priority *= 0.8
                topic.check_frequency_days = int(topic.check_frequency_days * 1.5)
                if topic.priority < 0.2:
                    topic.is_active = False

            # 趋势上升的主题 → 升级
            if topic.trend_direction == "rising":
                topic.priority = min(1.0, topic.priority * 1.2)
                topic.check_frequency_days = max(3, int(topic.check_frequency_days * 0.7))

        # 4. 自动发现新主题 (Serendipity Discovery)
        emerging = self._detect_emerging_topics(recent_sessions)
        for topic_name, confidence in emerging.items():
            if confidence > 0.7 and not self._is_already_tracked(topic_name):
                self._create_tracked_topic(
                    name=topic_name,
                    initial_priority=0.5,
                    source="serendipity",
                )

        return OptimizationReport(
            best_strategies=strategy_roi.top_n(3),
            topic_adjustments=...,
            new_topics=emerging,
        )
```

---

## 五、知识底座与自进化系统的深度耦合

这是整个设计方案中最关键的部分 — 知识底座如何驱动自进化系统做出明智决策。

### 5.1 耦合架构

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  ┌──────────────────┐         ┌──────────────────┐        │
│  │   知识底座        │         │   自进化系统       │        │
│  │   (SelfLearning)  │◄───────│   (Systems/)      │        │
│  │                  │  查询   │                  │        │
│  │  • 技术知识图谱   │────────→│  • 身体注册表     │        │
│  │  • 评估评分       │  证据   │  • 治理决策引擎   │        │
│  │  • 演化历史       │         │  • 探针系统       │        │
│  │  • 追踪主题       │         │  • 蓝绿部署       │        │
│  └──────────────────┘         └──────────────────┘        │
│           │                            │                   │
│           │                            │                   │
│           ▼                            ▼                   │
│  ┌──────────────────────────────────────────────────┐     │
│  │               MemAI 灵魂层（共享）                  │     │
│  │  • ProfileMemory: 技术事实 + 升级决策记录          │     │
│  │  • Event: 学习事件 + 进化事件 + 部署事件           │     │
│  │  • Scene → Arc → Epoch: 身份连续性               │     │
│  └──────────────────────────────────────────────────┘     │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 5.2 进化提案增强

**之前（无知识底座）：**

```python
upgrade_request = {
    "type": "body_upgrade_request",
    "body_id": "body_abc",
    "patch": "将 requests 替换为 httpx",
    "reason": "httpx 支持异步",
    "confidence": 0.7  # Agent 自己估计的，缺乏证据支撑
}
```

**之后（有知识底座）：**

```python
upgrade_request = {
    "type": "body_upgrade_request",
    "body_id": "body_abc",
    "patch": "将 requests 替换为 httpx",
    "reason": "httpx 支持 async/await，且在 HTTP/2 支持上优于 requests",
    "confidence": 0.92,  # 显著提升
    # 新增：知识底座证据
    "knowledge_evidence": [
        {
            "knowledge_id": "k_a1b2c3",
            "title": "Python HTTP 客户端：httpx vs requests (2026)",
            "score": 88,
            "tier": "core",
            "key_finding": "httpx 已成为 Python 异步 HTTP 的标准选择，requests 作者也推荐迁移",
            "sources": [
                "https://www.python-httpx.org/",
                "https://github.com/encode/httpx (15k+ stars)",
            ],
            "discovered_at": "2026-04-15",
        },
        {
            "knowledge_id": "k_d4e5f6",
            "title": "VoidCube 项目依赖健康度分析",
            "score": 75,
            "tier": "archive",
            "key_finding": "requests 库近12个月未发布大版本，维护模式，建议迁移到活跃维护的替代品",
            "sources": ["https://pypi.org/project/requests/#history"],
        },
    ],
    "contraindications": [],  # 知识底座中未发现反对此升级的证据
    "community_consensus": "strongly_support",  # 基于知识底座的社区信号分析
}
```

### 5.3 治理决策增强

治理引擎接收到包含知识证据的升级请求后，决策逻辑得到增强：

```python
class EnhancedGovernor(Governor):
    """增强版治理引擎 — 融合知识底座证据"""

    def handle_body_upgrade_request(
        self, request: BodyUpgradeRequest
    ) -> GovernanceDecision:
        # 原有逻辑：确定性规则检查
        base_decision = super().handle_body_upgrade_request(request)

        # 新增：知识底座证据评估
        if request.knowledge_evidence:
            evidence_score = self._evaluate_evidence(request.knowledge_evidence)

            if evidence_score < 0.5 and base_decision == "approve":
                return GovernanceDecision(
                    decision="request_more_evidence",
                    reason="知识底座证据不足，建议先进行主题学习后再提案",
                )

            if evidence_score >= 0.8:
                # 强证据支撑 → 缩短观察窗口
                return GovernanceDecision(
                    decision="approve",
                    watch_duration_seconds=base_decision.watch_duration * 0.7,
                    reason=f"知识证据评分 {evidence_score:.2f}，观察窗口缩短至70%",
                )

        return base_decision

    def _evaluate_evidence(self, evidence: list[dict]) -> float:
        """综合评估知识证据的可靠性"""
        if not evidence:
            return 0.0

        total = 0.0
        for e in evidence:
            score = e["score"] / 100
            tier_bonus = 0.1 if e["tier"] == "core" else 0.0
            source_count = min(len(e.get("sources", [])), 5) * 0.02
            total += score + tier_bonus + source_count

        return min(1.0, total / len(evidence))
```

### 5.4 进化效果反馈回知识底座

自进化系统部署完成后，结果反馈回知识底座，形成知识验证闭环比：

```
部署成功:
  → 相关 KnowledgeEntry.certainty_state → "confirmed"
  → 相关 KnowledgeEntry.confidence → min(1.0, confidence + 0.1)
  → 知识底座学到了"这个方案在实践中有效"

部署失败(回滚):
  → 相关 KnowledgeEntry.certainty_state → "disputed"
  → 创建 Conflict 记录
  → 触发一次针对性的"失败原因"学习会话
  → 知识底座学到了"这个方案在 VoidCube 环境中不可行，原因可能是..."

部署成功但性能下降(观察窗口检测到):
  → 相关 KnowledgeEntry 添加 caution 标记
  → 记录实际性能数据作为新的知识条目
  → 下次类似提案时自动引用此历史
```

---

## 六、数据模型设计

### 6.1 KnowledgeEntry（知识条目）

```python
@dataclass
class KnowledgeEntry:
    """知识底座中的一条技术知识"""
    id: str                              # "k_<12位hex>"
    title: str                           # 技术名称
    domain: str                          # ai-framework|language|devops|system|database|frontend|security
    summary: str                         # 核心摘要 (≤200字)
    detailed_notes: str                  # 详细笔记 (Markdown)

    # 五维评分 (0-100)
    score_practicality: float            # 实用性 (0-30)
    score_cutting_edge: float            # 前沿性 (0-20)
    score_maturity: float                # 成熟度 (0-20)
    score_learning_cost: float           # 学习成本 (0-15)
    score_long_term_value: float         # 长期价值 (0-15)
    total_score: float                   # 加权总分

    tier: str                            # core|archive|reference
    status: str                          # active|superseded|archived|disputed

    # 来源追溯
    sources: list[SourceInfo]
    github_repos: list[GitHubRepoInfo]

    # 时间元信息
    discovered_at: datetime
    last_reviewed_at: datetime
    last_updated_at: datetime

    # 知识演化
    supersedes: list[str]                # 替代了哪些旧知识ID
    superseded_by: str | None            # 被哪个新知识替代
    related_entries: list[str]           # 相关条目ID

    # 衰减管理
    half_life_days: int
    confidence: float                    # 0.0-1.0
    certainty_state: str                 # observed|inferred|confirmed|disputed|pending_verification

    # 进化线索
    evolution_signals: list[EvolutionSignal]  # 触发进化的信号列表

    # 灵魂层关联
    profile_memory_id: str | None
```

### 6.2 EvolutionSignal（进化信号）

```python
@dataclass
class EvolutionSignal:
    """知识底座识别出的进化机会"""
    signal_type: str          # "deprecation"|"better_alternative"|"performance_gain"|"security_fix"|"new_pattern"
    target_component: str     # 需要升级的项目组件名称
    knowledge_entry_id: str   # 支撑此信号的知识条目ID
    urgency: str              # "critical"|"high"|"medium"|"low"
    suggested_action: str     # 建议的升级动作描述
    estimated_impact: str     # 预估影响范围
    evidence_strength: float  # 证据强度 0.0-1.0
```

### 6.3 LearningSession（学习会话）

```python
@dataclass
class LearningSession:
    id: str
    topic: str
    trigger_type: str              # manual|scheduled|event|evolution_query
    start_time: datetime
    planned_duration_minutes: int
    actual_duration_minutes: int
    status: str                    # planned|in_progress|completed|interrupted

    # 成果统计
    discoveries_total: int
    discoveries_core: int          # 评分≥80
    discoveries_archive: int       # 评分65-79
    notes_created: int
    notes_updated: int
    notes_archived: int

    # 演化统计
    superseded_count: int          # 替代了多少旧知识
    complemented_count: int        # 补充了多少旧知识
    conflicts_detected: int        # 发现了多少矛盾

    # 效果追踪
    search_queries_used: list[str]
    search_effectiveness: dict[str, float]

    # 灵魂层关联
    scene_id: str | None
    event_ids: list[str]
```

### 6.4 TrackedTopic（追踪主题）

```python
@dataclass
class TrackedTopic:
    id: str
    topic_name: str
    domain: str
    priority: float                     # 0.0-1.0
    is_active: bool
    check_frequency_days: int
    last_checked_at: datetime | None
    next_check_at: datetime | None

    trend_direction: str                # rising|stable|declining|unknown
    trend_momentum: float

    search_templates: list[str]         # 可复用的搜索模板
    key_authors_or_orgs: list[str]
    key_repos: list[str]                # GitHub full_name

    total_discoveries: int
    high_value_discoveries: int
    avg_discovery_score: float
```

### 6.5 TechRelation（技术关系）

```python
@dataclass
class TechRelation:
    id: str
    source_entry_id: str
    target_entry_id: str
    relation_type: str    # supersedes|depends_on|complements|competes_with|inspired_by
    confidence: float
    evidence: str
    created_at: datetime
```

---

## 七、可持续迭代机制

### 7.1 知识衰减模型

不同领域的技术知识有不同的半衰期，知识不会永久有效：

```python
class KnowledgeDecayModel:
    """知识衰减模型 — 让过时知识自动淘汰"""

    HALF_LIFE_DAYS = {
        "ai-framework":      90,   # AI框架：迭代极快
        "machine-learning":  120,  # ML技术：4个月
        "frontend":          120,  # 前端生态：快速变化
        "devops":            180,  # DevOps：中等变化
        "security":          180,  # 安全：中等变化
        "database":          365,  # 数据库：较稳定
        "language":          365,  # 编程语言：较稳定
        "system":            730,  # 系统编程：非常稳定
    }

    def compute_vitality(self, entry: KnowledgeEntry) -> float:
        """
        计算知识生命力 (0.0=死亡, 1.0=完全新鲜)

        vitality = 0.5^(age_days / half_life)
        """
        age_days = (utc_now() - entry.discovered_at).days
        half_life = self.HALF_LIFE_DAYS.get(entry.domain, 180)
        vitality = 0.5 ** (age_days / half_life)

        # 回顾和更新会提升生命力
        if entry.last_reviewed_at > entry.discovered_at:
            review_age = (utc_now() - entry.last_reviewed_at).days
            bonus = 0.3 * (0.5 ** (review_age / (half_life / 2)))
            vitality = min(1.0, vitality + bonus)

        # 被替代 → 生命力归零
        if entry.superseded_by:
            vitality = 0.0

        return vitality

    def should_prune(self, entry: KnowledgeEntry) -> bool:
        """判断是否应该修剪（遗忘）"""
        vitality = self.compute_vitality(entry)

        thresholds = {"core": 0.15, "archive": 0.30, "reference": 0.50}
        return vitality < thresholds.get(entry.tier, 0.30)
```

### 7.2 间隔回顾机制

借鉴 Anki 的间隔重复原理，但用于知识回顾而非记忆：

| 时间点 | 动作 | 目的 |
|--------|------|------|
| 学习后 **1 天** | 快速回顾 | 确认理解正确，补充遗漏 |
| 学习后 **1 周** | 深度回顾 | 寻找实践机会，评估与项目相关性 |
| 学习后 **1 月** | 对比回顾 | 与相关技术对比，更新技术关系 |
| 学习后 **3 月** | 衰减检查 | 评估生命力，决定保留/更新/淘汰 |
| 学习后 **6 月** | 全面复查 | 重新评估评分，检查是否有替代技术出现 |

### 7.3 技术雷达看板

定期生成技术态势图，直观展示知识底座的健康状况：

```
═══════════════════════════════════════════════════════════════
                     VoidCube 技术雷达 2026-05-24
═══════════════════════════════════════════════════════════════

  前沿追踪 (Emerging)         已验证 (Proven)         衰退中 (Fading)
  ──────────────────         ──────────────         ──────────────

AI Agent   MCP Protocol 2.0      LangChain 0.3+       AutoGPT (Archived)
框架       CrewAI v1.0           LangGraph             BabyAGI
           AgentSpec Draft

Python     Python 3.14 模式匹配   Python 3.12+         Python 3.8 (EOL)
语言       sub-interpreters      asyncio最佳实践

DevOps     容器化 Wasm            Docker Compose v3     Docker Swarm
运维       Dagger SDK            GitHub Actions

═══════════════════════════════════════════════════════════════
知识底座统计：
  核心知识: 42条    备选知识: 87条    参考链接: 156条
  已淘汰: 23条     待裁决矛盾: 3条    追踪主题: 12个
  最近学习: 2026-05-23  下次检查: 2026-05-26
═══════════════════════════════════════════════════════════════
```

### 7.4 "学习→回顾→进化→验证"完整闭环

```
  ┌───────────────────────────────────────────────────────┐
  │                                                       │
  │  ┌──────────┐    ┌──────────┐    ┌──────────────┐    │
  │  │ 知识学习  │───→│ 知识演化  │───→│ 进化信号识别  │    │
  │  │ (Phase0-3)│    │ (Phase4) │    │ (存优去劣)    │    │
  │  └──────────┘    └──────────┘    └──────┬───────┘    │
  │       ↑                                  │            │
  │       │                           ┌──────▼───────┐    │
  │       │                           │ 升级提案生成  │    │
  │       │                           │ (带知识证据)  │    │
  │       │                           └──────┬───────┘    │
  │       │                                  │            │
  │       │                           ┌──────▼───────┐    │
  │       │                           │ 治理审批      │    │
  │       │                           │ (证据增强)    │    │
  │       │                           └──────┬───────┘    │
  │       │                                  │            │
  │       │                           ┌──────▼───────┐    │
  │       │                           │ 蓝绿部署      │    │
  │       │                           │ 探针验证      │    │
  │       │                           └──────┬───────┘    │
  │       │                                  │            │
  │       │                           ┌──────▼───────┐    │
  │       └───────────────────────────│ 效果反馈      │    │
  │           (验证通过/失败回写知识)   │ 更新确信度    │    │
  │                                   └──────────────┘    │
  │                                                       │
  └───────────────────────────────────────────────────────┘
```

---

## 八、配置设计

```yaml
# config.yaml 新增配置块
self_learning:
  enabled: true
  knowledge_base_dir: ".VoidCube/knowledge"

  # 会话配置
  session:
    max_duration_minutes: 120
    default_duration_minutes: 30
    idle_learn_enabled: false       # 空闲时主动学习（实验性）
    max_auto_sessions_per_day: 2

  # 调度
  scheduler:
    auto_learn_enabled: false
    daily_review_time: "09:00"
    weekly_review_day: "monday"

  # 搜索
  search:
    max_parallel_queries: 5
    max_results_per_query: 10
    github_min_stars: 100
    github_max_repos_to_analyze: 5
    prefer_recent_months: 6
    trusted_domains: []
    blocked_domains: []

  # 评估阈值
  evaluation:
    core_threshold: 80
    archive_threshold: 65
    min_confidence: 0.6
    contradiction_threshold: 0.6

  # 知识衰减
  decay:
    enabled: true
    check_interval_days: 7
    half_life_overrides: {}         # 例: {"ai-framework": 60}

  # LLM 配置（评估/总结/关系判断用）
  llm:
    provider: "auto"
    model: ""
    use_smart_routing: true

  # 进化联动
  evolution:
    auto_suggest_enabled: false     # 是否自动向自进化系统发送升级建议
    min_evidence_strength: 0.7     # 触发自动建议的最低证据强度
    require_human_approval: true    # 自动建议是否需要人工审批
```

---

## 九、实施路线图

### 第一阶段：核心数据层（基础）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 1 | 创建 `agent/self_learning/` 模块目录 | 模块骨架 | 小 |
| 2 | 实现 `schema.py` | 所有数据模型定义 | 小 |
| 3 | 实现 `knowledge_graph.py` | SQLite + FTS5 知识图谱存储 | 中 |
| 4 | 实现 `repository.py` | CRUD + 查询接口 | 中 |

### 第二阶段：搜索与评估层（核心）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 5 | 实现 `information_radar.py` | 多源并行搜索 | 大 |
| 6 | 实现 `github_analyzer.py` | GitHub 深度分析 | 中 |
| 7 | 实现 `evaluator.py` | 五维评分 + 增强维度 | 大 |
| 8 | 实现 `contradiction_detector.py` | 矛盾检测与裁决建议 | 中 |

### 第三阶段：知识沉淀与演化（灵魂）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 9 | 实现 `knowledge_precipitator.py` | 三层写入（文件+图谱+MemAI） | 中 |
| 10 | 实现 `evolution_engine.py` | 知识演替流程 | 大 |
| 11 | 实现 `decay_model.py` | 知识衰减与主动遗忘 | 小 |

### 第四阶段：调度与元学习（智能）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 12 | 实现 `learning_scheduler.py` | 四模式学习调度 | 中 |
| 13 | 实现 `meta_optimizer.py` | 元学习策略优化 | 中 |
| 14 | 实现 `interval_reviewer.py` | 间隔回顾机制 | 小 |

### 第五阶段：与自进化系统融合（闭环）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 15 | 实现 `evolution_bridge.py` | 知识底座→进化系统的桥接器 | 大 |
| 16 | 增强 `governor.py` | 治理引擎融合知识证据 | 中 |
| 17 | 增强 `body_registry.py` 相关流程 | 升级提案附带知识证据 | 中 |
| 18 | 实现进化反馈回路 | 部署结果回写知识底座 | 中 |

### 第六阶段：CLI 与集成（交付）

| # | 任务 | 产出 | 预估工作量 |
|---|------|------|-----------|
| 19 | 更新 `skills/self-learning/SKILL.md` | 反映新设计 | 小 |
| 20 | 实现 `/self-learning` CLI 命令 | 用户可交互的自学习 | 中 |
| 21 | 与 MemoryManager 集成 | 注册为可选 MemoryProvider | 小 |
| 22 | 更新 `config.yaml` | 新配置项 | 小 |
| 23 | 编写测试 | 单元测试 + 集成测试 | 中 |

---

## 十、设计总结

### 这个设计的独特价值

| 维度 | 传统做法 | 本设计的创新 |
|------|---------|-------------|
| 知识存储 | 扁平文件/笔记 | **知识图谱** — 技术间关系自动维护 |
| 评估方式 | 人工主观判断 | **多维自动化评分** — 五维+增强维度 |
| 知识更新 | 手动修改/覆盖 | **自动演化** — SUPERSEDES/COMPLEMENTS/CONFLICTS/DUPLICATE 五动作 |
| 遗忘机制 | 无 | **领域半衰期衰减模型** — 不同领域不同速率 |
| 学习策略 | 固定搜索模板 | **元学习优化** — 从历史效果中进化搜索策略 |
| 记忆持久化 | 仅文件系统 | **三层存储** — 文件+图谱+MemAI灵魂层 |
| 触发方式 | 仅手动 | **四模式触发** — 手动/定时/事件/进化联动 |
| 与进化关系 | 无关联 | **知识底座驱动进化** — 自进化系统不再盲目升级 |

### 核心理念

```
不是让 Agent 更聪明地 "升级自己"，
而是让 Agent 先 "理解世界" 再 "升级自己"。

知识底座回答 WHAT & WHY，
自进化系统执行 HOW & WHEN。

两者合一，才是真正的 "自愈自进化"。
```

---

> **文档版本**: v1.0
> **创建日期**: 2026-05-24
> **适用范围**: VoidCube 项目自愈自进化系统知识底座
