# Web 监控组件边界改进文档

## 1. 目的与结论

当前小屋页面用场景物件表达系统状态，但物件点击入口与后端状态所有者没有完全对齐：电脑显示器打开的是记忆传输队列，花盆和写字桌上的纸稿只是装饰，时钟则承担了唯一明确的定时任务入口。这样会让用户把 Auto 自主链路、员工执行、Mem 回写、Assist 交付和 API-A 定时任务误认为同一条链路。

本次改进采用以下固定边界：

| 场景入口 | 用户心智 | 目标面板 | 唯一数据所有者 |
| --- | --- | --- | --- |
| 花盆 | 记忆在生长、结果回流 | Mem 记忆传输与写回 | Mem 状态投影、记忆 outbox、写回事件 |
| 电脑显示器 | 员工代理正在工作 | 员工代理执行详情 | Auto canonical task、employee assignment/run、执行器心跳和结果 |
| 写字桌纸稿 | 星子安排的自主工作 | 星子 / API-B 自主任务安排 | `autonomous_observation` 的任务、候选、派工和历史投影 |
| 墙上时钟 | 用户要求在某时执行 | API-A 定时任务 | `scheduled_tasks` 及其 run |

这里的 API-A 是面向用户的主 API 和定时任务权威；API-B / 星子是自主判断与辅助入口。Assist 模式下星子可以代用户向 API-A 创建定时任务，但创建后仍归 API-A 的定时任务域。Auto 模式不使用定时任务驱动自主链路。

## 2. 当前问题定位

现有页面的关键实现如下：

- `#scheduleClock` 已打开 `schedules` 面板并请求 `/scheduled-tasks?include_completed=true`，这个方向保留。
- `.desk-monitor` 带有 `data-drill="outboxes"`，点击后进入“记忆传输队列”，与电脑代表员工执行的语义不符。
- `.plant-corner` 带 `aria-hidden="true"`，没有交互入口。
- `.desk-write` 带 `aria-hidden="true"`，纸稿没有交互入口。
- Agent 交付面板拥有 `/ui/delivery-events`、`/ui/delivery/control` 等独立状态，不应作为 Auto 员工结果的通用收件箱。
- `/ui/state` 已同时提供 `autonomous_observation`、`tier1_stats`、`mem_usage` 等投影；`scheduled_tasks` 是另一套独立存储和调度生命周期，不能把 Auto task 镜像或迁移到该列表来实现展示。

## 3. 目标信息架构

### 3.1 花盆：Mem 传输与回写

点击热区应覆盖花盆及叶片下部，打开专用 Mem 面板或 Mem 抽屉。面板只回答“结果是否进入 Mem、当前传输在哪一步、是否失败”，不展示完整员工日志。

建议分为四段：

1. 待发送：outbox 数量、最早创建时间、租约/重试状态。
2. 传输中：当前批次、目标 Mem 服务、最近心跳。
3. 已写回：最近 Auto 员工结果、写回时间、`source_task_id`、写回摘要。
4. 异常：失败原因、重试次数、是否阻断下一轮；异常必须可链接到电脑的具体执行记录。

首期数据优先复用 `/ui/state` 的 `tier1_stats`、`mem_usage`、`autonomous_observation` 中的 Mem 回流卡片和 timeline。若现有摘要不足，再增加只读的 Mem flow 投影接口；不要让前端直接读取 Mem 数据库。

### 3.2 电脑：员工执行详情

点击显示器打开员工执行面板，默认聚焦当前运行或最近一次运行的 Auto 员工任务。面板必须能区分：

- canonical `task_id` 与 employee `employee_task_id`；
- API-B 派工状态：待接手、已接手、执行中、已完成、失败、执行器失联；
- 员工角色、Provider、模型、工具集和开始/结束时间；
- 进度摘要、结果摘要、错误和重试；
- Mem 写回状态及关联的写回事件。

数据源以 `/ui/state.autonomous_observation` 为主，利用 `board`、`loop_stages`、`employee_dispatch` 和 `mem_writeback` 投影。若需要逐条日志，新增只读 employee-run 查询接口，不能复用 `/scheduled-tasks` 或交付面板状态。

### 3.3 纸稿：星子自主任务安排

纸稿热区打开“自主任务安排”面板，展示星子安排和推进的任务，而不是任何定时任务：

- 当前候选、API-B 判断、已批准派工、待员工接手；
- 任务的来源（学习、改进、记忆维护等）、优先级/效用、当前阶段；
- 任务进入员工执行后的关联 ID；
- 已完成任务的 Mem 回写结果和下一轮再读取状态；
- Auto 关闭或没有任务时的明确空态。

面板可复用自主闭环总览的数据投影，但应以“安排/队列”作为默认视图，点击任务再进入电脑的执行详情。纸稿不请求 `/scheduled-tasks`，也不显示 `next_run_at`、日历时间或“等待主 CLI”等字段。

### 3.4 时钟：API-A 定时任务

时钟保留打开 `schedules` 面板，但文案和数据契约必须明确这是 API-A 的用户定时任务：

- 数据源仍为 `/scheduled-tasks?include_completed=true`；
- 展示一次性、每日、每周计划、下次执行、暂停/失败/完成状态和创建者；
- 创建者可显示“用户 / 星子辅助”，但不能把“星子辅助创建”解释为 Auto 自主任务；
- Assist 模式允许星子通过 API-A 的创建接口协助添加任务，必须经过 API-A 的校验和确认策略；
- Auto 模式不在此面板创建或排队自主学习、自主改进、员工执行任务。

## 4. 模式边界与不变量

### Assist（日常辅助）

- 用户临时请求播放音视频、查论文、查资料等，可进入 Agent 交付面板。
- 星子可以提出或代用户调用 API-A 创建定时任务；记录 `created_by=api_b` 或等价的审计字段，但 schedule 的所有权仍是 API-A。
- Assist 员工结果只有在确实是用户可查看的交付产物时才推送交付面板；普通文本回复不必伪装成文件交付。

### Auto（自主链路）

- canonical task、API-B 判断、员工 assignment/run、Mem 写回和再读取组成闭环。
- Auto 员工完成结果直接进入 Mem 写回流程和自主观察投影，不进入 Agent 交付面板，不创建 API-A 定时任务。
- Auto 的轮询/驱动由自主链路 gate、review/drive loop 和 canonical task 状态负责；不能以 `scheduled_task_id`、`next_run_at` 或时钟动画作为运行依据。
- 关闭 Auto gate 时，只清理 Auto 自有任务；Assist 的 canonical task 和用户 schedule 不得被连带取消。

### Agent 交付面板

交付面板是“用户可直接查看的临时产物”边界，不是所有员工结果的总线。后端推送交付前应保留来源模式字段（例如 `mode`、`requested_via`、`source_task_id`），并在 Auto 来源时拒绝或丢弃交付推送，避免前端靠猜测过滤。

## 5. 点击与面板路由设计

建议建立显式的场景入口常量，避免继续用模糊的 `data-drill` 语义：

| 入口 | 建议标识 | 打开方式 |
| --- | --- | --- |
| 花盆 | `data-scene-entry="memory-flow"` | Mem flow 面板/抽屉 |
| 电脑 | `data-scene-entry="employee-runs"` | 员工执行详情面板 |
| 纸稿 | `data-scene-entry="autonomous-tasks"` | 自主任务安排面板 |
| 时钟 | `data-scene-entry="api-a-schedules"` | `schedules` 面板 |

每个热区都必须同时提供 `role="button"`、`tabindex="0"`、`aria-label`、焦点样式和 Enter/Space 键盘行为。点击热区只负责导航，不在事件处理器中拼装业务状态；面板自身从统一状态快照加载。

可以保留现有 `data-drill` 作为过渡别名，但实施完成后应删除 `outboxes` 作为电脑入口的旧绑定，并移除花盆/纸稿的 `aria-hidden`，避免兼容分支被后续误认为主逻辑。

## 6. 状态契约建议

统一 `/ui/state` 的只读快照结构，新增或明确以下投影字段（字段名可按现有风格调整）：

```json
{
  "autonomous_observation": {
    "board": {
      "autonomous_tasks": [],
      "employee_runs": [],
      "mem_writeback": []
    },
    "runtime": {
      "mode": "auto",
      "autonomous_chain_gate_active": true
    }
  },
  "memory_flow": {
    "pending": 0,
    "in_flight": 0,
    "recent_writebacks": [],
    "last_error": null
  }
}
```

约束：

- `autonomous_tasks` 的主键是 canonical `task_id`，不得用 `schedule_id` 代替。
- `employee_runs` 必须带 `task_id`、`employee_task_id`、`status`、`provider/model`、`writeback_status`。
- `mem_writeback` 必须带 `source_task_id` 和结果状态，保证可以从花盆追到电脑，再追到纸稿任务。
- `scheduled_tasks` 只由 `/scheduled-tasks` 返回，不能被合并进 `autonomous_tasks`。
- 面板显示“暂无数据”与“加载失败”必须区分；旧快照或后端不可用时不能回退到另一领域的数据。

## 7. 动画与真实状态

动画只表达对应组件的状态，不能用装饰动画暗示错误的业务状态：

- 花盆叶片常态轻微摆动；有 Mem 写回时增加短暂、可中断的传输提示，失败时显示静态错误态。
- 电脑屏幕代码/光标动画只在 employee run 为 `running` 且心跳未过期时播放；待接手、完成、失联时使用相应静态状态。
- 纸稿可在有新自主任务时出现一次“新增纸张”提示；没有任务时保持静态，不模拟定时倒计时。
- 时钟指针只表示当前时间；到期提示来自 API-A schedule 状态，不能把指针变化解释为 Auto 驱动。
- 所有状态动画应支持 `prefers-reduced-motion`，并在状态快照变化后可恢复到正确状态。

## 8. 分阶段实施顺序

### 阶段一：边界和契约

1. 为四个入口确定显式标识和 panel key。
2. 在 `/ui/state` 中补齐或稳定 autonomous tasks、employee runs、Mem writeback 的投影字段。
3. 为交付推送建立 Assist/Auto 来源校验，增加回归测试，确保 Auto 结果只走 Mem。

### 阶段二：前端路由和面板

1. 将花盆改为可访问热区并接入 Mem flow 面板。
2. 将电脑从 `outboxes` 改接 employee runs 面板，补充运行详情和写回链接。
3. 将纸稿改为可访问热区，新增自主任务安排面板，禁止请求 `/scheduled-tasks`。
4. 保留时钟的 schedule 面板，更新标题、空态和来源文案为 API-A 定时任务。

### 阶段三：动画、异常和清理

1. 按真实状态驱动四个物件动画和错误态。
2. 加入加载、空态、权限/网络失败、失联和过期心跳展示。
3. 删除电脑到 outboxes 的旧绑定、重复的 schedule 过滤和无效兼容分支；保留的兼容字段必须标注为迁移回退而不是主入口。

## 9. 测试与验收标准

### 前端交互

- 鼠标和键盘点击花盆、电脑、纸稿、时钟分别只打开目标面板。
- 四个入口在移动端和桌面端不重叠，焦点可见，Esc/关闭按钮行为一致。
- 纸稿面板请求中不出现 `/scheduled-tasks`；时钟面板请求中不出现自主任务接口。

### 数据隔离

- Assist 临时交付可在交付面板看到，Auto 员工结果在交付面板中不可见。
- Auto 完成后能在电脑详情看到执行结果，在花盆看到 Mem 写回，并在纸稿看到任务完成/回流状态。
- API-A schedule 的创建、暂停、恢复、失败和完成状态只在时钟面板出现；星子辅助创建保留审计来源。
- Auto gate 开关和自主 review/drive loop 不依赖 `scheduled_tasks`。

### 回归与自动化

- 为状态投影增加 Assist/Auto、员工完成/失败/失联、Mem 写回成功/失败的单元测试。
- 为路由增加浏览器级或 DOM 级点击映射测试，断言入口到面板的一对一关系。
- 为交付边界增加接口测试：Auto 来源 push 必须被拒绝或明确标记为不可展示。
- 执行项目虚拟环境中的相关 Supervisor/Web 测试，并运行 `git diff --check`。

## 10. 迁移注意事项

迁移期间可以从旧 `data-drill` 或旧快照字段读取一次性回退值，但必须满足：

1. 新字段存在时永远优先新字段。
2. 回退只用于读取旧数据，不得继续写入旧语义字段。
3. 迁移完成后删除 `desk-monitor -> outboxes`、纸稿/花盆的装饰-only 标记及把 Auto 任务转成 schedule 的代码路径。
4. 文档、测试和注释统一使用“API-A schedule / Auto autonomous task / Mem writeback”三种术语，避免把员工执行重新接回时钟或交付面板。
