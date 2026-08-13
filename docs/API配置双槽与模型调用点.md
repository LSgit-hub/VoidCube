# API 配置与模型调用点

本文记录当前源码中的模型配置和两条模型调用职责，不描述已退役接口。

## 单一配置源

- `config.yaml` 的 `runtime.active_provider` 选择当前 Provider。
- `providers.<name>` 保存 `selected_model`、`base_url`、凭证引用和 Provider 类型。
- `VoidCube_app.config.get_active_model_config()` 是运行时读取入口；根级 `model`、`provider`、`base_url` 只在迁移边界被删除，不构成第二套配置。
- 主请求和辅助请求都使用 OpenAI-compatible 消息契约。退役 Provider、模型名和端点在传输前由项目策略拒绝。

## API-A：执行面

API-A 由 CLI 主 Agent 承载，负责用户会话、工具调用和 Supervisor 已批准任务的 agent-pull 执行。自主任务写回必须携带 execution lease 的 `generation` 与 `attempt_id`。工具执行通过 `ToolExecutionCoordinator`，有副作用的工具在派发前写入 ActionJournal。

## API-B：认知与治理面

API-B 位于 Supervisor/Memory 侧，用于内生驱动判断、治理建议、记忆维护和任务投影，不直接冒充长期执行器。Supervisor 只批准、认领和观察任务；实际 API-A 执行通过 Gateway 的标准路由交接。

## 辅助调用

压缩、摘要、委派和记忆任务从 `auxiliary.*` 或对应服务配置解析模型。显式 Provider 选择是硬约束；`auto` 只在配置允许的活跃 Provider 之间选择。调用方不得从聊天文本、环境变量别名或旧 Provider 名猜测执行槽位。

## 验证

涉及模型、鉴权或请求协议的修改至少运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_integration_policy.py tests/test_packaging_contract.py -q
.venv\Scripts\python.exe scripts/build_wheel.py
```
