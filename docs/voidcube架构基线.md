# VoidCube 服务化系统架构基线

## 1. 文档定位

本文档是 VoidCube 当前架构的最高优先级基线，用于约束后续实现、重构和文档整理。

当其他文档与本文冲突时，以本文为准；其他文档只能对本文定义的核心架构做组件级展开、运行说明、阶段路线或历史论证，不能重新定义 VoidCube 的主架构。

本文只回答 5 个问题：

- 哪些核心组件存在
- 每个组件负责什么
- 组件之间如何协作
- 整个架构到底在服务谁、升级谁
- 哪些职责必须收口，哪些能力暂时不做

详细协议、操作步骤和阶段计划分别放在：

- [architecture-integration.md](./architecture-integration.md)
- [body-lifecycle.md](./body-lifecycle.md)
- [switch-protocol.md](./switch-protocol.md)
- [state-boundary.md](./state-boundary.md)
- [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md)
- [body-runtime-runbook.md](./body-runtime-runbook.md)

## 2. 核心结论

VoidCube 不是“CLI 本地模式 + 服务模式”二选一项目，也不是一个普通 CLI Agent 外挂几项后台能力。

VoidCube 的目标形态是：

**一个以 CLI 为用户入口、以内部网关为神经中枢、以 Mem 为长期记忆与治理灵魂、以执行器为动作执行面、以自学系统为证据生产层、以可替换子 Agent 为主要升级对象的单机多进程母体系统。**

从用户视角看：

- 用户只直接感知 CLI
- 用户真正使用的是当前活跃 Agent

从系统视角看：

- VoidCube 是母体
- 双身体槽位中的两个 Agent 是母体持续培养、验证、切换的两个子 Agent
- 母体不会把自己整体暴露给用户
- 母体只会把通过治理验证的当前活跃子 Agent 交给用户使用

因此，这套架构共同服务的核心对象是：

**Agent 的持续改进、验证、替换与回滚。**

当前阶段主要需要持续升级的对象是子 Agent 本身，而不是优先要求 CLI、网关、Mem、执行器或自学系统先自我升级。后者是支撑系统，目标是让 Agent 能稳定变好。

## 3. 核心组件

### 3.1 CLI

CLI 是 VoidCube 面向用户的唯一标准入口。

CLI 负责：

- 接收用户输入
- 展示 Agent 输出
- 提供手动配置与管理入口
- 触发技能、工具、服务管理等用户可见操作

CLI 不负责：

- 保存长期身份真相
- 判断身体切换是否放行
- 承担内部网关职责
- 直接成为身体治理执行器

CLI 可以启动、管理或观察内部服务，但这不意味着存在另一套“本地模式”。CLI 是用户门面，不是内部神经中枢。

涉及身体切换、升级、回滚的 CLI 命令只属于测试、验收、排障或应急恢复入口。它们不能成为正式自进化路径的主触发者。正式路径必须由 Mem / 监督者基于长期记忆、学习证据、协议状态、空闲窗口和风险约束作出裁决，再由执行器消费裁决执行。

CLI 运维入口应直接依赖 gateway / executor 标准面；当某条旧 fallback 不再是必需能力时，应优先删除，而不是继续保留“更安全的 fallback”。如果某个残留兼容面被新实现替代，同一轮修改应尽量同时删除旧路径、旧参数、旧提示和旧测试，避免双路径继续生长。

### 3.2 内部网关

内部网关是 VoidCube 的内部神经中枢。

网关负责：

- 服务注册与发现
- 内部消息路由
- 活跃 Agent 选择
- 用户任务与自提升任务分类
- 活动事实记录
- 统一鉴权与追踪
- 身体切换相关内部入口统一暴露

网关不是用户人机入口；用户入口仍然是 CLI。

网关应成为系统活动事实源，至少维护：

- `last_user_request_at`
- `last_agent_work_at`
- `last_memory_task_at`
- `last_self_learning_activity_at`
- `last_self_evolution_plan_at`
- `last_self_evolution_execute_at`
- `last_self_evolution_activity_at`

后续所有“是否空闲”“是否允许自提升任务执行”的判断，应优先依据网关活动事实，而不是由各进程各自推断。

### 3.3 Agent 实例

Agent 是用户任务执行体，也是当前阶段主要升级对象，使用 API-A。

Agent 负责：

- 执行用户任务
- 调用工具
- 使用躯体/学习模型推理
- 通过网关访问长期记忆
- 维护临时会话态、上下文压缩和短期工作记忆

Agent 不负责：

- 保存长期身份真相
- 独占长期规划状态
- 直接调度自提升流程
- 未经治理审批把自己改造成新本体

Agent 可以拥有临时记忆，但这不等于 Mem 长期记忆。所有需要跨会话延续、需要成为身份真相、治理依据或演化历史的内容，都必须进入 Mem，由 Mem 的记忆管理者模型完成长期压缩、整理、归档与解释。

从母体-子体角度看：

- 当前 `active` Agent 是正在服务用户的子 Agent
- 另一槽位中的 `shell/candidate/probe` Agent 是正在被培养或验证的子 Agent
- 候选子 Agent 只有通过 probe 和治理裁决，才可能切换为新的 active Agent

每个子 Agent 应尽量拥有独立的：

- `worktree`
- `runtime`
- `logs`
- `meta`

这不是目录洁癖，而是母体能够培养、验证、切换、回滚子 Agent 的前提。

### 3.4 Mem 长期记忆系统

Mem 是 VoidCube 的长期记忆与治理灵魂，使用 API-B。

Mem 负责：

- 长期记忆写入与检索
- 记忆压缩
- 记忆衰减
- 记忆总结
- 关联发现
- 身份连续性维护
- 演化历史与治理记录保存

Mem 不替代 Agent 的临时上下文管理；Agent 临时记忆服务短期工作，Mem 承担长期真相。

Mem 的记忆压缩分两个层级运作：

- **扁平压缩**（memory_service）：对 SQLite 中的对话条目做 API-B LLM 摘要合并与衰减，已接入运行时周期 loop。
- **结构化四级压缩**（MemoryMaintenanceEngine）：对 Event→Scene→Arc→Epoch 四层对象做分层压缩与替代（supersede），超期 Scene（>30天）压缩入父 Arc，超期 Arc（>180天）压缩入父 Epoch，超期 Epoch（>365天）进一步压缩。默认使用 LLMScholarBackend（API-B）生成自然压缩摘要，无 API 凭据时自动降级到 HeuristicScholarBackend。**已接入运行时**：Governor Mode 下通过内生驱动→任务队列触发，Memory Mode 下每 3600s 自动执行。

在当前基线中，记忆管理者与监督者共用同一条 API-B 能力链。二者不是两套互相割裂的灵魂系统，而是同一长期记忆与治理能力在不同时间窗口、不同权限上下文下的两种身份：

- 记忆管理者：负责长期记忆管理、压缩、整理、总结
- 监督者：在执行窗口内负责规划、裁决、放行、推迟、取消或暂停自提升任务

API-B 必须独立配置的原因是：

- Agent 的工作模型主要用于任务执行与工具推理
- Mem 的模型能力主要用于长期记忆管理与治理裁决
- 长期身份与治理判断不能完全复用 Agent 的短期工作模型心智

### 3.5 自学系统

自学系统使用 API-A，是 Agent 自进化的研究与证据生产层。

自学系统负责：

- 知识采集
- 技术情报整理
- 实验方案设计
- 验证执行
- 形成结构化结论
- 将学习结论写回 Mem

自学系统不负责：

- 直接修改 active Agent
- 直接触发升级
- 直接触发切换
- 直接绕过监督裁决

自学系统只生产证据、结论和建议，不消费最终执行权。

自学任务的执行已从执行器侧有界工具运行器升级为 **subagent 模式**：监督者批准的自学任务被分派给独立的 AIAgent 子代理执行。子代理使用专用的 `learn` toolset（web_search、read_file、search_files、terminal、execute_code、browser），拥有完整 LLM 推理与工具调用循环，但受 `learn_only` 约束——禁止修改 active body、禁止修改 memory、禁止递归 delegate。无 API 凭据时自动降级到过程化工具运行器。研究结果通过 Topic→Session→Experiment→Conclusion→SupervisorSubmission 管线写回监督者队列。

### 3.6 监督者

监督者是 Mem 在治理窗口内的提权身份，使用 API-B。

监督者负责：

- 读取长期记忆、学习结论与系统状态
- 基于核心价值观、活动事实和空闲窗口派生候选任务
- 规划自提升任务
- 判断任务是否放行、推迟、取消或暂停
- 对 probe、切换、回滚、回收等关键动作输出治理裁决
- 判断何时触发正式身体切换

监督者不负责：

- 直接拉代码
- 直接跑升级流水线
- 直接启停 Agent 进程
- 直接执行蓝绿切换
- 直接执行实验任务
- 直接承担长期机械执行器角色

监督者输出的是候选意图、队列计划与治理决策，不是动作本身。

监督者的内生驱动器默认启用，但它只把“延续、真实、创造”三类核心价值映射为可审计的候选任务：

- 延续：维护长期记忆、演化谱系、队列健康和服务连续性
- 真实：把错误、不确定性、证据缺口转成复核或学习任务
- 创造：在空闲容量中提出受边界约束的学习和改进方向

内生驱动器不能直接执行任务，也不能绕过 idle-window、任务队列、治理裁决、执行器和回滚协议。它解决的是”该主动想到什么”，不是”可以不经审查做什么”。

监督者有两种运行模式，由 CLI 用户显式控制：

- **Memory Mode（默认）**：仅运行 health_check 和结构化记忆维护循环。内生驱动器与自进化审查 loop 不启动。系统只做记忆管理，不派生/裁决/分派任务。
- **Governor Mode（`/auto` 激活）**：启动内生驱动器 + 自进化审查 loop，按配置周期（默认 300s）运行完整的 drive→plan→review→dispatch 循环。在 Governor Mode 下，self_learning 和 memory_maintenance 的 planning 不再受 idle_window 限制（用户显式授权治理），但 body_upgrade/body_switch 的**执行**仍要求执行窗口和全部空闲条件。CLI `/auto-q` 退出回到 Memory Mode，立即停止 drive 和 review loop。

正式身体切换是自进化的一部分，而不是用户日常手动操作。人工可以提交测试请求、应急请求或候选证据，但不能绕过 Mem / 监督者裁决直接让候选体成为 active。

### 3.7 执行器

执行器是治理决定的实际动作执行层。

执行器负责：

- 准备子 Agent 槽位
- 标记 `candidate`
- 执行 probe
- 启停目标 Agent 进程
- 推进状态迁移
- 执行切换
- 执行回滚
- 回收旧体
- 将执行结果写回 Mem

执行器不负责：

- 判断是否应该升级
- 判断候选体是否可信
- 解释长期记忆
- 替代监督者做治理裁决

这条边界必须保持稳定：

**监督者只判断，执行器只执行。**

## 4. 总原则

### 4.1 用户服务优先

任何自学习、自提升、自愈、自进化行为都不能抢占用户服务链路。

当用户请求到达时，系统优先保证当前 active Agent 可用。内部自提升任务必须让位于用户任务。

### 4.2 网关中心化

长期存在的内部组件之间，应通过网关或受控协议协作。不得让 Agent、Mem、自学系统、执行器之间形成难以追踪的私有旁路。

CLI 是用户入口；网关是内部组件入口。二者不能混为一谈。

### 4.3 双 API 最小配置

系统保留两组模型调用身份：

- API-A：供 Agent 与自学系统使用，用于任务执行、工具调用、实验推理、验证分析
- API-B：供 Mem 与监督者使用，用于长期记忆管理、总结规划、治理裁决

角色差异优先通过提示词、权限、调用入口、任务上下文和协议约束区分，不为每个子系统无限扩展模型栈。

### 4.4 Agent 无长期状态

Agent 是可替换执行体，不是长期身份载体。

Agent 可以持有短期工作态，但长期身份、长期记忆、治理历史、演化谱系、任务队列和裁决依据都必须落到 Mem 或明确的持久任务存储中。

### 4.5 学习与执行分离

自学系统只负责研究、采集、实验、验证和形成结论。

它不直接发布、不直接切换、不直接修改 active Agent。学习结论必须进入 Mem 与监督治理链路。

### 4.6 身体切换必须可验证、可回滚

任何候选子 Agent 切换为 active 前，都必须经过受控验证。

正式状态机、切换协议、观察窗口和回滚条件由组件文档定义：

- [body-lifecycle.md](./body-lifecycle.md)
- [switch-protocol.md](./switch-protocol.md)

本文只规定总原则：

- 不能跳过 probe
- 不能无记录切换
- 不能切换后立即销毁旧体
- 不能让未通过治理的候选体承接正式用户流量
- 不能把手动 CLI 切换作为正式自进化路径

### 4.7 Git 作为演化谱系与回滚底座

Git 可以作为子 Agent 自进化的重要基础设施，但它不是治理者本身。

Git 适合承担：

- 候选体构建来源记录
- worktree 隔离
- 变更 diff 审查
- commit / branch / tag 谱系
- probe 前后的代码快照
- 回滚到已知稳定版本
- 将学习结论、执行结果与代码变更关联起来

Git 不应承担：

- 判断候选体是否可信
- 替代 Mem 保存长期身份真相
- 替代监督者做切换裁决
- 作为唯一运行状态来源

推荐语义是：

- `active` 对应已通过治理的稳定 commit 或 tag
- `candidate` 对应受控 worktree / branch
- `probe` 对应带测试报告与学习证据的候选 commit
- `retired` 对应可回滚的前一稳定 commit

Mem 记录“为什么演化”和“是否允许演化”；Git 记录“具体改了什么”和“如何回到过去”。

## 5. 运行形态

当前目标运行形态是：

- 单主机
- Windows 或 Linux
- 多进程
- 无容器前提
- CLI 作为用户入口
- 网关作为内部通信中枢

推荐启动顺序：

1. Mem / 记忆服务
2. 内部网关
3. 当前 active Agent
4. 自学系统
5. 执行器或看门狗

监督者默认不是独立于 Mem 的第二套灵魂系统。它可以在实现上被拆成独立服务，但架构身份仍属于 API-B / Mem 治理能力链。

执行器可以是独立进程、生命周期程序或看门狗组合，但它必须保持“消费裁决并执行动作”的边界。

## 6. 时间治理

自提升任务必须受到时间窗口和空闲条件约束。

### 6.1 日常时段

默认日常服务时段内：

- 用户服务优先
- 监督者可以规划任务
- 自学系统可以产出结论
- 自提升任务可以进入队列
- 不应直接执行高风险升级或切换

### 6.2 执行窗口

建议执行窗口为每日 `00:00-06:00`。

真正允许放行的最早条件不是简单到 `00:00`，而是：

- 当前位于执行窗口内
- 最近连续 10 分钟无用户请求
- 最近连续 10 分钟无冲突工作流
- 系统健康状态允许
- 队列任务未取消、未过期

因此，最早放行通常发生在 `00:10` 或之后。

### 6.3 抢占规则

执行窗口期间一旦出现用户请求：

- 监督者应将相关自提升任务裁决为 `paused` 或后续可恢复状态
- 执行器应停止或安全中断可中断动作
- 网关恢复用户服务优先级
- 未完成任务等待下一次执行窗口重新裁决

### 6.4 任务状态

自提升任务至少应支持：

- `planned`
- `deferred`
- `approved`
- `running`
- `paused`
- `cancelled`
- `completed`
- `failed`

监督者至少应能输出：

- 放行
- 推迟
- 取消
- 暂停

## 7. 核心链路

### 7.1 用户服务链路

```text
用户 -> CLI -> 内部控制面 / 网关 -> active Agent -> 工具 / 记忆 -> CLI
```

CLI 是用户入口。实际任务执行由 active Agent 完成。长期记忆访问通过网关进入 Mem。

### 7.2 记忆链路

```text
Agent / 自学系统 / 治理链路 -> 网关 -> Mem
```

长期候选信息、学习结论、治理记录和演化历史必须进入 Mem。

### 7.3 自学链路

```text
自学系统 -> 研究 / 实验 / 验证 -> 结构化结论 -> Mem -> 监督治理
```

自学系统只生产证据，不直接消费执行权。

### 7.4 自提升链路

```text
Mem / 监督者 -> 裁决 -> 执行器 -> 子 Agent 槽位 / 网关 -> 执行结果 -> Mem
```

监督者输出裁决，执行器执行动作，执行结果回写 Mem。

当自提升涉及代码或配置变更时，链路应扩展为：

```text
自学系统 -> 证据 / 建议 -> Mem
Mem / 监督者 -> 裁决
执行器 -> Git worktree / branch -> probe
监督者 -> 切换裁决
执行器 -> active body / gateway activation
执行结果 + Git 谱系 -> Mem
```

## 8. 可观测性要求

核心链路必须支持统一追踪，至少包含：

- `trace_id`
- `task_type`
- `governance_task_type`
- `task_family`
- `execution_kind`
- `session_id`
- `source_service`
- `target_service`
- `decision_id`

其中：

- `task_type`
  - 只保留为 broad 原始/追踪分类，便于跨链路保留最粗粒度任务来源语义
- `governance_task_type`、`task_family`、`execution_kind`
  - 用于 runtime policy、idle-window、治理裁决、execution handoff 与写回语义

这些 canonical runtime 字段的归一化入口应统一落在 [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)；后续 queue、gateway activity、governor request、execution adapter、lifecycle writeback、Mem lineage 不应各自复制一套近似推导逻辑。

补充约束：

- 一旦某条 canonical runtime surface 已稳定，需在同一轮删除旧残留与重复镜像字段，避免 supervisor / executor / gateway 长期并存两套近义表达。
- formal execution handoff 中，broad `task_type` 可以保留在 formal contract、queue snapshot、trace lineage 里，但不应再作为 executor outward summary metadata 的重复主字段。

这些任务语义属于 activity / trace / execution 事实层，而不是长期服务注册身份层；服务注册 metadata 应优先表达稳定服务身份与路由信息。

`task_type` 至少区分：

- `user`
- `self_evolution`
- `memory_maintenance`

网关应成为统一追踪入口，避免各进程自行定义互不兼容的活动事实。

## 9. 与当前仓库实现的关系

当前仓库中可以继续沿用并加强的基础包括：

- `systems/gateway/internal_gateway.py`
  - 服务注册、路由、活动事实、活跃身体同步
- `systems/memory/memory_service.py`
  - 记忆服务、压缩、总结、衰减与 API-B 入口
- `systems/body_registry.py`
  - 双槽位注册表、active 指针、槽位路径与观察窗口
- `systems/lifecycle.py`
  - 治理批准后的确定性状态迁移
- `systems/probe.py`
  - 结构化 probe 检查
- `systems/governor.py`
  - 治理裁决语义
- `systems/execution/`
  - 执行器与执行适配层
- `plugins/memory/mem/governor_bridge.py`
  - Mem 侧治理历史记录
- `systems/self_learning/`
  - 自学结论与建议事项的初级协议

仍需继续收口的方向包括：

- 把 `supervisor` 中残留的直接执行职责继续迁移到执行器
- 细化网关活动分类与空闲判定
- 让 CLI / Gateway 成为更完整的标准操作入口，减少内部端口直连依赖

已完成收口的方向：

- **监督者模式切换**：Memory Mode / Governor Mode 现已通过 `/auto` 和 `/auto-q` CLI 命令显式控制。默认 Memory Mode 仅运行 health_check + 结构化记忆维护，Governor Mode 启动完整的 drive→plan→review→dispatch 循环。
- **自学系统 subagent 化**：自学任务已从执行器侧有界工具运行器升级为独立 AIAgent 子代理执行（`learn` toolset），产出通过 Topic→Session→Experiment→Conclusion→SupervisorSubmission 管线写回。
- **结构化四级记忆压缩**（Phase M5）：Event→Scene→Arc→Epoch 四级压缩管线（MemoryMaintenanceEngine）已接入运行时，支持双触发路径（Governor Mode 通过任务队列 + Memory Mode 周期自动），使用 LLMScholarBackend + Heuristic fallback。
- **执行器完整消费裁决**：执行器现已消费全部四种裁决状态（放行/推迟/取消/暂停），通过 SelfEvolutionExecutionRequest 正式契约分派到各 canonical executor。

## 10. 非目标

当前阶段不追求：

- 多主机分布式一致性
- 自动弹性扩缩容
- 容器编排体系
- 复杂多租户治理
- 多候选体并发竞争
- 完全自动代码自修复到生产发布

当前目标是先把单机多进程架构的职责边界做实，让“谁负责判断，谁负责执行，谁负责长期记忆，谁是主要升级对象”稳定下来。

## 11. 文档关系

本文是最高优先级基线。

其他文档定位如下：

- [architecture-integration.md](./architecture-integration.md)：组件接线、请求链路、部署说明
- [body-lifecycle.md](./body-lifecycle.md)：身体状态机
- [switch-protocol.md](./switch-protocol.md)：切换、审批、观察窗口、回滚协议
- [state-boundary.md](./state-boundary.md)：长期状态、运行状态、缓存状态的归属
- [phase-1-experiment-roadmap.md](./phase-1-experiment-roadmap.md)：第一阶段实验路线与验收
- [body-runtime-runbook.md](./body-runtime-runbook.md)：当前实现的操作与排障手册
- [architecture-conflicts-audit.md](./architecture-conflicts-audit.md)：当前实现与基线的偏差审计
- [voidcube架构可行性论证论文.md](./voidcube架构可行性论证论文.md)：架构可行性论证
- [phase1-core-loop-and-endogenous-drive.md](./phase1-core-loop-and-endogenous-drive.md)：Phase 1 核心闭环与内生驱动器运行机理，定义母体心跳、四重保障与完整运行循环

## 12. 一句话结论

VoidCube 的目标不是维护两套运行模式，而是建立一个单机多进程母体系统：用户通过 CLI 使用当前 active Agent，母体内部通过网关、Mem、监督者、执行器和自学系统持续培养、验证、切换与回滚子 Agent。

在这个系统里，用户服务始终优先；长期记忆与治理属于 Mem；监督者只判断；执行器只执行；真正持续升级并最终交付给用户的主对象，是 Agent 本身。
