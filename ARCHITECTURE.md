# VoidCube Architecture

本文描述当前实现的稳定边界。代码、测试和本文不一致时，应先修复实现或同步本文；阶段性方案、迁移日志和未实现设想不属于当前架构。

## 运行模型

VoidCube 是单机、单所有者系统，不提供人类用户注册服务。

```text
CLI / Desktop
├─ API-A Agent runtime ── MemoryClient ──> MemAI ──> memory.db
├─ Supervisor client ───────────────────> Supervisor
└─ Gateway control client ──────────────> Gateway

Supervisor ──治理、调度、UI 投影、员工任务编排
API-B ───────后台规划和复核
员工代理 ────执行获准任务并回传结果
```

星子不是第二个独立 Agent 进程，而是 Supervisor、MemAI 和 API-B 的组合能力。API-A 只拥有用户对话；Supervisor 决定是否值得做、是否转交和如何治理，员工代理才执行获准的副作用。

## 包和依赖方向

生产代码只从规范包导入：

```text
interfaces -> application -> domain contracts
infrastructure -> domain/application ports
systems -> application/domain ports
runtime -> composition of the above
extensions -> declared plugin/skill contracts
```

运行时包位于 `src/voidcube/`。`Mem/src/memai/` 是独立的记忆领域；`plugins/memory/mem/` 只做注册、配置转换和协议适配。旧的 `agent`、`tools`、`systems`、`VoidCube_*` 顶层包不是发行入口。

## 数据所有权

- MemAI 独占 `VOIDCUBE_HOME/runtime/memory/memory.db`，负责记忆 schema、召回、压缩、备份和迁移。
- Agent、Supervisor 和 Companion 通过类型化 MemoryClient/Provider 调用 MemAI，不能直接打开 `memory.db` 或发送 SQL。
- `state.db`、`actions.db`、`scheduled_tasks.db`、`scheduled_writebacks.db`、`registry.db` 和技能 registry 各有单独 owner；outbox 是可靠传输队列，不是第二份长期记忆。
- 所有记忆请求带 `owner_id`、`workspace_id`、`memory_domain` 和服务端 actor 能力。跨域传播必须可审计。

## Gateway 边界

Gateway 负责服务生命周期、健康、presence/scene/activity 聚合和外部管理入口。高频记忆和会话数据走对应 owner 的本地客户端，不经过通用 Gateway 路由。Gateway 不持有业务 SQLite 连接，也不执行 Memory CRUD。

Gateway 不可用时，已经启动的数据服务仍应能够在数据面通信；控制面标记为 degraded。Memory 不可用时，当前对话可以继续，但召回必须明确失败，已完成轮次留在 durable outbox。

## 模式与生命周期

`daily_companion` 和 `auto_evolution` 互斥。`/auto` 只开启 API-B 规划门，`/auto-q` 收口后台任务；切换不启动 API-A 自治执行线程。服务启动顺序通常为 Gateway、Memory、Supervisor、插件服务，每个服务都有独立 ready/health 状态。

## 变更验收

架构变更至少检查：依赖方向、SQLite owner 唯一性、Gateway 脱离后的数据面、重启/outbox 恢复、scope/actor 权限、完整测试和 wheel 内容。提交前运行 `scripts/python_architecture.py`、文档契约、相关集成/打包测试和 `scripts/run_ci_tests.py`。
