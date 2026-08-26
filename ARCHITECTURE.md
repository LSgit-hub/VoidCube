# VoidCube Architecture

本文是 VoidCube 的规范架构契约。README、代码注释和其他 `docs/` 文档用于补充实现细节；如果它们与本文冲突，以本文为准。架构边界发生变化时，必须同时更新本文、实现和测试。

## 1. 产品运行模型

VoidCube 是单机、单所有者的本地智能体系统，不建立人类用户注册或登录服务。

- API-A Agent 负责用户 CLI 对话和一次性回合执行。
- Supervisor 负责治理判断、任务状态、调度和 UI 投影。
- Memory/MemAI 负责长期记忆、记忆检索、分域权限和治理事件。
- API-B 负责后台规划/复核；获准的工作交给隔离员工代理执行。
- 星子不是第二个独立 Agent 进程，而是 Supervisor、Memory/MemAI 和 API-B 的组合能力。

两种产品模式保持互斥：`daily_companion` 提供日常伴侣辅助；`auto_evolution` 运行受治理的后台计划和员工任务。`/auto-q` 收口 Auto 任务并回到日常模式。

## 2. 进程拓扑

目标拓扑如下。箭头表示本地类型化客户端调用，不表示必须使用 HTTP。

```text
Desktop / CLI
├─ API-A Agent runtime
│  ├─ MemoryClient ───────> Memory Service ───────> memory.db
│  ├─ Session client ─────> Session owner (state.db)
│  └─ local tools
├─ Supervisor UI/client ──> Supervisor
└─ Gateway control client ─> Gateway

Supervisor
├─ MemoryClient ───────────> Memory Service
├─ employee execution orchestration
└─ governance and UI projection

Gateway
├─ lifecycle / health
├─ presence / scene / activity aggregation
├─ external HTTP ingress and management API
└─ no local Memory CRUD data bus
```

Gateway、Memory Service 和 Supervisor 可以是独立进程，也可以在开发/测试启动器中由同一个宿主编排启动。进程编排不改变数据库 owner 规则。

## 3. 模块和依赖方向

依赖方向为：

```text
interfaces -> application -> domain contracts
infrastructure -> domain/application ports
systems -> application/domain ports
runtime -> systems/application/infrastructure composition
extensions -> declared registry contracts
```

生产代码从 `voidcube.*` 规范包导入。客户端依赖服务的类型化协议，不依赖服务内部的 SQLite repository、SQL 语句或文件路径。数据服务依赖领域命令和仓储实现；RPC handler 不执行客户端传来的 SQL。

## 4. 控制面与数据面

VoidCube 的本地通信分为两类：

### 数据面

数据面承载高频、低延迟、需要明确 owner 的业务数据访问：记忆 recall/remember、会话读写、任务状态、动作日志等。数据面调用直接到相应 owner service，通过 `MemoryClient`、`SessionClient` 等类型化本地客户端完成，不经过 Gateway。

### 控制面和外部面

控制面负责服务生命周期、启动/停止、健康检查、presence、scene/activity 聚合和管理操作。外部 HTTP、桌面 UI 的管理入口以及需要统一边界鉴权的公开接口可以进入 Gateway。Gateway 可以编排控制面，但不持有业务数据库连接，不执行 Memory CRUD，不把 `/api/{path:path}` 作为所有内部模块的总线。

Gateway 停止时，已经启动的 Memory Service、Supervisor 和本地数据客户端仍应能在数据面直接通信；只有控制面聚合和外部入口暂时不可用。

## 5. Gateway 规则

Gateway 保留：

- 服务生命周期和健康状态协调；
- presence、scene、activity 等跨服务状态聚合；
- 外部 HTTP/API 入口和管理面；
- 迁移期间带调用统计、退役日期的兼容入口。

Gateway 禁止：

- 作为 Agent、Supervisor、UI、子代理访问本地 Memory 的必经跳转；
- 作为通用内部 CRUD/RPC 总线；
- 持有 `memory.db` 或其他业务 SQLite 连接；
- 代替 Memory Service 做业务缓存、事务或锁重试；
- 允许调用方通过任意路径或任意 `memory_actor` 绕过 owner 权限。

现有 `/api/mem/*` 代理属于迁移对象。兼容代理只能短期保留，必须记录调用者、成功/失败计数和退役版本；不能把兼容路由重新当成规范内部入口。

## 6. Memory Service 与长期记忆

长期记忆只有一个规范数据库文件：

```text
VOIDCUBE_HOME/runtime/memory/memory.db
```

Memory Service 是该文件的唯一 owner。这里的“唯一”是按数据库文件计算：每个 SQLite 文件都应有一个明确 owner，但项目可以有多个业务数据库文件；`memory.db` 是长期记忆的唯一真相，不得创建第二个长期记忆库。

```text
Agent / Supervisor / Companion
          |
          | MemoryClient
          v
    Memory Service
          |
          | controlled SQLite executor
          v
       memory.db
```

客户端不得导入 `sqlite3`、构造 Memory repository、读取 `memory.db` 路径或发送 SQL。Memory Service 对外提供领域接口，例如 `recall`、`get_context`、`remember`、`promote`、`outbox_health`；接口返回稳定的版本化结果和错误码。

Memory 的三个域仍是：

- `agent_interaction`：API-A 会话、工具结果和任务事实；
- `companion`：日常伴侣语义、目标理解和偏好；
- `evolution`：Auto 计划、实验、治理和身体 lineage。

所有请求绑定 `owner_id`、`workspace_id`、`memory_domain` 和服务端 actor 能力。客户端不能任意声明 actor；MemoryClient 传递受签发的调用角色，Memory Service 负责校验该角色能访问的域。owner/workspace 上下文由本地会话和服务端策略管理，而不是由通用 Gateway 临时注入。

## 7. 本地通信和身份边界

本地客户端的优先传输顺序为：

1. Windows Named Pipe 或 Unix Domain Socket，利用端点权限隔离本机服务；
2. 迁移期 loopback HTTP，仅绑定本机回环地址并使用本地服务 token；
3. 不允许以可被任意进程伪造的环境变量路径或裸 SQLite 文件作为回退。

MemoryClient 是唯一推荐的调用入口。传输协议应包含协议版本、请求 ID、deadline、幂等键和调用能力。`busy`、瞬态不可用和明确幂等的操作才允许客户端按契约重试，客户端不得解析异常字符串自行猜测。

## 8. SQLite 并发模型

Memory Service 内部负责全部 SQLite 细节：

- 一个受控写入执行器/队列串行化写事务；
- 读操作使用受控连接或读执行器，启用 WAL 以允许读写并行；
- `BEGIN IMMEDIATE`、busy timeout、有限指数退避和最终错误码集中管理；
- 事务短小，事务内不得执行网络请求、模型调用或 embedding；
- 高频记忆写入按 turn/批次提交，支持 durable outbox 和幂等键；
- 成功提交后由服务统一推进 revision、缓存失效和审计事件；
- checkpoint、备份、迁移和 integrity check 由 owner 服务执行。

`accepted` 只表示进入内存队列，`durable` 表示已写入 durable outbox，`committed` 才表示已提交到 `memory.db`。用户明确要求保存的事实、任务状态和治理记录必须等待 `committed`；普通对话记忆可以按产品策略使用 `durable`。

## 9. 其他 SQLite 文件

其他数据库不与 `memory.db` 合并为一个默认“大一统 Data Service”。每个文件单独登记 owner，未来只有在证实共享边界、吞吐和故障模型确有收益时才评估合并：

| 数据库/模式 | 业务域 | 当前或目标 owner |
|---|---|---|
| `state.db` | 会话、transcript、搜索投影 | Session owner：`SQLiteOwnerLease("session-owner")` 文件级独占 + `BEGIN IMMEDIATE` jitter 写门禁 + 启动恢复（阶段 5 已落实） |
| `actions.db` | 动作审计日志 | Action Journal owner：`SQLiteOwnerLease("action-journal-owner")`，全部写路径走 `_execute_write`（阶段 5 已落实） |
| `scheduled_tasks.db` | Supervisor 定时任务 | Scheduled Task owner：`SQLiteOwnerLease("scheduled-task-owner")`，构造时启动恢复 expired claims（阶段 5 已落实） |
| `scheduled_writebacks.db` | 调度回写传输队列 | 调度/写回 owner：`SQLiteOwnerLease("scheduled-writeback-owner")`（阶段 5 已落实） |
| `registry.db` | 进程执行注册 | Process Registry owner：`SQLiteOwnerLease("process-registry-owner")`；`ensure_process_registry()` 惰性单例，import 不获取 lease（阶段 5 已落实） |
| `.skills_registry.sqlite3` | 可重建技能索引 | Skill Registry owner：连接级 `SQLiteOwnerLease("skill-registry-owner")`（进程内重入、跨进程互斥）；文件系统仍是内容权威（阶段 5 已落实） |
| `runtime/memory/*-write-outbox.sqlite3` | durable 传输队列（非迁移期临时态） | 行级 lease 即并发门禁（显式声明，无文件级独占锁）：api_a→agent、companion→supervisor、gateway→gateway 守护进程；多进程 drain `duplicate_claims == 0` 为入库契约；`outbox_state` 持久化 `outbox_owner` 元数据（阶段 5 已落实） |

这些文件的客户端同样只能使用领域 API。outbox 是传输可靠性机制，不是长期记忆的第二份真相；在确认断线恢复和关闭顺序前，不得擅自删除现有客户端 outbox。

## 10. 启动、故障和恢复

启动器可以先启动 Gateway 以提供控制面，再启动 Memory Service 和 Supervisor，但“注册到 Gateway 成功”不能成为 Memory 数据面健康的唯一条件。每个 owner service 必须有自己的 ready/health 状态和关闭流程。

- Memory Service 未就绪时，MemoryClient 返回明确的 `service_unavailable`，调用方按业务优先级排队或降级；不得打开数据库文件回退。
- Gateway 不可用时，数据面直接调用仍可继续；控制面状态标记为 degraded。
- Memory Service 重启后，从 durable outbox 恢复，幂等键防止重复记忆；未持久化的 `accepted` 请求可以丢失，必须由契约向调用方明确。
- schema migration、备份和 integrity check 由 owner 在单写者窗口执行。

## 11. 迁移规则

整改按以下顺序推进：

1. 补齐并维护本文和 SQLite/Gateway 清单；
2. 定义 MemoryClient、本地身份、scope/actor 能力和错误/幂等协议；
3. 让 Agent、Supervisor、Companion 直接调用 Memory Service，移除本地 Memory 热路径的 Gateway 跳转；
4. 在 Memory Service 内建立单写者队列、批量事务、busy/retry、缓存和 outbox；
5. 再按业务域独立整改 `state.db`、`actions.db`、调度和 registry；
6. 统计并删除已退役的 Gateway `/api/mem/*` 兼容路由；
7. 删除失效的直接 SQLite 入口、重复锁逻辑和永久兼容分支。

迁移期间允许 loopback HTTP 作为临时传输，但不允许改变 owner 规则，也不允许新代码继续依赖 Gateway 代理。每个阶段都要有并发、重启、权限和回滚测试。

## 12. 架构门禁

提交前至少验证：

- 活跃 Agent/UI/子代理包不直接导入 `sqlite3` 或 Memory repository；
- 每个 SQLite 文件有且只有一个运行时 owner；
- Gateway 停止时本地 Memory 数据面仍可直接通信；
- 并发读写、`SQLITE_BUSY`、批量事务、幂等重试和 outbox 恢复测试通过；
- 请求协议、鉴权、技能或打包变更按 `AGENTS.md` 运行退役集成扫描和相关测试；
- Skill Registry 变更运行 `pytest tests/test_skill_registry.py` 并核对 `added/reparsed/reused/removed` 统计；
- `git diff --check`、`scripts/python_architecture.py`、文档契约和 wheel 契约检查通过。

