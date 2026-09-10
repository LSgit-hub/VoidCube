---
name: goal-manager-plugin-testing
category: devops
description: 目标管理器（goal_manager）插件功能验证与 HTTP API 使用指南 — 6003 端口全链路测试（项目/节点/边/批量/回滚/证据/乐观锁），含端点清单、payload 格式与实测踩坑。当用户要测试目标管理器插件功能、直接用 HTTP 操作目标图谱、或验证 goal_* 工具链路时使用。
---

# 目标管理器插件测试与 API 使用

## 触发条件
- 用户要求测试/验证 goal_manager 插件功能（创建测试图谱、走全链路）
- 需要不经过 goal_* 工具、直接用 HTTP 操作目标图谱（当前会话工具未注入时）
- 排查目标图谱数据、验证环检测/乐观锁/批量回滚等行为

## 服务基础信息
- 服务地址：`http://127.0.0.1:6003`（uvicorn，独立插件进程）
- 鉴权：默认**无 token**；仅当服务配置了 `service_token` 时，`/api/*` 才要求 `X-Goal-Service-Token` 或 `Authorization: Bearer` 头（实测当前环境无鉴权）
- 根路径 `/` 返回 `{"service": "goal_manager", ...}`；`/health` 健康检查
- 数据落在 `~/.VoidCube/runtime/goals/`（GoalStore SQLite）
- Web UI 挂载：supervisor(6002) 的 `/ui/goal-manager/`

## API 端点清单（2026-08-27 实测）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/goals/projects | 项目列表 |
| POST | /api/goals/projects | 创建项目（同事务建根节点），返回 `project`+`root`+`batch_id` |
| GET | /api/goals/projects/{id} | 项目详情 |
| GET | /api/goals/projects/{id}/focus?node={node_id} | 焦点视图/next_actions 推荐 — 返回 `{focus: {...}, children: [...]}` |
| GET | /api/goals/projects/{id}/overview | 总览 |
| GET | /api/goals/projects/{id}/graph?start_node={node_id}&depth={n} | 目标子图 — **start_node 必填，缺失返回 422**；depth 默认 1，depth=2 含孙节点 |
| GET | /api/goals/nodes/{node_id} | 节点详情 |
| POST | /api/goals/nodes | 创建节点 |
| PATCH | /api/goals/nodes/{node_id} | 更新节点（乐观锁） |
| DELETE | /api/goals/nodes/{node_id}?reason=... | 软删除节点 |
| POST | /api/goals/edges | 建边（环检测） |
| DELETE | /api/goals/edges/{edge_id}?reason=... | 软删除边 |
| POST | /api/goals/batch | 批量原子事务，返回 batch_id |
| POST | /api/goals/rollback | 回滚批次（LIFO） |
| POST | /api/goals/redo | 重放最近回滚 |
| POST | /api/goals/evidence | 附加证据（**node_id 放在 body，不是路径**） |

## 关键 payload 格式

创建项目：
```json
{"name": "测试项目", "description": "...", "reason": "...", "created_by": "agent", "actor_type": "agent"}
```

创建节点：
```json
{"project_id": "...", "type": "task", "title": "...", "description": "...",
 "status": "planned", "progress": 0.0, "reason": "...", "created_by": "agent", "actor_type": "agent"}
```
节点类型实测可用：`project`（根）、`milestone`、`task`。

建边：
```json
{"source_id": "...", "target_id": "...", "edge_type": "decomposes_to|depends_on|blocks",
 "reason": "...", "created_by": "agent", "actor_type": "agent"}
```

更新节点（乐观锁）：
```json
{"expected_version": 1, "patch": {"progress": 0.4, "status": "in_progress"}, "reason": "..."}
```

批量操作 operations 元素：`{"op": "create_node", "type": "task", "title": "...", "reason": "..."}` 或 `{"op": "update_node", "node_id": "...", "expected_version": 1, "patch": {...}, "reason": "..."}`。

## 实测踩坑（重要）

1. **graph 必须传 `start_node`**：缺了返回 422 `Field required`。用根节点 ID 起步。
2. **乐观锁必须先读版本**：PATCH 用错的 expected_version 返回 409 `node version conflict`（响应里带 `latest` 完整节点）。批量 update 后节点版本会 +1，直接猜 version=1 必失败 —— 先 GET graph 取真实 version。
3. **409 不全是失败**：环检测、版本冲突返回 409 是**正确行为**，验证脚本里应作为"预期拒绝"标记 PASS，别当 FAIL。
4. **batch_id 别被后续响应覆盖**：批量操作返回的 batch_id 要立刻存变量，后续调用（如 graph 查询）会覆盖 `data` 变量导致 rollback 传空报 422。
5. **ToolRegistry 要用全局单例**：`from voidcube.extensions.tools.registry import registry`，`ToolRegistry()` 新实例是空表（list_tools 返回 0 是假象）。验证插件工具注册必须用单例。
6. **激活链路验证**：`activate_all_plugins()` 返回 `{"goal_manager": true, "memory": false}` —— memory 插件入口缺 activate() 是预期现象，不是故障；goal_manager 激活成功会把 14 个 goal_* 工具注册进全局工具表。
7. **当前会话工具注入**：Agent 工具快照在会话启动时注入，当前会话看不到 goal_* 工具是正常的；工具已在全局注册表 + 工具集 `goal_manager` 中，新会话会自动带上。当前会话可用 HTTP 直连 6003 代替。
8. **plugin.json 遗留字段**：`data_owner`/`data_root`/`health_path` 是早期设计，实际协议未使用（无害冗余），别照它们写新插件清单。
9. 创建项目会同时返回根节点（node_type=project）+ batch_id，根节点 ID 用于 graph/edge 起点。
10. **focus 响应字段是 `children` 不是 `nodes`**：`{focus: {...}, children: [...]}`。脚本找 `nodes` 会拿到空列表误判为插件缺陷；focus 只返回核心节点 + 直接子节点（两层边界，不含孙节点）——这是产品规则，不是 bug。深查用 graph + depth。
11. **DAG 多父节点验证过**：一个节点可被多个父节点指向（如"数据质量校验"同时挂在"数据准备"和"训练实验"下），节点只存一份，两条 decomposes_to 边指向同一 target——合法，不会报错。
12. **graph depth 参数**：默认 1 只含直接子节点；depth=2 返回全部孙节点（多层子目标测试用它验证）。
13. **工作目录会漂移**：外部切过 cwd（如 desktop/）后，write_file 相对路径和 python 相对路径都会落错地方。写测试脚本用绝对路径（如 /f/My_code/VScode_py/VoidCube/desktop/_task_work/），运行前先确认宿主 cwd 与 Python 路径；不要假设 `.venv/Scripts/python.exe` 一定存在，当前环境可直接用已配置的 `python -m pytest`。
14. **完成节点不是“附了 evidence 就自动满足验收条件”**：`completed` 要求 `progress=1.0`，且每个 acceptance criterion 对象都必须显式包含 `"met": true`。正确顺序是：创建 `in_progress` 节点并声明条件 → 附加 evidence → PATCH 时同时把已核验条件更新为 `met: true`、`status: completed`、`progress: 1.0`。否则返回 422 `completed nodes must satisfy all acceptance criteria`。
15. **Evidence 类型是受限枚举**：当前服务仅接受 `ci_build|file|git_commit|issue|manual|note|pr|test_result`，不能用自造的 `source`、`test` 等值。源码证据通常用 `file`，pytest 结果用 `test_result`。
16. **PATCH payload 不接受 `created_by`**：更新节点只传 `expected_version`、`patch`、`reason` 和 actor/session 上下文；加入 `created_by` 会触发 422（底层 `update_node()` 不接收该参数）。创建节点和附加 evidence 才使用 `created_by`。
17. **父节点不会因必需子节点完成而自动变成 completed**：子任务和里程碑完成后，重新读取父/根节点最新版本，显式补齐父节点验收条件并 PATCH 完成；最后用 project overview 验证整棵目标图均为 completed。

## 验证脚本骨架

```python
import json, urllib.request
BASE = "http://127.0.0.1:6003"
def call(method, path, payload=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
```

测试链路建议顺序：创建项目 → 建节点 → 父子边(decomposes_to) → 依赖边(depends_on) → 故意造环验证 409 → 批量操作 → 读 graph(带 start_node) → 正确版本 PATCH → 错误版本验证 409 → 附加证据 → rollback → redo。追加多层子目标：在一层节点下建第二层子节点（depth=2 查询验证）→ 建多父节点（两条 decomposes_to 指向同一节点验证 DAG）→ focus 验证两层边界。

## AI 可靠性审查矩阵
除基础 API 冒烟测试外，面向 Agent 使用时必须验证以下行为：

1. 完成一致性：对同一节点分别通过 `update_node`、`goal_batch_apply` 和 `redo` 设置 `status=completed`，比较 progress、confidence、completed_at 和 acceptance criteria/evidence 约束。普通更新和 batch 必须遵守同一规则；不能让未验证目标保持高置信度完成。
2. 证据闭环：验证节点是否能在没有 evidence 或未满足 acceptance criteria 时进入 completed；检查 evidence 是否真正参与状态机，而不是仅作为附加记录。
3. 重试安全：模拟 HTTP 超时后重复 project/node/batch/evidence 操作，确认是否会重复创建。非幂等写操作应支持 `idempotency_key`，或明确禁止自动重试。
4. 并发恢复：读取版本后由两个执行者更新同一节点，确认 409 返回 latest；检查 batch 内连续修改同一节点时的版本语义。
5. 失败恢复：验证 rollback/redo 在重启后、产生新写入后、跨 batch 和外部副作用场景下的行为。Goal 状态回滚不等于文件、Git、部署或外部 API 的补偿回滚。
6. 下一步可解释性：检查 `next_actions` 是否说明 readiness、阻塞节点、失败历史、所需证据和确认要求，而不只是返回候选节点。
7. 身份与确认：验证 actor/session 是否来自可信调用上下文；检查高风险批量取消、完成、依赖修改和 rollback 是否需要确认，而不只测试 root delete。
8. 交付链：除了 route-mock E2E，还要验证真实服务、静态 Web bundle 和 UI origin；缺少 `web/dist/index.html` 时应报告为交付链缺陷，不要误判为 Agent 核心逻辑失败。

## 审查中确认的实现陷阱
- 历史缺陷：`apply_batch` 的更新路径曾绕过普通更新的完成校验，以新建节点为例可得到 `completed + progress=0.5 + confidence=1.0 + completed_at=null`。当前实现已通过共享的 `_validate_completion(fields)` 统一覆盖创建、普通更新、batch 和 redo；回归测试应分别验证这些路径都拒绝伪完成，并验证失败 batch 不留下部分修改。
- 完成时间也属于状态一致性约束：进入 `completed` 时应自动生成 `completed_at`，从 `completed` 改回其他状态时必须清空，不能只检查 `status` 和 `progress`。
- `registry.get_effect()` 在插件工具未实际注册时返回全局默认值 `non_idempotent_write`。必须先执行插件激活/工具发现，再判断 Goal 工具 effect；不能用未激活状态的 registry 查询结果否定 `agent_tools.py` 中的显式注册。
- Goal 工具中读取工具应注册为 `read_only`；真正的 create/batch/evidence/rollback/redo 等操作通常不是幂等写。审查 effect 时要按实际副作用分类，并建议请求级幂等键。
- 软删除节点的 batch 更新不能仅凭 SQL 片段判断可复活；先做最小运行时复现，确认 `_get_node` 是否在更新前过滤 `deleted_at`。
- API 路径以当前 `server.py` 和 `tools/client.py` 为准。证据接口在当前 Agent client 中是 `POST /api/goals/nodes/{nodeId}/evidence`；不要沿用旧测试/文档中可能出现的 `/api/goals/evidence`。

## CLI 集成边界（已验证）
当 Goal Manager 被用作 Agent 的目标达成辅助后，推荐让 `/goal` 作为用户交互和编排层，让 Goal Manager 独占结构化项目、节点、依赖、进度、证据和完成校验；Session Goal 只保存会话到项目的绑定以及插件不可用时的回退状态。

实现最小接入时：
1. 新建 `/goal <objective>` 先持久化本地 Session Goal，再尽力创建 Goal Manager 项目并保存 `project_id`/`root_node_id`，避免插件故障导致用户目标丢失。
2. 通过注入的可选 backend/protocol 调用插件，不要让核心 CLI 硬编码导入 `plugins.goal_manager`；fake backend 才能覆盖服务异常和超时降级。
3. Agent 启动提示应包含绑定 ID，并明确完成条件、证据要求和禁止伪完成的约束。
4. `/goal status` 优先读取远端实时状态，但远端暂时不可用时保留本地绑定和本地目标展示，不要清除绑定。
5. `/goal complete` 必须先验证 Goal Manager 根节点及其必需子节点、验收条件和证据，验证失败时不得完成 Session Goal。
6. `/goal blocked <reason>` 应同步远端阻塞状态；若尚未实现，不能把本地 blocked 当成远端已同步。

当前阶段明确的范围边界：不自动把每次 Agent 工具调用映射为图节点，不自动回滚文件/Git/部署等外部副作用；项目创建幂等键、服务超时重试和 blocked 同步属于后续增强，不能在审查中假设已经存在。

## Agent 接入增强的实现准则

审查或实现创建幂等、超时重试和 blocked 同步时，遵循以下边界，避免“表面重试安全”：

1. **幂等必须由 Goal Service 持久化保证**：仅在 CLI 内缓存 `project_id` 不足以覆盖“服务已提交，但响应在网络中丢失”的情况。创建项目请求应携带稳定 `idempotency_key`，服务端数据库对该键建立唯一约束，并在重复请求时返回第一次创建的同一 project/root；键应基于会话与目标实例生成并持久化，不能每次重试重新随机生成。
2. **数据库升级必须兼容已有 goals.db**：`schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 不会给旧表自动加列。若在 `goal_projects` 上新增幂等字段，初始化时要显式检查 `PRAGMA table_info` 并迁移，或使用独立 idempotency 表；测试必须覆盖从旧 schema 启动。
3. **只重试可证明安全的调用**：健康检查和 GET 可短退避重试；POST 创建只有带服务端幂等键时才能自动重试。连接失败可能代表未送达，读取超时可能代表已提交，不能把二者都当作安全的裸 POST 重发。
4. **超时要可配置且有总预算**：客户端至少区分 connect/read timeout，并限制尝试次数与总等待时间。CLI 交互链路应快速降级为 Session Goal，不因插件卡住整个 Agent；降级时保留已有 project/root 绑定。
5. **blocked 同步使用根节点乐观锁**：先读根节点的 `version`，再 PATCH `status=blocked`，reason 同时写入审计事件。遇到 409 时读取 `latest`：若已 blocked 可视为成功，否则有限次重放；不能盲猜 expected_version，也不能覆盖并发完成状态。
6. **本地与远端不是虚假原子事务**：建议先保证本地目标存在，再尽力同步远端。远端失败时本地可进入 blocked，但应记录“远端未同步/待重试”的可观察状态，不能向用户暗示两边已经一致。
7. **测试故障窗口而非只 mock 500**：至少覆盖同键重复创建、提交后响应丢失再重试、连接失败、读取超时、409 并发冲突、远端已 blocked 的收敛、服务不可用时绑定不丢失，以及旧数据库迁移。

## Adaptive Goal Execution Protocol（首版设计边界）

当目标管理器从“图谱记录器”扩展为 Agent 的执行辅助时，采用渐进式闭环，不把执行硬编码成一次性线性流程：

```text
粗粒度计划 -> 当前层展开 -> 找最大不确定性 -> 最小高信息价值行动 -> 观察结果 -> 证据验证 -> 局部收敛 -> 再展开或重新规划
```

推荐的通用执行链是：

```text
目标 -> 假设 -> 探查 -> 执行 -> 证据 -> 判断 -> 再规划
```

关键规则：
- 全局计划可以粗粒度且暂定；当前执行层必须具体、可验证，并受深度、节点数、时间或工具调用预算限制。
- 每轮优先解决当前最大不确定性，先做信息价值最高且副作用最小的动作；不要要求一次性拆出大量未来节点。
- 必须区分动作执行、结果观察、假设确认、验收条件满足和目标完成。Agent 自述成功不能单独触发 `completed`。
- 建议阶段包括意图确认、目标建模、计划审查、执行记录、结果校验、失败/阻塞/重新规划、目标收敛与复盘。
- 决策建议至少区分 `plan`、`investigate`、`execute`、`verify`、`replan`，服务端只返回可解释建议，不替 Agent 隐含执行动作。
- 计划重新规划必须留下不可覆盖的版本记录，包含触发事实、原因、假设、不确定性、预算和变更；不能覆盖旧计划。
- 外部文件、Git、部署和第三方 API 的副作用只能记录证据，Goal Manager 不应承诺自动补偿回滚；本地 Session 与远端目标服务不是原子事务。
- 父节点不能仅因子节点全部完成而自动完成；根目标必须经过独立验收。

首版适合新增三类一等记录而不改变现有 DAG 语义：
1. `plan_revision`：按项目递增、不可覆盖的计划版本。
2. `execution_attempt`：动作、状态、结果观察、幂等键和重试关系独立记录。
3. `recommendation`：依据依赖、阻塞、证据和未决不确定性生成下一步建议。

落地时要保留历史项目兼容语义：新完成门只对显式启用协议的项目生效。重点故障窗口必须测试：幂等重试、提交后响应丢失、连接失败、读取超时、409 乐观锁冲突和旧数据库迁移。HTTP 客户端不能把所有 POST 都当作可安全重试；只有服务端持久化幂等键后才允许自动恢复。

## AI 专项审查补充项（当前实现复核经验）

除了状态机和 API 正确性，还必须检查这些会直接影响 Agent 判断的契约：

1. **身份不能由请求体自报**：`actor_type=user|supervisor` 只能来自已认证的调用上下文，不能仅凭 JSON 字段决定审批、拒绝、回滚或大规模删除权限。若服务 token 只证明“能访问服务”，还需要把调用方身份/角色绑定到 token 或网关上下文。
2. **输入校验应拒绝未知字段**：审查 Pydantic `extra="allow"`。对 Agent 工具，拼写错误或过时字段被静默丢弃会造成“调用成功但意图未生效”；除非有明确兼容契约，否则应使用 `extra="forbid"` 并测试未知字段返回 422。
3. **effect 声明必须按真实副作用分类**：版本冲突保护不等于幂等。节点删除、带状态转换的更新、审核提交、证据写入和批量操作若没有请求级持久化幂等键，不应标成可安全自动重试的 `idempotent_write`。用注册后的 `registry.get_effect()` 与实现行为逐项比对。
4. **叙事日志不能替代执行证明**：`record_execution_result`、`record_observation`、`verify_evidence` 的接口可以记录 Agent 自述，但完成状态只能由实际状态约束、可定位证据和独立验收共同决定。测试“伪造成功记录后是否仍不能完成”。
5. **建议接口与强制约束要区分**：`next_actions` 和 protocol recommendation 只是建议时，Agent 仍可能跳过探查、依赖或验证；需要明确哪些规则由服务端拒绝，哪些规则只由提示词/协议引导，并为关键跳步添加拒绝测试。
6. **提示词是可测试的协议表面**：CLI 注入的 Goal Manager 工作流提示词应有稳定的语义断言，而不是依赖易变的整句文案。运行 CLI 回归时，若工作流提示词测试失败，应视为 Agent 行为契约回归，而不是普通文案问题。

## 验证清单
- 服务在跑：`curl http://127.0.0.1:6003/` 返回 200
- 工具已注册（用全局单例，且先激活/发现）：`registry.list_tools()` 含 14 个 goal_* 工具，`registry.list_toolsets()` 含 goal_manager
- 图谱读写、环检测、乐观锁、批量/回滚/重做、证据全部 PASS
- AI 行为矩阵通过：完成验证、证据绑定、重复调用、版本冲突、恢复和 next_actions 可解释性
- 权限矩阵通过：未认证、错误 token、伪造 `actor_type`、非授权审批/回滚/批量删除均被正确拒绝
- 输入契约通过：未知字段、错误别名、缺失 reason、过期 version 均得到明确结构化错误
- 工具 effect 与真实副作用一致；没有把“版本保护”误当成请求幂等
- CLI 集成回归：`/goal` 创建与绑定、服务不可用降级、状态读取、完成校验、Session 持久化，以及 Goal Manager 工作流提示词语义断言
- 测试图谱可保留在库里供 Web UI 查看（/ui/goal-manager/），用户确认后再清理

## 相关
- 插件开发/集成规范见 `voidcube-plugin-development` 技能
- 前端 e2e：desktop/tests/e2e/goal-manager.spec.ts
- 服务端实现：plugins/goal_manager/server.py（Pydantic payload 模型在文件头部，改 payload 前先读它）
