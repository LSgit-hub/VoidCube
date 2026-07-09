# API 配置双槽与模型调用点

本文是当前 API 配置的权威口径，用来避免 API-A / API-B 再混线。

## 1. 两个槽位

### API-A：用户交互 / 主 Agent

- 保存位置：`runtime.active_provider` + `providers.<provider>`
- 配置入口：`/api` 的快速配置或自定义 Provider
- 代码入口：
  - `VoidCube_cli.api_config.persist_api_a_config()`
  - `VoidCube_cli.config.get_active_model_config()`
  - `VoidCube_cli.runtime_provider`
- 主要调用点：
  - 主 CLI 用户消息与工具循环
  - Gateway 的 `user_chat` 泳道
  - API-B 已转交后的 API-A 自主执行面 / `supervisor_task` 泳道
- 用途：主 CLI 用户交互、Agent 工具调用、API-A 自主执行面

API-A 不写 `memory.llm.*`。

### API-B：Mem / Supervisor 自主链路

- 保存位置：`memory.llm.*`
- 配置入口：`/api -> 3 记忆系统模型配置`
- 代码入口：
  - `VoidCube_cli.api_config.persist_api_b_config()`
  - `Mem/src/memai/model_config.py`
  - `memai.model_config.resolve_mem_llm_client()`
- 主要调用点：
  - `systems/memory/memory_service.py`
  - `systems/memory/tier1_to_tier2_bridge.py`
  - `systems/supervisor/endogenous_drive.py`
  - `systems/supervisor/planning_runtime.py`
  - `systems/governor.py`
  - `systems/execution/adapters.py`
- 用途：Mem 压缩 / 摘要 / 治理证据、Supervisor 内生驱动与 API-B 判断

API-B 不读 `providers.agnes-ai`，不从 API-A active provider 推断模型或 key。

## 2. API-B 凭证解析顺序

API-B provider 由 `memory.llm.provider` 决定，key 环境变量由 `memory.llm.api_key_env` 决定。

当前可读来源：

1. `effective_env`：VoidCube 对 `memory.llm.api_key_env` 的最终解析值
2. `process_env`：当前进程环境变量
3. `voidcube_env`：`~/.VoidCube/.env`
4. `auth_store`：同名 provider 的 auth store
5. `credential_pool`：同名 provider 的 credential pool

例：`memory.llm.provider=deepseek` 时，API-B 读取 `DEEPSEEK_API_KEY`、DeepSeek auth store、DeepSeek credential pool。不会用 `OPENAI_API_KEY` 或 `agnes-ai` key 兜底。

`/api -> 4 当前配置` 和 `VoidCube doctor` 都会显示这些来源的无密钥诊断状态，只显示 `usable / present_unusable / missing / error`，不输出 secret 内容。

## 3. 已废弃字段

以下字段不再作为模型配置来源：

- 根级 `model`
- 根级 `provider`
- 根级 `base_url`
- `custom_providers`
- `memory.model`
- 把 plugin 级 `memory.provider` 当 LLM provider

加载配置时这些旧字段只会被丢弃或忽略，不做迁移兼容。

## 4. 校验入口

- `/api` 展示 API-A 与 API-B 两套状态
- `/api -> 4 当前配置` 展示 API-B 凭据来源诊断
- `VoidCube doctor` 同时校验 API-A 与 API-B
- API-B base URL 指向本地 Gateway `:6000` 会被判为错误，因为这会让 Mem / Supervisor 回环到 API-A chat 面
