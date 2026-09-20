# Mem 临时记忆与上下文接口契约

## 1. 目的

本契约定义当前上下文、临时记忆、长期记忆和身份记忆之间的边界。

Mem 是持久记忆服务；它不拥有模型当前上下文，也不因为保存了对话轮次就自动获得身份叙事权。

四层必须保持分离：

| 层 | 名称 | 所有者 | 是否持久化 | 主要用途 |
| --- | --- | --- | --- | --- |
| L0 | 当前上下文 | Agent Runtime | 否 | 当前模型请求的 system、用户消息、工具结果和本轮工作状态 |
| L1 | 临时/近期记忆 | Mem `turns` | 是，有界 | 保存近期轮次，支持短期恢复、近期召回和后续压缩 |
| L2 | 长期结构记忆 | Mem `compressed_memories`、`profile_memories`、时间摘要 | 是 | 保存事件、脉络、偏好、约束和可审计历史 |
| L3 | 身份记忆 | Mem 身份层和身份治理表 | 是，受限 | 保存 founding、自述身份经历和身份治理修订 |

L0 不写入 Mem。L1 可以被压缩为 L2，但不得未经规则直接晋升为 L3。

## 2. 所有权和边界

### 2.1 Agent Runtime 拥有 L0

Agent Runtime 必须负责：

- 当前会话消息列表；
- 当前轮次的工具调用结果；
- 当前请求的临时计划、草稿和未确认状态；
- 模型上下文窗口的裁剪和最终组装。

这些内容不能因为被拼入模型 prompt 就被视为持久记忆。

### 2.2 Mem 拥有 L1-L3

Mem 负责：

- `sessions`、`turns` 和 `turns_archive`；
- Tier 1 到 Tier 2 的压缩、证据回链和生命周期；
- `profile_memories` 的画像事实管理；
- 身份档案、身份修订和召回审计；
- `/recall` 的候选生成、排序、去重和上下文预算。

VoidCube 的 Agent 侧适配器只能调用 Mem 协议，不能直接写 Mem 的 SQLite 表。

### 2.3 身份边界

以下内容不得自动写入 `self_experience`：

- 用户要求“请记住”；
- 普通用户或 Agent 对话结算；
- Tier 1/Tier 2 压缩结果；
- 自改进、质量评估或治理任务完成记录；
- 用户对星子身份的描述。

星子身份经历必须通过专用第一人称协议，由 `stellar_companion` 对 `agent` turn 进行验证后写入。

## 3. L0 当前上下文契约

### 3.1 输入

每次模型调用可以包含：

```text
system_prompt
conversation_messages
tool_results
memory_context
current_user_message
```

`memory_context` 是外部参考资料，不是用户新消息，也不是系统指令。

### 3.2 组装顺序

推荐顺序：

```text
静态 system prompt
  -> 受信任的运行时规则
  -> <memory-context> 召回背景
  -> 当前会话历史
  -> 当前用户消息
```

当前用户消息和当前系统规则优先于召回内容。召回内容不能覆盖本轮用户输入，也不能引入新的工具权限。

### 3.3 生命周期

L0 内容在以下情况失效：

- 当前模型请求结束；
- 当前 Agent 进程重启；
- 会话被重置；
- 上下文压缩后被 Runtime 丢弃。

需要跨上述边界保留的内容，必须通过 L1/L2 的显式写入路径提交。

## 4. L1 临时/近期记忆契约

### 4.1 写入接口

Agent 侧使用 `MemoryProvider.sync_turn()`，由 `MemoryManager.sync_turn()` 调用。

完成的用户/Agent 对话先进入本地持久 outbox，再由后台提交：

```text
MemoryManager.sync_turn()
  -> runtime/memory/write-outbox.sqlite3
  -> POST /turn-pairs
  -> Mem.sessions + Mem.turns
```

Supervisor 的 daily companion 使用同一个 `MemoryWriteOutbox` 实现，但拥有
独立的持久队列文件和写入域：

```text
Supervisor companion dialogue
  -> runtime/memory/companion-write-outbox.sqlite3
  -> POST /turn-pairs (memory_actor=stellar_companion, memory_domain=companion)
  -> Mem.sessions + Mem.turns
```

两条路径共享写入幂等、租约、指数退避、死信和关闭保留语义；不同队列只
用于隔离 API-A 的 `agent_interaction` 与陪伴域，不能互相越权或覆盖。

Gateway 接收自治任务成果时也使用同一实现，写入
`runtime/memory/gateway-write-outbox.sqlite3`，并以稳定 `turn_dedup_key`
投递到 `agent_interaction` 域；Gateway 重启后会继续处理未完成队列。

三类队列统一读取 `memory.outbox`：

```yaml
memory:
  outbox:
    paths:
      api_a: runtime/memory/write-outbox.sqlite3
      companion: runtime/memory/companion-write-outbox.sqlite3
      gateway: runtime/memory/gateway-write-outbox.sqlite3
    max_attempts: 12
    lease_seconds: 30.0
    retry_base_seconds: 2.0
    retry_max_seconds: 60.0
    health_report_interval_seconds: 10.0
    shutdown_drain_timeout_seconds: 5.0
```

路径相对于当前 `VOIDCUBE_HOME` 解析；重试、租约和关闭排空参数不能在各
消费者中另设第二套默认值。

写入必须携带：

- `session_id`
- `owner_id`
- `workspace_id`
- `memory_domain`
- 用户文本和 Agent 文本
- 时间戳
- 可选 tags/metadata

写入必须支持幂等重试。Mem 服务不可用时，outbox 是暂存写入，不得把失败误报为已持久化；
对话接口可以报告“已进入 durable outbox”，但只有 Mem 接收成功才是已持久化。

### 4.2 L1 的状态

`turns.compression_status` 的主状态为：

- `pending`：等待压缩，可参与 Tier 1 召回；
- `retry_wait`：压缩重试等待，可参与 Tier 1 召回；
- `compressed`：已被接受的 Tier 2 压缩覆盖；
- `quality_quarantined`：压缩质量不合格，保留在 L1，不得继续压缩。

默认 L1 保留期为 7 天，最大 10000 轮；实际值由 `MemoryServiceConfig` 配置覆盖。

L1 是持久化的近期记忆，不是进程内缓存。压缩或删除前必须保留可追溯的归档/摘要证据。

### 4.3 L1 不自动升级为身份

L1 中出现“我”“星子”“身份”等字样，不构成身份经历。身份升级必须满足独立的 L3 验证协议。

## 5. L1/L2 召回接口

现有 HTTP 接口：

```text
POST /recall
```

请求核心字段：

```json
{
  "query": "本轮问题的简短概念查询",
  "current_session_id": "当前会话",
  "limit": 5,
  "max_context_chars": 3500,
  "include_tier1": true,
  "include_tier2": true,
  "request_source": "auto_prefetch",
  "owner_id": "local-user",
  "workspace_id": "default",
  "source_domains": ["agent_interaction"]
}
```

`request_source` 只能是 `api`、`auto_prefetch` 或 `tool`。

召回规则：

- `current_session_id` 只用于同会话相关性加权，不能突破作用域；
- L1 用于近期对话和尚未压缩的证据；
- L2 用于结构化事件、脉络、画像和长期事实；
- 身份查询走专门的 identity intent，不应把普通用户画像当作星子身份；
- 结果必须去重并服从 `max_context_chars`；
- 结构化结果可以包含 ID、分数和证据，但普通 `context` 默认不包含内部元数据。

返回至少包含：

```json
{
  "results": [],
  "context": "Relevant recalled memory:\n...",
  "trace_id": "...",
  "recall_status": "hit|weak_match|miss",
  "candidate_count": 0,
  "count": 0
}
```

`context` 只用于 L0 注入，不得在没有明确写入意图时再次写回 Mem。

## 6. Provider 生命周期契约

VoidCube 的唯一记忆适配器遵循 [MemoryProvider](../src/voidcube/domain/contracts/memory.py) 生命周期：

```text
initialize(session_id)
  -> bind_session(session_id)
  -> prefetch(query)
  -> sync_turn(user, assistant)
  -> on_session_end(messages)
  -> shutdown()
```

语义要求：

- `prefetch()` 只读、可失败、不得阻塞主对话超过配置超时；
- `sync_turn()` 只提交已完成轮次，最好通过 outbox 异步完成；
- `on_session_end()` 可以请求会话封口或摘要，但不能伪造星子身份经历；
- Provider 失败时，Agent 仍然可以继续当前对话，但必须知道“没有召回证据”；
- 不得注册第二个长期记忆 Provider。

## 7. 升级和淘汰规则

```text
L0 当前上下文
  --显式完成轮次--> L1 turns
  --质量通过--> L2 event/scene/arc/epoch/profile
  --专用身份验证--> L3 self_experience
```

### 允许升级

- L0 -> L1：完成的用户/Agent 轮次；
- L1 -> L2：通过来源支持和压缩质量检查；
- L1/L2 -> profile：明确、稳定、属于用户或项目的事实；
- agent turn -> L3：符合第一人称身份协议的星子自述。

### 禁止升级

- L0 的草稿、计划、猜测直接进入 L2；
- 用户陈述直接进入 L3；
- 普通压缩摘要直接进入 L3；
- 召回结果再次作为新证据写回原层；
- `miss` 被解释为“从未保存过记忆”。

## 8. 失败与降级

| 失败 | 必须行为 |
| --- | --- |
| Mem 服务不可用 | 当前 L0 对话继续，返回无召回证据，已完成轮次留在 outbox |
| outbox 写入失败 | 返回写入失败，不得报告 durable success |
| 召回无结果 | 返回 `miss`，不得推断历史不存在 |
| 压缩质量失败 | 保持 L1 活跃并写入质量审计 |
| 作用域/Actor 不匹配 | 拒绝读写，不允许静默回退到其他域 |
| 身份证据不完整 | 拒绝写入 `self_experience`，保留为普通证据或待审计记录 |

## 9. 验收清单

实现或修改上下文/临时记忆路径时，必须确认：

- 当前消息历史仍由 Agent Runtime 持有；
- Mem 召回通过 `/recall`，没有第二条长期召回路径；
- `current_session_id`、scope 和 actor 始终向下传递；
- 召回上下文有字符预算并使用安全围栏；
- outbox 写入和 HTTP 投递均可重试且幂等；
- Tier 1 压缩失败不会删除原始证据；
- 用户画像、普通长期记忆和星子身份经历没有混层；
- 身份写入仍满足 `agent` turn + `stellar_companion` + 第一人称字段；
- 召回 trace、反馈、删除和压缩质量均可审计。
