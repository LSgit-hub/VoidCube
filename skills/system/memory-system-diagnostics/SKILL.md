---
name: memory-system-diagnostics
description: 系统化诊断 VoidCube Mem 记忆系统问题。当记忆服务不可用、mem_search 报错、记忆数据异常或怀疑记忆写入/持久化链路故障时使用。
---

# 记忆系统诊断

## 会话恢复与记忆召回的边界

如果 `mem_timeline` 能找到目标轮次，但 `/resume` 为空、错位或恢复到其他正文，不要据此判断记忆丢失。分别查询 `~/.VoidCube/runtime/memory/memory.db` 的 Tier1 与 `~/.VoidCube/state.db` 的 `sessions/messages`：前者证明记忆写入，后者才是 `/resume` 的权威正文。重点检查：

1. 目标 `sessions.id` 是否存在但 `messages` 为 0。
2. 较早会话是否在新会话创建后仍收到大量消息或更晚时间戳。
3. Agent 切换会话时，`SessionPersistence` 使用的动态 session ID 是否同步更新；仅更新 `agent.session_id` 不足以保证 SQLite 写入目标改变。
4. 数值 `/resume N` 的 recent 列表应按 `last_active` 排序，并以 `started_at` 作为稳定次级排序，不能只按创建时间排序。

确认 SQLite 写入归属问题后，应修复生命周期同步，不要从 Tier1/Tier2 反构 canonical transcript，也不要用新增 durable memory 掩盖恢复故障。

## 执行边界（必须先做）

先确定诊断代码运行在哪个命名空间。`execute_code`、`terminal` 的 Podman/Docker
后端通常使用独立的 `/root` 和 `~/.VoidCube`，其中没有主机的记忆数据库；
容器内的 `127.0.0.1:6000` 也不是 Windows 主机 Gateway。

```bash
python -c "import os, pathlib; print('VOIDCUBE_HOME=', os.getenv('VOIDCUBE_HOME')); print('HOME=', pathlib.Path.home()); print('TERMINAL_ENV=', os.getenv('TERMINAL_ENV'))"
```

报告必须明确标注 `host` 或 `sandbox`：

- `mem_search` 的主机可用性只能在运行 VoidCube CLI/AIAgent 的主机进程中验证，不能用沙箱内的 Python/终端结果代替。
- 沙箱内检查失败时，只能报告“沙箱无法访问主机记忆服务”，不能推断主机数据库不存在或记忆已丢失。
- 主机侧优先运行 `voidcube serve status`；必要时运行 `voidcube serve start` 后再做功能验证。

## 触发条件
- `mem_search` 返回错误 (HTTPError, memory_service_unavailable)
- 记忆数据看起来丢失或过期
- 怀疑记忆写入链路故障
- 记忆系统行为异常

## 诊断流程（按层推进）

### 第 1 层：功能验证
```bash
# 快速冒烟测试
mem_search(query="诊断测试", limit=1)
```
该调用必须来自主机 Agent 的真实工具回合。若返回 HTTPError，记录完整的
Gateway URL、HTTP 状态和执行边界，再进入第 2 层；不要只显示 `HTTPError`。

### 第 2 层：文件系统布局
```bash
# 主机规范路径（由 RuntimeLayout 定义）
ls -la ~/.VoidCube/runtime/memory/
find ~/.VoidCube/runtime -name "memory.db" -type f 2>/dev/null
```
规范数据库是 `~/.VoidCube/runtime/memory/memory.db`。项目根目录、`memories/`
目录和 body slot 下的数据库只属于迁移来源或历史残留，不能作为当前活跃库的判断依据。

### 第 3 层：数据库完整性

注意：当前 Mem schema 已无单块 `memories` 表。记忆数据分布在 `compressed_memories`（事件/场景/弧）、`profile_memories`（用户事实/偏好）、entity graph 和 FTS/向量索引中。

版本链与去重字段已存在，诊断时勿误判为"未实现"（2026-08 复查确认）：
- `compressed_memories.superseded_by`（单值，指向新版本 memory_id；`compressed_memories` 无 `supersedes` 列）、`profile_memories.supersedes`（JSON list）—— Tier2 的 supersedes 已闭环：写入端 `mem_remember` 会把被 supersede 的旧条目 `status='superseded'` + `weight*0.3` + 打 `superseded_by`；普通召回 `_tier2_candidates` 非 as_of 分支用 `status='active'` 过滤，旧条目被天然排除（不只是 as_of 双时态才消费）。真正缺口在 Tier1 turns 层无收敛——诊断/状态结论走 `add_turn` 存进 turns（compressed_to_tier2=0），不进 Tier2，同主题新旧结论在 tier1 并列返回。
- `turns.dedup_key` + 唯一索引 `(session_id, dedup_key)` —— 这是靠 `write_id/idempotency_key` 推导的幂等去重（只防同一条 turn 重复写入）。另有最终召回层 `_deduplicate_and_rank` 的 3-gram shingle 软去重（Jaccard≥0.88 / overlap≥0.92），但阈值只挡"几乎相同"文本，挡不住"同主题不同措辞"的结论并存。

```python
import sqlite3
conn = sqlite3.connect('活跃memory.db路径')

# 完整性
conn.execute('PRAGMA integrity_check')

# 表清单（全量）
conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")

# 核心业务表行数（逐表查询，避免 vec0 表报错）
core_tables = ['turns', 'sessions', 'compressed_memories', 'profile_memories',
               'entity_nodes', 'entity_edges', 'entity_memory_links',
               'memory_embeddings', 'turns_archive',
               'recall_traces', 'recall_feedback',
               'memory_promotion_candidates', 'memory_promotion_refs']
for tbl in core_tables:
    try:
        cnt = conn.execute(f'SELECT COUNT(*) FROM [{tbl}]').fetchone()[0]
        print(f'{tbl}: {cnt} 行')
    except Exception as e:
        print(f'{tbl}: 跳过 ({e})')
```

常见 pitfall：部分表（如 `memory_embeddings_vec*`）依赖 `vec0` 扩展模块，在裸 `sqlite3` 连接下会报 `no such module: vec0`。这是正常的 —— 只需跳过这些内部表，不影响核心诊断。

关键判断：
- `turns` 有数据但 `compressed_memories` 和 `profile_memories` 均为空 → 录制正常但压缩/持久化链路断了
- `compressed_memories` 最新日期远滞后于 `turns` → 压缩进程停滞
- `turns.compression_status` 分布可判断压缩进度：`SELECT compression_status, COUNT(*) FROM turns GROUP BY 1`（活跃 schema 的列名是 `compression_status`，枚举 compressed/pending/quality_quarantined/retry_wait；`compressed_to_tier2` 是旧列名，在新库上查询会报错）
- 压缩停滞的按日证据链：`SELECT substr(timestamp,1,10) d, compression_status, COUNT(*) FROM turns GROUP BY d, compression_status ORDER BY d DESC` —— 若某日起 compressed 归零而 pending 逐日积压，即压缩停摆的日期起点
- 全部核心表为空 → 服务根本没启动过

### 第 4 层：服务与网关
```bash
# 检查网关端口
ss -tlnp | grep -E '6000|6001|6002'
# 或
netstat -an | grep -E '6000|6001|6002'

# 检查 gateway 配置
grep -n "gateway_address" ~/.VoidCube/config.yaml
```
当前服务端口是 Gateway `6000`、Memory `6001`、Supervisor `6002`。
`memory.mem.gateway_address: ''` 是“继承系统默认 Gateway”的合法哨兵值，不能单独判定为故障；
只有实际主机侧 `/` 健康检查和 `mem_search` 都失败时，才判定网关未启动或不可达。

### 第 5 层：代码污染排查
这是最难发现的。症状：turns 正常写入但 memories 为空，日志中有奇怪的 mock 错误。

```bash
# 检查 PYTHONPATH
echo $PYTHONPATH

# 运行时加载路径
python -c "import sys; [print(p) for p in sys.path]"

# 搜索 mock 污染痕迹
grep -c "failing_decay\|fail_write\|fail_cognition\|Mock object" ~/.VoidCube/run/*.log

# 对比运行时源码路径
python -c "import sys; [print(p) for p in sys.path]"
```

根本原因模式：`PYTHONPATH` 指向测试源码或旧 worktree（含测试 monkeypatch），而非当前生产代码。
测试 mock 函数（`failing_decay`、`fail_write`）替换了真实的记忆写入路径时，必须先停止该测试进程，
再重启主机侧服务；不要在生产数据库上手工改代码或数据。

### 第 5.5 层：回收反馈闭环审计（recall_feedback）

`recall_traces` 记录每次召回结果，`recall_feedback` 允许用户标注相关性以训练排序。诊断召回有效性时应检查：

```sql
-- 反馈覆盖率
SELECT COUNT(*) as total, 
       SUM(CASE WHEN verdict='relevant' THEN 1 ELSE 0 END) as relevant,
       SUM(CASE WHEN verdict='irrelevant' THEN 1 ELSE 0 END) as irrelevant,
       SUM(CASE WHEN verdict IS NULL THEN 1 ELSE 0 END) as no_feedback
FROM recall_feedback;

-- 按意图查看命中/空结果分布
SELECT intent, status, COUNT(*) as cnt 
FROM recall_traces 
GROUP BY intent, status ORDER BY cnt DESC;
```

常见 pitfall：
- 反馈覆盖率极低（如 6/726 = 0.8%）→ 排序优化无法生效，weak_match 噪声持续污染上下文
- `recall_traces.status='hit'` 含 weak_match → 命中率高估，需结合语义分数判断实际质量
- 无 feedback 记录不能说明召回链路有问题，但说明排序没有学习信号

### 第 6 层：日志交叉验证
```bash
# 统计错误类型分布
grep -oE "(HTTPError|409 Conflict|503|memory_service_unavailable|database is locked)" \
  ~/.VoidCube/run/*.log | sort | uniq -c | sort -rn

# 查看记忆相关 agent 日志
grep -i "mem\|memory" ~/.VoidCube/run/*.log | tail -50
```

沙箱日志和主机日志必须分开标注，不能混合统计。

### 第 7 层：复查修复是否实质（git 历史验证）

用户报告"已修复，请复查"时，先走 git 历史定位改动，对照上次结论逐条核对，
不要通读整个代码库（低效，且易被"只改注释/变量名"的表面改动误导）。

```bash
cd <项目根> && git log --oneline -15      # 定位最近的修复 commit
git show <commit> --stat                  # 看改动文件范围
git show <commit> -- <关键文件路径>        # 看具体 diff，对照上次问题逐条核对
git status --short                        # 确认有无未提交改动
```

判断"实质修复 vs 表面改动"的关键信号：
- 核心评分/策略文件里真的改了权重、门槛、分支条件，而不是只改注释或变量名
- 新增了针对上次问题的回归测试（场景能对上，如"强相关结果优先于图谱邻居""身份邻居不得压过诊断记忆"）
- 有配套 config 字段、schema 迁移、健康指标暴露
- 关键函数签名真的变了（如评分策略从"无 query_relevance"变成"query_relevance 权重最大"）

确认代码层面改到位后，再进入第 8 层跑测试验证行为。

### 第 8 层：验证修复（运行测试）

记忆系统修复后必须用项目虚拟环境跑相关测试，否则修复只是"静态可见"，无法确认真实生效。

> 环境路径会变，不要照搬 compacted 上下文里的旧路径（可能已失效）。
> 先 `pwd` + `ls .venv` 确认，再选对解释器：
> - Windows 主机（真实开发机）：`/f/My_code/VScode_py/VoidCube` + `.venv/Scripts/python.exe`
> - Linux/沙箱：`/workspace` + `.venv/bin/python`

```bash
# Linux/沙箱
cd /workspace && .venv/bin/python -m pytest -q \
  tests/test_memory_recall.py tests/test_memory_entity_graph.py \
  tests/test_memory_domain_isolation.py tests/test_mem_memory_provider.py \
  tests/test_memory_health_signals.py 2>&1 | tail -40

# Windows 主机（Git Bash 下）
cd /f/My_code/VScode_py/VoidCube && .venv/Scripts/python.exe -m pytest -q \
  tests/test_memory_recall.py tests/test_memory_entity_graph.py \
  tests/test_memory_outbox_operational.py 2>&1 | tail -40
```

常见坑：
- 系统 `python` 通常没装 pytest；必须用项目 venv 的 python 跑（AGENTS.md 规定测试用项目 venv）。
- Windows 主机上 `.venv/Scripts/python.exe` 是正常 venv 结构，不是跨平台残留；只有"在 Linux 沙箱里看到 `F:\...` 路径"才是 `.pyc/__pycache__` 从开发机带进容器的残留，需清理后再跑。
- venv 缺 fastapi/pydantic 等运行时依赖时，所有测试会在 **collect 阶段**就 ImportError，根本跑不到断言。先 `import fastapi, pydantic` 验证依赖是否就绪。

## 自指污染防护审计（evaluation 标签链路）

背景：评估/诊断类会话会把诊断报告持久化进记忆，后续召回时自指污染自己。f1e682f 引入了 `evaluation` 标签来标记这类 turn 并在读侧排除。审计时按"写侧 → Tier1 读侧 → bridge 出口 → Tier2 读侧"四段逐段核对，**四段都通才算闭环**，任何一段断裂都意味着污染未被真正解决。

**2026-09-04 实测修正（重要）**：下列"已知代码位置"基于旧版代码，经实际 `grep -rn` 验证后发现与当前代码状态严重不符。**审计时务必以本次命令的实际输出为准，不要依赖本条历史注释做正确性推断**。

实测命令（以当前代码为基准）：
```bash
grep -rn "evaluation" Mem/src/memai/                         # 全量搜索 evaluation 关键词（记忆服务代码在 Mem/src/memai/，非 src/voidcube/systems/memory/）
grep -rn "tags" Mem/src/memai/application/memory_service.py  # 写侧 tags 是否被读取
grep -n "evaluation" Mem/src/memai/application/tier1_to_tier2_bridge.py  # bridge 出口
grep -rn "_EVALUATION_TAG" Mem/src/memai/application/recall.py          # Tier1 读侧
sqlite3 memory.db "PRAGMA table_info(compressed_memories)"                   # compressed_memories 列结构
sqlite3 memory.db "SELECT COUNT(*) FROM compressed_memories WHERE hidden=0 AND (title LIKE '%诊断%' OR title LIKE '%评估%' OR title LIKE '%审查%' OR title LIKE '%review%')"  # 当前污染量
```

**2026-09-04 早间实测结论（已被下方复核推翻，仅作历史存档）**：曾误判为上述命令全部返回 0 结果（除最后两条 SQL），据此断言"没有任何 `evaluation` 相关代码，四段全部未实现"。该结论源于**检索路径错误**——记忆服务实际代码在 `Mem/src/memai/`，而非 `src/voidcube/systems/memory/`。

历史 Tier2 污染统计（2026-09-04 早间）：`hidden=0` 的诊断/自检/评估类记忆 **20 条**（占总 Tier2 382 条的 5.2%）。

**2026-09-04 复核实测（以当前代码为准，推翻上文"未实现"结论）**：四段防护链在当前源码中**已全部落地**：
- 写侧 tags 传递：`Mem/src/memai/application/memory_service.py` `request.tags` 透传（约 4328/4452 行），非硬编码空 tags
- Tier1 读侧：`Mem/src/memai/application/recall.py` `_tier1_candidates` 含 `NOT EXISTS(...='evaluation')`（约 1700-1702 行）
- Tier2 读侧：`Mem/src/memai/application/recall.py` `_EVALUATION_SOURCE_EXCLUSION_SQL` 经 `source_turns JOIN turns` 双层溯源过滤（约 34-42 行）
- bridge 出口：`Mem/src/memai/application/tier1_to_tier2_bridge.py:39`；实体图谱 `Mem/src/memai/indexes/entity_graph.py:36` 同款过滤
- `_EVALUATION_TAG = "evaluation"` 定义于 recall.py:131

**剩余的真正缺口（判断闭环与否的唯一判定点）**：写侧**未做自动判定注入**——`evaluation` tag 依赖调用方显式传 `request.tags`。诊断类 turn 若不带 tag，进 Tier2 时无标签可挡。审计时以"调用方/agent 侧是否对诊断类 turn 带 evaluation tag"为准，**不要**再判断"读侧是否接线"（早已接线）。

结论模板：闭环 = 写侧标签可达（含自动注入与否）∧ Tier1 召回过滤 ∧ bridge 候选过滤 ∧ Tier2 读侧过滤 ∧ 历史污染隔离。四段读侧过滤现已实现；逐段核对时若只检索 `src/voidcube/systems/memory/` 会得到"全部断裂"的错误结论，务必检索 `Mem/src/memai/`。

## 压缩停摆专项：LLM 健康检查超时链（2026-08-20 实测确认）

### 判别：自动压缩停摆 ≠ Tier2 断更（2026-08-28 实测，重要）

停摆期间 `compressed_memories` 可能**依然每日新增**——Tier2 有独立于
turns 自动压缩链的显式写路径（origin_type 分布实测：`agent_explicit_memory`=68、
`governance_task`=15、`experience_synthesis`=6、`identity_revision`=1，
来源含 `agent_explicit_memory` 时即为 `mem_remember` 等显式写入）。判断标准：
`SELECT memory_type, origin_type, compressed_at FROM compressed_memories ORDER BY compressed_at DESC LIMIT 10`，
若最新日期接近今天且 origin 为显式类型 → **长期记忆/身份/摘要并未受损**，
停摆仅影响 turns→Tier2 自动压缩链，维护任务可降级为"已知问题记录"而非紧急处置，
与 `turns.pending` 积压同时观察（pending>200 阈值仍适用，但不等于身份层数据丢失）。

**双通道脱节的量化判别（2026-09-02 实测新增）**：压缩停摆可表现为 turns 标记与
Tier2 产出完全脱节——turns 压缩标记停流，但 compressed_memories 仍在每日更新。
用两个 MAX 直接对比即可判定：
```sql
-- turns 侧：最后一次真正回写 compressed 的时间（若 7 天前即断流）
SELECT MAX(timestamp) FROM turns WHERE compression_status='compressed';
-- Tier2 侧：Tier2 是否仍在产出
SELECT MAX(created_at) FROM compressed_memories;
-- 两者差 > 数天，且 pending 逐日积压 → turns 自动压缩链断，Tier2 靠显式路径存活
```
这一对 MAX 对比能一眼看出"主压缩断流但 Tier2 未断更"的双通道脱节，避免误判为
"系统整体停摆"。注意两次查询时间粒度/时区：turns 用 `timestamp`（本地时区，
`compressed_memories` 用 `created_at`（UTC），对比时仅看日期跨度即可。

### 根因链 B：LLM 健康但事件提取产出 0 事件（2026-08-22 实测确认，与 A 并列）

超时链不是压缩停摆的唯一根因。实测第二种模式：`LLM health check passed` 连续
207 次（模型 deepseek-v4-flash，最后一条在日志行 434170），但质量门仍全拒，
pending 逐日积压。**这是 Tier1→Tier2 事件提取阶段的质量问题，不是服务/DB/超时
故障。**

证据链判别（如何区分模式 A 与 B）：
1. 统计 `grep -c "LLM health check passed"` 与 `grep -c "LLM unhealthy"`。
   若 passed 高频且最后一条 passed 晚于/接近最后一条 rejected → 排除超时链，
   走模式 B。
2. 按行号提取全部 rejected 记录：
   `grep -nE "quality gate rejected" memory.log | tail -20`。
   若拒绝原因从 `degraded_fraction`（8/16 前后）转为清一色 `no_valid_events`
   → 事件提取产出 0 事件，指向 Tier1→Tier2 bridge 的提取逻辑/提示词/解析问题。
3. 确认 config.yaml compression 段正常（enabled=true, threshold, target_ratio,
   protect_last_n）→ 配置不是原因。

修复方向（模式 B）：在隔离环境排查事件提取逻辑/提示词/解析，或按既有
"压缩重试调度"机制（`memory_promotion_candidates` 表 + turns 的
`lifecycle_retry_count/lifecycle_retry_after/lifecycle_last_error` 列）手动重试，
不要直接改生产 DB。

### 根因链 C：辅助压缩通道回退到不可用模型（2026-09-02 实测确认，新增）

与 A/B 并列的第三种压缩停摆根因：turns 自动压缩依赖**辅助/摘要压缩通道**
（`auxiliary_client`），该通道连主 provider 失败后回退到备用模型，而备用模型已
被项目策略标记为不可用，导致 context summary 永远生成失败并暂停 600s。

日志特征链（agent.log，一次压缩尝试的完整链条）：
```
voidcube.infrastructure.providers.auxiliary_client: Auxiliary compression:
  connection error on gpt - falling back to openrouter (备用模型)
root: Failed to generate context summary: Requested provider or model is
  unavailable by project policy. Further summary attempts paused for 600 sec
```

判别（与 A/B 的关键区别）：
1. 主通道（deepseek-v）正常，但**辅助/摘要通道**报 `connection error on gpt`，
   且随后的 `falling back to openrouter (...)` 指向一个被策略禁用的模型。
2. 统计 `grep -c "unavailable by project policy"` agent.log 与
   `grep -c "Auxiliary compression"`——若命中且紧随 `falling back to openrouter`，
   即辅助回退链配置失效。这是**配置/策略层** bug，不是超时（A）也不是事件提取（B）。
3. 佐证：`LLM health check passed` 存在且健康（排除超时链 A），`no_valid_events`
   不是主导（排除 B）——回退链模型禁用是独立根因。

修复方向：剔除回退链中的禁用/失效模型，或令辅助通道直接使用可用的主 provider，
避免回退到被策略拦截的模型。触及模型/协议/配置 → 改后需跑退役集成扫描与测试，
避免下次会话把旧回退链当主逻辑。

### 根因链 A：LLM 健康检查超时链（2026-08-20 实测确认）

`request_timeout_seconds`(或客户端默认超时)过小 < 实际 LLM 推理耗时 → `LLM health check` 必超时 → 压缩降级为启发式模式 → 事件标记 degraded → 质量门禁 `max_degraded_fraction=0.0`(默认值,任何降级即拒绝)拒绝整批 → turns 永不压缩,pending 逐日积压。这是**配置/运维层故障**,不是压缩代码逻辑 bug。

### 证据链（按序取证）
1. **DB 按日压缩分布**：`SELECT substr(timestamp,1,10) d, compression_status, COUNT(*) FROM turns GROUP BY d, compression_status ORDER BY d DESC` → 找 compressed 归零的起点日期。
2. **日志行号定位**：memory.log 无 ISO 时间戳,用行号估算时间：
   - 最后 `LLM health check passed` 行号 vs 首个 `LLM unhealthy` 行号(故障切换点)
   - `quality gate rejected` / `produced no events` 的末次行号(压缩活动停摆点)
   - 尾部 N 行内无 rejected 事件 → worker 已无有效产出
3. **量化超时矛盾**：`curl -s --max-time 60 -X POST http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"<模型名>","messages":[{"role":"user","content":"ping"}],"max_tokens":5}' -w "HTTP %{http_code} 总耗时 %{time_total}s" -o /tmp/ollama_test.json` 实测推理耗时,与配置超时对比。注意 curl 落盘 + 单条命令,避免管道组合被安全扫描拦截。
4. **确认超时来源**：读 `Mem/src/memai/application/memory_service.py` 的 `_check_llm_health`(每压缩周期 re-probe,`asyncio.to_thread(client.complete_json)`)与 `_resolve_mem_llm_client`,确认超时取自哪个配置字段。

### 配置漂移 vs 服务重启时序（定位"配置矛盾"的关键手段）
当 config.yaml 明文的 LLM/超时与运行时日志不符时,对比**进程创建时间**与 **config.yaml 修改时间**：
```bash
wmic process where "ProcessId=<PID>" get CreationDate   # 进程启动时间
stat -c "%y" ~/.voidcube/config.yaml                     # 配置修改时间
```
若 config 修改晚于进程启动 → 服务仍加载旧配置,改配置后未重启。修复方向：重启服务;或调大超时(如 2.0s → ≥8s,注意该值影响所有同步调用,健康检查可单独设更长超时);或将 `max_degraded_fraction` 提到 0.2~0.3 容忍单次降级(需权衡质量底线)。

### 运行时源码定位（区分活跃源码与旧构建产物）
```bash
wmic process where "ProcessId=<PID>" get CommandLine   # 看进程从哪个路径加载
grep -rln "<特征日志字符串,如 'LLM health check'>" <候选目录> --include="*.py"  # 命中即活跃源码
```
`build/lib/` 可能是旧构建产物、`src/voidcube` 是 agent/宿主侧,都不要当作记忆服务本体;以进程 CommandLine + 特征字符串双重确认。

### 修复验证
重启后观察:日志重新出现 `LLM health check passed`,下一周期出现压缩产出;DB 侧 `compression_status='compressed'` 计数回升。若 24h 内零产出,检查是否有告警/熔断缺失(本次故障静默 7 天才被发现,是运维观测缺口)。

## "保留 N 天原文不压缩"配置下的停滞判别（2026-09-02 实测新增，重要）

用户若配置了"保留最近 N 天会话原文、期间不压缩"（等价于 `protect_last_n` /
retention window），则 `turns.pending` 会**常态保持**最近 N 天级别的积压量，
此时 `pending > 200` 阈值会产生**误报**，不可直接当作压缩停滞。

判别停滞的唯一可靠方法：按对话日构建"升入 vs 已压缩 vs 未消化"表，并用窗口边界
判断——窗口内（>= 今天 - N 天）的 turn 保持 pending 是**设计的**，窗口外才应被压缩：

```sql
SELECT substr(timestamp,1,10) d,
       COUNT(*) t,
       SUM(CASE WHEN compression_status='compressed' THEN 1 ELSE 0 END) cd,
       SUM(CASE WHEN compression_status='pending'   THEN 1 ELSE 0 END) pd
FROM turns GROUP BY d ORDER BY d DESC LIMIT 14;
```

判读关键：
- 窗口内 pending 占比：`>= 配置 N 天` 内的 pending 占全部 pending 的比例接近 100%
  （实测 94%）即保护窗口在正常工作。
- 窗口边界日（今天 - N 天）通常是"已压缩/未压缩"的精确分界（实测 08-26 只剩 2 条
  pending，更早已全压缩，完美吻合 7 天窗口）。
- 若某条 pending 日期落在窗口外（超出 N 天）却始终未压缩、且 quality_audit 对其
  批次持续 `no_valid_events` 拒绝，才是真正的压缩卡点；窗口内的 pending 一概视为
  配置预期，不要再当成"停滞故障"去修。

**决定性代码证据：`_tier1_candidates`（Mem/src/memai/application/recall.py:1697）**
过滤条件是 `compression_status != 'compressed'` —— 即 Tier1 召回检索的是**未压缩**的
turn 原文（含 pending），排除已压缩的。主召回流程（recall.py:517/547）再 `extend`
合并 Tier2（Tier2 = 已压缩语义记忆）。因此"保留原文不压缩"**不会造成记忆查询盲区**：
N 天内活动内容走 Tier1 原文召回，N 天前走 Tier2 精炼语义召回，mem_search 两层都覆盖。
判断配置是否影响查询，以这条 `!= 'compressed'` 过滤为准，不要凭直觉认为
"未压缩 = 查不到"。

（补充：`include_tier1 and plan.intent != 'identity'` 时并入 Tier1，`tier2`
在 515-521 行并入；两者顺序 Tier2 先、Tier1 后。）

## 健康指标基准（2026-08-17 实测参考值）

正常健康系统的参考基线：
- `turns.pending` < 50（压缩积压低）
- `profile_memories.active` > 0（用户偏好层生效）
- `compressed_memories` 最新日期与 turns 最新日期相差不超过数小时
- `recall_traces.failure` 占比 < 5%
- `memory_embeddings` 覆盖 > 80% 的活跃 tier2 记忆
- `entity_nodes` 随对话逐步增长，不与 turns 数量级差过大

异常阈值（需介入）：
- turns.pending > 200：压缩流水线明显停滞 —— **仅适用于无保护窗口配置**；
  若设置了"保留 N 天原文不压缩"，该阈值失效，须改用上文"保留 N 天原文不压缩"配置下的
  停滞判别（窗口内 pending 属预期，窗口外 pending 才是真卡点）
- profile_memories.active = 0 且 superseded > 50：偏好层完全失效
- recall_traces.failure > 10：召回链路有系统性错误
- 503 错误 > 100/天：服务稳定性问题
- 409 Conflict > 1000/天：并发写入冲突频繁

## 备份新鲜度检查与安全快照（2026-08-22 实测）

备份落后是独立于压缩停摆的第二风险源：memory.db 活跃更新（mtime 新鲜）但
backups/ 最新完整备份可能落后数天（实测落后 6 天，8/16 vs 8/22），灾难恢复
窗口大开。诊断与维护时必查：

```bash
# 最新完整备份 vs 活跃库时间差
ls -la "C:/Users/lishuo/.VoidCube/runtime/memory/backups/" | tail -10
stat -c "%y %n" "C:/Users/lishuo/.VoidCube/runtime/memory/memory.db"
```

补备份**禁止用 cp/文件复制**（WAL 模式下拷库不安全），必须用 SQLite 官方
backup API——只读源库、WAL 安全、可在服务运行中在线执行：

```python
import sqlite3, time
src = r"C:/Users/lishuo/.VoidCube/runtime/memory/memory.db"
name = f"memory-maintenance-snapshot-{time.strftime('%Y%m%dT%H%M%S')}.db"
dst = rf"C:/Users/lishuo/.VoidCube/runtime/memory/backups/{name}"
s, d = sqlite3.connect(src), sqlite3.connect(dst)
s.backup(d)          # 官方在线备份 API
d.close(); s.close()
# 校验快照（必须做）：integrity + 行数核对
v = sqlite3.connect(dst)
print(v.execute("PRAGMA integrity_check").fetchone()[0])   # 期望 ok
print(v.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
v.close()
```

命名遵循系统惯例（`memory-<timestamp>.db` 风格），快照落 backups/ 不干扰
系统自身备份轮转。

## 维护结论回写 Mem 的官方 API 契约（2026-08-28 实测）

Auto 员工/维护任务的结果**必须回写 Mem**，禁止走 `media_display` 交付面板
（治理规则，实测返回 `autonomous_employee_delivery_forbidden`）。可行路径是
直接 POST 记忆服务 HTTP API，不需要也无法导入 `voidcube.plugins.memory.mem`
（`src` 布局下该导入路径不存在，实测 `No module named 'voidcube.plugins'`）。

### 端点与必填字段（经 422 试错确认）

```bash
POST http://127.0.0.1:6001/turn-pairs
Content-Type: application/json
```

空 body `{}` 会返回 HTTP 422，错误详情直接列出必填字段：
`session_id`、`user_content`、`write_id`（三者均必填）。完整 schema 用
`GET http://127.0.0.1:6001/openapi.json` 查 `TurnPairCreate`，可选字段：
`assistant_content`(默认空串)、`tags`、`owner_id`(默认 local-user)、
`workspace_id`(默认 default)、`memory_actor`(默认 api_a)、
`memory_domain`(默认 agent_interaction)、`metadata`。

### 成功响应与验证

```json
{"status":"stored","session_id":"...","write_id":"...","write_status":"committed",
 "turn_ids":{"user":"<uuid>","agent":"<uuid>"},"commit_revision":509,"replayed":false}
```

- `write_status=committed` 即写入成功；`commit_revision` 会自增（508→509）可作为落库佐证。
- 回写后**必须再验证落库**：直接查 memory.db `turns` 表
  `SELECT speaker, substr(text,1,80), timestamp FROM turns WHERE session_id='<sid>'`，
  应看到 user/agent 两条 turn。
- session_id 建议用自描述唯一值（如 `20260828_0031_memory_maintenance_<task_id>`），
  不必预先建 session，turn-pairs 写入会随之创建。

## 时间维度诊断（time_dimension）专项（2026-08-21 新增）

当 `mem_timeline` 返回空、不完整或数据断裂时，按以下步骤排查：

### 根本查询
```sql
-- 时间轴数据完整性
SELECT MIN(timestamp) as earliest, MAX(timestamp) as latest FROM turns;
SELECT COUNT(*) as total, compression_status FROM turns GROUP BY compression_status;

-- 最近 N 天每天的有效 turn 数
SELECT substr(timestamp, 1, 10) as day, COUNT(*) as cnt FROM turns 
WHERE compression_status = 'compressed' 
GROUP BY day ORDER BY day DESC LIMIT 30;
```
注意：turns 表的时间列是 **`timestamp`**（2026-08-28 实测 `PRAGMA table_info(turns)` 确认），
用 `created_at` 查询会报错——本技能旧版此处误用 `created_at`，已修正。

### 已知结构变化（2026-08-14 重构后，2026-08-22 复核修正）
- 时间轴不再使用独立的 `memory_timeline` 表，而是从 `turns` 实时聚合
- ~~`time_summaries` 表（预聚合缓存）不存在，不要查询~~ **已过时**：
  2026-08-22 实测 `time_summaries` 表存在且活跃（day/session 级摘要带
  supersede 链：session→day→week）。schema 演进后该表已恢复/重建，
  以 `sqlite_master` 实际表清单为准，不要凭旧结论跳过查询
- 时间戳必须用 ISO 格式（带时区偏移），非 ISO 格式会被 `time_dimension.py` 跳过

### 常见故障模式
| 症状 | 根因 | 修复方向 |
|------|------|----------|
| 某天 turn 数骤降为零 | 当天服务中断/未创建新会话 | 检查 logs/supervisor.log 是否有中断记录 |
| 时间范围正确但 count=0 | `compression_status` 全为 `pending` 或 `retry_wait` | 检查压缩流水线状态（见"压缩停摆专项"） |
| 时间戳不是 ISO 格式 | 旧数据写入格式不兼容 | 需数据迁移脚本，不要手工改 DB |
| `mem_timeline` 返回空但有 turns | 查询时区与数据时区不一致 | 确认数据库使用 UTC+8，查询传入正确的 date 字符串 |

### 跨时区 / 时区语义专项（2026-09-05 实测，重要）

**核心教训：naive（无时区偏移）时间戳的真实语义因表/字段而异，不能一刀切当 UTC 或当 +08。**
本技能旧文"确认数据库使用 UTC+8"属误导——只对 turns/sessions 成立，对 Tier2 的 timespan 是 UTC。

判别方法（快速、只读）：取同一行一个 naive 字段 + 一个已知 aware 的 sibling 字段
（如 compressed_memories.timespan_start naive vs created_at aware），分别按"naive 视作 UTC"
和"naive 视作 +08"算出与 aware 的时差，看哪个接近 0h。

实测结论（Mem 库 2026-09-05）：
- turns.timestamp / turns_archive.timestamp / sessions.created_at / sessions.updated_at
  的 naive 值 = 本地 +08:00（早期 `datetime.now()` 写入），迁移应补 +08:00。
- compressed_memories.timespan_start / timespan_end 的 naive 值 = UTC 语义
  （对比 created_at：按 UTC 解读 ~0h 偏差、按 +08 解读 ~8h），迁移应补 +00:00。
- 写库根源：add_turn / add_turn_pair 曾用 `datetime.now().astimezone()`（跟随主机时区），
  已统一为 `datetime.now(timezone.utc)`（方案A）。
- 写 turns 表的入口只有 add_turn / add_turn_pair；turns_archive 的 timestamp 保留原 turn(aware)、
  compressed_at 用 UTC；identity_seed / identity_experience / sqlite_repository 全用
  `datetime.now(timezone.utc)`，均健康，无非 UTC 写入口。
- `parse_timestamp` / `_parse_utc_timestamp` / `_parse_review_datetime` 对 naive 均按 UTC 处理，
  因此对 turns 的 naive 会错位 8h——修复靠"迁移 naive 变 aware"，不靠解析器臆测时区。

**SQLite julianday() 对时区偏移的行为（实测）**：
- 带偏移 ISO（aware）：`julianday(timestamp)` 会把偏移换算成 UTC 儒略日再比较 → 跨时区/混存安全。
  `julianday('...+08:00')` 与等值 `...+00:00` 相等。
- 无偏移 ISO（naive）：按 UTC 处理 → 与真实本地时刻错位 8h。
- 结论：跨时区安全的 SQL 比较应写成 `julianday(timestamp) >= julianday(?)` 且参数带偏移。

**timeline_view（mem_timeline 后端，/turns/timeline）跨时区圈天 bug（2026-09 修复）**：
- 病根：用 naive `dateT00:00:00` 字符串 + SQLite 字符串比较圈"某一天"，未按
  config.time_summary_timezone 换算边界 → 跨天错位。实测 +08 查 09-05 会把
  UTC 09-05 16:00（= +08 09-06）错圈进 09-05。
- 修复：`day_period(date, timezone_name=config.time_summary_timezone)`（返回含偏移边界）
  + `WHERE julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)`
  + `ORDER BY julianday(timestamp)`。

**tzdata 硬依赖**：`resolve_time_summary_timezone`（time_summary.py:200）无 tzdata 时只兜底
Asia/Shanghai / UTC / Etc/UTC 三个，其他时区抛 ValueError。本机实测缺 tzdata
（`ZoneInfo('Asia/Shanghai')` 直接 ZoneInfoNotFoundError，Asia/Shanghai 仅靠 fixed_offsets
兜底才可跑）。跨时区部署必须 `pip install tzdata`。聚合时区可经 env
`MEMORY_TIME_SUMMARY_TIMEZONE` 注入（voidcube system.py:149），config 默认 Asia/Shanghai。

### 跨时区部署专项（timezone 混用，2026-09-05 实测确认，新增）

**症状**：部署到不同时区主机后，`mem_timeline` 错位 / 每日曲线出现 8 小时突跳 / 时间轴聚合异常。

**核心根因：三层时区基准解耦，写入跟随主机本地时区。** 实测证据（同库三张表现状）：

- `turns.timestamp`（新）= `datetime.now().astimezone().isoformat()` → **跟随主机本地时区**（当前 +08:00 aware）
  - `Mem/src/memai/application/memory_service.py` `add_turn`(~4338) / `add_turn_pair`(~4448)
- `compressed_memories.created_at` / `profile_memories.created_at` = `datetime.now(timezone.utc)` → **UTC aware**
- 时间轴聚合桶 = `config.time_summary_timezone`（`Mem/src/memai/application/config.py:28`，默认 `"Asia/Shanghai"`）

三层时区（存储的 turns 本地 / Tier2 UTC / 聚合 Asia/Shanghai）混用，主机一时区一变，写入就漂移。

**子根因 B：naive 历史数据被当 UTC 解析（错位 8h）**
`time_summary.py::parse_timestamp`(307-314) 与 `memory_service.py::_parse_utc_timestamp`(160-172)：
```python
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)  # naive 当 UTC
```
但历史 naive 数据（如 `2026-07-09T10:23:00.083480`）实际是本地 +08:00 生成，被当 UTC 后转 Asia/Shanghai 桶即**前移 8 小时**（上午 10:23 → 记成 18:23）。

**子根因 C：tzdata 缺失导致跨时区部署直接崩**
`time_summary.py::resolve_time_summary_timezone`(200-214) 用 `ZoneInfo(tz)` 查 IANA 库，仅硬编码 `Asia/Shanghai`/`UTC`/`Etc/UTC` 三个固定偏移，环境无 `tzdata` 时其他时区（如 `America/New_York`）抛 `ValueError`。Windows 主机最易触发。

**修复方向**：
- A. 写库统一 UTC：`add_turn`/`add_turn_pair` 改 `utc_now().isoformat()`（`schema.py:13` 已有 `utc_now()`），与 Tier2 对齐，主机时区不再影响写入。
- B. 修 naive 语义：一次性迁移给 `tzinfo is None` 的 timestamp 补历史时区（本机 +08:00），并把解析函数对 naive 从"当 UTC"改为"按配置时区兜底"或直接抛错。
- C. 时区单一来源：存储时区也从 `config.time_summary_timezone` 统一取，部署时 config.yaml 显式设置，不依赖主机系统时区。
- D. tzdata 保障：部署/镜像固定 `pip install tzdata`；`resolve_time_summary_timezone` 补充常用业务时区或统一清晰报错。
- E. 验证：迁移后一天内 turns 应变 `+00:00`（方案A）或统一 `+08:00`；`mem_timeline` 各天连续无 8h 突跳；在非 +08:00 主机（`TZ` 环境变量模拟）冒烟测试写库/分桶/查询。

诊断入口：先查 `turns` 的 `timestamp` 是否 mixed（`SELECT DISTINCT` naive vs aware），再查 `compressed_memories.created_at` 是否 UTC，两者基准不一致即坐实三层混用。

### 修复落地实测（2026-09-05 执行确认，推翻上文 B 方向——重要）

上文"修复方向 B（把解析函数对 naive 按配置时区兜底 / 统一 +08）"**经实测是错误的**，因为 **naive 语义因表而异**，解析器无法臆测统一时区：

**关键实测证据**：对齐同一 memory_id 的 `timespan_start`（naive）与 `created_at`（UTC aware）——`compressed_memories.timespan_start/end` 的 naive 值按 UTC 解读才 ~0h 偏差（按 +08 解读偏 8h），即它是 **UTC 语义**；而 `turns.timestamp`/`sessions.created_at` 的 naive 值是**本地 +08:00** 语义。两者**相反**。`_cmem_row_to_dict` 直接把 timespan 当字符串返回、不经 parse_timestamp，且 `parse_timestamp` 实际只处理无 naive 的 `time_summaries.period_start/end`。

**如何判断某表 naive 的真实语义**（务必先做再改单一时区假设）：
```sql
SELECT memory_id, timespan_start, created_at FROM compressed_memories
WHERE timespan_start LIKE '2026-%' AND timespan_start NOT LIKE '%+%' LIMIT 15;
```
用 Python 解析 naive `timespan_start` 分别按 `+00:00` 和 `+08:00` 解读，看哪个更接近同一行的 aware `created_at`（差 ~0h 者即真实语义）。此为只读，必须在备份库或只读连接上做。

**正确修复 = 差异化数据迁移 + 写库统一 UTC，不改解析器**：
- A. 写库统一 UTC（见上）—— 保证今后不产生 naive。
- B（修正）. **数据迁移**，白名单 + **按字段偏移**，把存量 naive 全变 aware：
  - `turns.timestamp` / `turns_archive.timestamp` / `sessions.created_at` / `sessions.updated_at` → 补 `+08:00`
  - `compressed_memories.timespan_start` / `timespan_end` → 补 `+00:00`
  - **解析器保留 `tzinfo=timezone.utc` 不变**（naive 语义因表而异无法统一臆测；迁移后数据全 aware，naive 分支不再触发）。
- 迁移**必须白名单**，禁全列扫描，否则误报：`timeline_parent_id`（值是 ID 如 `scene_xxx`）、`time_summaries.timezone`（值是时区名 `Asia/Shanghai`）、`memory_index_metadata.updated_at`（值是 `%Y-%m-%d %H:%M:%S` 空格格式非 ISO T）都是**非 naive 时间戳**的假阳性。校验值必须匹配 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}`且无偏移。迁移前用 SQLite backup API 备份，apply 后复核全库 naive=0 + integrity=ok。

**SQLite 日期函数对时区偏移的行为（实测）**：`julianday()`/`date()` 会把带偏移的 aware ISO 字符串**正确换算成 UTC 再比较**（`julianday('...T15:00:00+08:00') == julianday('...T07:00:00+00:00')`；naive 被当 UTC，`julianday('...T15:00:00')` ≠ 上两者）。因此压缩选批 `julianday(timestamp)<julianday(cutoff)` 对 aware 数据（无论存 +08 或 +00）都正确，不会错位；只有 naive 会错。

**净效果**：方案 A（写库 UTC）是唯一代码改动；解析器 diff 归零；方案 B 落地为纯数据迁移。测试用 venv 跑 `Mem/tests/test_temporal.py test_maintenance.py test_query_planner.py test_recall_planning.py test_self_identity_experience.py test_repository_and_benchmark.py test_incremental_repository.py test_query_interface.py test_pipeline.py test_compression_policy.py`（48 passed，前 21 + 后 27）。

## 架构自检方法（2026-08-19 新增）

当用户要求检查 agent 文件架构或验证系统状态时，按以下步骤执行：

### 步骤 1：确认 src 布局规范
```bash
ls -la src/voidcube/
# 应包含：domain/, application/, runtime/, infrastructure/, interfaces/, systems/, extensions/
```

### 步骤 2：验证旧包已删除
```bash
find . -type d -name "VoidCube_app" -o -name "VoidCube_cli" -o -name "VoidCube_core" 2>/dev/null
# 应无结果（.venv 除外）

python -c "import sys; sys.path.insert(0, 'src'); import VoidCube_app" 2>&1
# 应报 ImportError
```

### 步骤 3：检查模块导入
```bash
python -c "
import sys
sys.path.insert(0, 'src')
import voidcube.runtime.agent.runner
import voidcube.interfaces.cli.commands.registry
print('OK: 核心模块可导入')
"
```

### 步骤 4：查找残留引用
```bash
# 检查 src 下的 Python 文件是否还有旧包引用
find src -name "*.py" | xargs grep -l "from VoidCube_app\|from VoidCube_cli\|from VoidCube_core\|^import agent$\|^import tools$" 2>/dev/null
```

### 步骤 5：检查日志路径映射
```bash
# 确认日志文件实际位置
ls -la ~/.VoidCube/logs/ 2>/dev/null || ls -la /path/to/logs/
# 注意：沙箱环境和主机环境的日志路径可能不同
```

### 步骤 6：环境兼容性检查
```bash
# 确认当前运行环境（Windows/Linux）
python -c "import sys; print(sys.platform)"
# Windows: .venv\\Scripts\\python.exe
# Linux: .venv/bin/python
```

### 常见问题模式
| 症状 | 根因 | 修复方向 |
|------|------|----------|
| 架构扫描脚本语法错误（Non-UTF-8） | Windows venv 在 Linux 沙箱中执行 | 切换到实际运行环境验证 |
| 日志文件找不到 | 路径映射错误（~/.VoidCube/logs 在不同环境指向不同位置） | 明确标注 host/sandbox 边界 |
| 旧包仍可导入 | 遗留兼容层或 PYTHONPATH 污染 | 清理导入路径，检查 sys.path |
| 模块导入失败 | 路径配置错误或依赖缺失 | 检查 src/ 布局，验证 __init__.py |

## canonical mem binding 路径冲突（2026-09-06 实测新增）

症状：memory.log 尾部**反复刷屏**：
```
RuntimeError: memai import source does not match the canonical shared binding:
  expected F:\f\Mem\src\memai\model_config.py
  loaded  F:\My_code\VScode_py\VoidCube\Mem\src\memai\model_config.py
```

**机制**：`src/voidcube/infrastructure/gateway/service_launcher.py` 的 `_verify_canonical_mem_import_source()`（约 657-684 行）在启动 `memory` 服务时调用 `validate_canonical_mem_source(config.supervisor.execution.git_repo_path)`，算出 `expected = git_repo_path / Mem/src/memai/model_config.py`，再与 `Path(model_config.__file__).resolve()`（`actual`）比对，不一致即 `raise RuntimeError`。

**根因**：`config.supervisor.execution.git_repo_path` 的取值来自 `os.getenv("SUPERVISOR_GIT_REPO", config.supervisor.execution.git_repo_path)`（system.py:588-590）。若被指向一个**遗留的测试/临时 git 仓库副本**（如做过跨时区部署测试时 clone 的 `F:\f`），而 `mem-source-binding.json` 的实际绑定（`source_path`）指向主工程（如 `F:\My_code\VScode_py\VoidCube\Mem\src`），两者不一致即触发。

**判别与修复**：
```bash
# 1. 看规范绑定 vs 实际绑定的冲突
cat ~/.VoidCube/runtime/memory/mem-source-binding.json    # source_path 是"实际绑定"（主工程）
grep -rn "SUPERVISOR_GIT_REPO" 项目/.env config.yaml      # git_repo_path 被谁指向测试副本
# 2. 确认被指向的副本是残留（看是否含 Mem/ .pytest_cache 等测试痕迹）
ls -la F:/f/ && ls -la F:/f/Mem/src/memai/model_config.py
```
修复：把 `SUPERVISOR_GIT_REPO` / `git_repo_path` **切回主工程绝对路径**。服务本身可能仍 healthy（uvicorn 走得通），但 canonical 源判定失效、依赖该校验的启动路径将来会被 `raise` 阻塞。属"新增/暴露的配置冲突"，改配置后需重启记忆服务。

**注意**：`execution` 配置段在项目里可能有多个来源（config 明文 / env / Supervisor 侧），逐处核对，别只看 config.yaml。

**根因再修正（2026-09-06 晚间实测，重要）**：`F:\f` **往往不是被显式配置**。排查 `config.yaml`、`.env`、User/Machine/Process 三级环境变量、`runtime/supervisor` 状态文件、进程 CommandLine（`os.chdir(主工程)`）**全部搜不到** `F:\f`。**决定性复刻实验**（判断真相的唯一硬证据）：用项目 venv 完全复刻启动流程 `os.chdir(主工程)` + `load_VoidCube_dotenv(force_reload=True)` + `get_config()` + `validate_canonical_mem_source(git_repo_path)`，得到 `git_repo_path='./'` 且 resolve 到**主工程**——即**存活主实例本身没有故障**。真正根因是**启动 memory/supervisor 的另一个（可能是被 gateway 拉起的）子进程其 cwd 是残留克隆目录 `F:\f`**，导致相对路径 `"./"` 被 `Path(source_root).resolve()/Mem/src` 解析成 `F:\f\Mem\src`，与 actual（主工程 editable 安装）不一致 → raise。属**隐患**（未来在残留目录重启会被此校验卡死），而非当前主服务故障。

**修复**：`system.py:588` 明确 `os.getenv("SUPERVISOR_GIT_REPO", config.supervisor.execution.git_repo_path)`——设**用户级环境变量**为绝对路径即可：`setx SUPERVISOR_GIT_REPO "F:\My_code\VScode_py\VoidCube"`（优先级最高，彻底摆脱 cwd 影响）。**注意**：`setx` 只对后续新进程生效，**已在运行的服务需重启继承**；且因存活主实例本用主工程（无实际故障），此报错只是隐患，重启后所有启动路径都用绝对路径才根治。

## 修复核验纪律：状态分布搬家 ≠ 真修复（2026-09-06 实测）

用户报"已改进，请复查"时，**不要只看压缩状态分布的占比变化**，必须盯**决定性顶线指标 `MAX(compressed)` 是否前移**。实测警示：某轮改动后 `quality_quarantined` 从 137 骤降到 2（表面好转），但 `pending` 从 225 暴涨到 392、`MAX(compressed)` 仍钉在 08-27 纹丝不动 —— 这是**把被拒记录"搬家"到 pending 重试，根本没有修复压缩链**。

**核验三条纪律**：
1. **决定性指标**：`SELECT MAX(timestamp) FROM turns WHERE compression_status='compressed'` 是否前移。只有这个动才是真正愈合；`pending`/`quarantined`/`retry_wait` 的分布变化都可能是假象。
2. **对齐 commit → 问题**：`git log --oneline` 列出近期提交，逐条映射到上次报告的问题（如"事件提取重构"对应对 P0）。一旦看到"改了某逻辑结构但对顶线指标无影响"，即怀疑"治标不治本"。
3. **进程是否加载新代码**：对比**进程启动时间**（`powershell (Get-Process -Id <PID>).StartTime`）与 **commit 时间**。进程启动晚于 commit → 已加载新代码（排除"没重启"）；但仍无效果 → 问题在别处。

**修复后验证标准**：跑一次 `POST /compressed/run-all-rules` 后 `quality_audit` 近期 `event_count>0` 且 `MAX(compressed)>断流点`，才算 P0 真正愈合。

## no_valid_events 的隐藏陷阱：safe_complete_json 静默吞错（2026-09-06 实测新增）

根因链 B（事件提取产出 0 事件）有一个**极易误判的陷阱**：新事件提取协议 `llm_client.extract_events`（llm_client.py:845）内部走
```python
result = self.safe_complete_json(...) or {"events": []}
```
即 **LLM 调用一旦抛异常（超时/连接中断/响应解析失败），会被 `safe_complete_json` 静默吞掉，返回空 list → 事件提取"产出 0 事件" → `no_valid_events` → turns 永不压缩**。

**诊断意义**：`no_valid_events`（quality_audit `event_count=0`）**不等于**"事件提取逻辑/提示词写错"。它可能只是映射到"LLM 调用失败被吞"。因此：
- 单改事件提取**代码结构**（如换协议、加 `_usable_payload`、`valid_turn_ids` 校验）通常**无效** —— 因为真正的失败在底层 LLM 调用，错误被吞掉后上层只看到 0 事件。
- 判别：先确认 `quality_audit` 新增字段 `event_count` / `valid_event_count` 是否都为 0（都为 0 → 提取层就没产出候选，非被校验拦）；再查 config.yaml 的 `request_timeout_seconds` 是否过小（实测 `memory.mem.request_timeout_seconds: 2.0` 极低，技能根因链 A 特征）；再看完整异常堆栈是否落在 `socket.recv_into`（底层连接/超时）。
- 修复方向：**不让 `safe_complete_json` 静默吞错**（先让提取调用真实暴露错误），再对症调超时 / 修连接 / 换模型。改完用"顶线指标前移"复核。

## 时区混用检测（2026-09-08 实测新增）

**症状**：`mem_timeline` 分桶错位、每日曲线出现 8h 突跳。

**检测命令**：
```sql
-- turns.timestamp 时区分布
SELECT 
  SUM(CASE WHEN timestamp LIKE '%+00:00' THEN 1 ELSE 0 END) AS utc_cnt,
  SUM(CASE WHEN timestamp LIKE '%+08:00' THEN 1 ELSE 0 END) AS local_cnt,
  COUNT(*) AS total
FROM turns;

-- sessions.created_at 时区分布
SELECT 
  SUM(CASE WHEN created_at LIKE '%+00:00' THEN 1 ELSE 0 END) AS utc_cnt,
  SUM(CASE WHEN created_at LIKE '%+08:00' THEN 1 ELSE 0 END) AS local_cnt,
  COUNT(*) AS total
FROM sessions;
```

**判断标准**：
- 若 +00:00 和 +08:00 都有数据 → 时区混用，需迁移
- 新写入的 turn 用 UTC（+00:00），存量数据仍是 +08:00 → 你的时区改进只覆盖了部分数据

**修复建议**：
- 运行一次性数据迁移，给所有 +08:00 的 naive 时间戳补全偏移（改为 aware），或统一转为 UTC
- 迁移前必须备份，apply 后复核 `naive=0` + integrity=ok
- 迁移白名单：仅处理 `timestamp`/`created_at` 等时间列，跳过非时间列（如 `timeline_parent_id`、`timezone` 字段）

## profile_memories 诊断污染识别（2026-09-08 实测新增）

**症状**：`active` 仅剩个位数，`superseded` 大量且 value 含诊断报告片段。

**检测命令**：
```sql
-- profile 状态分布
SELECT status, COUNT(*) FROM profile_memories GROUP BY 1;

-- subject='project' 的污染量
SELECT COUNT(*) FROM profile_memories WHERE subject='project' AND status='superseded';

-- 查看污染样本
SELECT memory_id, substr(value, 1, 100) FROM profile_memories 
WHERE subject='project' AND status='superseded' LIMIT 3;
```

**根因**：诊断报告文本被当成"项目约束/偏好"写入 profile 层，互相 supersede 挤掉了真实偏好。

**防护缺口**：虽然 Tier1/Tier2 读侧有 `evaluation` 标签过滤，但**写侧未做自动判定注入** —— `evaluation` tag 依赖调用方显式传 `request.tags`。诊断类 turn 若不带 tag，进 Tier2 时无标签可挡。

**修复方向**：
- P0：在写入端对诊断/评估类内容强制附加 `evaluation` tag
- P1：把已污染的 superseded 诊断垃圾条目标记为 quarantined
- P2：恢复真实用户偏好（如果有备份可还原）

## 根因分类速查

| 症状 | 根因 | 修复方向 |
|------|------|----------|
| 主机 mem_search HTTPError | Gateway/Memory 未启动、服务注册失败或请求被拒绝 | 在主机侧启动服务并检查 6000/6001/6002 |
| 沙箱 mem_search HTTPError | 容器 localhost 无法访问主机 Gateway | 标记为沙箱网络限制，不修改主机记忆配置 |
| compressed_memories + profile_memories 均为空，turns 正常 | mock 污染写路径 / 压缩进程挂了 | 修复 PYTHONPATH / 检查压缩日志 |
| compressed_memories 停滞（最新日期远落后于 turns） | 压缩进程被 mock 拦截或 409 阻塞 | 同上 / 检查 workspace_id 一致性 |
| turns.pending 逐日积压、compressed 某日起归零，日志 `LLM unhealthy` + `quality gate rejected` | LLM 健康检查超时（超时阈值 < 实测推理耗时）→ 降级启发式 → 质量门禁默认 `max_degraded_fraction=0.0` 全拒 | 重启服务加载新配置 / 调大超时 / 提高降级容忍度（见"压缩停摆专项"） |
| config.yaml 明文配置与运行时日志不符 | 配置修改晚于进程启动,服务未重启仍加载旧配置 | 对比进程 CreationDate 与 config mtime,重启服务 |
| profile_memories.active=0 且 superseded>50 | 需双向验证：用户侧近期无显式偏好(正常) vs 捕获链路无观测日志/批量状态变更无 tombstone(异常)；勿单方面定性 | 为 `_settle_explicit_profile_capture` 加结构化日志与指标输出 |
| profile_memories.subject='project' 的 superseded 大量且 value 含「% / 风险 / 依赖 / 优化 / 修复 / 缺失 / 扫描」 | **诊断报告文本被当作项目约束/偏好写入**，互相 supersede 挤掉真实偏好（2026-09-02 实测：89 条） | 隔离/降级这些诊断垃圾条目，恢复真实偏好 active；在写入端对诊断类内容强制打 evaluation 标签而非落 profile |
| turns.compression_status='compressed' 的 MAX(timestamp) 停在数天前，但 compressed_memories MAX(created_at) 仍接近今天 | 双通道脱节：主 turns→Tier2 自动压缩链断流，Tier2 靠显式路径存活 | 定位辅助压缩回退链模型（见根因链 C），剔除失效模型恢复断流 |
| database is locked | 并发写入冲突 | 重启服务释放锁 |
| 两个 memory.db | 多 slot/多环境残留 | 确认活跃路径，清理旧库 |
| turn sync 持续 409 Conflict | CLI 与 Mem workspace_id 不一致 | 统一 workspace_id 契约 |

## 生产库清理与工具响应陷阱（2026-09-08 实测新增）

### profile_memories 清理前置检查
- 删除 profile 记忆前必须用 SQLite backup API 创建可验证快照；禁止用 `shutil.copy2`/文件复制作为 WAL 数据库备份。
- 先检查 `memory_embeddings_vec` 是否存在以及当前连接是否加载 `vec0`。若 vec 表存在但扩展未加载，profile 删除触发器可能执行 `DELETE FROM memory_embeddings_vec` 并使整个 DELETE 失败，报 `no such module: vec0`。
- 不能因为触发器失败就直接删除触发器。删除触发器会破坏 embedding/FTS 同步契约。正确方向是：停止写入进程后，在加载 vec0 的正式运行环境中通过应用层删除；或者编写经过备份、schema/索引/触发器完整复制和逐表计数校验的离线迁移。迁移失败时保留原库，不替换活跃库。
- 任何删除操作都必须串行执行：备份 → 删除 → integrity/行数/索引验证 → 服务 API 冒烟。不要并行启动修改和验证命令，否则验证可能读到旧库或中间状态。
- 不要把 `PRAGMA integrity_check=ok` 解释为业务索引健康；还要核对触发器、FTS、embedding 行数和关键记忆召回。

### 工具调用后的最终响应完整性
- 工具调用成功或失败后，必须生成新的、独立的 final response。禁止把工具前的旧草稿当作最终答案，也不能在 finalizer 得到空字符串时静默返回空响应。
- finalizer 应区分三种状态：有新工具结果时基于新结果回答；工具失败时明确报告失败和边界；工具结果为空时报告“无结果”，而不是复用上一轮正文。
- 需要为该链路增加回归测试：工具调用后 final 文本为空、异常、超时、旧草稿非空四种场景，均不得返回旧内容冒充当前结果。

## 注意事项
- 不要直接修改数据库，先确认问题根因
- PYTHONPATH 污染非常隐蔽，优先排查
- turns 正常 ≠ 记忆系统正常，只说明录制链路通；必须同时检查 compressed_memories 和 profile_memories
- 对比 worktree 源码和运行时 sys.path 是定位 mock 污染的关键手段
- Mem schema 会演进：不要假设存在 `memories` 表；以 `sqlite_master` 实际列出的表为准
- `vec0` 扩展模块报错 (`no such module: vec0`) 是正常的 —— 仅在查询 `memory_embeddings_vec*` 内部表时出现，不影响核心诊断
- `gateway_address` 为空可能是继承系统默认地址的哨兵值，不一定是故障；优先以 mem_search 功能验证为准
- 测试在 collect 阶段 ImportError 通常是 venv 缺运行时依赖（fastapi/pydantic），不是测试本身失败；先验证 `.venv` 依赖，不要误判为测试或源码问题
- memory_service `/health` 的 JSON 很长（>1500 字符会被截断），必须分段拉取完整内容；`status="degraded"` 的根因通常在 **`agent_outbox`** 段——实测 2026-08-28：`healthy=false, dead_letter_count=9, issues=["<outbox_id>:dead_letter"]`，死信 reporter 的 `last_error`（如 `HTTPError: HTTP Error 404`）与 `oldest_failure_age_seconds`（约 10 天）直接给出故障起点。degraded 不一定影响当前写入——该例 `last_success_at` 仍新鲜（死信只占历史重试名额），诊断时以 `write_status=committed` 的实际写入为准。另：旧版曾记载"/health 看不到 outbox"，已被本实测推翻，agent 侧 `outbox_status()` 作为补充验证而非唯一来源
- "supersedes 未实现" 是常见误判：Tier2（compressed_memories/profile_memories）的 supersedes 已闭环（写入置 `status='superseded'`，普通召回 `status='active'` 过滤）。真正缺口在 Tier1 turns——诊断/状态结论走 `add_turn` 不进 Tier2、无收敛机制，同主题新旧结论并列返回；此外最终软去重（shingle）阈值只挡"几乎相同"文本，挡不住同主题不同措辞。诊断时先查字段是否存在、再定位是哪一层没消费，勿直接下"完全没做"或"召回层没接线"的结论。
- 评估/诊断类会话里引用的测试样例文本（如"量子香蕉 ZXQ-917"）会被原样持久化成记忆，后续用同样样例复测时会精确命中自己（lexical 全匹配、分数 0.9+，min_score 再高也挡不住）。这是结构性自指污染。现已确认系统有 `evaluation` 标签机制，但存在结构性断裂（写侧 `add_turn_pair` 硬编码空 tags + Tier2 读侧无过滤），详见下方"自指污染防护审计"一节，按四段链路逐段核对后如实报告剩余缺口。
- 自指污染的数据层定位（2026-08-16 实测确认）：测试样例文本（"量子香蕉"/"ZXQ-917" 等）在 Tier2（compressed_memories/profile_memories）实际为 0 条，污染目前只停在 Tier1 `turns.text`（4 条诊断报告 turn，均 `compressed_to_tier2=0` 待压缩）。这印证写侧 `add_turn_pair` 硬编码 `tags='[]'` 是唯一根因——诊断 turn 无 `evaluation` 标签，Tier1 读侧与 bridge 出口的标签过滤对它们全部空转；当前 Tier2 干净只是压缩尚未轮到这些 session 的时间差，一旦压缩（无标签可挡）样例文本就会进入 Tier2。盘点污染时应查 `turns.text`，而非只查 `compressed_memories`/`profile_memories`。
- `recall_traces` 表列名是 `status`（非 `recall_status`），且只有 `hit`/`empty` 两态，`weak_match` 结果不会写入 trace，命中率因此虚高（实测 516 hit / 29 empty ≈ 94.7%，含大量弱相关召回）。诊断"召回命中率/有效性"时勿只看 trace 的 hit 比例，需结合语义召回实测的 `weak_match` 状态。
- 实体图谱独立于 turns/memories 存在，`entity_nodes`/`entity_edges`/`entity_memory_links` 三表联合反映知识关联密度。节点数过少（< 10）或边数极少可能表明图谱构建未激活。
- `memory_embeddings` 表的 `source_type` 列区分来源（agent_interaction/companion/evolution），`provider` 列标识向量提供方。诊断嵌入覆盖率时应按 provider 分组统计，而非只看总数。

## 身份档案层诊断（profile_memories）专项

profile_memories 是用户偏好和事实的单独存储层，与 compressed_memories 互补。诊断时注意：

### 表结构
- 列：`memory_id, subject, predicate, value, summary, status, confidence, certainty_state, created_at, updated_at`
- **无** `identity_layer` 列（这是 compressed_memories 独有字段）
- **无** `superseded_by` 列（这是 compressed_memories 独有字段；profile 层版本链用
  **`supersedes`** 这个 JSON list 列）
- 用 `subject/predicate/value` 三元组建模，类似 RDF

### Schema 列名漂移速查（2026-09-02 试错实测，写 SQL 前先核对）
不同表列名不统一，按直觉写会报 `no such column`，先 `PRAGMA table_info()` 确认：
- `compressed_memories`：内容列是 **`memory_type`**（非 `memory_kind`！`memory_kind` 不存在）；
  `created_at`（UTC）、`compressed_at`、`hidden`、`identity_layer`、`origin_type`(显式/派生来源)、
  `superseded_by`(单值)。
- `profile_memories`：**`supersedes`**(JSON list)，**无** `superseded_by`。
- `recall_traces`：**`error_type` / `error_detail`**（非 `error`）；`status`(hit/empty/failure)、`intent`、`query`。
- `turns`：时间列是 **`timestamp`**（非 `created_at`）；状态列 `compression_status`；
  无 `compressed_to_tier2`(旧列，新库查询会 `no such column`)。

### 状态分布（2026-08-21 实测）
- active: 通常很少（个位数），只保留最新正确版本
- superseded: 大量，表示被新版本替代
- quarantined: 质量门禁拦截，含虚假信号或过时结论

### 常见误判
1. **active=0 未必故障**：可能用户近期无显式偏好写入，属正常
2. **superseded 多未必故障**：版本替代机制工作正常的标志，检查 superseded_by 链接是否完整
3. **quarantined 多需关注**：隔离区包含被确认不可信的数据，应追查根因（测试污染、误判、过期信息）

### 检索建议
- `mem_search` 同时返回 Tier1 turns 和 Tier2 compressed，profile 层不直接暴露给 recall 查询
- 检查 profile 完整性需直接 SQL 查询，或确认 `mem_remember` 写入后能成功 `mem_search` 召回

### 与 compressed_memories 的区别
| 维度 | profile_memories | compressed_memories |
|------|------------------|---------------------|
| 内容 | 用户偏好、事实、身份核心 | 事件、场景、弧、时代 |
| schema | subject/predicate/value | title/metadata/tags |
| identity_layer | 无 | 有（self_foundation 等） |
| 召回方式 | 不直接进入 recall，需 mem_search 关键词命中 | 直接参与 recall 检索 |
| 典型状态 | active 少，superseded/quarantined 多 | 大部分 active |
