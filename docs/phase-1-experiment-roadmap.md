# Phase 1 Experiment Roadmap

## 1. 目的

本文件把 VoidCube 当前已经形成的理论文档，压缩为一条可执行的第一阶段实验路线。

Phase 1 的目标不是一次性完成完整自进化生命体，而是建立最小闭环：

- 有双身体槽位
- 有灵魂层治理入口
- 有候选体 `probe`
- 有正式切换
- 有观察窗口
- 有回滚

只要这个闭环跑通，VoidCube 就从“有理论的 Agent”进入“具备可验证身体轮换能力的实验系统”。

这里的核心不是为了让 CLI、网关、执行器自己先变得多强，而是为了让系统能持续产出更好的 Agent，再切给用户使用。

再用当前基线的话说：

- VoidCube 是母体
- Phase 1 要先证明母体能稳定培养、验证、切换两个子 Agent
- 用户最终只接触当前活跃子 Agent

## 2. Phase 1 总目标

Phase 1 必须回答 5 个问题：

1. 当前哪具身体是本体
2. 另一具空壳身体如何被改造
3. 候选体如何在不接真实流量的前提下接受审查
4. 网关如何把流量切给新本体
5. 失败时如何回滚，并保留灵魂连续性

换句话说，Phase 1 的真实目标是：

- 围绕 Agent 这个升级对象
- 把自学习、自治理、自切换的内部链路做实

同时把子 Agent 做成独立可管理对象，而不是混在母体工程临时目录里。

## 3. Phase 1 不做什么

为了保证实验收敛，Phase 1 明确不做：

- 多候选体并发竞争
- 多本体协同
- 自动多轮自进化循环
- 热上下文迁移
- 跨主机分布式切换
- 全自动代码自修复到生产发布
- 复杂权限系统

Phase 1 只做最小、可观测、可回退的单机实验。

## 4. Phase 1 的最小成功标准

如果以下 6 件事都能完成，Phase 1 就算成功：

1. 系统能识别 `slot-A` / `slot-B`
2. 系统能标记其中一个是 `active`，另一个是 `shell`
3. `shell` 能被构造成 `candidate`
4. `candidate` 能进入 `probe` 并跑最小健康检查
5. 审批通过后网关能把活跃指针切到新体
6. 新体异常时旧体能在观察窗口内恢复

## 5. 推荐实现顺序

建议按以下顺序推进，而不是并行散开：

### Step 1：建立身体槽位注册表

交付物：

- `.body-registry.json`
- `slot-A/slot-B` 的最小元数据结构

至少记录：

- `active_slot`
- `shell_slot`
- `retired_slot`
- `generation`
- `watch_window`

验收标准：

- 系统在没有运行任何 Agent 时，也能读出当前身体拓扑

### Step 2：建立固定双槽位目录模型

交付物：

- `.body-slots/slot-A/`
- `.body-slots/slot-B/`
- 每个槽位的 `worktree/`、`runtime/`、`logs/`、`meta.json`

验收标准：

- 每个槽位可被单独识别
- 两个槽位的 runtime 状态互不覆盖
- 两个子 Agent 的 `worktree/logs/meta` 也互不污染

### Step 3：建立最小生命周期执行器

交付物：

- 一个能做如下动作的执行入口：
  - 初始化槽位
  - 把 `shell` 标记为 `candidate`
  - 发起 `probe`
  - 执行切换
  - 执行回滚
  - 回收 `retired -> shell`

这一步未必要完整自动化，但必须把动作边界固定下来。测试、验收、排障和应急可以直接调用执行入口；正式自进化切换必须来自 Mem / 监督者批准后的执行请求。

验收标准：

- 状态转移只通过受控入口发生

### Step 4：建立灵魂层治理入口

交付物：

- `Memory Mode` 与 `Governor Mode` 的最小请求区分
- `Governor Mode` 的结构化输入输出
- `SelfEvolutionExecutionRequest`，用于把批准后的任务交给 executor

至少先支持三类治理请求：

- `health_review_request`
- `switch_request`
- `rollback_request`

验收标准：

- 同一个 Mem 能区分记忆态与监督态输入
- 监督态输出结构化决策而不是自由散文
- body upgrade / switch 类正式执行请求必须包含目标 slot、Git candidate commit、rollback commit、probe 引用和回滚预案

### Step 5：建立 probe 最小健康检查

交付物：

- 一套最小 `probe` 检查清单

建议首批检查包括：

- 启动成功
- 配置加载成功
- 记忆读写通路正常
- 一个最小工具调用成功
- 一个最小任务回放成功

验收标准：

- `probe` 的通过/失败能被结构化记录

### Step 6：建立最小切流能力

交付物：

- 网关或活跃指针切换能力

至少做到：

- 当前正式请求只指向一个 `active`
- 切换后 `active_slot` 改变
- 旧体进入 `retired`

验收标准：

- 不出现双 `active`

### Step 7：建立观察窗口与回滚

交付物：

- 观察窗口状态
- 回滚条件判断
- 回滚动作入口

验收标准：

- 新本体异常时旧本体可恢复
- 回滚结果被写回灵魂层

## 6. 推荐模块落点

为了尽量贴合现有仓库，Phase 1 建议这样落模块：

### 6.1 身体槽位与注册表

建议新建或集中在：

- `systems/`
- 或 `VoidCube_cli/` 下的专门运行时模块

建议不要继续把“身体槽位状态”散落在 CLI 临时逻辑里。

应该把它们正式看成“母体管理两个子 Agent”的状态基座。

### 6.2 worktree / runtime 隔离

优先复用：

- `cli.py` 中已有的 worktree 逻辑

但要把它从“用户并行工作辅助”升级为“正式身体槽位能力”。

### 6.3 生命周期执行器

优先落在：

- `systems/execution/`
- `systems/lifecycle.py` 及相关生命周期模块

它的语义应该明确为“身体生命周期执行器”，而不是继续混成“监督者兼执行器”。

它存在的意义，是把监督治理面对 Agent 的升级判断，落实成真实动作。

### 6.4 网关切流

优先落在：

- `systems/gateway/`

因为从理论上讲，切流属于神经系统，而不是 CLI。

### 6.5 灵魂治理

优先落在：

- `Mem`
- `plugins/memory/mem`
- 或连接 Mem 与主运行时的桥接层

目标是让 Mem 不再只是记忆后端，而成为治理入口。

而执行器、自学系统、网关等模块，都是为了共同支撑 Agent 的持续改进与替换。

## 7. 推荐第一批文件级交付物

如果进入实现期，我建议第一批“明确会出现的新文件/模块”是：

- `docs/phase-1-experiment-roadmap.md`
- 一个身体注册表模块
- 一个槽位元数据模块
- 一个生命周期状态机模块
- 一个最小 `probe` 检查模块
- 一个灵魂层治理请求/响应 schema 模块

注意：这里先强调“模块边界”，不要求一开始全自动。

## 8. 推荐第一批结构化对象

Phase 1 至少需要这些对象：

### 8.1 `BodyRegistry`

负责全局身体拓扑：

- 哪个槽位 `active`
- 哪个槽位 `shell`
- 哪个槽位 `retired`

### 8.2 `BodySlotMeta`

负责单槽位元数据：

- 当前状态
- worktree 路径
- runtime 路径
- 最近探测结果

### 8.3 `ProbeReport`

负责最小探测结果：

- 启动是否成功
- 配置是否成功
- 检查项列表
- 总体结果

### 8.4 `GovernorDecision`

负责监督态输出：

- `approve`
- `reject`
- `rollback_required`
- 风险级别
- 原因摘要

## 9. 推荐第一批测试/半自动边界

Phase 1 不需要一开始就全自动。建议接受以下测试、验收和排障过渡形态：

- 候选体构建可以半自动
- `probe` 可先由脚本触发
- 治理请求可先由人工构造证据触发
- 观察窗口可先定长

但必须避免：

- 无记录的手工切换
- 绕过灵魂层的直接晋升
- 未经 `probe` 的直接上岗

正式路径不应是“人点一下切换身体”。正式路径应是：Mem / 监督者根据学习证据、Git lineage、probe report、空闲窗口、风险和回滚保护生成批准，再由 executor 消费 `SelfEvolutionExecutionRequest` 执行动作。人工入口保留测试、验证和应急价值。

## 10. 推荐验收场景

Phase 1 最好至少完成以下 3 个实验场景：

### 场景 A：正常升级

- `slot-A = active`
- `slot-B = shell`
- A 改造 B
- B 经 `probe` 通过
- 网关切到 B
- A 进入 `retired`
- 稳定后 A 回收为 `shell`

### 场景 B：候选失败

- `slot-A = active`
- `slot-B = candidate/probe`
- B 检查失败
- 灵魂层拒绝
- A 持续工作
- B 回到 `shell`

### 场景 C：切换后回滚

- B 切换成功成为 `active`
- 观察窗口内 B 异常
- 灵魂层要求回滚
- 网关切回 A
- B 退出工作态

## 11. Phase 1 完成后的下一步

只有当 Phase 1 跑通，才建议进入 Phase 2：

- 多候选体
- 更复杂的治理评分
- 自动化补丁生成与应用
- 更智能的观察窗口
- 更强的 Mem 审计与谱系追踪

## 12. 结论

Phase 1 的本质不是“让 Agent 自动改自己很多次”，而是：

**先证明 VoidCube 能在灵魂连续的前提下，安全地更换一具身体。**

只要这件事成立，后面的自愈与自进化才有现实基础。
