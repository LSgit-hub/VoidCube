# CLI 展示与 Gateway 双泳道

VoidCube 在同一个 CLI 中展示用户对话和自主执行，但两者具有独立的调度、身份和状态写回契约。

## 用户会话泳道

- 用户输入进入前台 turn scheduler，由当前 `session_id` 关联 SQLite 会话记录。
- 工具进度通过结构化 ToolEvent 投影到 CLI block store；成功、失败、取消、超时和未知状态分别呈现。
- 会话消息以 SQLite 为权威源，JSON 只从已提交行刷新镜像。

## 自主执行泳道

- Supervisor 将已批准的 agent-pull 任务通过 Gateway 暴露给 CLI。
- CLI 认领后保存 `(task_id, generation, attempt_id)`；续租、完成、失败和报告提交复用该 fencing 身份。
- 自主执行状态不混入用户输入队列；任务结束后才以受控摘要回到观察界面。
- Supervisor recovery 未健康时禁止新 claim，已有租约仍可续租或终结。

## Gateway 边界

Gateway 是跨进程调用 Executor 和 Supervisor 自主任务接口的标准入口。它只转发持久化 fencing token，不根据 metadata 推断 owner。HTTP 409 `stale_execution_lease` 表示写入者已过期，调用方不得通过重试旧请求覆盖新 owner。

## 后台进程展示

后台命令使用持久化 ProcessRegistry。CLI 从持久化完成通知队列生成一次性消息；主动 poll/wait 后消费标记同步落盘。服务重启后，无法证明控制权的命令显示为 `unknown`，不会被静默标成失败或成功。
