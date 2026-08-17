# Chronicle Scholar LM 设计文档

本目录包含 Mem 记忆框架的 v1 设计规范。

在 VoidCube 中，这一框架不只是可选的记忆辅助工具。它是长期记忆与灵魂侧治理层的设计基础。

在当前 VoidCube 基线中，这也意味着：

- VoidCube 作为母系统运行
- 双身体架构实际上是两个子 Agent 槽位
- Mem 帮助母系统保持身份连续性，并决定哪个经过改进的子 Agent 可以安全地面向用户

它的职责不是分析人格，也不是保存完整对话记录。它负责将长期交互历史组织成结构化、可修订、可压缩的时间线，并提供治理协议可以依赖的持久记忆基础。

核心原则：
- 时间是首要索引。
- 叙事结构比原始细节的累积更重要。
- 优先压缩，而非删除。
- 修订必须显式进行并保留版本。
- 证据与不确定性必须保持可见。

以 VoidCube 的术语来说，这些文档主要涵盖：
- Mem 如何存储长期记忆真相，
- Mem 如何保存身份连续性的证据，
- 记忆维护与监督治理如何共享同一个灵魂域，以及
- 下游治理如何依赖可安全审计的记忆，而不是任由原始对话记录发生漂移。

它们还支持一个实际的项目目标：

- 使母系统能够改进子 Agent，同时不丢失在身体替换后仍须延续的灵魂真相

文档：
- `docs/01-system-constitution.md`：角色、约束、认知规则与系统边界。
- `docs/02-schema-v1.md`：`Event`、`Scene`、`Arc` 和 `Epoch` 的正式记忆对象模式。
- `docs/03-mainline-sideline-rules.md`：主线、支线、休眠脉络和噪声的评分与路由规则。
- `docs/04-compression-revision-rules.md`：压缩、修订、取代和受控遗忘的生命周期规则。
- `docs/05-query-interface.md`：检索接口、排序流程与响应结构。
- `docs/06-prompts-and-evaluation.md`：提示词框架与基准测试计划。
- `docs/07-profile-and-fact-memory.md`：偏好、约束、定义和持久事实等稳定非时间线记忆的设计。
- `docs/08-uncertainty-and-conflict-rules.md`：确定性状态、冲突跟踪、取代和审计安全检索规则。
- `docs/09-query-planner.md`：将自然语言请求转化为结构化查询执行计划的规划层。
- `docs/10-governance-event-schema.md`：用于身体切换、自我进化决策、回滚和失败样本的治理事件模式。
- `docs/11-time-summary-index-hierarchy.md`：永久会话、日、周、月摘要索引的目标架构，包括确定性聚合与反向展开。
- `docs/12-memory-system-multi-round-audit.md`：记忆系统多轮、多角度审查总账、缺陷登记和工程化交付门槛。
- `docs/MemAI v0.2 设计路线图.md`：记忆框架 v0.2 演进路线图。

建议实现顺序：
1. 时间归一化
2. 事件提取
3. 场景构建
4. 脉络绑定
5. 压缩与修订
6. 查询层
7. 提示词优化与基准调优
8. 档案与事实记忆
9. 用于 VoidCube 自我进化的治理事件模式与索引
