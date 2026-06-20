# Body Lifecycle

## 1. 文档定位

本文档定义 VoidCube 身体层的正式状态机。

总架构以 [voidcube架构基线.md](./voidcube架构基线.md) 为准；本文只规定 body slot 在生命周期中的状态、允许转移、禁止转移、并发约束和记录要求。

身体是执行容器，不是长期身份载体。按当前基线，两个 body slot 对应母体管理的两个子 Agent。任一时刻只有一个子 Agent 可以处于 `active` 并接收正式用户流量。

切换审批、观察窗口和回滚流程见 [switch-protocol.md](./switch-protocol.md)。

## 2. 状态集合

当前正式状态限定为 5 种：

- `shell`
- `candidate`
- `probe`
- `active`
- `retired`

任何实现不得引入新的正式状态来绕过这些状态的治理语义。若需要补充中间执行细节，应放入执行记录、probe report、watch-window 信息或任务状态中，而不是扩大 body state 集合。

## 3. 状态定义

### 3.1 `shell`

`shell` 是可被下一轮改造使用的空壳槽位。

特征：

- 不接收正式用户流量
- 不持有 active lease
- 不保存长期身份真相
- 可以被执行器准备为下一轮候选体
- 应保留自己的 `worktree/runtime/logs/meta`

`shell` 的意义是提供一个可隔离改造的子 Agent 槽位，避免直接热改当前 active Agent。

### 3.2 `candidate`

`candidate` 是已经完成构造或补丁应用、等待进入验证的候选身体。

特征：

- 构建或改造已完成
- 尚未获得 probe lease
- 不接收正式用户流量
- 等待监督者批准进入 probe
- 必须具备可追踪的构建来源与版本信息

`candidate` 只能作为待验证对象存在，不能直接晋升为 `active`。

### 3.3 `probe`

`probe` 是候选身体进入正式切换前的受控验证态。

特征：

- 可获得受限 probe lease
- 仅用于健康检查、回放测试、兼容性验证
- 不接收正式用户流量
- 使用候选槽位自己的 `worktree/runtime/logs/meta`
- probe 结果必须结构化记录

`probe` 是切换前的强制安全缓冲层。任何候选体未经 probe，不得成为 `active`。

### 3.4 `active`

`active` 是当前唯一对外工作的合法身体。

特征：

- 持有 active lease
- 接收正式用户流量
- 由网关路由为当前工作 Agent
- 由 active body pointer 指向其 launch target
- 运行痕迹写入自身槽位的 runtime/logs

系统任一时刻只能有一个 `active` body slot。

### 3.5 `retired`

`retired` 是旧 active 在切换完成后的短期保留态。

特征：

- 不接收新用户流量
- 保留回滚能力
- 可用于观察窗口内诊断
- 观察窗口成功后应被同步并回收为 `shell`
- 观察窗口失败时可恢复为 `active`

`retired` 不是无意义残留，而是回滚保护层。观察窗口结束前不应直接销毁。

## 4. 允许状态转移

正式允许的状态转移如下：

```text
shell -> candidate
candidate -> probe
probe -> active
probe -> shell
active -> retired
retired -> active
retired -> shell
```

### 4.1 `shell -> candidate`

含义：空壳槽位被构造为候选体。

必要条件：

- 目标槽位当前为 `shell`
- 构建来源可追踪
- 槽位 `worktree/runtime/logs/meta` 已准备

结果：

- 槽位进入候选审查阶段
- 记录候选版本、构建来源和 materialized 信息

### 4.2 `candidate -> probe`

含义：候选体获准进入受控验证。

必要条件：

- 候选构造完成
- 监督者批准进入 probe
- probe lease 可发放

结果：

- 槽位进入 `probe`
- 后续只允许执行受控健康检查与回放验证

### 4.3 `probe -> active`

含义：候选体通过验证并成为新的正式工作体。

必要条件：

- probe 通过
- 监督者批准切换
- 网关和 active body pointer 可同步
- 旧 active 可进入 `retired`

结果：

- 目标槽位成为新的 `active`
- 旧 active 进入 `retired`
- watch-window 启动
- 切换记录写入 Mem

### 4.4 `probe -> shell`

含义：候选体验证失败或被拒绝后回到空壳。

必要条件：

- probe 失败，或监督者拒绝切换
- 失败原因可记录

结果：

- 候选路线终止
- 槽位回收或等待后续修复
- 失败经验写回 Mem

### 4.5 `active -> retired`

含义：旧 active 在新 active 接管后进入保留态。

必要条件：

- 新 active 已被批准并接管
- 旧 active 不再接收新流量

结果：

- 旧体保留在观察窗口内
- 旧体可作为回滚目标

### 4.6 `retired -> active`

含义：观察窗口内触发回滚，旧体恢复为 active。

必要条件：

- 新 active 失败或被判定不可信
- 监督者批准回滚
- retired 槽位仍具备恢复条件

结果：

- retired 槽位恢复为 `active`
- 失败体退出正式工作态
- 回滚记录写回 Mem

### 4.7 `retired -> shell`

含义：观察窗口成功后，旧体同步并回收为空壳。

必要条件：

- 观察窗口通过
- 当前 active 稳定
- 旧体已完成必要同步或清理

结果：

- retired 槽位回到 `shell`
- 成为下一轮可改造槽位
- 系统重新回到一个 `active` + 一个 `shell` 的稳定结构

## 5. 禁止状态转移

以下状态转移默认禁止：

- `shell -> probe`
- `shell -> active`
- `candidate -> active`
- `candidate -> retired`
- `active -> shell`
- `active -> candidate`
- `retired -> candidate`
- `probe -> retired`

禁止原因：

- 不能跳过候选构造
- 不能跳过 probe
- 不能绕过监督审批
- 当前 active 不能直接被改造成候选体
- retired 只能用于回滚或回收，不能直接进入新候选路线

如果实现确实需要处理异常恢复，应通过显式 recovery task、rollback task 或人工修复流程完成，并写入 Mem，而不是扩大状态机的合法跳转。

## 6. 并发约束

身体层必须满足以下并发约束：

- 同时只能有一个 `active`
- 同时最多只有一个主要 `candidate`
- 同时最多只有一个主要 `probe`
- 同时只应有一个由 registry 承认的 active body pointer
- `retired` 只应在观察窗口内保留
- `retired -> shell` 前应完成稳定性确认、必要同步和清理

若 registry、gateway activation、active body pointer 出现不一致，应优先暂停切换动作，并进入人工或受控恢复流程。

## 7. 槽位结构要求

每个 body slot 至少应有独立的：

- `worktree`
- `runtime`
- `logs`
- `meta`

这些结构分别承担：

- `worktree`：该子 Agent 的代码或模板工作树
- `runtime`：该子 Agent 的运行态、缓存、临时状态
- `logs`：该子 Agent 的运行观察与审计记录
- `meta`：该子 Agent 的生命周期、版本、租约、probe 和切换元数据

长期身份、长期记忆、治理历史和演化谱系不得只保存在槽位本地；这些内容应写入 Mem。

## 8. 生命周期记录要求

每次状态变化都必须记录，至少包括：

- `slot_id`
- 源状态
- 目标状态
- 触发原因
- 发起者
- 监督裁决 ID
- 执行结果
- 时间戳

如果状态变化伴随切换、回滚或回收，还应记录：

- active slot 变化
- retired slot 变化
- watch-window 状态
- probe report 摘要
- active body pointer 路径
- `worktree/runtime/logs/meta` 路径引用

记录应写回 Mem 或受控持久存储，不能只留在执行器本地日志中。

## 9. 稳定轮换形态

稳定轮换形态应收敛为：

```text
slot-A = active
slot-B = shell

shell -> candidate -> probe -> active
old active -> retired -> shell
```

或者反向：

```text
slot-B = active
slot-A = shell

shell -> candidate -> probe -> active
old active -> retired -> shell
```

也就是说，系统长期保持一个服务用户的 active 子 Agent 和一个可被培养的 shell 子 Agent。候选构造、probe、切换、观察窗口和回收都只是这个稳定结构之间的受控过渡。

## 10. 与其他文档关系

- 总架构基线见 [voidcube架构基线.md](./voidcube架构基线.md)
- 切换审批、观察窗口与回滚流程见 [switch-protocol.md](./switch-protocol.md)
- 状态归属与长期状态边界见 [state-boundary.md](./state-boundary.md)
- 当前实现操作步骤见 [body-runtime-runbook.md](./body-runtime-runbook.md)
- 历史讨论草稿见 [archive/body-lifecycle-and-switch.md](./archive/body-lifecycle-and-switch.md)
