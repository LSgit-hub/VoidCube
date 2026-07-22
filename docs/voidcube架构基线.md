# VoidCube 服务化系统架构基线

更新日期：2026-07-21

## 1. 文档定位

本文只定义当前可执行架构和必须长期保持的边界，不保存迁移过程，也不把未来设想写成现行能力。实现细节从 [项目文件架构](./项目文件架构说明.md) 进入；未完成事项统一记录在 [当前问题](./全链路问题清单.md)。

## 2. 核心模型

VoidCube 由四个协作域组成：

| 域 | 当前职责 | 不负责 |
| --- | --- | --- |
| API-A / Agent | 用户推理、工具执行、自主学习、替身代码改进 | 长期身份真相、治理裁决、未经同意的身体激活 |
| API-B / Mem | 长期记忆、结构化压缩、证据与治理事件持久化 | 用户交互 UI、命令执行、身体切换 |
| Supervisor / Governor | 内生驱动、任务治理、风险审查、身体升级建议 | 直接替代 API-A 执行学习或编辑代码 |
| Gateway / Executor | 服务发现、双泳道状态、请求转发、身体生命周期机械操作 | 生成治理意图、解释长期记忆 |

模型配置只有两个逻辑槽：API-A 服务用户和自主执行，API-B 服务记忆与治理判断。两者可以使用同一供应商，但配置、凭据归属和运行语义必须分开。

## 3. 进程与入口

标准安装只暴露 `voidcube` 与 `vc`，都进入 `voidcube.py`。交互式调用链为：

```text
voidcube.py
  -> VoidCube_cli/main.py
    -> cli.py
      -> run_agent.py
```

统一启动器按以下顺序确保服务可用：

```text
Gateway :6000
  -> Memory :6001（向 Gateway 注册）
  -> Supervisor :6002（注册 supervisor 与内嵌 executor）
```

Executor 不运行独立 daemon。`VoidCubeExecutionService` 挂载在 Supervisor 进程中，经 Gateway 的标准 executor 路由访问。

Gateway 是跨进程调用 Executor 的唯一标准入口；Supervisor 进程内部通过
`VoidCubeExecutionFacade` 调用同一组 Execution Adapter，不需要为了形式统一绕行 HTTP。
两条入口必须收敛到同一实现，Supervisor 的判断逻辑不得直接修改身体或绕过 Adapter。

Supervisor 进程启动不等于自主链路启动。基础健康检查、结构化记忆维护、服务注册恢复和身体观察可以继续运行，自主链路门控初始为关闭。

## 4. 两条执行链路

### 4.1 用户链路

```text
用户输入
  -> 主 CLI
  -> API-A 主 Agent（user_chat）
  -> 工具 / Gateway / Mem
  -> 用户输出
```

主 CLI 始终是用户入口。启用自主链路不会把用户会话切换成另一种模式，也不会让用户请求等待自主任务完成。

### 4.2 自主链路

```text
/auto
  -> Supervisor 激活 gate
  -> API-B 内生驱动与治理复核
  -> 治理在途存储
  -> API-A 自主执行组件（supervisor_task）
  -> 结果写回 Mem
  -> 后续 API-B 周期重新读取
```

当前 `/auto` 是临时启用开关。它完成三件事：

1. 激活 Supervisor 的 autonomous-chain gate。
2. 启动内生驱动与治理复核周期任务，并立即触发首轮完整周期。
3. 启动当前 CLI 内嵌的 API-A 自主执行组件，用于拉取、认领、执行和写回自主任务。

`/auto-q` 是对应的临时停用开关：

1. 请求 Supervisor 关闭 gate 并取消内生驱动与治理复核任务。
2. 中断当前 CLI 正在执行的自主任务并写入明确终态。
3. 停止本地 API-A 自主执行组件和相关展示。

停用自主链路不会停止基础健康检查、Gateway、Memory、Supervisor 或用户主 CLI。未来可以在闭环成熟后评估移除 `/auto`，但这不是当前行为。

## 5. 双泳道与展示

Gateway 的两个一等泳道是：

| 泳道 | 所有者 | 内容 |
| --- | --- | --- |
| `user_chat` | 主 CLI / API-A 主 Agent | 用户会话、用户工具调用、用户输出 |
| `supervisor_task` | API-A 自主执行组件 | API-B 已转交的学习、改进及其工具过程 |

泳道由当前执行任务的来源决定，不由 CLI 是否显示自主面板决定。没有正在执行的自主任务时，主 CLI 会话仍属于 `user_chat`。

CLI 的自主面板只在 gate 已启用且存在判断活动、待执行事项或执行事件时显示；空态不得挤占用户输入。Web 小屋只读展示 API-B 判断、转交、API-A 回报和 Mem 回流，不提供用户聊天、人工队列管理或身体激活控制。

## 6. 记忆与治理存储

Memory Service 维护两层记忆：

- Tier 1：SQLite 会话、完整轮次、时间轴和可追溯归档。
- Tier 2：MemAI 的 Event、Scene、Arc、Epoch 结构化长期记忆。

Tier 1 -> Tier 2 桥接使用 API-B 的 LLM 提取与 Scholar 能力。LLM 不可用时必须返回显式健康/降级状态；压缩、升级和清退不得通过低质量静默替代造成不可逆信息损失。

治理在途 Store 由 Supervisor 持有，用于协调判断、认领、执行请求和回写投影；Mem 治理事件保存对应的追加式可恢复历史。每次任务变化先写 Mem，再发布 Store；启动时由较新的 Mem 投影校正 Store。管理清空写入截止事件而不删除历史。能够跨会话影响身份、判断或身体谱系的结果必须写回 Mem。

## 7. 身体治理

身体槽位与 Gateway 泳道是两组独立概念：

- 身体槽位回答“哪份代码正在服务、哪份代码正在改进与验证”。
- Gateway 泳道回答“当前执行属于用户请求还是监督者转交任务”。

身体升级主链路为：

```text
学习证据
  -> API-A 在 shell worktree 中改进代码
  -> 提交改进报告与 Git lineage
  -> Supervisor 验证边界并评分
  -> candidate / probe
  -> Governor 审查
  -> awaiting_user_consent
  -> 用户明确同意
  -> Executor 激活并进入观察窗口
```

不可妥协的约束：

- 活跃身体不得被自主改进直接覆盖。
- 改进报告必须绑定可验证的 baseline、commit、changed files 和学习证据。
- probe 失败的候选不得继续参与后续切换。
- Governor 只能建议或拒绝，不能替代用户同意。
- 激活失败或观察窗口失败必须使用经过验证的 Git lineage 回滚。

## 8. 请求与集成边界

- 所有现役模型调用统一构建 OpenAI-compatible `chat.completions` 请求。
- 在该协议边界改变前，通用聊天模型输出的浮点数组不构成正式 embedding 能力；真正语义检索必须先明确独立协议、模型版本、写入和回填规则。
- Provider 配置只描述模型、Base URL、凭据和明确支持的调用选项，不进行协议探测或消息协议切换。
- 主 Agent、辅助模型、Memory 和工具侧模型调用共享退役集成策略，不能各自保留隐藏回退。
- 可加载技能、运行态配置、源码、测试夹具和 wheel 都属于退役扫描表面。

## 9. 失败与恢复原则

- Gateway 注册丢失时，Memory、Supervisor 和内嵌 Executor 分别恢复自己的标准注册。
- 自主链路停用时，仍在 `running` 的任务必须写成带明确中断原因的 `failed` 终态；已进入 `awaiting_user_consent` 的身体候选保留给用户处理，不能静默遗留无法解释的状态。
- 服务不可达时，CLI 可以关闭本地执行状态，但必须提示 Supervisor 状态可能陈旧。
- 历史数据迁移只能是一次性、可验证操作；迁移完成后删除旧路径读取和长期双写。
- 诊断读取不得偷偷修复身体布局或持久化状态。

## 10. 文档所有权

- 本文：稳定的系统边界与当前运行语义。
- [当前问题](./全链路问题清单.md)：尚未解决的工程问题。
- [项目文件架构](./项目文件架构说明.md)：目录和调用入口。
- [开发与验证](./开发与验证.md)：验证命令与发布门槛。
- 专项设计：只解释仍在使用的协议细节，不维护第二份路线或完成日志。

任何实现变更如果改变本基线，必须在同一阶段更新代码、测试和本文。任何已完成或已经失真的计划文档应直接删除，由 Git 保存历史。
