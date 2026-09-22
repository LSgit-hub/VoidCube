---
name: system-health-audit
description: 系统化自检 VoidCube 全栈健康状态。当用户要求"检查系统问题/缺陷/健康状态"、"review your system"、"找问题"或主动进行全面诊断时使用。覆盖服务、记忆管道、outbox、body_runtime、安全扫描器、技能库等多个维度。
tags: [health, audit, diagnosis, self-check]
---

# VoidCube 系统健康自检流程

## 触发条件
- 用户说"检查系统问题/缺陷"、"find problems"、"review your system"
- 定期健康审查任务
- 多个子系统出现异常时需要关联分析

## 执行步骤（按层推进，每层收集量化证据）

### 第 0 层：全端口扫描与服务注册结构（新增，2026-09-02）
在健康检查前先扫描所有已知服务端口，确认无意外缺失或多余服务：
```bash
for port in 6000 6001 6002 6003; do
  echo -n "Port $port: "
  python -c "import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('localhost',$port)); print('OPEN' if r==0 else 'CLOSED'); s.close()" 2>/dev/null || echo "N/A"
done
```
- 6003 = goal_manager（可选，非核心但常见）
- 发现未预期端口（如 6004+）可能是残留进程，需追查
- **服务注册结构陷阱**：`curl localhost:6000/` 的 `registered_services` 可能是汇总字典（含 `total/agents/memory/supervisor/executor` 整数计数）而非服务详情；此时每个值是 int 而非 dict，调用 `.get('healthy')` 会 `AttributeError`。改用 `/health` 端点获取详情。

### 第 1 层：服务端口与健康检查
```bash
# 三个核心服务逐一探测
curl -s --max-time 5 http://localhost:6000/health
curl -s --max-time 5 http://localhost:6001/health
curl -s --max-time 5 http://localhost:6002/health
```
解析关键字段：
- gateway `/health` 可能返回 404（非标准端点），这不是故障，检查 `status` 字段
- memory `/health` 关注：`status`、`agent_outbox`（dead_letter/stale_reporter）、`transport_outboxes`（各 outbox 状态）、`maintenance.tier2_bridge`
- supervisor `/health` 关注：`body_runtime.healthy` + `violations`、`stellar.companion_loop_running`、`intent_state`

### 服务重启验证（serve CLI，实测 2026-08-26）
改过 launcher/启动链路后需真实重启验证（进程内冒烟测不了 spawn 序列）：
```bash
# 入口是 console script，不是 python -m voidcube（会报 No module named voidcube.__main__）
.venv/Scripts/voidcube.exe serve status   # 每次执行都用当前（新）代码 → 无重启即可验证 launcher 的进程归属/健康判定
.venv/Scripts/voidcube.exe serve stop
.venv/Scripts/voidcube.exe serve start    # 输出新 PID 表
# 重启后验证
curl -s http://localhost:6000/            # gateway 根路径 healthy（含 active_cli_executor）
curl -s http://localhost:6000/admin/routes    # 路由表设计上只有 /supervisor/ 与 /executor/ 前缀
curl -s http://localhost:6000/admin/services  # memory/supervisor 已注册且 healthy=true
curl -s http://localhost:6001/health | grep -o '"gateway_registration":{[^}]*'   # memory 侧 gateway 注册 healthy
# 无新增异常：按重启时刻过滤 errors.log
grep "2026-08-2X H:MM" C:/Users/<user>/.VoidCube/logs/errors.log | grep -iE "error|exception|traceback|failed" | head
```
要点：
- **会话 lease 自动恢复**：gateway 重启后 CLI 会话不丢，`active_cli_executor` 里 session 仍存活（stale_after_seconds=90 内的短暂中断可恢复），记忆有持久化不受影响。
- 重启属环境动作（会中断 gateway/播放器/supervisor companion 循环约 10-30s），执行前向用户说明。
- memory `/health` 的 `commit_revision` 是记忆数据库写入修订号，递增不能证明新代码已加载。部署核验须结合服务 PID/启动时间、模块来源路径及构建版本或源码提交标识；数据库 revision 只用于观察数据写入。

### 第 2 层：记忆数据库完整性
**表结构速查（实测 2026-08-26）**：`turns` 的正文列是 `text`（无 `content`）；
`compressed_memories` 无 `timestamp` 列，时间列是 `created_at` / `compressed_at`。
写临时查询前先 `PRAGMA table_info(<table>);` 确认列名，避免按直觉猜列名报错。
```bash
DB="C:/Users/%USERNAME%/.VoidCube/runtime/memory/memory.db"

# 压缩状态分布（核心指标）
sqlite3 "$DB" "SELECT compression_status, COUNT(*) as cnt, MIN(timestamp) as oldest FROM turns GROUP BY 1 ORDER BY cnt DESC;"

# 总量与时间范围
sqlite3 "$DB" "SELECT COUNT(*) as total, MIN(timestamp) as earliest, MAX(timestamp) as latest FROM turns;"

# Tier2 记忆数量
sqlite3 "$DB" "SELECT memory_type, COUNT(*) FROM compressed_memories GROUP BY 1;"
sqlite3 "$DB" "SELECT status, COUNT(*) FROM compressed_memories GROUP BY 1;"
sqlite3 "$DB" "SELECT status, COUNT(*) FROM profile_memories GROUP BY 1;"

# 积压评估：pending 中超龄条目
sqlite3 "$DB" "SELECT COUNT(*) as stale_7d FROM turns WHERE compression_status='pending' AND timestamp < datetime('now','-7 days');"
sqlite3 "$DB" "SELECT COUNT(*) as stale_14d FROM turns WHERE compression_status='pending' AND timestamp < datetime('now','-14 days');"
```

**调查触发条件，不是故障判据**：
- pending 数量大、候选年龄长或 Tier2 时间落后只触发调查；不能凭单次快照判定流水线停滞。按 scope 比较至少两个周期的候选量、处理量、入库量、last_succeeded_at 和新流入速度。
- partially_compressed 可以是合法的质量隔离结果，整批 quality_evidence.passed=false 不代表已接受事件绕过门禁；需检查 accepted/rejected event 和覆盖 turn 的实际写入边界。
- quality_quarantined 非零不等于新故障或持续增长。turn.timestamp 是原始对话时间，不是进入隔离的时间；统计拒绝/隔离趋势应使用 audit 的 evaluated_at 或实际状态变更时间（查询前 PRAGMA 确认字段）。
- 标题包含“评估/审计”的可见记忆只是待人工核验候选，不凭 LIKE 命中量断言污染，也不据此自动修改生产库。

**时区混用检测（2026-09-08 新增）**：
```bash
# turns.timestamp 时区分布
sqlite3 "$DB" "SELECT SUM(CASE WHEN timestamp LIKE '%+00:00' THEN 1 ELSE 0 END) AS utc_cnt,
               SUM(CASE WHEN timestamp LIKE '%+08:00' THEN 1 ELSE 0 END) AS local_cnt,
               COUNT(*) AS total FROM turns;"

# sessions.created_at 时区分布
sqlite3 "$DB" "SELECT SUM(CASE WHEN created_at LIKE '%+00:00' THEN 1 ELSE 0 END) AS utc_cnt,
               SUM(CASE WHEN created_at LIKE '%+08:00' THEN 1 ELSE 0 END) AS local_cnt,
               COUNT(*) AS total FROM sessions;"
```
若 +00:00 和 +08:00 都有数据 → 时区混用，需迁移。新写入用 UTC、存量是 +08:00 是常见模式。

**profile_memories 污染检测（2026-09-08 新增）**：
```bash
# profile 状态分布
sqlite3 "$DB" "SELECT status, COUNT(*) FROM profile_memories GROUP BY 1;"

# subject='project' 的污染量（诊断报告被当偏好写入）
sqlite3 "$DB" "SELECT COUNT(*) FROM profile_memories WHERE subject='project' AND status='superseded';"

# 查看污染样本
sqlite3 "$DB" "SELECT memory_id, substr(value, 1, 100) FROM profile_memories 
               WHERE subject='project' AND status='superseded' LIMIT 3;"
```
若 active ≤ 5 且 superseded > 50，且 value 含「诊断/修复/风险/依赖」等诊断报告片段 → 污染确认。

### 第 3 层：Outbox 健康
从 memory `/health` 的 `transport_outboxes` 和 `agent_outbox` 字段获取：
```bash
curl -s http://localhost:6001/health | python -c "
import sys,json
d=json.load(sys.stdin)
ao = d.get('agent_outbox', {})
print(f'Dead letter: {ao.get(\"dead_letter_count\")}')
print(f'Stale reporters: {ao.get(\"stale_reporter_count\")}')
for r in ao.get('reporters',[]):
    if r.get('dead_letter_count',0)>0 or r.get('stale'):
        print(f'  - {r[\"session_id\"][:30]}: dl={r[\"dead_letter_count\"]} stale={r[\"stale\"]} err={str(r.get(\"last_error\",\"\"))[:80]}')
"
```
**常见根因模式**：
- `HTTP Error 404: Not Found` + stale reporter → 旧版 API 路径被废弃，outbox 路由未清理
- companion outbox 全 stale → 可能不是故障（companion 是后台任务），但需确认
- api_a outbox dead_letter > 0 → 当前会话的记忆写入有阻塞

### 第 4 层：Tier2 Bridge 状态
```bash
curl -s http://localhost:6001/health | python -c "
import sys,json
d=json.load(sys.stdin)
bridge = d.get('maintenance',{}).get('tier2_bridge',{})
print(f'State: {bridge.get(\"state\")}')
print(f'Consecutive failures: {bridge.get(\"consecutive_failures\")}')
print(f'Eldest candidate age: {bridge.get(\"oldest_candidate_age_seconds\",0)/86400:.1f} days')
last = d.get('maintenance',{}).get('last_tier2_bridge_result',{})
if last.get('failed_checks'):
    print(f'Failed checks: {last[\"failed_checks\"]}')
    ve = last.get('quality_evidence',{})
    if ve:
        print(f'source_support: {ve.get(\"source_support\")} (threshold 0.35)')
        print(f'polarity_consistency: {ve.get(\"polarity_consistency\")} (threshold 1.0)')
        print(f'identifier_fidelity: {ve.get(\"identifier_fidelity\")} (threshold 1.0)')
        print(f'rejected_reasons: {list(ve.get(\"rejected_event_reasons\",{}).keys())}')
"
```
**质量门禁失败模式**（来自 endogenous-failure-analysis 技能）：
- `source_support` 低 → 事件提取缺乏原始 turn 支持（LLM 幻觉/过度推断）
- `polarity_consistency` 低 → 同一事件的正面/负面信号冲突
- `identifier_fidelity` 低 → 事件中的标识符（人名/术语）与 turn 原文不一致

### 第 5 层：Body Runtime 与工作树
```bash
curl -s http://localhost:6002/health | python -c "
import sys,json
d=json.load(sys.stdin)
br = d.get('body_runtime',{})
print(f'Healthy: {br.get(\"healthy\")}')
print(f'Active slot: {br.get(\"active_slot\")}')
for v in br.get('violations',[]):
    print(f'  VIOLATION: {v[\"code\"]} - {v[\"message\"]}')
"
```
常见 violation：
- `slot_git_head_unavailable` → worktree Git HEAD 不可解析，需先检查 `.git`、`git rev-parse HEAD`、来源仓库和 slot 元数据；不要直接 `git init`，因为空壳仓库无法恢复真实提交历史
- `slot_worktree_dirty` → 有未提交更改，可能阻塞安全隔离执行

若两个 violation 同时出现，优先按“过期或空壳物化”路径调查：核对 `worktree-origin.json` 的 source/source_commit、来源仓库是否存在，以及 `.git` 是否为无提交的自包含仓库。修复方向是重新物化 slot 或清除元数据后由 Supervisor 重建，不能用 `git init` 掩盖问题。

**过期物化根因模式（实测 2026-08-26）**：两个 violation 同时出现时，优先怀疑
slot 的来源仓库被移动/删除后从未重新物化：
```bash
# slot 元数据位置
ls ~/.VoidCube/runtime/body/slots/<slot>/
cat ~/.VoidCube/runtime/body/slots/<slot>/worktree-origin.json   # 看 source / source_commit
ls -d <source 路径>                                              # 来源仓库是否还存在
ls ~/.VoidCube/runtime/body/slots/<slot>/worktree/.git           # worktree 是否有 git
```
- 若 worktree-origin.json 的 `source` 指向的仓库已不存在（典型：仓库迁移目录后
  遗留旧路径），且 worktree 内无 `.git` → 过期物化。修复方向：重新物化 slot 到
  当前仓库路径，或清除该 slot 元数据让 supervisor 重建。**不要**在 worktree 里
  `git init`——来源仓库都没了，init 无意义。
- 注意 `meta.json` 的 `materialization_mode` 可能是 null，而
  `worktree-origin.json` 里是 `git_worktree`——以 worktree-origin.json 为准。
- 相关代码位置：`src/voidcube/systems/body_registry.py`（violation 判定约 346-400 行）。

**空壳 git 仓库变体（实测 2026-08-30，重要）**：即使 worktree **有** `.git`，
也可能是"空壳仓库"而非完好工作树。此变体极易被上面的"无 `.git` → 过期物化"
规则漏判。判定命令（进入 worktree 实测，不要只看目录是否存在）：
```bash
cd "C:/Users/<user>/.VoidCube/runtime/body/slots/<slot>/worktree"
git rev-parse HEAD 2>&1              # fatal: unknown revision → HEAD 无 commit
git status 2>&1 | head -10           # "No commits yet" + 只有 untracked 配置目录
git branch -a 2>&1                   # 可能只有 remotes/origin/*，本地无分支 commit
cat .git/config                      # origin url 可能已被改成当前仓库（如 F:/My_code/VScode_py/VoidCube）
cat .git/HEAD                        # ref: refs/heads/master 但该 ref 无 commit
ls -la .git/index.lock               # 0 字节残留锁文件也可能在，加剧 git 不可用
```
特征：`.git` 存在且**自包含**（含 objects/refs/logs，不是指向主仓库的 gitdir
文件）；本地 master 从无 commit（`No commits yet`），因此 `git rev-parse HEAD`
失败 → `slot_git_head_unavailable`；目录里只有若干点开头的配置目录且未跟踪 →
`slot_worktree_dirty`。此时**在 worktree 里 `git init` = 再建一个空仓库，无意义**。
修复方向同样：重新物化 slot 到当前仓库，或清除 slot 元数据让 supervisor 重建。

### 第 6 层：安全扫描器
```bash
# 检查 tirith 是否可用
which tirith 2>/dev/null || echo "tirith not found"
which cosign 2>/dev/null || echo "cosign not found"

# 统计日志中的 tirith 失败
grep -c "tirith spawn failed" ~/.VoidCube/logs/agent.log
```
**重要**：AGENTS.md 规则 3 要求涉及打包/技能的改动前必须运行退役集成扫描。tirith 失效意味着这条规则无法执行。

### 第 7 层：日志错误统计
```bash
# errors.log 概览
analyze_log --file ~/.VoidCube/logs/errors.log --mode summary

# agent.log 错误行
grep -cE "(ERROR|Error|error)" ~/.VoidCube/logs/agent.log
grep -oE "(HTTPError|409 Conflict|503|memory_service_unavailable|database is locked|tirith)" ~/.VoidCube/logs/agent.log 2>/dev/null | sort | uniq -c | sort -rn

# 具体错误类型分布
grep -oE "HTTP Error [0-9]+|WinError [0-9]+" ~/.VoidCube/logs/agent.log 2>/dev/null | sort | uniq -c | sort -rn
```

### 第 8 层：备份新鲜度
```bash
ls -la ~/.VoidCube/runtime/memory/backups/ | tail -5
stat -c "%y %n" ~/.VoidCube/runtime/memory/memory.db
```
备份落后活跃库超过 3 天即应警告。补备份用 SQLite backup API（禁止 cp）。

### 第 9 层：技能库审计（周期性，非每次必做）
当发现有多个技能可能命中同一请求、或发现废弃技能仍注册时：
```bash
# 统计技能数量
find ~/.VoidCube/skills -name "SKILL.md" 2>/dev/null | wc -l
find /f/My_code/VScode_py/VoidCube/skills -name "SKILL.md" 2>/dev/null | wc -l

# 检查 deprecated 技能
grep -rl "deprecated: true" ~/.VoidCube/skills/ /f/My_code/VScode_py/VoidCube/skills/ 2>/dev/null

# 检查描述重叠（简单版：按关键词分组）
grep -h "^description:" ~/.VoidCube/skills/*/SKILL.md 2>/dev/null | sort | uniq -c | sort -rn | head
```

## 诊断输出模板

交付结构化报告，按严重性排序：

```
## VoidCube 系统健康报告 [日期]

### 🔴 严重（立即处理）
1. [问题名] — [量化证据] — [根因假设]
   影响：[具体影响]
   建议：[修复方向]

### 🟡 中等（本周内）
...

### 🟢 低风险（观察/计划）
...

### 基线数据
- turns: total=N, compressed=M, pending=K, quarantined=Q
- outbox: dead_letter=X, stale=Y
- body_runtime: healthy=true/false, violations=[...]
- backup freshness: N days old
```

## 已知陷阱
- **既有 degraded 基线（2026-08-26 实测，别当新问题重复排查）**：memory 的 degraded 根因是 agent_outbox dead-letter（api_a 队列 HTTP 404 死信，最早 08-16）+ stale reporter；supervisor 的 degraded 根因是 slot-A body_runtime `slot_git_head_unavailable` + `slot_worktree_dirty`（过期物化，来源仓库路径已迁移）。两者与当日代码改动无关，健康报告中标注"既有问题"即可；tirith spawn failed WinError 2 警告也是既有（tirith 未安装）。
- **Windows 路径验证陷阱（2026-08-30 实测）**：检查 slot source 是否存在时，`ls -d` 在 Windows bash 中对不存在路径返回 exit 0 而非报错。正确做法：用 `&& echo exists || echo MISSING` 模式，或先 `test -d` 判断。例如 `F:\My_code\Traecode\VoidCube`（旧仓库路径）已不存在但 `ls -d` 不报错。
- **process list duplicate_or_in_flight_action（2026-08-30 实测）**：连续调用 `process action=list` 会被去重拦截。与 scheduled_task 相同的去重机制，换参数签名（如 `action=poll session_id=<id>`）可绕过，或直接跳过重复查询。
- **find + grep 管道信号错误（2026-08-30 实测）**：`find ... | xargs grep` 在技能库审计时出现 `grep: terminated by signal 13` 和多个 `Permission denied`，这是管道被截断的正常现象，非实际错误，检查结果仍可信。
- **admin/services 端点（2026-08-30 实测）**：`curl http://localhost:6000/admin/services` 返回不可访问，该端点可能仅在特定构建中存在或被移除。改用 `/health` 端点获取服务注册状态。
- **不要混合主机和沙箱数据**：`~/.VoidCube` 在主机和 Podman 容器中路径不同，明确标注 host/sandbox
- **memory `/health` 的 stale_reporter_count=0 不代表正常**：要看具体 reporter 列表，可能有 stale 但未计入计数的情况
- **companion outbox 全 stale 不一定是故障**：companion 是后台 loop，无用户交互时正常不 report
- **Tier2 bridge 连续失败 ≠ 永久失败**：可能是单次 LLM 调用超时，重试后恢复；检查 consecutive_failures 次数
- **quality_quarantined 的根因在提取逻辑**：不是 DB 问题，不要试图手工改 DB；检查事件提取提示词和 source_support 计算
- **skill_registry 入口零检查**：AGENTS.md 规则 3 要求零废弃入口，但 tirith 失效时此规则无法执行——这是系统级矛盾
- **eval 标签防护四段链路**：写侧→Tier1读→bridge出口→Tier2读，任何一段断裂都是缺口，需逐段验证
- **analyze_log 的 ~ 展开**：Windows 主机上 analyze_log 传 `~/.VoidCube/logs/errors.log` 可能报 File not found，改用绝对路径 `C:/Users/<user>/.VoidCube/logs/errors.log`
- **gateway 健康端点**：`/health` 与 `/api/health` 都 404 属正常（非标准端点）；根路径 `/` 返回 healthy JSON（含 registered_services、routes）
- **terminal 工具输出尾部**：`security_scanner_status: unavailable` 字段是 tirith 失效的旁证，与第 6 层检查互相印证
- **全仓库 grep 会超时**：Windows 主机上对仓库根目录做全量 grep（如找 violation 代码位置）容易超时（实测 120s），先限定到 `src/voidcube/` 或具体子系统目录
- **模型通道 401 定位**：错误信息 `User not found` 来自 LLM transport 通道；若 401 前出现 `Auxiliary auto-detect: using openrouter (...)`，问题在 auxiliary provider 的 key/账号而非主通道——用 `grep -B3 \\\"User not found\\\" ~/.VoidCube/logs/agent.log | tail -10` 定位
- **辅助压缩 key 过期的 401 变体（2026-08-30 实测）**：`User not found` 只是 401 的一种（openrouter 账号问题）。辅助**压缩**通道的 deepseek key 过期时，报的是 `Failed to generate context summary: ... 'api key: ****ired is invalid', type: 'authentication_error', code: 'invalid_request_error'`（注意是 `authentication_error` 而非 `User not found`）。定位命令用 `grep -B2 \\\"Authentication Fails\\\" ~/.VoidCube/logs/agent.log`。两者都是**辅助/压缩通道**问题（session 背景也在 scheduled task 里），与主通道 deepseek-v 无关，别当成主模型 key 失效去改主配置。
- **定时任务 API-A 超时模式（2026-08-30 实测）**：多个 scheduled_task 失败报告 `API-A background execution timed out after 600 seconds`，错误来源是子 agent 在 API-A 中执行时 LLM 调用超时。特征：任务状态 failed，elapsed_ms 接近 600000ms，error_code=null。排查方向：检查 API-A 的 LLM 配置（model/provider）和超时设置
- **native_image_input 参数错误模式（2026-08-28 实测）**：多个 scheduled_task 失败报告 `prepare_chat_messages() got an unexpected keyword argument 'native_image_input'`。这是 LLM 客户端接口不兼容——某个 provider adapter 调用 prepare_chat_messages 时传了 native_image_input 参数但目标模型不支持。排查方向：检查 endogenous_drive_prompts 或 llm_client 相关代码中的 prepare_chat_messages 调用签名
- **记忆压缩积压监控**：当 pending 压缩任务 > 200 条且 oldest pending > 7 天，需立即排查 Tier2 bridge 状态。根因可能是：a) Tier2 LLM 调用持续失败 b) quality_evidence 门禁持续拦截 c) 数据库锁竞争。修复方向：检查 tier2_bridge.consecutive_failures 和 last_tier2_bridge_result.quality_evidence
- **proposal_selection_drift 诊断**：当 reference_alignment >> cognitive_alignment（如 0.88 vs 0.52），说明引用节点绑定正确但任务类型/姿态选择失配。排查：检查 task_type priors 计算逻辑（endogenous_task_priors.py）和 cognitive_alignment 打分器（endogenous_materialization.py score_lm_proposal_cognitive_alignment）

## 本次审计新增经验（2026-08-31）

### Tier2 多 scope 部分成功不能计为全局失败
维护循环按 memory_domain/owner/workspace 分 scope 执行。若一个 scope `compressed`、另一个 scope `quality_rejected` 或超时，结果是**部分成功 + 局部降级**，不应递增全局 `consecutive_failures`，否则健康状态会夸大故障并触发错误告警。
- 维护结果应显式提供 `successful_scope_count` 与 `failed_scope_count`。
- 仍须保留失败 scope、质量证据和 quarantine 信息，不能用“部分成功”掩盖质量门禁失败。
- 只有所有实际执行 scope 都失败时，才累计全局连续失败。
- `quality_quarantined` 是提取质量问题，不是 SQLite 损坏；不要直接改生产 DB、降低阈值或删除积压来制造健康状态。

### scheduled_task 列表工具被动作去重阻断时的只读回退
若 `scheduled_task list` 反复返回 `duplicate_or_in_flight_action`，不要反复重试同一签名，也不要直接删除未知任务。可改用只读 SQLite 查询核对 `scheduled_tasks` 表结构和字段：
- 删除候选必须是 `schedule_type=once` 且 `status`/`last_run_status` 为终态；
- `active_run_id` 必须为空，并检查 `scheduled_task_runs` 中没有 running/leased 记录；
- 周期任务（daily/weekly）与 active 任务列为保护名单；
- 只按已核验的 schedule_id 调用 delete，再用不同参数签名重新验证。
数据库查询仅用于核验，不直接写任务库。

### 物化修复后的证据链
修复 body slot 后，至少重新验证 `git rev-parse HEAD`、`git status --short`、`worktree-origin.json`/`meta.json` 源路径，以及 Supervisor `/health` 的 violations；端口可达不能替代这些检查。

## 本次审计新增经验（2026-09-02）

### 全端口扫雷（6000-6003）
首次系统审计时确认了 6003 goal_manager 端口也开放且健康（`{"service":"goal_manager","status":"ok"}`），此端口在既有审计流程中被遗漏。建议把 6003 加入第 0 层必查列表。

### api_a outbox 死信模式（2026-09-02 实测，非新增基线）
api_a 队列 8 条死信，last_error=`MemoryProtocolError: Memory Service HTTP error 404`，最早失败约 1 天前。根因是旧版 API 路径被废弃但 outbox 重试逻辑未同步清理历史任务。症状：agent.log 短时间内密集出现 `WARNING plugins.memory.mem: Memory Service outbox delivery failed`（同一错误重复 ~8 次后静默）。处理方式：手动清理 dead_letter 队列或重启 memory 服务触发 drain。

### GPT provider Connection error 持续链（2026-09-02 实测）
日志中密集出现 `Streaming failed before delivery; falling back to non-streaming: Connection error`（约每 10-20 分钟一次），说明某后台任务（疑似 scheduled_task 或 auxiliary 通道）在用 gpt provider 的 `aixj.vip/v1` endpoint 做周期性请求但持续失败。此模式与主通道（agnes-ai）无关，定位用：
```bash
grep -B3 "Connection error" ~/.VoidCube/logs/agent.log | grep -E "scheduled_task|auxiliary|provider" | tail -20
```
修复方向：排查配置中 gpt provider 是否仍在使用，停用无用 provider 或修复 endpoint。

### quality_quarantined 积压诊断（2026-09-02 实测）
`turns` 表中有 135 条 `quality_quarantined` 记录，按日分布最新一条仍为今天。这些 turn 被质量门禁拦截后进不进 Tier2 也不被压缩。根因在 Tier2 bridge 的事件提取逻辑（见 memory-system-diagnostics 技能），处理方式不是手工改 DB，而是排查 bridge 配置与 LLM 辅助压缩状态。若 quarantined 持续逐日递增，视为压缩流水线受阻信号。

### Tier2 evaluation 标签污染量化（2026-09-02 初测，2026-09-04 重大修正）
**⚠️ 重要修正（2026-09-04）**：此前认为存在"启动时幂等 reconciliation 机制"，但 2026-09-04 实测 `grep -rn "evaluation" src/voidcube/systems/memory/` 返回 **0 行**，四段防护（写侧 tags/读侧过滤/bridge出口/Tier2过滤）全部未实现。以下统计为当前真实污染量，非"部分修复后残留"。

`compressed_memories` 中 `hidden=0` 的诊断/自检/评估类记忆：**20 条**（占总 Tier2 382 条的 5.2%）。包括：系统健康审计、技能注册表审计、记忆系统自检×6、能力审计等。**全部未被隔离，每次召回都可能命中。**

审计命令（2026-09-04 验证版，以 grep 结果为准，不要依赖旧版结论）：
```bash
# ① 先看防护代码是否存在（关键前置步骤）
grep -rn "evaluation" src/voidcube/systems/memory/ | head
# 若 0 结果 → 四段全部断裂，见下方"修复优先级"

# ② 统计当前污染量
python - <<'PY'
import sqlite3, os
db = os.path.expanduser('~/.VoidCube/runtime/memory/memory.db')
conn = sqlite3.connect(db)
total = conn.execute("SELECT COUNT(*) FROM compressed_memories WHERE hidden=0").fetchone()[0]
diag  = conn.execute("SELECT COUNT(*) FROM compressed_memories WHERE hidden=0 AND (title LIKE '%诊断%' OR title LIKE '%评估%' OR title LIKE '%审查%' OR title LIKE '%review%' OR title LIKE '%自检%')").fetchone()[0]
print(f'Tier2 hidden=0: {total} 总条, 其中诊断/评估类: {diag} ({diag/total:.1%})')
PY
```

**修复优先级（2026-09-04 实测）**：
1. P0：写侧 `add_turn_pair` 在 `plugins/memory/mem/__init__.py:574` 和 `supervisor/service_runtime.py:865` 两处主写路径，注入 evaluation 标签
2. P1：Tier2 读侧 `recall.py` 的 `_tier2_candidates` 加入 `AND NOT hidden=1 AND NOT (title LIKE '%诊断%')` 临时兜底过滤
3. P2：手动将现存的 20 条诊断类记忆 `UPDATE compressed_memories SET hidden=1 WHERE ...` 隔离
4. 长期：`compressed_memories` 表补充 `tags TEXT` 列，桥接层在写入时传递 tags，读侧用 tags 过滤替代 LIKE 兜底

### Tier2 重复记忆检测（2026-09-02 新增阈值）
6 组标题完全相同的重复条目（最高 10 条），影响召回质量和上下文窗口。检测命令：
```sql
SELECT title, COUNT(*) as cnt FROM compressed_memories
WHERE hidden=0 GROUP BY title HAVING cnt > 1 ORDER BY cnt DESC;
```
阈值建议：cnt > 3 即需告警；cnt > 5 严重。

### GPT provider Connection error 日志定位（2026-09-02 实操）
Connection error 每 10-20 分钟一次、间隔不规律（10:50、10:54、11:02、11:14、11:23...）不是固定定时任务，更像是多个后台任务各自独立触发。grep 时用 `-B3` 看上下文比只看错误本身更重要，以区分不同触发源。

## 本次审计新增经验（2026-09-10）

### Tier2 bridge 的 no_events_generated 需要区分两类根因
当前实测 Memory `/health` 可能整体为 `healthy`，但 `maintenance.tier2_bridge` 仍为 `failed`：
- `eligible_candidate_count` 有积压（本次 251）
- `consecutive_failures > 0`
- 多个 scope `status=no_events_generated`
- `quality_evidence.event_count=0`、`valid_event_count=0`
- `failed_checks` 通常包含 `event_coverage`、`no_valid_events`

不要直接把 `no_events_generated` 定性为提示词/事件校验 bug。它也可能是底层 LLM 调用、超时、连接或 JSON 解析异常被 `safe_complete_json` 一类封装静默转换为 `events=[]`。诊断顺序应是：
1. 先确认是否真正发起 LLM 请求；
2. 让底层异常可观测，区分调用失败与成功返回空事件；
3. 检查 `event_count`/`valid_event_count`；
4. 修复后以 `MAX(turns.timestamp WHERE compression_status='compressed')` 前移作为决定性证据，不能只看 pending/quarantined 分布变化。

### canonical Mem binding 错误必须关联进程/工作目录调查
若 memory.log/supervisor.log 大量出现：
```text
expected F:\\f\\Mem\\src\\memai\\model_config.py
loaded   F:\\My_code\\VScode_py\\VoidCube\\Mem\\src\\memai\\model_config.py
```
而当前环境变量和主进程 cwd 已指向主仓库，优先怀疑旧子进程、重复服务实例或某个启动分支仍在残留目录 cwd 中运行。应检查每个相关 PID 的 CommandLine、WorkingDirectory、启动时间，再决定是否重启；不要仅凭当前主实例健康就忽略正在持续增长的日志错误。

### 插件 provider 与 activate 生命周期契约不能混用
若日志反复出现 `插件 memory 入口 ... 缺少可调用 activate()`，但 memory provider 仍可注册且服务健康，说明 provider 注册路径和普通插件生命周期检查存在契约漂移。不要用空 `activate()` 消除 warning；先明确该入口应属于 provider 还是 plugin service，再统一 registry 判定与测试。

### Windows 路径安全扫描的盘符冒号误拦截
在 Windows 本地 backend 上，合法工作目录如 `F:\\My_code\\...` 可能被安全层错误报告为 `Blocked dangerous workdir`。若安全扫描器同时显示 unavailable，应把它作为安全降级/路径规范化缺陷调查，而不是项目命令本身危险。回归测试应覆盖 Windows drive-letter paths 与真正的 shell 元字符。

### 健康报告增加工作树、备份和缓存卫生指标
除服务健康外，建议固定报告：
- active body slot 的 `git status --short`、HEAD 与 source_commit 是否一致；
- 最新 SQLite 在线备份相对活跃库的天数，接近 3 天阈值时预警；
- 构建残留目录（build/dist/egg-info）；
- 非 `.venv`、非 `.git` 的幽灵 pyc 数量和 Python 版本混存情况。
这些通常不影响即时服务，但会造成源码漂移、旧代码加载和后续排障误判。

### 扫 Traceback 型静默缺陷（新增，2026-09-10）

只看 `error/warning` 计数会漏掉"有 Traceback 但被上层 except 吞成一句日志"的静默缺陷。
固定加一步：按时间戳切片，把 `AttributeError` / `'NoneType'` 这类关键字**连同其历史出现时间**列出来。

```bash
python -c "
import pathlib,re
p=pathlib.Path.home()/'.VoidCube/logs/errors.log'
lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
ts=re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
stamped=[(ts.match(x).group(1),i) for i,x in enumerate(lines) if ts.match(x)]
for i,x in enumerate(lines):
    if 'AttributeError' in x or \"'NoneType'\" in x:
        t=next((s for s,j in reversed(stamped) if j<i),None)
        print(f'{t} | {x.strip()[:110]}')"
```

**判据**：同一异常在不同日期反复出现 = **复发型缺陷**，值得当 P0/P1 修；只在某一次出现 = 偶发。
本轮据此发现 `'NoneType' object has no attribute 'strip'` 在 4 周内出现 4 次（08-05/08-08/08-21/09-10），
属长期静默降级，而非当次事故。注意：日志里非时间戳行（代码片段、续行）会污染按字符串比较的时间过滤，
必须先 `match` 出真正的时间戳前缀再切片。

### Tier2 `no_events_generated` 必须回读候选原文再定性（修正，2026-09-10）

看到 `event_count=0` **不要**直接判定为"提取逻辑/提示词 bug"，也不要直接改 DB 或降阈值。
先取 `last_tier2_bridge_result.scopes[].quality_evidence.sample_turn_ids`，回读这批 turn 原文：

```bash
python -c "
import sqlite3,pathlib,json
db=pathlib.Path.home()/'.VoidCube/runtime/memory/memory.db'
c=sqlite3.connect(db)
ids=json.load(open('sample_ids.json'))   # 从 health 的 sample_turn_ids 抄下来
for r in c.execute(f'select turn_id,speaker,length(text),substr(text,1,160),tags from turns where turn_id in ({chr(44).join(chr(63)*len(ids))})',ids):
    print(tuple(r))"
```

本轮实测：样本是「23 字元提示」「"OK"」「点歌寒暄 + agent 自我能力否认」——**0 事件是合法结果**，
真问题是**候选选择层按"最旧优先 + 无信息量过滤"持续选中无信息 chatter**，
而 pending 池里同时积压着 145 条实质内容（≥201 字符，其中 93 条 ≥800 字符）长期未被压缩。

因此诊断要分成两层，结论完全不同：
1. **选择层**（选错批次）→ 看候选项的信息量分布与 `sample_turn_ids` 原文；
2. **提取层**（选对了但提不出）→ 才去看提示词、质量门禁、`failed_checks`。

只看 `no_events_generated` 这个状态名会把两层混为一谈，导致修错地方。

### 一个方便的批量采集模板

多层审计时把 health 的四个端点一次拉完，避免多次往返：

```bash
python -c "
import urllib.request,json
gw=json.load(urllib.request.urlopen('http://localhost:6000/',timeout=25))
mem=json.load(urllib.request.urlopen('http://localhost:6001/health',timeout=25))
sup=json.load(urllib.request.urlopen('http://localhost:6002/health',timeout=25))
gm=json.load(urllib.request.urlopen('http://localhost:6003/',timeout=25))
print('status:',gw.get('status'),mem.get('status'),sup.get('status'),gm.get('status'))
print('body:',json.dumps(sup.get('body_runtime'),ensure_ascii=False))
ao=mem.get('agent_outbox') or {}
print('outbox:',{k:ao.get(k) for k in ['healthy','stale_reporter_count','dead_letter_count','pending_count']})
m=mem.get('maintenance') or {}
print('bridge:',json.dumps(m.get('tier2_bridge'),ensure_ascii=False))
print('scopes:',[(s.get('status'),len(str(s.get('quality_evidence')))) for s in (m.get('last_tier2_bridge_result') or {}).get('scopes',[])])"
```

## 本次修复经验（2026-09-10）

### 健康状态修复的实际执行顺序
当服务整体可达但健康报告显示 Tier2 bridge 失败时，先不要直接修改生产数据库或降低质量门禁。推荐顺序：

1. 盘点所有运行进程的 CommandLine/cwd，确认 canonical source 是否仍指向旧仓库；环境变量正确并不代表所有子进程已经继承了正确值。
2. 在修改代码前对活跃 memory.db 使用 SQLite Online Backup API 做可验证快照；禁止用文件复制备份 WAL 数据库。
3. 把“LLM 调用/协议解析失败”和“LLM 合法返回空事件”分成两种状态。`safe_complete_json` 之类的宽容 API 不应在事件提取链路中把异常转换成 `events=[]`。推荐增加 extractor 专用严格模式，而不是全局改变兼容 API。
4. 重启服务加载新代码后，主动调用公开的单批 bridge 接口；`run-all-rules` 可能因 cadence 返回 accepted 但实际跳过 bridge，不能用它作为唯一验证。
5. 若严格模式生效但结果仍为 `no_events_generated`，读取候选 turn 原文和 `/llm/health`，区分“模型返回合法空事件”与“错误仍被吞掉”。低信息样本不能强行压缩或降低质量门禁。
6. 验证压缩恢复时，**不要**把 `MAX(turns.timestamp WHERE compression_status='compressed')` 前移当作决定性指标（2026-09-10 修正，原表述有误）。bridge 按"最旧优先"排空积压，只要新压缩的 turn 比历史最大时间戳更旧，MAX 就不会变——此时压缩其实已经成功。正确指标组合：`compressed` 行数增长 + `compressed_memories` 计数增长 + 最近一次 scope 的 `status=compressed` 且 `failed_checks=[]` + `last_succeeded_at` 非空。

### Memory 插件与 provider 生命周期边界
如果 `plugins.memory.mem` 同时被通用 plugin registry 和 Agent runtime 使用，先确认两套生命周期契约：

- registry 要求模块级 `activate(manager, config)`；
- `MemoryProvider` 要求实例级 `initialize(session_id)`/`shutdown()`。

最小修复可以增加模块级 no-op adapter，但不得在其中重复创建或初始化 provider。长期方案是让 registry 明确区分 provider 插件和普通插件，避免用空入口掩盖边界设计问题。

### Windows workdir 安全校验
工作目录是路径值，不是 shell 源码。Windows drive path 的 `:`、反斜杠和 UNC 前缀不应因通用 allowlist 被拒绝。应对 workdir 做 shell/control 字符拒绝（`; | & $ \` < >`、换行、NUL），同时允许 Windows/POSIX/UNC 路径。Tirith 命令扫描不可用与路径语法校验是两个独立维度，不能混为一个失败原因。新增验证时至少覆盖合法 Windows drive/UNC 路径和恶意 shell 字符。

### 日志和健康快照的时间语义
重启后 health 中的 `last_tier2_bridge_result` 可能仍是旧快照；主动 maintenance 请求也可能因 cadence 被跳过。必须结合请求开始/完成时间、rules-status 和新日志时间判断结果是否来自本次执行。历史日志中的错误计数不能当作重启后的新故障，需按重启时间切片。

### 当前已验证的修复门禁
本次修复曾验证：Mem extractor 25 个测试、memory health 60 个测试、插件/Memory 34 个测试、terminal/workdir/Tirith 27 个测试，以及汇总 154 个主仓库测试全部通过；架构 gate、compileall、packaging contract 和 wheel verification 也通过。后续同类修复应保留“代码测试 + 服务重启 + API 冒烟 + 顶线业务指标”的四层证据链。

## 本次审计新增经验（2026-09-10 第二轮）

### 静默失败类缺陷要专门排查：子进程输出变成 None
审计时不要只看「有没有 ERROR」。本轮在 `errors.log` 里发现带 Traceback 的
`AttributeError: 'NoneType' object has no attribute 'strip'`（`checkpoint_manager._run_git` 的
`git add -A`），根因是 Windows 下 `subprocess.run(text=True)` 用 cp936 解码，子进程输出含非法
字节时解码异常在后台线程被吞、`run()` 仍返回但对应流为 `None`。
完整机制、复现配方与修法见技能 `system/python-windows-subprocess-none-stream`。
审计要点：这类缺陷**不报错、只是内容变空**，普通 ASCII 单测覆盖不到；发现后要顺手
`rg -n "text=True" src/` 量化影响面（本轮 112 处 / 约 40 文件）。

### 顶线业务指标不前移时，要怀疑「选错了输入」而不是「算法坏了」
Tier2 压缩停滞 14 天（`MAX(compressed)` 不动）时，抽查 bridge 实际候选批次原文比读代码更快定位：
本轮抽样发现候选是 `"OK"`（2 字）、23 字元提示、点歌寒暄，即 **0 事件是合法结果，但候选选择
按「最旧优先 + 无信息量过滤」持续挑中最古老的无信息 chatter**。候选池 580 条里仍有 145 条
实质内容长期未被压缩。
结论：零事件路径加有界重试（让状态机收敛）与「选择层加信息量过滤」（让顶线指标前移）是
**两个独立缺陷**，只修前者不会移动 `MAX(compressed)`。

### 自动提交会把不相关改动混到一起并误述提交信息
本轮发现后台自动提交（疑似 session checkpoint / 技能审查的 `git add -A`）把 5 个产品代码修复
与 5 个新增技能合进同一个提交，而提交信息只写了「新增技能」。
审计建议固定检查：`git show --stat <最新提交>`，比对提交信息与实际改动范围；若不一致，
说明自动提交缺少路径白名单。这会让按信息 review 的人整批漏掉产品代码改动。

## 本次审计与修复新增经验（2026-09-10 第二轮）

### subprocess text 模式解码缺陷（真实缺陷类，已修复并加守卫）
`subprocess.run(..., capture_output=True, text=True)` 在 Windows 按 locale（中文环境常为 cp936）解码。子进程输出含该编码无法表示的字节时：
- 解码异常发生在内部 `_readerthread` **线程**里被吞掉（只打印线程异常），`run()` 仍正常返回；
- 但**对应的流变成 `None`** → 调用方 `result.stderr.strip()` 抛 `AttributeError`，且真实错误文本丢失。

实测复现：`encoding="cp936"` + 子进程 stderr 写 `b"\x80\xff\xfe"` → `stderr is None`；`encoding="utf-8", errors="replace"` → 正常返回 str。
历史上该 AttributeError 在 errors.log 出现 4 次（08-05/08-08/08-21/09-10），长期静默降级。

修复契约（不变量是 `errors=`，不是某个具体 encoding）：
- git/诊断路径 → `encoding="utf-8", errors="replace"`（git 输出本身是 UTF-8）
- 展示路径（git_display/status/clipboard/ssh 等）→ 保留 locale 编码 + 仅加 `errors="replace"`；**不要**强制 utf-8，否则 cp936 中文会变成替换符（显示回归）
- 所有此类调用都要用 `(result.stdout or "")` 兜底
仓库已全树收敛并新增 AST 守卫测试（`tests/test_subprocess_text_decoding.py`）断言 `src/voidcube` 下所有 `text=True` 调用必须声明 `errors`。

### Tier2 停滞的真根因：低信息旧会话垄断锚点（已修复）
现场特征：bridge 连续多个周期返回**同一批 2 条 turn**（如 "OK" + 23 字短提示），`no_events_generated` 永不收敛，`consecutive_failures` 持续增长。
根因：`select_candidate_turns` 用「最旧优先」锚定**一个会话**（`ORDER BY julianday(timestamp) ASC LIMIT 1` 取 session，再取该会话 ≤batch_size 条）。实测最旧端堆了 **15 个"短提示+OK"式会话（最长 23 字符）**，把 135 条 ≥200 字符的实质内容全堵在身后；每个周期只吃掉一个会话的 2 条，于是顶线指标长期不动。

诊断命令（按会话看信息量分布，判断是否被低信息会话堵住）：
```sql
SELECT session_id, COUNT(*) n, MAX(length(trim(text))) maxlen,
       SUM(length(trim(text))>=200) n_substantive
FROM turns
WHERE compression_status IN ('pending','retry_wait') AND memory_domain='agent_interaction'
GROUP BY session_id ORDER BY MIN(timestamp) ASC LIMIT 15;
```
修复：候选选择改为**信息量优先 + 可回退**——锚点条件追加 `length(trim(coalesce(text,''))) >= 40`，无候选时依次回退到"无信息量要求"与"无相关性要求"（新增 `low_information_fallback` 标志，与既有 `low_relevance_fallback` 同一模式）。注意：信息量条件**只能加在锚点查询上**，若加进批次查询会把含短 turn 的整批误排除。

配套修复（同一链路）：零事件批次必须推进 `_record_quality_rejection`（有界退避 → 3 次后 `quality_quarantined`），否则 turn 永远停在 pending 被反复选中。

修复后实测：bridge 选中的会话由 "OK+短提示" 变为实质内容，`events=1`、`status=compressed`、`failed_checks=[]`；`compressed` 计数 816→820、`compressed_memories` 411→415。

### 自动提交把不相关改动混入并误述（治理问题，非代码路径）
`7b655e3` 提交信息只写"新增技能"，实际裹进 5 个产品代码修复 + 3 个测试。排查结论：**产品代码里不存在这种"自动提交"**——`git add -A` 只出现在 `checkpoint_manager`（走 shadow 仓库的 `GIT_DIR`，不写真实仓库）与 `evolution_authoring/executor.py`（已带路径白名单）；88 个定时任务也无提交步骤。结论是**agent 经 terminal 工具自行执行 `git add -A && git commit`** 造成。治理方式：在 `AGENTS.md` 增加提交纪律（路径限定、禁止裸 `git add -A`、提交信息须描述实际改动），必要时再加 `pre-commit` 钩子强制。

### 质量门禁的"全或无"覆盖度会让实质批次永远无法压缩（已改为部分提交）
`min_event_coverage`（`Mem/src/memai/application/config.py` 的 `tier2_min_event_coverage`，默认 **1.0**）要求批次内**每个** turn 都被事件覆盖。实测实质批次常含少量天然无事件的 turn（确认、寒暄、纯工具往返），100% 覆盖无法达成，于是整批被拒——**连已验证的 event 一起丢弃**，顶线指标不动。

现场判据（`/health` 的 scope quality_evidence）：
```
event_count=4  valid_event_count=4  backlink_completeness=1.0
source_support=0.74  identifier_fidelity=1.0  polarity_consistency=1.0
event_coverage=0.684   failed_checks=['event_coverage','validated_event_coverage']
```
即"提取没问题，只是覆盖度不满分"。小批次（4 turn）常 coverage=1.0 而成功，所以**成功率结构性地取决于批次大小**。

修复（已实现，2026-09-10）：改为**部分提交**——只落库"已验证事件"及**被这些事件覆盖**的 turn，其余 turn 留在 `pending` 由下轮重新组批；`status="partially_compressed"`，审计状态记 `partial`。落库内容仍须通过除覆盖度外的**全部**检查，因此不降低质量门槛也不丢数据。注意：非覆盖度失败（如 `compression_ratio`）仍**整批拒绝**，不能让部分提交变成绕过门禁的后门。

判据：`tier2_min_event_coverage` 是配置项，`config.yaml` 默认未设置 → 运行在 1.0。若只想调阈值而不改代码，可在配置里设置，但那属于降低门禁，需明确权衡。

修复后实测：全量周期 `successful_scope_count=2 / failed=0`、`state=idle`、`consecutive_failures=0`、`last_succeeded_at` 非空；agent_interaction 走 `partially_compressed`（events=2/valid=2），companion 直接 `compressed`。

### 时间戳时区混用（已修复，2026-09-10）
现场：`turns.timestamp` 同时存在 `+00:00`（212 条）与 `+08:00`（1202 条）；`sessions.created_at/updated_at`、`turns.last_decay_at`、`turns_archive.timestamp`、`recall_traces.*`、`session_summary_sources.turn_timestamp` 同样混用；另有 3 列是无偏移的 `2026-09-07 05:05:46` 形态（第三种格式）。

危害只体现在**字符串比较**式查询：`timestamp < datetime('now','-7 days')`、`MAX(timestamp)`、`ORDER BY timestamp` 会因偏移差最多 8 小时而错序/错判。`julianday(timestamp)` 是偏移感知的，**不受影响**（bridge 选批用的就是它）。

修复：在 `MemoryDatabaseBootstrap._setup_schema` 增加 `_normalize_timestamp_timezones(cursor)`（`Mem/src/memai/migrations/schema.py`），把带非 0 偏移的值改写为规范 UTC ISO，**保持时刻不变**（`18:26+08:00 → 10:26+00:00`），幂等，无偏移值不猜测、原样保留。列白名单是模块级 `_TIMESTAMP_COLUMN_TARGETS`。
- 注意：`MemoryDatabaseBootstrap` 是 `dataclass(frozen=True, slots=True)`，**不能**用带注解的类级常量（会变成 slot 描述符并抛 `TypeError: 'member_descriptor' object is not iterable`），必须放模块级。
- 实测：启动即归一化 **6711** 条，所有目标列 `+08:00` 计数归零，服务 healthy；`turns.timestamp` 由 212/1202 变为 1414/0。
- 影响面提示：归一化后**原始字符串**展示的时间会从本地时间变为 UTC（如 UI 若直接打印 `timestamp`，会少 8 小时）；偏移感知的解析与格式化不受影响。排查显示差异时先确认展示层是否直接引用原始字符串。

先备份再执行；迁移在服务启动时自动运行一次，无需手工脚本。

治理方式（2026-09-10 实测结论：**只加文档规则不够**）：
- 先加文档规则（`AGENTS.md` 提交纪律）后，**同类事故仍然复发**——`58f1bca` 又把 bridge 三项改动 + 聚合改动 + 技能文件 + 26 个解码修复文件裹进一条只描述"子进程解码"的信息里。规则本身还恰好在这个违规提交里才被加入，说明该提交路径不读/不受文档约束。
- 因此必须用**可强制**的机制：仓库级 `.githooks/pre-commit` + `git config core.hooksPath .githooks`。守卫做确定性判断：暂存集合同时含 `skills/` 与（`src/` 或 `Mem/src/`）时拒绝提交，提示中给出按路径拆分命令与 `--no-verify` 逃生口。
- 验证方式：造两个探针文件（分别放 `skills/` 与 `src/`）暂存后 `git commit`，应返回非 0 且 `git rev-list --count HEAD` 不变；再分别单独暂存 `src/`、`skills/`、`Mem/src+Mem/tests` 三种情形，hook 应返回 0（代码+测试同提是正常做法，不能误拦）。
- 停用：`git config --unset core.hooksPath`。守卫只覆盖"技能 × 代码"混合这一条确定性规则，提交信息与改动范围是否一致仍需人工把关。

### 自审/诊断类 Tier2 记忆会污染召回（已做兜底隔离，2026-09-10）
现象：Agent 自审产生的"系统健康审计""记忆系统自检""技能库审计"等报告被当作持久记忆写入 Tier2，并在**无关提问**时被召回。实测 14 条（`hidden=0`）全部可召回。

为什么既有防护没覆盖：`_quarantine_evaluation_memories` 要求 source turn **带 evaluation 标签**；而这批记忆的 `source_turns` 要么为空（agent 直接写入），要么指向未打标签的普通 turn。`origin_type` 也无法区分（hidden=0 里 215/314 为 NULL）。

兜底实现：`MemoryDatabaseBootstrap._quarantine_diagnostic_memories`（`Mem/src/memai/migrations/schema.py`），按**标题特征词** `_DIAGNOSTIC_TITLE_MARKERS = ("审计", "自检")` 置 `hidden=1`，并写 `memory_deletion_audit`（audit_id 前缀 `diagnostic-quarantine-`）。可逆、不删除数据；幂等。
- 上线前务必做**误伤检查**：先 `SELECT title FROM compressed_memories WHERE hidden=0 AND (title LIKE '%审计%' OR title LIKE '%自检%')` 人工过一遍（本次 14 条全是自审类，无误伤）。
- 实测：hidden 111→125、visible 314→300、剩余未隔离自审类 0、审计记录 14；召回查询"记忆系统健康审计 自检"返回 **0 条**。
- **这是兜底不是终解**：长期应给 `compressed_memories` 加 `tags` 列、在写路径传播来源 turn 的标签、并在召回读侧按 tags 过滤（即"四段防护"）。在此之前不要用更宽的 LIKE 词表扩大打击面。

### 单次 `/tier2/compress` 的 403 未复现（2026-09-10）
曾观察到 `workspace_id='default'` + `memory_actor='api_a'` 返回 HTTP 403，复查时 `default` 与 `VoidCube` 均返回 200（`no_candidates` / `no_events_generated`）。结论：**不可复现**，疑似发生在服务重启窗口内（actor/workspace 尚未就绪）。遇到 403 时先确认服务启动是否已完成，再判断是否真是授权缺陷；不要据此改鉴权逻辑。

## 大规模更新后审计：证据分层与反证检查

适用于服务 healthy、全量测试通过，但怀疑功能没有真正闭环的复查。先做只读检查，不因诊断直接重启服务、修改生产库或启用屏幕采集。

1. **锁定版本和审计基线**：记录 `git rev-parse HEAD`、`git status --short`、近期 diff 和构建目录的检查前状态。终端/文件搜索优先使用专用工具，勿机械照搬历史技能里的 grep/ls/sed。
2. **按入口追完整链**：入口 → composition root → adapter → 结果归一化 → canonical writeback → UI/health 投影。检查主路径、错误返回、异步 accepted、重复请求和恢复重试。facade 内未调用 writeback 仅证明本层未写回；还须检查调用方、adapter 和 reconcile 是否拥有写回职责。用 fake adapter 返回 failed/accepted/completed 做无生产副作用的复现，不仅凭“executed”字面量判定已发生假成功。
3. **区分计数生命周期**：gateway activity 的进程计数可能重启清零，而 last_execute_at/recent_metadata 保留历史事件。plan 可能统计空规划轮次，execute 可能仅在终态 reconcile 时增加。把 counter 含义、启动时间、task/run 当前状态一起核验；plan>0 且 execute=0 不能单独证明执行链断裂。
4. **按消费分支核查 perception**：分别检查普通 CLI Agent、Supervisor companion 和 Auto 分支。全局查 `perception_context`、`_companion_perception_context`、`PerceptionQueryService` 及 payload 构造。在本次复查中，子审查曾误称 perception 完全未接模型，但主审查找到 `service_runtime.py` 的 companion payload 接线；正确结论只能限定到已检查的分支。默认 parser=None、懒加载或 Auto 排除也可能是产品/隐私设计，先核对契约再判缺陷。端到端测试应断言 fake model transport 实际收到上下文，不能只验证 HTTP 查询可用。
5. **对子审查执行反证核验**：零引用搜索不是动态调用不存在的证明。报告持久化重复/无限积压前，先读 store 的主键、upsert/事务和 runtime 的先重试后采集顺序；未经复现的风险标为待验证，不升级为确定故障。报告普通 Agent 缺功能前先确认这是用户要求而非未来扩展。
6. **验证不能被工具超时或管道掩盖**：用项目虚拟环境直接运行 `.venv/Scripts/python.exe scripts/run_ci_tests.py`，长任务设置 `background=true, notify_on_complete=true`，等待真实结束。不要使用 `pytest | tail; echo ...` 的最终 exit code 判断测试成功；管道可能掩盖 pytest 非零退出。120 秒工具超时不能证明测试死锁；本次相关套件延长等待后完整通过。只读审计也要明确全量、定向和未执行的验证范围。
7. **卫生检查排除自我污染**：wheel 构建会创建 build/egg-info 等，必须与检查前快照比较，不把本轮构建产物归为历史问题。__pycache__ 内 pyc 的源码在父目录，可用 `importlib.util.source_from_cache()` 求路径；普通 CPython import 通常不会仅凭无源码的 __pycache__/name.cpython-*.pyc 加载模块。孤儿缓存是卫生问题，除非证明 loader/import 路径确实使用它，否则不宣称旧代码正在运行。构建成功与 wheel verifier 通过不证明技能资产或干净安装交付完整，还须检查 wheel 条目和安装后资源解析。

8. **execution facade 必须做状态闭环**：正式 execute 入口不能把 adapter 返回统一包装成 `executed/completed`。先验证 canonical task 是否存在、状态是否可 claim，再建立 execution lease；adapter 结果归一化为 `completed/failed/running/awaiting_user_consent/handoff/rejected`，异常也必须写回 `failed`，不能留下永久 `running`。canonical task writeback 是权威状态，Gateway activity 只是观测投影。测试至少覆盖 standalone handoff、canonical success、adapter failure、不可 claim、lease 复用和 activity 记录；只断言 HTTP 200 或响应字面量不算执行闭环。

9. **perception 接线审计必须按消费分支反证**：分别核查普通 CLI Agent、Supervisor companion、Auto 分支；发现 `PerceptionQueryService` 或 `perception_context` 不等于所有分支都未接入。对已经接线的 companion，应断言 fake model transport 实际收到 payload，而不是只断言 HTTP 查询可用。可选 parser/backend 未配置时先标为“能力未启用/设计待确认”，不要为了消除 `parser=None` 自动安装模型或改变屏幕隐私授权；优先补 worker 的失败计数、退避、最后成功时间和结构化健康状态。

交付按“已复现缺陷 / 静态风险 / 设计待确认 / 改进建议”分栏；附当前版本、证据、影响范围和验证限制。不要将所有假设写成持久故障基线。

## 复用建议
- 每次系统自检后，将新发现的模式追加到 `endogenous-failure-analysis` 或 `memory-system-diagnostics` 技能的"已知根因模式"部分
- 如果发现了新的健康指标阈值，更新本技能的"关键阈值"段落
