# VoidCube Documentation Map

本文档是 `docs/` 目录入口。

它的目标是防止文档互相抢职责：核心结论写进基线，组件规则写进组件规范，操作步骤写进 runbook，历史草稿进入 archive。

## 1. 当前执行依据

日常实现、重构和验收优先看这 9 份：

1. [VoidCube 服务化系统架构基线](./voidcube架构基线.md)
2. [VoidCube 架构集成说明](./architecture-integration.md)
3. [Phase 1 核心闭环与内生任务驱动器](./phase1-core-loop-and-endogenous-drive.md)
4. [Body Lifecycle](./body-lifecycle.md)
5. [Switch Protocol](./switch-protocol.md)
6. [State Boundary](./state-boundary.md)
7. [Git Evolution Lineage](./git-evolution-lineage.md)
8. [Agent Evolution Boundary](./agent-evolution-boundary.md)
9. [Mem Integration Contract](./mem-integration-contract.md)

它们的分工是：

| 文档 | 职责 |
| --- | --- |
| [voidcube架构基线.md](./voidcube架构基线.md) | 最高优先级总基线，定义 VoidCube 是什么、谁负责什么、主升级对象是谁。 |
| [architecture-integration.md](./architecture-integration.md) | 组件接线说明，定义 CLI、网关、Mem、Agent、自学系统、执行器如何连接。 |
| [phase1-core-loop-and-endogenous-drive.md](./phase1-core-loop-and-endogenous-drive.md) | Phase 1 核心闭环哲学与运行机理，定义内生任务驱动器如何成为母体心跳、四重保障如何落地、完整运行循环如何串联。 |
| [body-lifecycle.md](./body-lifecycle.md) | body slot 状态机，定义状态集合、允许转移、禁止转移和记录要求。 |
| [switch-protocol.md](./switch-protocol.md) | 切换协议，定义租约、切换前置条件、观察窗口、回滚和回收。 |
| [state-boundary.md](./state-boundary.md) | 状态归属，定义哪些状态归 Mem、网关、body runtime，以及哪些必须回写。 |
| [git-evolution-lineage.md](./git-evolution-lineage.md) | Git 演化谱系，定义候选体来源、diff、commit、probe report 与回滚点如何进入治理链路。 |
| [agent-evolution-boundary.md](./agent-evolution-boundary.md) | Agent 进化边界，定义哪些路径可作为子 Agent 身体进化，哪些属于母体基础设施。 |
| [mem-integration-contract.md](./mem-integration-contract.md) | Mem 集成契约，定义当前轻量适配层与未来完整 Mem 治理记忆之间的目标接法。 |

## 2. 实施与运维文档

这些文档用于推进阶段实现、操作当前系统、排查过渡问题：

| 文档 | 用途 |
| --- | --- |
| [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md) | Phase 1 实验路线与验收标准。 |
| [body-runtime-runbook.md](./body-runtime-runbook.md) | 当前身体运行机制的操作步骤、接口示例和排障。 |
| [architecture-conflicts-audit.md](./architecture-conflicts-audit.md) | 当前实现与核心基线之间的冲突点和干扰点。 |
| [mem-maturity-audit.md](./mem-maturity-audit.md) | Mem 接回 VoidCube 主链路前的成熟度审计、缺口和验收标准。 |
| [supervisor-runtime-structure.md](./supervisor-runtime-structure.md) | 当前 `systems/supervisor/` 的模块分工、mixins 角色和维护边界。 |
| [stable-status-and-next-stage.md](./stable-status-and-next-stage.md) | 当前稳定状态、验证记录、未完成事项和下一阶段目标。 |

## 3. 理论与研究资料

这些文档有阅读价值，但不作为第一执行入口：

| 文档 | 用途 |
| --- | --- |
| [constitution.md](./constitution.md) | 原则层与身份连续性总纲。 |
| [voidcube架构可行性论证论文.md](./voidcube架构可行性论证论文.md) | 架构可行性论证。 |
| [knowledge-foundation-for-self-evolution.md](./knowledge-foundation-for-self-evolution.md) | 自学习知识底座研究长文。 |
| [project-architecture-analysis.md](./project-architecture-analysis.md) | 当前仓库架构分析快照。 |

## 4. 历史草稿

历史草稿已经移入 [archive/](./archive/)。

这些文档只用于追溯早期思路，不作为实现、验收或重构依据：

| 文档 | 替代正式文档 |
| --- | --- |
| [archive/body-lifecycle-and-switch.md](./archive/body-lifecycle-and-switch.md) | 已由 [body-lifecycle.md](./body-lifecycle.md) 和 [switch-protocol.md](./switch-protocol.md) 吸收。 |
| [archive/single-repo-dual-body-experiment.md](./archive/single-repo-dual-body-experiment.md) | 已由 [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md)、[body-runtime-runbook.md](./body-runtime-runbook.md) 和 [body-lifecycle.md](./body-lifecycle.md) 吸收。 |

如果后续确认 archive 中某份文档不再提供任何追溯价值，可以删除。

## 5. 工程支持

| 文档 | 用途 |
| --- | --- |
| [editor-setup.md](./editor-setup.md) | 编辑器与工程环境配置说明。 |

## 6. 维护规则

- 新架构结论写入 [voidcube架构基线.md](./voidcube架构基线.md)。
- 新组件协议写入对应组件规范，不在多个文件重复展开总论。
- 新操作步骤写入 [body-runtime-runbook.md](./body-runtime-runbook.md)。
- 阶段目标与验收写入 [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md)。
- 阶段完成状态、验证命令和下一阶段目标写入 [stable-status-and-next-stage.md](./stable-status-and-next-stage.md)。
- 冲突点、技术债和过渡实现问题写入 [architecture-conflicts-audit.md](./architecture-conflicts-audit.md)。
- 已被正式文档吸收的草稿移动到 [archive/](./archive/)。
- archive 文档不作为执行依据。
- 休息、换会话或阶段切换前，把最新执行状态和下一步写入对应文档；不要只依赖聊天上下文。

## 7. 下一步整理建议

当前核心规范组已经稳定。下一步建议整理实施层文档：

1. 精简 [body-runtime-runbook.md](./body-runtime-runbook.md)，让它只保留当前可操作步骤、接口示例和排障。
2. 更新 [architecture-conflicts-audit.md](./architecture-conflicts-audit.md)，把已经通过文档收口解决的干扰点标记完成。
3. 再看 [project-architecture-analysis.md](./project-architecture-analysis.md) 是否仍需要保留，或是否可以移动到 archive。
