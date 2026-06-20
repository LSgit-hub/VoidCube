# VoidCube 架构集成说明

## 1. 文档定位

本文档是 [voidcube架构基线.md](./voidcube架构基线.md) 的集成与接线说明。

核心基线负责定义 VoidCube 是什么、哪些组件存在、职责边界是什么；本文只回答更具体的接线问题：

- 用户请求如何进入系统
- 内部组件按什么方向互相调用
- 服务启动后如何注册与发现
- 自学习、自提升、身体切换链路如何串联
- 当前仓库目录分别落在哪个组件位置

本文不重新定义总架构。若本文与核心基线冲突，以核心基线为准。

## 2. 集成总图

```text
用户
  -> CLI
  -> 内部控制面
  -> 网关
  -> active body route target
  -> 工具 / Mem / 外部能力
  -> CLI

自提升链路：

自学系统
  -> Mem
  -> 监督者裁决
  -> 执行器
  -> body slot / gateway activation
  -> 执行结果写回 Mem
```

集成时必须保持两个入口分工：

- CLI 是用户入口
- 网关是内部组件入口

开发调试时可以直连内部服务端口，但正式架构叙事中，Memory Service、Self-Learning、Executor 都不应成为日常用户入口。

## 3. 服务角色与连接方向

### 3.1 CLI

CLI 连接方向：

- 接收用户输入
- 调用内部控制面或网关入口
- 展示当前 active body 所承载 Agent 的返回结果
- 提供人工运维入口，例如启动服务、查看状态、触发受控流程

CLI 不应绕过治理链路直接完成身体切换、回滚或升级。

当前 CLI 运维侧已具备 `VoidCube_cli/ops/executor.py`，统一通过 gateway `/api/executor/...` 调用执行器，不再主动回退到 supervisor deprecated 路由。已挂载的常用命令包括：

- `VoidCube body status`
- `VoidCube body upgrade`
- `VoidCube body upgrade --start-agent`
- `VoidCube agent start`

这些命令只用于测试、验收、排障或应急恢复。正式身体切换应由 Mem / 监督者根据协议判断触发，而不是由用户日常手动发起。

### 3.2 网关

网关连接方向：

- 接收 CLI 或内部服务转发请求
- 路由用户任务到当前 active body route target
- 路由内部服务之间的受控调用
- 维护服务注册表
- 维护活动事实与 trace 信息
- 同步 `active_body`、`body_slots` 与 `body_routing` 管理面事实

网关至少应成为以下事实的统一来源：

- 当前 active body route target
- 当前 active body activation 状态
- 最近用户请求时间
- 最近 Agent 工作时间
- 最近记忆任务时间
- 最近自提升活动时间
- 最近自提升规划时间
- 最近自提升执行时间

### 3.3 Mem / 记忆服务

Mem 连接方向：

- 接收 Agent、自学系统、治理链路的长期记忆读写
- 接收学习结论与治理记录写入
- 在监督者身份下输出结构化裁决
- 将裁决历史与执行结果持久化

Mem 可以暴露记忆接口和治理接口，但二者都属于 API-B 能力链。实现上可以分端口、分路由或分服务，但架构上不应形成两套灵魂系统。

### 3.4 Agent 实例

Agent 连接方向：

- 从网关接收正式用户任务
- 通过工具层执行环境动作
- 通过网关访问 Mem
- 将需要长期保留的事实输送到 Mem
- 将运行痕迹写入当前 body slot 的 runtime/logs

Agent 不应直接决定自己成为新 active，也不应把长期身份真相只保存在本地。

### 3.5 自学系统

自学系统连接方向：

- 从 Mem 或指定资料源读取材料
- 使用 API-A 做研究、实验和验证
- 输出结构化学习结论
- 将结论写回 Mem
- 形成可被监督者读取的建议事项

自学系统不直接调用执行器做发布或切换。

### 3.6 执行器

执行器连接方向：

- 接收监督者裁决或受控编排请求
- 操作 body registry 与 body slot
- 执行 prepare、candidate、probe、activate、rollback、recycle
- 启停目标 Agent 进程
- 将执行结果写回 Mem
- 必要时通知网关同步 `active_body` 与 `body_routing`

执行器只执行，不替代监督者做最终治理判断。

当前实现中，执行器已经具备三层落点：

- `systems/execution/service.py`：标准 executor API wrapper，供 gateway / CLI 接入。
- `systems/execution/facade.py`：统一执行门面，聚合当前执行能力。
- `systems/execution/adapters.py` 与 `systems/lifecycle.py`：具体动作适配与确定性状态迁移。

当前 canonical HTTP 执行面已经收口到 executor service：

- gateway 推荐入口：`/api/executor/...`
- executor 直连入口：`/executor/...`
- body activation 标准入口：`/api/executor/body/activate`
- supervisor 仅保留 runtime / governance 路由，以及少量 execution route hint 元数据

## 4. 标准链路

### 4.1 用户任务链路

```text
用户
  -> CLI
  -> 网关
  -> active body route target
  -> 工具 / Mem
  -> active body route target
  -> 网关
  -> CLI
```

说明：

- CLI 负责用户体验与命令入口
- 网关负责找到当前 active body route target
- Agent 负责执行任务
- 需要长期记忆时，Agent 通过网关进入 Mem

### 4.2 记忆写入链路

```text
Agent / 自学系统 / 执行器
  -> 网关
  -> Mem
```

适用内容：

- 用户偏好与长期事实
- 学习结论
- probe 结果摘要
- 切换与回滚记录
- 执行结果
- 治理历史

### 4.3 自学习链路

```text
自学系统
  -> 资料采集 / 实验验证
  -> 学习结论
  -> Mem
  -> 监督者读取
```

自学系统产出的是证据和建议，不是执行许可。

### 4.4 自提升链路

```text
Mem / 监督者
  -> 任务规划
  -> 裁决
  -> 执行器
  -> body slot / Agent process / gateway activation
  -> 执行结果
  -> Mem
```

执行器必须把实际动作结果写回 Mem，让后续治理拥有历史依据。

当自提升涉及 Git 变更时，推荐接线为：

```text
自学系统
  -> 学习证据 / 改进建议
  -> Mem
  -> 监督者裁决
  -> executor
  -> Git worktree / evolution branch
  -> probe / diff / test report
  -> 监督者切换裁决
  -> active body
  -> Git 谱系与执行结果写回 Mem
```

Git 负责记录“改了什么、从哪来、如何回滚”；Mem / 监督者负责判断“为什么改、是否可信、何时切换”。

### 4.5 身体切换链路

```text
shell slot
  -> prepare
  -> candidate
  -> probe
  -> governor review
  -> active
  -> old active retired
  -> watch-window
  -> recycle or rollback
```

详细状态机与协议分别见：

- [body-lifecycle.md](./body-lifecycle.md)
- [switch-protocol.md](./switch-protocol.md)

## 5. 注册与发现

所有长期运行的内部服务启动后，应向网关注册。

建议注册信息至少包含：

- `service_name`
- `service_type`
- `service_id`
- `address`
- `health_endpoint`
- `metadata`
- `body_slot`，仅 Agent / body 相关服务需要
- `started_at`

这里的 `metadata` 主要用于稳定服务身份与路由相关事实，而不是承接每次任务的 runtime 语义。
例如更适合放在 service registration metadata 里的内容包括：

- `slot_id`
- `body_version`
- 服务自身版本或静态能力标记

相反，下面这些属于任务事实，不应作为长期服务注册元数据去维护：

- broad 原始 `task_type`
- `governance_task_type`
- `task_family`
- `execution_kind`

它们应优先进入 activity metadata、trace、execution request 与治理写回，而不是长驻在 `/register` 或 `/admin/services` 的服务身份面上。

当前网关已自动识别这些标准服务路由：

| service_type | 标准路由 |
| --- | --- |
| `memory` | `/mem/` |
| `agent` | `/agent/` |
| `supervisor` | `/supervisor/` |
| `executor` | `/executor/` |

网关应基于注册信息完成：

- active body 路由
- active body route target
- 内部服务发现
- 健康检查
- draining / activation 状态更新
- trace 与活动时间线补全

## 6. 启动顺序

推荐启动顺序：

1. Mem / 记忆服务
2. 内部网关
3. 当前 active body 对应的 agent 进程
4. 自学系统
5. 执行器或看门狗

原因：

- Mem 先启动，保证长期记忆与治理记录可写
- 网关随后启动，接收服务注册
- active body 对应 agent 进程启动后注册到网关
- 自学系统和执行器再加入内部链路

调试时可以只启动部分进程，但这只是接线完成度差异，不代表存在第二套正式架构。

## 7. 当前仓库落点

| 架构组件 | 当前落点 | 说明 |
| --- | --- | --- |
| CLI | `cli.py`、`VoidCube_cli/`、`VoidCube_cli/ops/executor.py` | 用户入口、命令、配置、手动运维与 executor ops 客户端 |
| 网关 | `systems/gateway/internal_gateway.py` | 服务注册、路由、活动事实、body activation |
| Agent 实例 | `run_agent.py`、`agent/`、`systems/agent/run_agent_instance.py` | 用户任务执行体与独立实例运行入口 |
| Mem / 记忆服务 | `systems/memory/`、`plugins/memory/mem/`、`Mem/` | 长期记忆、API-B、治理历史 |
| 自学系统 | `systems/self_learning/` | 学习结论、建议事项、实验记录 |
| 执行器 | `systems/execution/service.py`、`systems/execution/`、`systems/lifecycle.py` | executor 标准入口、执行门面、动作适配、确定性状态迁移。 |
| 治理语义 | `systems/governor.py`、`plugins/memory/mem/governor_bridge.py` | 裁决 schema、确定性裁决、Mem 侧历史 |
| 身体注册 | `systems/body_registry.py` | slot、active pointer、watch-window、runtime/logs/meta |
| Probe | `systems/probe.py` | 候选体结构化健康检查 |

## 8. 接线验收标准

一条集成链路是否合格，至少看这些点：

- CLI 能通过标准入口触发用户任务
- 网关能识别当前 active body 对应的服务实例
- Agent 能通过网关访问 Mem
- 自学系统能把结构化结论写入 Mem
- 监督者能输出结构化裁决
- 执行器能消费裁决并执行动作
- body slot 的 `worktree/runtime/logs/meta` 不互相污染
- active body pointer 与网关 activation 能保持一致
- 切换、回滚、回收结果能写回 Mem
- `trace_id` 与 broad `task_type` 能贯穿关键链路
- 涉及 runtime policy、治理裁决与 execution handoff 的链路，`governance_task_type`、`task_family`、`execution_kind` 也能贯穿
- 这些 canonical runtime 字段的归一化应复用 [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)，而不是在 gateway / supervisor / executor / governor / Mem bridge 各自复制判断分支

## 9. 当前过渡边界

当前仓库仍存在一些过渡状态：

- `supervisor` 已不再保留第二套 Python 执行对象面；剩余过渡语义主要收缩到 execution-side route hint 元数据
- executor service 已有标准入口，gateway 已支持 `/executor/` 标准内部路由
- 网关活动分类已区分 `self_learning`、self-evolution planning / execution；`planning_runtime` 也已把这些事实接到 `governance_task_type`、`task_family`、`execution_kind` 与 idle policy，后续重点转为继续下沉到 trace 归因和更多 runtime policy
- canonical runtime task profile 的归一化逻辑已收敛到共享 helper [`systems/runtime_task_profile.py`](../systems/runtime_task_profile.py)，主链不应再新增本地复制版推导
- 自学系统仍需加强为完整独立运行单元
- CLI 已挂载常用 executor 运维命令，但其定位是测试、验收、排障与应急恢复
- 正式自进化切换仍需进一步由 Mem / 监督者自动触发
- Git 谱系、diff、回滚点与 Mem 治理记录之间的接线仍需继续做实
- 运行手册中仍保留部分内部端口直连示例，用于联调、验收和应急恢复

这些问题不改变架构方向。它们属于接线完成度与职责收口问题。

## 10. 一句话结论

`architecture-integration.md` 的职责不是重新解释 VoidCube 是什么，而是把核心基线落成可接线的组件关系：CLI 进入，网关路由，Agent 工作，Mem 保存长期真相并输出治理裁决，自学系统提供证据，执行器消费裁决并执行身体生命周期动作。
