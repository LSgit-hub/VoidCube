# CLI 展示组件分工与 gateway 双槽设计

## 1. 这份文档解决什么

这份文档和内生驱动那几份文档是不同关注点，单独成文，避免混在认知引擎主线里。

它只回答两件事：

- VoidCube 里到底有几套子代理展示，各自管什么
- 为什么 gateway 的 agent scene 需要做成双槽，怎么做才不破坏现有可观测性

与其他文档分工：

- 内生驱动那几份：定义“监督者认知核心是什么”
- 本文：定义“监控可观测性（CLI 展示 + gateway 聚合）怎么分工、怎么演进”

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
  - 监督者任务 CLI 在 auto 模式报 `learning` / `code_editing`，带 `execution_kind` + `task_id`（见 `cli.py` 的 `_current_gateway_presence_snapshot`）
- gateway 取“最后一个非 idle 上报者”占用槽位，两进程同时活跃时互相覆盖，聚合视图分不开。

这就是为什么“按 session 过滤”不成立：session_id 虽不同，但 gateway 单槽后写覆盖，下游读到的是混合结果。

## 3. 组件分工目标

| 组件 | 角色 | 应显示 |
|---|---|---|
| 主 CLI 自身的子代理展示 | 主 CLI 与用户交互时的子代理 | 本进程 user_chat 子代理（本地 manager，已隔离） |
| 最小 CLI（`VoidCube_cli/ops/dashboard.py`） | 监督者任务（学习 / 改造）专用观测面 | 仅 supervisor_task 子代理，不掺入用户交互子代理 |
| Web 小屋（`systems/supervisor/ui_runtime.py`） | 监督者认知核心总览 | 双泳道治理 / 决策溯源 / 健康度（已完成 drill-down） |

收敛判据：最小 CLI 在“主 CLI 正与用户交互 + 监督者任务同时在跑子代理”时，仍只显示监督者任务那一套，不被用户交互子代理覆盖或混入。

## 4. gateway 双槽设计

核心思路：**additive 双槽，top-level 保持不变以零破坏现有可观测性。**

### 4.1 判别信号

两个 reporter 的 scene 其实已不同（监督者任务报 `learning` / `code_editing`，用户交互报 `executing`），但 auto 模式空档期也会报 `executing`，所以 scene 不是 100% 可靠判别信号。需要 reporter 显式打标 `agent_role`，gateway 再以 scene 启发式作回退。

### 4.2 数据结构

在 `_scenes_cache["agent"]` 下新增 `lanes`：

```text
agent:
  scene / scene_task_id / subagent_* / ...   # top-level，照旧，后写覆盖，喂状态栏与旧测试
  lanes:
    supervisor_task: { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
    user_chat:       { scene, scene_task_id, subagent_foreground_count, ..., reachable, last_fetched_at }
```

并维护一个 `session_id -> lane` 映射，使 idle 上报能清掉正确的 lane。

### 4.3 三处改动

1. **Reporter 侧（`cli.py` 的 `_push_cli_agent_scene`）**：metadata 增加 `agent_role`，由 `_auto_mode_active` 派生（auto = `supervisor_task`，否则 `user_chat`）。这是权威来源标签。
2. **Gateway 侧（`_touch_activity` 的 agent_scene 分支）**：在更新 top-level（照旧）之外，按 `agent_role`（缺失时 scene 启发式：`learning` / `code_editing` → supervisor_task，`executing` → user_chat）把字段写进对应 lane；并记录 `session -> lane`，使该 session 后续 idle 上报清空对应 lane。
3. **Consumer 侧（`dashboard.py`）**：agent 段读 `scenes["agent"]["lanes"]["supervisor_task"]` 的 SA 计数 / focus；lanes 缺失时回退 top-level（兼容旧 gateway）。`status.py`（主 CLI 状态栏）保持读 top-level 不动。

### 4.4 为什么不破坏现有功能

`lanes` 是纯增量字段，top-level 维持 last-writer-wins。现有 `test_gateway_activity` / `test_scene_status_observability` / `status.py` 全部读 top-level，零影响。supervisor_task lane 独立保存，永不被 user_chat 覆盖 —— 根问题解决。

## 5. 落地顺序

1. gateway 加 `lanes` + session→lane 映射 + 路由逻辑（`agent_role` 优先、scene 启发式回退）+ 新测试，断言两 lane 互不覆盖
2. reporter（`cli.py`）发 `agent_role` + 测试，断言 auto / 交互两路打标正确
3. `dashboard.py` 读 supervisor_task lane + 测试，断言用户交互子代理活跃时仍只显示监督者任务那一套

## 6. 现状

- 第 1 步（gateway 双槽 + 路由 + 测试）：已落地
- 第 2 步（reporter 打标）：已落地
- 第 3 步（dashboard 读 supervisor lane）：已落地

Web 小屋 drill-down（双泳道治理 / 决策溯源 / 健康度）此前已完成，属本分工里的 Web 总览面。
