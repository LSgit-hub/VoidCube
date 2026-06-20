# Switch Protocol

## 1. 文档定位

本文档定义 VoidCube body slot 从候选验证到正式切换、观察窗口、回滚与回收的正式协议。

总架构以 [voidcube架构基线.md](./voidcube架构基线.md) 为准；身体状态机以 [body-lifecycle.md](./body-lifecycle.md) 为准。本文只规定切换过程中各方如何协作、何时允许切换、何时拒绝、何时回滚。

切换协议解决的不是“复制一个运行中的 Agent”，而是：

- 验证候选 body
- 授予或撤回运行租约
- 切换网关流量
- 保留观察窗口
- 必要时回滚
- 将全过程写回 Mem

正式切换属于自进化治理链路，不属于日常人工操作。CLI、HTTP 或脚本入口只能用于测试、验收、排障、应急恢复，不能绕过 Mem / 监督者裁决直接把候选体提升为 active。

## 2. 协议参与方

### 2.1 Mem / 监督者

负责输出治理裁决：

- 是否允许 `candidate -> probe`
- 是否允许 `probe -> active`
- 是否要求回滚
- 是否允许 `retired -> shell`
- 是否已经满足自进化触发条件，例如学习证据、空闲窗口、风险约束和回滚保护

监督者只输出裁决，不直接执行切换动作。

### 2.2 执行器

负责消费裁决并执行动作：

- 准备槽位
- 运行 probe
- 切换 active body pointer
- 启停 Agent 进程
- 更新 body registry
- 回滚或回收槽位
- 写回执行结果

### 2.3 网关

负责流量与路由：

- 停止向旧 active 派发新请求
- 将正式用户流量指向新 active
- 将旧 active 标记为 draining 或退场状态
- 维护 `active_body`、`body_slots` 与 `body_routing` 管理面状态

### 2.4 Agent / Body Slot

被切换的对象是 body slot 中的子 Agent 运行体。

每个参与切换的槽位都必须具备独立的：

- `worktree`
- `runtime`
- `logs`
- `meta`

## 3. 租约定义

### 3.1 `probe lease`

`probe lease` 是授予候选体的临时、受限接入权。

允许：

- 健康检查
- 关键任务回放
- 兼容性验证
- 受控工具调用

禁止：

- 接收正式用户流量
- 写入未经标记的长期身份事实
- 宣告自己成为 active
- 绕过监督者直接请求网关切流

### 3.2 `active lease`

`active lease` 是授予当前正式工作体的活跃接入权。

允许：

- 接收正式用户请求
- 调用工具
- 通过网关访问 Mem
- 写入正常任务运行记录

约束：

- 任一时刻只能有一个有效 `active lease`
- active lease 的归属必须与 body registry 和 active body pointer 一致
- active lease 转移必须留下治理记录与执行记录

## 4. 切换前置条件

进入正式切换前，必须同时满足：

- 当前存在合法 `active` 槽位
- 目标槽位处于 `probe`
- 目标槽位的 probe report 已生成
- probe report 整体通过
- 候选构建来源、版本、目标能力与风险说明可追踪
- 目标槽位拥有独立 `worktree/runtime/logs/meta`
- Mem 可写入治理裁决记录
- 执行器可更新 body registry 与 active body pointer
- 网关可同步 `active_body` 与 `body_routing`
- 切换请求来自 Mem / 监督者治理链路，或被标记为测试、验收、排障、应急恢复请求

若任一条件不满足，切换应被拒绝或推迟。

## 5. 正常切换流程

### 5.1 提交切换请求

切换请求至少应包含：

- `request_id`
- `event_type = switch_request`
- `target_slot_id`
- 候选版本
- probe report 引用
- 风险摘要
- 回滚预案
- watch-window 参数

请求进入 Mem / 监督者裁决链路。

人工请求不得直接成为正式切换请求。人工最多提供：

- 测试触发
- 候选证据
- 应急恢复说明
- 排障上下文

监督者必须把这些输入重新转化为治理请求后，才能进入正式切换裁决。

监督者批准正式切换后，应生成 `SelfEvolutionExecutionRequest` 交给执行器。执行器只执行这份已批准交接单，不自行判断是否应该切换。

### 5.2 监督者审查

监督者至少审查：

- probe 是否通过
- 候选体是否满足目标改进
- 是否存在关键回归
- 是否破坏长期记忆或身份边界
- 是否具备回滚保护
- 当前是否允许执行切换

可能输出：

- `approve`
- `approve_with_watch`
- `reject`
- `defer`

正式切换建议优先使用 `approve_with_watch`，即切换后进入观察窗口。

### 5.3 执行器执行切换

监督者批准后，执行器执行：

- 确认目标仍为 `probe`
- 确认旧 active 仍存在
- 更新目标槽位为 `active`
- 更新旧 active 为 `retired`
- 更新 body registry
- 更新 active body pointer
- 启动或绑定新 active body 对应的 agent 进程
- 将执行结果写回 Mem

执行器不得在没有裁决的情况下自行切换。

### 5.4 网关同步流量

网关执行：

- 停止向旧 active 派发新请求
- 等待在途请求结束或进入安全边界
- 将正式用户流量指向新 active
- 将旧 active 标记为 draining / retired
- 更新 active body activation 状态

### 5.5 进入观察窗口

切换完成后，系统进入 watch-window。

观察窗口期间：

- 新 active 接收真实流量
- 旧 active 保留为 `retired`
- 系统持续检查健康状态
- 失败时可触发 rollback request
- 成功到期后可触发 post-switch review

## 6. 拒绝协议

出现以下任一情况，应拒绝或推迟切换：

- 目标槽位未处于 `probe`
- probe report 不存在
- probe report 未通过
- 候选体无法稳定启动
- 核心能力退化
- 关键任务回放失败
- 长期记忆或身份边界异常
- active body pointer 与 registry 不一致
- 网关无法同步新 active
- 当前存在用户服务抢占或冲突工作流
- 回滚路径不可用

拒绝后应执行：

- 当前 active 继续服务
- 候选体退出或保持受控状态
- 失败原因写回 Mem
- 必要时执行 `probe -> shell`

## 7. 观察窗口协议

观察窗口是切换后的安全缓冲层。

观察窗口至少应检查：

- 新 active 进程健康
- 网关路由健康
- 基础任务成功率
- 工具调用是否正常
- Mem 读写是否正常
- runtime/logs 是否写入目标槽位
- 是否出现身份边界异常
- 是否出现跨槽位污染

观察窗口结束时，监督者应输出：

- `approve_recycle`：允许 `retired -> shell`
- `rollback_required`：要求回滚
- `defer`：继续观察或等待人工处理

观察窗口结束前，旧 active 不得被直接删除。

## 8. 回滚协议

### 8.1 回滚触发条件

观察窗口内出现以下情况，应触发回滚审查：

- 新 active 异常退出
- 网关健康检查失败
- 关键功能显著失效
- 任务成功率明显下降
- Mem 读写异常
- 行为与治理约束冲突
- 监督者判定新 active 不可信

### 8.2 回滚执行

监督者输出 `rollback_required` 后，执行器执行：

- 停止或隔离失败 active
- 将旧 `retired` 恢复为 `active`
- 更新 body registry
- 更新 active body pointer
- 通知网关切回旧 active
- 将失败新体转为可诊断或待回收状态
- 写回回滚结果

### 8.3 回滚记录

回滚记录至少包括：

- 失败 slot
- 恢复 slot
- 失败版本
- 失败原因
- 回滚时间
- 用户影响范围
- 后续修复建议

回滚不是异常文档化之外的临时操作，而是正式协议的一部分。

## 9. 回收协议

观察窗口成功通过后，旧 `retired` 可回收到 `shell`。

回收前应满足：

- 新 active 稳定
- 监督者批准回收
- 旧体已完成必要同步
- 旧体上的运行进程已清退
- 旧体 runtime 中不再保存唯一长期真相

回收动作包括：

- `retired -> shell`
- 清理短期 runtime/cache/tmp
- 保留必要 meta 与谱系引用
- 准备下一轮候选构造

## 10. 协议硬约束

- 不允许从 `candidate` 直接切为 `active`
- 不允许绕过 `probe`
- 不允许无治理裁决切换 active
- 不允许把 CLI / HTTP 手动请求当成正式切换裁决
- 不允许未写入 Mem 的切换成为正式历史
- 不允许切换瞬间立即销毁旧 active
- 不允许同时存在两个有效 active lease
- 不允许候选体接收正式用户流量
- 不允许跨槽位混写 `runtime/logs/meta`

## 11. Git 在切换协议中的位置

Git 是演化谱系、差异审查和回滚底座，不是切换裁决者。

建议每次候选体进入 `candidate` 或 `probe` 时，记录：

- `source_branch`
- `source_commit`
- `candidate_branch`
- `candidate_commit`
- `diff_summary`
- `test_report_ref`
- `probe_report_ref`
- `rollback_commit`

推荐分支语义：

- `main` 或稳定分支：当前母体稳定代码
- `body/slot-A`、`body/slot-B`：槽位 worktree 对应分支或引用
- `evolution/<task-id>`：自进化任务候选分支
- `stable/<version>`：通过治理后的稳定标记

切换前，监督者应能看到 Git diff、测试结果、probe report 和回滚点。切换后，执行器应把实际 active commit、retired commit、回滚点和执行结果写回 Mem。

## 12. 与其他文档关系

- 总架构基线见 [voidcube架构基线.md](./voidcube架构基线.md)
- 身体状态机见 [body-lifecycle.md](./body-lifecycle.md)
- 状态归属见 [state-boundary.md](./state-boundary.md)
- 当前实现操作见 [body-runtime-runbook.md](./body-runtime-runbook.md)
