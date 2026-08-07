# `/goal` 命令实施说明

## 目标

`/goal` 为当前 CLI session 保存一个明确的执行目标，并让后续 Agent 请求看到该目标。它是 session 状态管理能力，不是新的 Agent、Provider 或工具协议。

## 命令契约

| 命令 | 行为 |
| --- | --- |
| `/goal` 或 `/goal status` | 显示目标、状态和原因 |
| `/goal <目标>` | 创建活动目标并将目标加入输入队列 |
| `/goal complete [说明]` | 将活动目标标记为已完成 |
| `/goal blocked <原因>` | 将活动目标标记为受阻 |
| `/goal clear` | 清除已完成或已受阻目标 |

一个 session 同时只能有一个活动目标。活动目标不能直接覆盖或清除，必须先完成或标记为受阻。

## 状态与持久化

目标记录存储在 `SessionDB.session_goals`，按 `session_id` 隔离，并随 session 恢复。状态只有 `active`、`completed`、`blocked` 三种；目标正文限制为 4000 个字符。

活动目标会附加到 Agent 的 ephemeral system prompt；目标完成或受阻后重建 Agent，移除该约束。`/status` 会显示当前目标。

## 与 `/plan` 的边界

`/plan` 仍然是规划技能，负责生成工作计划文件。`/goal` 负责声明和跟踪 session 的最终目标；两者可以组合使用，但不互相替代。

## 明确不做的行为

当前版本不会在模型返回后自动无限续跑，也不自动猜测“目标已完成”。产品级自动继续依赖外部调度、预算和终止状态；VoidCube 在具备同等预算治理前，完成和受阻都必须由用户或未来的受控执行器明确写入。
