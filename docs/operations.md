# 运行与恢复

## 首次启动

使用 Python 3.14 虚拟环境安装开发版本：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[all,dev]"
.venv\Scripts\python.exe -m voidcube doctor
.venv\Scripts\python.exe -m voidcube
```

用户配置在 `VOIDCUBE_HOME/config.yaml`，默认 `~/.VoidCube/config.yaml`。凭据放在 `VOIDCUBE_HOME/.env` 或凭据存储，不要写入仓库配置。

## 服务生命周期

```powershell
.venv\Scripts\python.exe -m voidcube serve start
.venv\Scripts\python.exe -m voidcube serve status
.venv\Scripts\python.exe -m voidcube serve stop
```

状态异常时先看 `serve status` 和 `VOIDCUBE_HOME/logs/`，再运行 `doctor`。桌面端关闭窗口不会自动停止后台服务；使用服务菜单或 `serve stop` 停止。

### Gateway 服务认证

Gateway 配置了 `auth_token` 时，注册、管理接口、会话接口和健康上报接口需要 Gateway root token（`Authorization: Bearer <token>`，或 `x-voidcube-gateway-token`）。服务注册成功后会返回只属于该注册实例的动态 `service_token`；服务后续调用 `POST /health/{service_id}` 时必须同时使用 URL 中对应的 `service_id` 和请求头 `x-voidcube-service-token: <service_token>`。不要把 Gateway root token 当作服务 token，也不要复用其他服务实例的 token。服务被替换或删除后，旧 token 立即失效。

Memory、Supervisor 和 Goal Manager 会在注册成功后保存服务 token，并在周期健康检查中上报自身状态。Memory 会在注册检查发现实例缺失时按检查周期重新注册；Supervisor 会在健康检查周期恢复缺失的 supervisor/executor 注册；Goal Manager 在健康刷新发现 401/404 时会清理旧凭据，并在当前刷新周期立即重新注册和再次上报。若当前 Gateway 仍不可用，各服务会继续按自身周期重试。若嵌入式测试或隔离运行环境不提供 Gateway，可设置 `VOIDCUBE_DISABLE_GATEWAY_HEALTH_REPORT=1` 禁用 Supervisor 的上报；这不会关闭本地健康检查。

Gateway 动态路由通过 `/admin/routes` 管理。路由前缀必须以 `/` 开始和结束，目标实例必须已经注册，且 `target_service` 必须与实例的服务类型一致；服务暂时不健康时仍可保留路由配置，恢复后会继续使用。管理接口更新或删除包含斜杠的前缀时，使用 URL 编码后的完整前缀。会话注册会清理过期的 CLI 会话，并规范化 `session_id`、`owner_id` 和 `workspace_id` 的首尾空白；会话标识和身份字段有长度上限，空白或超长值会被拒绝。

Gateway 的旧 Agent 代理入口 `/v1/agent/query` 和 `/v1/chat/completions` 已退役并返回 `410`。当前对话和自治执行必须通过 CLI/API-A 与 Supervisor 的现行链路；`GatewayAgentAdapter` 仅保留为兼容代码，不应作为新功能的接入点。

## 数据和备份

长期记忆位于 `VOIDCUBE_HOME/runtime/memory/memory.db`，备份和导出由 MemAI owner 执行。不要手工复制正在写入的 SQLite 文件，也不要删除 outbox 作为“清理”手段。停止服务后再进行文件级备份，并保留 `backups/` 的校验结果。

## 日常模式和 Auto

日常模式使用 `daily_companion`。`/auto` 开启 `auto_evolution`，后台任务须经过 API-B 复核和治理后才会派给员工代理；`/auto-q` 收口并回到日常模式。出现员工任务积压时先查看 Supervisor 状态和任务日志，不要直接修改数据库状态。

## Goal 命令与差距评估

`/goal <目标>` 会先进行复杂度判断：简单的一次性请求创建轻量会话目标；包含多步执行、验证、变更风险或多个交付物的目标自动绑定目标管理器。需要强制使用目标管理器时使用 `/goal glq <目标>` 或 `/goal --glq <目标>`，参数放在目标前。参数缺少目标时只显示用法；目标正文中的 `glq` 不会启用管理器。

绑定随该目标持久化，已有绑定继续同步原项目；后续普通 `/goal` 创建仍使用会话后端。管理器健康检查或创建失败时保留会话目标并提示回退；目标状态查询会尝试恢复已有绑定，避免临时服务故障造成永久失联。

状态命令为 `/goal status`、`/goal edit <新目标>`、`/goal pause [说明]`、`/goal blocked <原因>`、`/goal resume [说明]`、`/goal complete [说明]` 和 `/goal clear`。`edit` 会保留目标生命周期状态，更新权威目标内容，丢弃旧续跑提示并重新排队；已完成目标不能编辑。活动目标不能直接覆盖或清除，需要先完成、暂停或标记受阻。agent 可通过 `session_goal` 工具创建、读取和更新会话目标；工具只在没有未完成目标时创建。agent 报告的同一工作阻碍必须在三个连续轮次重复，目标才自动标记为受阻；turn 执行错误、执行不可用或空响应会立即停止并标记活动目标为受阻。

2026-09-23 源码审查：对比基线为上游 Goal 工具实现及其 continuation 模板。目标预算及用量被排除，不作为 VoidCube 的待补差距。

| 能力 | VoidCube 当前实现 | 上游会话可见行为 |
| --- | --- | --- |
| 存储与启动 | 会话目标持久化；创建、恢复时入队一次 | 线程目标，提供创建、查询、更新工具 |
| 自动续跑 | 只有成功完成且 `session_goal` 工具可用的 turn 才会在轮末以原子“空队列检查+入队”方式续入 pending-input 队列；失败、取消、执行不可用或空响应会停止续跑；暂停、完成、阻塞或清除时会丢弃旧续跑提示；状态变更与续跑入队使用生命周期锁，队列已有输入时优先处理用户内容 | 保留原目标完整范围；检查当前权威状态与证据；区分实际进展和仅有计划/状态复述；确认运行中的作业再等待，超时不等于作业停止 |
| 状态与阻塞 | active/completed/blocked/paused；agent 通过会话工具报告工作阻碍，每轮每原因至多计一次，三轮同因才阻塞；turn 执行错误、执行不可用或空响应立即阻塞 | 支持暂停；同一障碍连续至少三轮才标记 blocked；turn 错误会停止活动 goal |
| 预算 | 未实现，按需求不纳入本次补齐范围 | 工具支持 token 预算及用量查询 |
| 完成校验 | 会话模式直接状态迁移；绑定管理器时先校验根节点；目标提示要求从完整目标导出条件并逐项检查权威证据 | 续跑规则要求逐项核对证据后调用完成工具；不据此推断服务端自动验收 |
| 状态事件 | `GoalEvent` 统一通知 created/updated/completed/blocked/paused/cleared，携带会话 ID、目标快照、原因和可选 turn ID；命令、模型 Goal 工具和轮次 blocker 审计都接入同一事件总线；CLI 状态栏实时显示目标状态和摘要；无订阅者时不改变现有行为 | app-server 通过 goal updated/cleared notification 让前端同步线程目标状态 |
| 目标修订 | `/goal edit <新目标>` 原子更新未完成目标的 objective，保留 active/paused/blocked 状态，清理旧续跑并重新排队；活动 Agent 在下一次模型迭代收到新的目标 steering；持久化 revision 拒绝旧读者覆盖新状态；完成目标不可编辑 | 外部 thread goal set 支持 objective 更新，并在活动轮次注入新的 steering |
| 目标工具 | 提供 `get_goal/create_goal/update_goal`，并保留 `session_goal` 聚合兼容入口；只有显式用户请求创建，且不覆盖未完成目标 | 提供 get/create/update；创建仅由用户或系统明确要求，不覆盖未完成目标 |
| 目标分解 | 管理器有节点、依赖边和证据流程，提示词要求多步工作至少三个子目标 | 可见 goal 工具不要求目标树或固定节点数 |

代码依据：`commands/handlers/goal.py` 控制创建与状态命令，`application/session_goal.py` 提供目标用例，`application/ports/runtime.py` 定义目标端口，runtime composition 注入可选 Goal Manager；`application.py` 的 `_effective_system_prompt` 注入目标，`pending_input_runtime.py` 在轮末续入目标，并在已有排队输入时优先处理该输入。`interfaces/cli/session_goal_runtime.py` 仅保留兼容导出，不是主逻辑来源。目标数据结构位于 `infrastructure/persistence/session_db.py`。

当前聚焦测试已覆盖 CLI 入队优先级、turn 成功/失败/取消终态、会话工具调用、持久化状态变化和目标复杂度自动升级。目标管理器只在复杂目标或用户显式要求时启用。
