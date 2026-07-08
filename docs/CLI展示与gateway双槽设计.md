# CLI 展示组件分工与 gateway 双槽设计

## 1. 这份文档解决什么

这份文档和内生驱动那几份文档是不同关注点，单独成文，避免混在认知引擎主线里。

它只回答两件事：

- VoidCube 里到底有几套子代理展示，各自管什么
- 为什么 gateway 的 agent scene 需要做成双槽，怎么做才不破坏现有可观测性

与其他文档分工：

- 内生驱动那几份：定义“监督者认知核心是什么”
- 本文：定义“监控可观测性（CLI 展示 + gateway 聚合）怎么分工、怎么演进”

> **2026-07 对齐说明**：本文只讨论可观测性分槽，不重新定义监督者运行模式。`/auto` 开关当前只是自主链路的临时启停门控，不限制主 CLI 输入；判断 `supervisor_task` lane 时应以“当前正在执行监督者任务”为准，而不是把整个主 CLI 会话理解成被 `/auto` 开关接管。Web 小屋只作为 API-B 观测面，不成为用户聊天入口。

## 2. 子代理展示分层事实

子代理展示分两层看，这是所有设计的前提：

### 2.1 本地渲染层（进程内）—— 天然两套，互不干扰

- `SubagentDisplayManager` 不是单例。每次 `delegate` 派生子代理时新建一个，挂在 `parent_agent._subagent_display_manager` 上（`tools/delegate_tool.py`）。
- 两个独立 CLI 进程 = 两个 agent 实例 = 两套独立 manager。各自的 `/tasks` 命令与实时渲染只显示本进程子代理（`cli.py`）。
- 所以“监控监督者任务子代理会干扰主 CLI 与用户交互”在本地渲染层根本不存在。

### 2.2 gateway 聚合层（dashboard.py / Web 的数据源）—— 改造前只有一套，且后写覆盖

- gateway 只有一个 `_scenes_cache["agent"]` 槽位。
- 两个进程都用 `source_service="cli_agent"` + 各自 session_id 上报，而且两者都会上报：
  - 主 CLI 用户任务时报 `executing`
  - 监督者任务 CLI 在执行自主链路项 / 当前执行焦点时上报 `learning` / `code_editing`，带 `execution_kind` + `task_id`（见 `cli.py` 的 `_current_gateway_presence_snapshot`）
- gateway 取“最后一个非 idle 上报者”占用槽位，两进程同时活跃时互相覆盖，聚合视图分不开。

这就是为什么“按 session 过滤”不成立：session_id 虽不同，但 gateway 单槽后写覆盖，下游读到的是混合结果。

## 3. 组件分工目标

| 组件 | 角色 | 应显示 |
| --- | --- | --- |
| 主 CLI 自身的子代理展示 | 主 CLI 与用户交互时的子代理 | 本进程 user_chat 子代理（本地 manager，已隔离） |
| 最小 CLI（`VoidCube_cli/ops/dashboard.py`） | 自主链路中 API-A 子代理执行学习 / 改造任务的专用观测面 | 仅 supervisor_task 子代理，不掺入用户交互子代理 |
| Web 小屋（`systems/supervisor/ui_runtime.py`） | API-B / 监督者认知核心总览 | 监督者动作、反馈、API-B 判断、记忆状态与自主任务回报；不展示用户聊天内容 |

收敛判据：最小 CLI 在“主 CLI 正与用户交互 + 监督者任务同时在跑子代理”时，仍只显示监督者任务那一套，不被用户交互子代理覆盖或混入。

补充边界：主 CLI 当前不再挂载 `API-A 自主执行面` 的执行流程窗口。主 CLI 只保留用户链路输入输出、`/auto` 门控提示与用户链路状态栏；API-A 自主执行流程的细节观察应只出现在最小 CLI / 专用观察面里。

## 4. gateway 双槽现行协议

> **前提澄清：两个 lane 都属于 API-A。** gateway 的 `agent` scene 槽位专属 API-A。双槽拆的是「同一个 API-A 的两种工作模式」：`supervisor_task`（执行监督者 / API-B 派来的学习、改造任务）与 `user_chat`（与用户交互）。`supervisor_task` 这个名字指**任务来源**是监督者，**执行者仍是 API-A**，不是 API-B。API-B（`supervisor` 槽位）只负责产出任务与自维护，干活的两种模式都是 API-A。两个 lane 互不覆盖。

当前正式读法只有三条：

- 主 CLI 状态栏读 `user_chat`
- 最小 CLI 读 `supervisor_task`
- Web 小屋不读用户聊天，只看 API-B 观测投影与替身状态

### 4.1 判别信号

reporter 必须显式打 `agent_role`：

- `user_chat`：主 CLI 与用户交互
- `supervisor_task`：API-A 执行 API-B 已转交的自主链路任务

scene 名只作为展示字段，不作为主判别事实。

### 4.2 数据结构

`_scenes_cache["agent"].lanes` 是当前主协议：

```text
agent:
  lanes:
    supervisor_task: { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
    user_chat:       { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
```

gateway 维护 `session_id -> lane` 映射，使 idle 上报清掉正确 lane。

### 4.3 消费规则

- `VoidCube_cli/status.py` 只读 `lanes.user_chat`
- `VoidCube_cli/ops/dashboard.py` 只读 `lanes.supervisor_task` 与 `active_cli_executor(agent_lane=supervisor_task)`
- `systems/supervisor/ui_runtime.py` 不把 gateway agent lane 当聊天入口，只展示 API-B 自主闭环读模型与替身状态

top-level `agent.scene` 只作为历史聚合影子存在，不再作为新消费面的事实源。

## 5. 当前状态

- `API-B 主视角自主闭环总览`：继续只读 Supervisor 的 `autonomous_observation`
- `API-A 自主执行观察面`：只读 gateway `scenes.agent.lanes.supervisor_task` 与 `active_cli_executor(agent_lane=supervisor_task)`，不再回退 `user_chat` 或 top-level 聚合

这意味着即便主 CLI 的 `user_chat` 正在活跃，最小 dashboard 也只会显示自主链路自己的 API-A 执行位、子代理焦点与链路项，不再出现用户聊天焦点串入自主执行观察面的情况。

Web 小屋当前是 API-B 状态房间：只展示 API-B 判断、API-A 对 API-B 可见的认领 / 执行状态、Mem 回流、API-B 再读取和替身信息，不展示用户聊天内容，也不提供队列管理入口。
