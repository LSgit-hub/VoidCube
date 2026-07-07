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
| Web 小屋（`systems/supervisor/ui_runtime.py`） | API-B / 监督者认知核心总览 | 监督者动作、反馈、任务治理、记忆状态与自主任务回报；不展示用户聊天内容 |

收敛判据：最小 CLI 在“主 CLI 正与用户交互 + 监督者任务同时在跑子代理”时，仍只显示监督者任务那一套，不被用户交互子代理覆盖或混入。

补充边界：主 CLI 当前不再挂载 `API-A 自主执行面` 的执行流程窗口。主 CLI 只保留用户链路输入输出、`/auto` 门控提示与用户链路状态栏；API-A 自主执行流程的细节观察应只出现在最小 CLI / 专用观察面里。

## 4. gateway 双槽设计

> **前提澄清：两个 lane 都属于 API-A。** gateway 的 `agent` scene 槽位专属 API-A。双槽拆的是「同一个 API-A 的两种工作模式」：`supervisor_task`（执行监督者 / API-B 派来的学习、改造任务）与 `user_chat`（与用户交互）。`supervisor_task` 这个名字指**任务来源**是监督者，**执行者仍是 API-A**，不是 API-B。API-B（`supervisor` 槽位）只负责产出任务与自维护，干活的两种模式都是 API-A。两个 lane 互不覆盖。

核心思路：**additive 双槽，top-level 保持不变以零破坏现有可观测性。**

### 4.1 判别信号

两个 reporter 的 scene 其实已不同（监督者任务报 `learning` / `code_editing`，用户交互报 `executing`），但自主链路空档期也可能报 `executing`，所以 scene 不是 100% 可靠判别信号。需要 reporter 显式打标 `agent_role`，gateway 再以 scene 启发式作回退。

### 4.2 数据结构

在 `_scenes_cache["agent"]` 下新增 `lanes`：

```text
agent:
  scene / scene_task_id / subagent_* / ...   # top-level，照旧，后写覆盖，仅作兼容聚合
  lanes:
    supervisor_task: { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
    user_chat:       { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
```

并维护一个 `session_id -> lane` 映射，使 idle 上报能清掉正确的 lane。

### 4.3 三处改动

1. **Reporter 侧（`cli.py` 的 `_push_cli_agent_scene`）**：metadata 增加 `agent_role`。正在执行/收尾自主任务的回合标记为 `supervisor_task`；普通用户回合即使 `/auto` 开关开启也标记为 `user_chat`。这是权威来源标签。
2. **Gateway 侧（`_touch_activity` 的 agent_scene 分支）**：在更新 top-level（照旧）之外，按 `agent_role`（缺失时 scene 启发式：`learning` / `code_editing` → supervisor_task，`executing` → user_chat）把字段写进对应 lane；并记录 `session -> lane`，使该 session 后续 idle 上报清空对应 lane。
3. **Consumer 侧（`dashboard.py` / `status.py`）**：最小 dashboard 的 agent 段读 `scenes["agent"]["lanes"]["supervisor_task"]` 的 SA 计数 / focus；主 CLI 状态栏 `status.py` 读 `lanes.user_chat`。lanes 缺失时才回退 top-level，用于兼容旧 gateway。

### 4.4 为什么不破坏现有功能

`lanes` 是纯增量字段，top-level 维持 last-writer-wins，只保留给旧聚合视图与旧测试兼容。新消费端必须优先读 lane：最小 dashboard 读 `supervisor_task`，主 CLI 状态栏读 `user_chat`。这样 `supervisor_task` lane 独立保存，永不被 `user_chat` 覆盖；用户链路状态栏也不会被自主任务覆盖。

## 5. 落地顺序

1. gateway 加 `lanes` + session→lane 映射 + 路由逻辑（`agent_role` 优先、scene 启发式回退）+ 新测试，断言两 lane 互不覆盖
2. reporter（`cli.py`）发 `agent_role` + 测试，断言监督者任务 / 用户交互两路打标正确
3. `dashboard.py` 读 supervisor_task lane + 测试，断言用户交互子代理活跃时仍只显示监督者任务那一套

## 6. 现状

- 第 1 步（gateway 双槽 + 路由 + 测试）：已落地
- 第 2 步（reporter 打标）：已落地
- 第 3 步（dashboard 读 supervisor lane）：已落地
- 第 4 步（主 CLI status 读 user_chat lane）：已落地

进一步收口后，最小 dashboard 已不再把 gateway agent top-level 兼容聚合当作 API-A 自主观察来源，而是显式拆成两层：

- `API-B 主视角自主闭环总览`：继续只读 Supervisor 的 `autonomous_observation`
- `API-A 自主执行观察面`：只读 gateway `scenes.agent.lanes.supervisor_task` 与 `active_cli_executor(agent_lane=supervisor_task)`，不再回退 `user_chat` 或 top-level 聚合

这意味着即便主 CLI 的 `user_chat` 正在活跃，最小 dashboard 也只会显示自主链路自己的 API-A 执行位、子代理焦点与链路项，不再出现用户聊天焦点串入自主执行观察面的情况。

Web 小屋 drill-down（双泳道治理 / 决策溯源 / 健康度）此前已完成，属本分工里的 Web 总览面。当前 Web 小屋 timeline 已过滤 Gateway 的 `user_request` 与 `agent_scene`，只展示 API-B 治理、自主任务与 Mem 相关观测，不展示用户聊天内容。
