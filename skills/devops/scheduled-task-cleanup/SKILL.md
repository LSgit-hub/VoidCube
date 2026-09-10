---
name: scheduled-task-cleanup
description: 清理 VoidCube 定时任务列表（批量删除一次性历史任务、保留周期性任务）。当用户说"清理定时任务/任务记录"、"删除已完成/失败/取消的任务"、"除了每周任务都删掉"时使用。包含 DB 只读审计法（避免工具 list 输出爆炸）、scheduled_task 动作去重（duplicate_or_in_flight_action）绕过方法、并行批量删除模式、"过期未认领 active 任务"的识别与上报判据，以及**取消失效 active 任务的治本流程**（scheduled_tasks 只是物化视图，真源是 autonomous_chain_store.json，须用 6002 的 decision 接口把 approved 置为 cancelled，否则 12~23 秒内复生）。
---

# 定时任务清理（scheduled-task-cleanup）

## 触发条件
- 用户要求清理定时任务记录 / 任务列表
- 用户要求删除已完成、失败、取消的一次性任务，保留某个周期性任务（如每周任务）

## 核心流程

### 1. 审计任务列表（首选 DB 只读查询，不要用工具 list 做全量审计）

**实测教训（本会话踩过）**：对 88 个任务调用
`{"action": "list", "include_completed": true}` 会返回**每条任务的完整 instruction
+ recent_runs 里每条 run 的完整 result_summary**，单体输出足以打满上下文窗口。
所以：**工具 list 只用于最后的轻量复核，审计阶段一律走 DB 只读查询。**

DB 路径：`~/.VoidCube/runtime/supervisor/scheduled_tasks.db`
表：`scheduled_tasks`（schedule_id/schedule_type/status/title/next_run_at/last_run_at/active_run_id/run_at）
    `scheduled_task_runs`（run_id/schedule_id/status/result_summary/error/...）

审计三步（一条 python -c 搞定，只输出小字段）：

```python
c.execute("select schedule_type,status,count(*) from scheduled_tasks group by 1,2")   # 分类统计
# 候选必须无在途运行：
c.execute("select count(*) total, sum(case when active_run_id is null or active_run_id='' "
          "then 1 else 0 end) no_active_run from scheduled_tasks "
          "where schedule_type='once' and status in ('completed','failed','cancelled')")
ids = [r[0] for r in c.execute("select schedule_id from ... order by status,created_at")]
print('|'.join(ids))    # 一行输出全部待删 ID，避免多行刷屏
```

分类规则：
- 周期性（schedule_type=weekly/daily，status=active）→ **保留**
- 一次性（schedule_type=once，status=completed/failed/cancelled）→ 删除候选
- 一次性 `active` → 见下方「过期未认领的 active 任务」，**不要盲目保护**
- 删除前确认候选 `active_run_id` 全为空、`last_run_status` 为终态，避免打断执行

### 2. 并行批量删除
- 按候选列表拆成多批（每批 10~20 个），同一批内所有 `scheduled_task` action=delete 调用**并行发出**
- 每个 delete 用 schedule_id 定位，逐个返回 {"status": "deleted"} 即成功
- 删除时**绝不触碰**要保留任务的 schedule_id（先把它列为受保护 ID）

### 3. 验证终态
- 再次 list 确认只剩预期保留的任务（count 等于保留数）
- 若 list 被去重拦截（见下），换参数签名再查

## 关键坑：duplicate_or_in_flight_action 动作去重

**现象**：同一动作签名（action + 参数组合）在一段时间内重复调用会返回：
`{"error": "duplicate_or_in_flight_action", "action_id": "<id>", "state": "succeeded"}`
- 例如本会话：先调 `list`（成功），后续再调 `list` 或 `list+include_completed:true` 均被拦截，报重复
- `sleep 5` 等待无法绕过（去重窗口更长）

**绕过方法**：调用一个**合法但参数签名不同**的变体：
- `{"action": "list"}` ↔ `{"action": "list", "include_completed": true}` ↔ `{"action": "list", "include_completed": false}`
- 三者互为不同签名，可轮流使用；`include_completed: false` 是实测有效的兜底查询
- 注意：返回内容可能不含 recent_runs 详情（include_completed=false 只给当前任务），验证"只剩 N 个任务"足够用

**原理推断**：去重基于规范化后的动作签名，而非业务内容；delete 动作因每次 schedule_id 不同，天然不冲突。

## 新判据：过期未认领的 active 任务（active ≠ 一定该保护）

`active` 里混着两类任务，必须区分：

| 类型 | 特征 | 处置 |
|---|---|---|
| 正常待执行 | run_at 在未来或刚到期，执行器有空闲/会认领 | 保留 |
| **过期卡死** | run_at 已过去数小时~数天，`due_count` > 0 却没人跑 | **上报用户定调**（若选"删除"，必须先读下方「治本」章节：删 active 会在 12~23 秒内被上游复生） |

判别字段（都在工具 list 的返回里，不需要额外查询）：
- `due_count`：已到期数量；若 due_count == active 任务数，说明全都到期没跑
- `employee_executor`：`{"status": "waiting_for_employee_executor", "active_count": 0, "queued_count": N}`
  —— `queued_count > 0` 且 `active_count == 0` 即"队列空转、无人认领"
- 每条任务的 `run_at` / `next_run_at` 与当前时间对比

本会话实测：剩余 10 个 once/active 的 run_at 全在 1~2 天前，`due_count=10`、
`queued_count=10`、`active_count=0` —— 全部过期未执行。

**关键态度**：这类任务**不要按"active 就保护"静默保留，也不要擅自删除**。
它们是"卡住的下游信号"而非垃圾数据，直接删会掩盖上游缺陷。正确做法是
向用户给出三选项并说明倾向：
  A. 先查根因（自主链 gate 为何关闭 / 执行器为何不认领），任务原样保留
  B. 收敛（只留 1~2 条信息量最高的，其余同质任务删掉）
  C. 全删，等 gate 修复后由自主链重新生成
另注意：若多条 active 任务标题高度同质（本会话是同一假说的 10 个复核变体），
这通常是"同质化任务堆积"问题在定时任务层的体现，值得在报告里点出来。
参考历史日志：取消的 run 常带 `自主链路 gate 已关闭，员工执行被取消`。

## 治本：删除 active 任务会被上游重新物化（决定性发现，必读）

实测：把 10 个 once/active 删掉后，**12~23 秒内被原样重建**，且新记录携带的
`autonomous_task_id` 与被删记录**逐字相同**。这说明 scheduled_tasks 只是**物化视图**。

- 真源：`~/.VoidCube/runtime/supervisor/autonomous_chain_store.json`
  （约 13 MB，结构 `{"version": int, "tasks": [...]}`）
  - `tasks[].status` 词表：`completed` / `failed` / `approved` / `cancelled`
  - 只有 `approved` 会被调度器扫描并物化成 scheduled_tasks；
    本会话 10 条 `approved` ↔ 10 条 `once/active` 一一对应
  - 判据：物化时会把该 task 的 `updated_at` 刷成当前时刻。
    若看到一批 `updated_at` 以 ~4 秒间隔递增、且时间与你的删除动作同步，
    就是调度器正在逐条重新物化（本会话实测 13:40:35→13:41:29 共 10 条）
- 结论：在 scheduled_tasks 层删 active 任务 = **除草不除根**，会陷入"删除→复生"循环。
  删一两次验证现象即可停止，不要反复删。

### 正确治本流程
1. **先备份（两样都要）**
   - `autonomous_chain_store.json` → 复制到 `runtime/supervisor/backups/chain-cancel-<stamp>/`
   - `scheduled_tasks.db` → 用 SQLite Online Backup API（`sqlite3.connect(src).backup(dst)`），
     不要用文件复制；备份后跑 `PRAGMA integrity_check` 确认
2. **用官方接口终结上游**（不要手改 JSON，理由见下）
   ```
   POST http://localhost:6002/autonomous-chain/tasks/{task_id}/decision
   body: {"decision": "cancel", "reason": "<说明>", "actor": "api_a"}
   ```
   - 契约源码：`src/voidcube/systems/supervisor/autonomous_task_review_service.py::decide`
   - 合法 decision 值：`planned` / `approve(d)` / `defer(red)` / `fail(ed)` /
     `pause(d)` / `cancel(led)` / `run(ning)` / `complete(d)` / `auto`
   - `cancelled` 是**终态**：再次 decide 会返回 `status=unchanged` 且 reason 提示不可重判
   - 成功返回 `{"status": ..., "task": {"status": "cancelled"}}`，逐条确认
   - 相关端点（都在 6002 的 openapi 里）：
     `/autonomous-chain/tasks`（GET 列表 / POST）、`/autonomous-chain/tasks/clear`、
     `/autonomous-chain-gate/status|activate|deactivate`
3. **再删已物化的 scheduled_tasks 行**（此时不会再生），走 scheduled_task action=delete
4. **持久性验证（必做，否则不算修复）**：在 2~4 分钟窗口内每 40 秒采样一次
   - chain store：`approved` 归零、`cancelled` 增加相应条数
   - scheduled_tasks：总数稳定在保留数，不再增长
   - 复生窗口只有 12~23 秒，所以至少要观察 2 分钟；本会话实测 4 个采样点全部稳定

### 为什么不要手改 JSON
运行中的 supervisor 持有内存状态，会在下一次物化/落盘时覆盖文件改动
（本会话里这些 task 的 `updated_at` 正是被运行进程刷新的）。decision 接口
改的是同一状态机的正规路径，安全且可审计（会写入 `decision_history`，
格式与历史 `actor=supervisor, status=cancelled, reason=...` 一致）。

## 删除的副作用：scheduled_task_runs 不级联

**实测**：删掉 77 个任务定义后，`scheduled_task_runs` 仍有 134 行
（76 completed / 49 failed / 9 cancelled）。删除只作用于 `scheduled_tasks`，
运行历史完整保留（可审计、可用于复盘失败原因）。

报告时必须向用户说明这一点，并让其决定是否额外归档 run 记录
（默认建议：保留，因为这是唯一的执行证据链）。

## 验证要点（双向核验）
1. **DB 侧**：再查一次分类统计，`total` 应等于保留数，且只剩预期分类
   （本会话：88 → 11，只剩 `once/active` 10 + `weekly/active` 1）
2. **工具侧**：调 `{"action": "list", "include_completed": false}`，`count` 应等于保留数。
   用 false 而不是 true —— 既避开去重窗口，也避免 recent_runs 大输出
3. 保留任务字段核对：schedule_type、status=active、next_run_at 正常；
   逐条比对保留名单与删除前登记的保护 ID 一致
4. 若接口仍带出 recent_runs（已删任务的执行日志），说明那是运行日志快照，
   不影响任务列表；同时说明 runs 表未级联清空（见上节）

## 附加建议（AGENTS.md 要求）
- 完成后给出下一步建议：如"历史运行日志归档"、"相关主题遗留任务的处理方向"
- 批量删除前先向用户/上下文确认保留名单，防止误删周期性任务
