# Body Runtime Runbook

## 1. 文档定位

本文档说明当前仓库里的 body runtime 如何操作、观察和排障。

它不是架构规范。相关规范分别见：

- [voidcube架构基线.md](./voidcube架构基线.md)
- [architecture-integration.md](./architecture-integration.md)
- [body-lifecycle.md](./body-lifecycle.md)
- [switch-protocol.md](./switch-protocol.md)
- [state-boundary.md](./state-boundary.md)

本文只服务 4 个目标：

- 为测试、验收、排障或应急恢复走通 `shell -> candidate -> probe -> active -> retired -> shell`
- 验证 governor 裁决与 executor 动作边界
- 查看 active body、watch-window、governor history
- 排查当前实现与完整自动化之间的差距

正式身体切换不是人工日常操作，也不是 CLI 命令直接决定的动作。正式路径必须是：

```text
自学系统 / 运行证据 -> Mem
Mem / 监督者 -> 基于协议裁决
执行器 -> 消费裁决并执行
执行结果 -> Mem
```

本文中的 CLI 和 HTTP 示例仅用于测试、验收、排障、应急恢复，不能作为绕过 Mem / 监督者的正式自进化入口。

## 2. 当前实现能力

当前已经具备：

- 双槽位 body registry
- 槽位 `worktree/runtime/logs/meta` 隔离
- `candidate` 标记与 workspace materialize
- `probe` 执行与结构化 probe report
- governor 审批
- `active_slot` 与 `.body-active.json` 切换
- watch-window 后台监督
- watch-window 成功后回收旧体
- watch-window 失败后回滚
- 按槽位清退应退场的 agent 进程
- active body 激活后可同步到 gateway 路由目标
- 旧 body 可进入 gateway draining 状态

当前仍属于下一阶段集成工作的部分：

- 切换瞬间完整自动拉起新 active body 对应的 agent 进程
- 回滚时自动补起恢复体进程
- 旧 active 的更精细优雅退出
- CLI / Gateway 覆盖全部内部调试流程

一句话边界：

**当前已经做实 body 治理协议和启动目标切换，但还不是零人工的一键热切换平台。**

## 3. 核心对象与端口

常用对象：

- `slot-A`
- `slot-B`
- `.body-registry.json`
- `.body-active.json`
- `Mem` governor history

常用服务：

| 服务 | 默认端口 | 用途 |
| --- | --- | --- |
| Memory Service / Governor | `8001` | 记忆、治理裁决、watch-window 评估 |
| Gateway | `8000` | 内部路由、active body/agent 同步 |
| Executor | `8004` | body slot 操作、probe、启动/停止 Agent |
| Self-Learning | `8003` | 自学系统 |

标准日常入口优先级：

1. CLI
2. Gateway
3. 内部调试接口

下文中的 `Invoke-RestMethod` 示例默认用于联调、验收和排障，不是普通用户入口。

测试、验收或应急执行动作优先使用 CLI 运维命令：

- `VoidCube body status`
- `VoidCube body upgrade`
- `VoidCube body upgrade --start-agent`
- `VoidCube agent start`

这些 CLI 命令现在只走 gateway `executor` 标准入口。若 executor 路径不可用，命令应直接失败并优先修复 gateway / executor 接线，而不是再绕回 supervisor。

需要联调 HTTP 链路时，再使用 Gateway 标准路由：

- Gateway 路由：`http://127.0.0.1:8000/api/executor/...`
- Executor 直连：`http://127.0.0.1:8004/executor/...`，仅用于排查 gateway 注册或转发问题
- Supervisor 兼容路由：仅保留过渡期兼容、排障与应急恢复价值，不应再作为新的集成目标

## 4. 关键接口

### 4.1 Governor 裁决接口

位于 Memory Service。

- `POST /governor/review`
- `POST /governor/watch-window/evaluate`
- `GET /governor/watch-window/status`
- `GET /governor/history`
- `GET /activity/idle-check`
- `POST /activity/track`

### 4.2 Executor 状态查询接口

位于 Executor。

- `GET /executor/body/registry`
- `GET /executor/body/active-target`
- `GET /executor/body/slots`
- `GET /executor/body/slots/{slot_id}`

### 4.3 Executor 动作接口

位于 Executor。

- `POST /executor/body/slots/{slot_id}/prepare`
- `POST /executor/body/slots/{slot_id}/candidate`
- `POST /executor/body/probe/run`
- `POST /executor/body/probe/report`
- `POST /executor/body/upgrade/execute`
- `POST /executor/agents/start`
- `DELETE /executor/agents/{instance_id}`

## 5. 服务启动

推荐启动顺序：

1. Memory Service（端口 `8001`）
2. Gateway（端口 `8000`）
3. 当前 active body 对应的 agent 进程
4. Executor（端口 `8004`）
5. Self-Learning（端口 `8003`）

如果 CLI 已提供服务管理入口，优先使用：

```powershell
VoidCube services start
```

如果只做局部联调，可以只启动相关服务，但要记住这只是接线不完整，不是另一套正式架构。

## 6. 测试 / 验收换身流程

本节用于验证协议实现，不代表正式自进化触发方式。

正式自进化触发应来自 Mem / 监督者根据学习证据、长期记忆、空闲窗口、风险约束和回滚保护做出的裁决。

### 6.1 测试编排入口

测试时可使用 Executor 的编排接口验证完整流程：

```powershell
VoidCube body upgrade --body-version v2 --watch-window-seconds 120
```

说明：

- 正式推荐入口是 CLI -> Gateway `/api/executor/...`

或使用 Gateway HTTP 路由：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/executor/body/upgrade/execute `
  -ContentType 'application/json' `
  -Body '{"body_version":"v2","watch_window_seconds":120}'
```

该入口当前会串起：

- prepare shell slot
- mark candidate
- governor 审批进入 probe
- 执行 probe
- governor 审批切换
- 更新 active slot 与 `.body-active.json`
- 启动 watch-window

测试时如果希望切换后启动新 active body 对应的 agent 进程：

```powershell
VoidCube body upgrade --body-version v2 --watch-window-seconds 120 --start-agent
```

或使用 Gateway HTTP 路由：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/executor/body/upgrade/execute `
  -ContentType 'application/json' `
  -Body '{"body_version":"v2","watch_window_seconds":120,"start_agent":true}'
```

### 6.2 检查初始状态

```powershell
VoidCube body status
```

正常稳定态通常是：

- 一个槽位为 `active`
- 一个槽位为 `shell`

### 6.3 准备替身槽位

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/executor/body/slots/slot-B/prepare `
  -ContentType 'application/json' `
  -Body '{}'
```

预期结果：

- 准备 `slot-B/worktree`
- 准备 `slot-B/runtime`
- 准备 `slot-B/logs`
- 写入 worktree manifest
- 写入 runtime manifest

### 6.4 标记 candidate

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/executor/body/slots/slot-B/candidate `
  -ContentType 'application/json' `
  -Body '{"body_version":"v2"}'
```

该接口默认可先 prepare 再标记 candidate。

### 6.5 申请进入 probe

```powershell
$payload = @{
  request_id   = "health-001"
  event_type   = "health_review_request"
  body_id      = "slot-B"
  source_actor = "active_body"
  summary      = "Candidate build complete"
  evidence     = @{
    build_complete = $true
  }
  constraints  = @{
    target_transition = "candidate_to_probe"
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/governor/review `
  -ContentType 'application/json' `
  -Body $payload
```

成功后：

- `slot-B` 进入 `probe`
- 该槽位获得 probe lease

### 6.6 执行 probe

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/executor/body/probe/run `
  -ContentType 'application/json' `
  -Body '{"slot_id":"slot-B"}'
```

当前基础检查包括：

- entrypoint 是否存在
- runtime 目录是否存在
- probe 上下文是否可建立

probe 结果会写回槽位元数据，并作为切换审批证据。

### 6.7 测试切换裁决

正式环境中，这一步应由 Mem / 监督者根据协议自动发起。人工构造 payload 只用于测试、验收或排障。

```powershell
$payload = @{
  request_id   = "switch-001"
  event_type   = "switch_request"
  body_id      = "slot-B"
  source_actor = "gateway"
  summary      = "Promote body after probe pass"
  evidence     = @{}
  constraints  = @{
    watch_window_seconds = 120
  }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/governor/review `
  -ContentType 'application/json' `
  -Body $payload
```

预期结果：

- governor 输出 `approve_with_watch`
- `slot-B` 从 `probe` 升为 `active`
- 旧 active 降为 `retired`
- `.body-active.json` 指向新 active launch target
- watch-window 启动

### 6.8 查看 active body target

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/executor/body/active-target
```

重点检查：

- `slot_id`
- `worktree_path`
- `runtime_path`
- `logs_path`
- `launch_script_path`
- `launch_cwd`

### 6.9 启动 active body 对应的 agent 进程

```powershell
VoidCube agent start
```

预期行为：

- 新 Agent 读取 active body pointer
- 以 active slot 的 `worktree` 作为工作目录
- 将运行痕迹写入 active slot 的 `runtime`
- 将日志写入 active slot 的 `logs`

## 7. 观察窗口

### 7.1 查看状态

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8001/governor/watch-window/status
```

重点看：

- `watch_window.status`
- `task_running`
- `last_outcome`

### 7.2 自动监督逻辑

watch-window 后台任务会：

- 轮询 registry 中的 watch-window 状态
- 检查 `active_slot` 与 `retired_slot`
- 必要时运行 Agent health check
- 健康到期时提交 `post_switch_review`
- 健康失败时提交 `rollback_request`
- 按裁决清退应退场进程
- 同步 gateway active body 路由

### 7.3 测试 / 应急评估

正式环境中，观察窗口评估应由监督者自动触发。人工调用只用于测试、故障注入或应急恢复。

成功路径：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/governor/watch-window/evaluate `
  -ContentType 'application/json' `
  -Body '{"healthy_override":true}'
```

失败路径：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/governor/watch-window/evaluate `
  -ContentType 'application/json' `
  -Body '{"healthy_override":false,"metrics":{"reason":"manual failure injection"}}'
```

## 8. 回收与回滚

### 8.1 回收路径

watch-window 成功时：

- Memory Service 处理 `post_switch_review`
- 监督者输出回收裁决
- 执行器将旧 `retired` 回收到 `shell`
- 执行器清空 `retired_slot`
- 执行器清退仍绑定旧槽位的 Agent 进程
- 结果写回 Mem

### 8.2 回滚路径

watch-window 失败时：

- Memory Service 处理 `rollback_request`
- 监督者输出 `rollback_required`
- 执行器将旧 `retired` 恢复为 `active`
- 执行器更新 `.body-active.json`
- 网关切回旧 active
- 执行器清退失败槽位进程
- 回滚结果写回 Mem

## 9. 治理历史检查

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/governor/history?limit=20"
```

应能看到：

- review 记录
- execution outcome 记录
- registry 快照
- 切换 / 回滚 / 回收相关事件

## 10. 故障排查

### 10.1 `probe` 无法执行

检查：

- 槽位是否处于 `probe`
- `worktree_path` 是否存在 entrypoint
- `runtime_path` 是否已 bootstrap

优先调用：

- `GET /executor/body/slots/{slot_id}`
- `GET /executor/body/active-target`

### 10.2 切换成功但新进程不像新 body

检查：

- `.body-active.json` 是否已更新
- `GET /executor/body/active-target` 是否指向新槽位
- 新 Agent 是否是切换后重新启动的

当前实现边界：

- 已运行中的 Agent 不会因为 registry 改变而自动变身
- 当前保证的是后续启动目标已经切换

### 10.3 watch-window 没有自动动作

检查：

- `GET /governor/watch-window/status`
- `GET /activity/idle-check`
- 当前时间是否处于执行窗口
- 系统是否已空闲超过 10 分钟
- 监督者模式是否已激活

### 10.4 槽位状态混乱

优先以这些来源为准：

- `.body-registry.json`
- `.body-active.json`
- `GET /executor/body/registry`
- `GET /executor/body/active-target`

不要只看某个槽位目录下的零散文件。

### 10.5 gateway 路由未同步

检查：

- active body 对应的 agent 进程是否已启动
- 该进程是否已注册到 gateway
- gateway activation 是否指向新 slot
- 旧服务是否处于 draining

## 11. 当前现实边界

当前系统已经能回答：

- 谁是 active
- 谁是 shell
- 谁在 probe
- 谁该 retired
- 谁可回滚
- 下一个 Agent 应从哪个 body 启动

下一阶段要继续做实：

- 切换后自动拉起新 active body 对应的 agent 进程
- 回滚时自动补起恢复体进程
- 旧 active 的优雅退出
- Mem / 监督者自动触发正式自进化切换
- CLI / Gateway 标准入口只承担测试、验收、排障与应急恢复
- 与 gateway 流量切换串成更完整闭环

## 12. 与其他文档关系

- 总架构基线见 [voidcube架构基线.md](./voidcube架构基线.md)
- 集成接线见 [architecture-integration.md](./architecture-integration.md)
- 生命周期见 [body-lifecycle.md](./body-lifecycle.md)
- 切换协议见 [switch-protocol.md](./switch-protocol.md)
- 状态边界见 [state-boundary.md](./state-boundary.md)
