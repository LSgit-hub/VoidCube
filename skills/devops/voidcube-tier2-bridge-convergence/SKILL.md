---
name: voidcube-tier2-bridge-convergence
description: 诊断和修复 VoidCube Tier1→Tier2 记忆压缩流水线"停滞/不收敛"（no_events_generated 长期失败、pending 永不下降）。含判定性 SQL 签名（retry_count 全 0）、零事件分支漏记重试的根因、候选池取证与测试套路
category: devops
created: 2026-09-10
updated: 2026-09-10
---

# VoidCube Tier1→Tier2 压缩流水线不收敛（no_events_generated 长期失败）

## 症状

`GET http://localhost:6001/health` 的 `maintenance.tier2_bridge`：

```json
{"eligible_candidate_count": 251, "oldest_candidate_at": "2026-07-27T18:40:11+08:00",
 "oldest_candidate_age_seconds": 3888330.67, "state": "failed",
 "consecutive_failures": 2, "last_failure_reason": "no_events_generated",
 "last_succeeded_at": null}
```

关键特征：`last_succeeded_at` 为 `null`、`oldest_candidate_at` 停在几周前、
`consecutive_failures` 持续增长，而 `turns` 表里 `pending` 计数**永不下降**。

## 判定性诊断：先看 retry_count 分布

这是最快区分「不收敛」与「正常零事件」的一条查询：

```bash
python -c "
import sqlite3,pathlib
db=pathlib.Path.home()/'.VoidCube/runtime/memory/memory.db'
c=sqlite3.connect(db)
print('status/retry:')
for r in c.execute('select compression_status,compression_retry_count,count(*) from turns group by 1,2 order by 1,2'):
    print('  ',tuple(r))
c.close()"
```

判读：

- **`pending` 一大堆且 `compression_retry_count` 全为 0** → 零事件路径从未推进重试计数 →
  同一批 turn 被无限重选 → 流水线**不收敛**。这就是本 skill 要修的场景。
- 正常/健康状态下应当能看到 `retry_wait`（重试中）和 `quality_quarantined`（3 次后隔离）的计数，
  说明有界重试机制在工作。

## 根因：两条相邻分支不对称

`Mem/src/memai/application/tier1_to_tier2_bridge.py` 的 `run_cycle` 里，两个失败分支写法不一致：

```python
# 约 1302 行：零事件分支 —— 只写审计，漏了 _record_quality_rejection
if not tier2_output.get("events"):
    logger.warning("Tier 2 bridge cycle produced no events for %d candidate turns; ...")
    self._persist_quality_audit(quality_evidence, "rejected")
    return BridgeResult(status="no_events_generated", ...)

# 约 1321 行：质量门禁分支 —— 审计 + 重试计数，成对出现
if not quality_evidence["passed"]:
    self._persist_quality_audit(quality_evidence, "rejected")
    self._record_quality_rejection(candidates)
    return BridgeResult(status="quality_rejected", ...)
```

后果链条：LLM 合法返回 0 事件 → 只写审计、不动 turn 状态 → turn 永远停在 `pending` →
每周期被重新选中 → `consecutive_failures` 无界增长、`last_failure_reason` 永远
`no_events_generated` → 既有「有界重试 → `quality_quarantined`」机制**永不触发**。

顺带解释了为什么 `pending` 的 `retry_count` 全 0：它们从未被计入重试。

## 修复

```python
if not tier2_output.get("events"):
    logger.warning(...)
    self._persist_quality_audit(quality_evidence, "rejected")
    # 零事件同样要推进有界重试：否则这批 turn 永远停留在 pending 并被反复选中。
    self._record_quality_rejection(candidates)
    return BridgeResult(status="no_events_generated", ...)
```

原则：**复用既有词汇与阈值，不新增状态、不改 schema、不放宽质量门禁**。
两种情形语义一致，都是「已评估但未被接受」：

- 质量门禁失败 → `retry_wait`（退避 `2**(n-1)` 小时）→ 3 次后 `quality_quarantined`
- 零事件 → 现在走同一条路

> `turns.compression_status` 有 CHECK 约束，只允许
> `pending / retry_wait / compressed / quality_quarantined`。想新增一个状态必须写迁移，
> 所以优先复用 `retry_wait` → `quality_quarantined`。

**为什么这在语义上安全**：先有「LLM 异常不再被静默转成空事件」的修复
（`safe_complete_json(..., raise_on_error=True)`，异常直接抛到 `run_cycle` 的 `except` →
`status="failed"`），零事件才是可信信号——它确实意味着「模型成功且认为无可记忆内容」，
重复零事件**不是瞬态状况**，因此隔离是对的终端状态。这两个修复是互补的，别只做一个。

## 真根因（2026-09-10 第二轮）：低信息旧会话垄断锚点

只修上面的「零事件不记重试」**不足以**恢复顶线指标。修复后仍可能观察到：
`consecutive_failures` 归零后又涨、`state=failed`、压缩计数不增长。

### 判定性诊断：连续两个周期是否返回同一批 turn

这是最快的判别手段——**连续调用压缩接口，打印每批的 `sample_turn_ids` 和这批 turn 的长度**：

```bash
python -c "
import urllib.request,json,sqlite3,pathlib
db=pathlib.Path.home()/'.VoidCube/runtime/memory/memory.db'
def compress():
    req=urllib.request.Request('http://localhost:6001/tier2/compress',
      data=json.dumps({'owner_id':'local-user','workspace_id':'VoidCube',
                       'memory_domain':'agent_interaction','memory_actor':'api_a'}).encode(),
      headers={'Content-Type':'application/json'},method='POST')
    return json.loads(urllib.request.urlopen(req,timeout=300).read().decode('utf-8','replace'))
for i in range(4):
    d=compress()
    print(i+1,d.get('status'),'candidates',d.get('candidate_count'),'events',d.get('events_generated'))
    c=sqlite3.connect(db)
    for tid in (d.get('sample_turn_ids') or []):
        row=c.execute('select speaker,length(text),substr(text,1,60) from turns where turn_id=?',(tid,)).fetchone()
        print('    ',row)
    c.close()"
```

若每轮都是**同一批超短内容**（实测："OK" 2 字符 + "请分析当前项目中最值得优先修复的代码质量问题。" 23 字符）→
锚点被低信息会话占死，实质内容排在后面永远轮不到。

### 为什么会这样（选批机制）

`select_candidate_turns` 是「**最旧优先 + 单会话**」：先用
`ORDER BY julianday(timestamp) ASC LIMIT 1` 取一个 session 作为锚点，再取该 session
≤`batch_size` 条。而 `eligible_candidate_count` 是**全局**计数，
所以「有几百个候选」不代表批次有内容——它可能只是一个会话的 2 条垃圾。

按会话看信息量分布（本 skill 的池级长度直方图**看不出来**，必须按 session 分组）：

```sql
SELECT session_id, COUNT(*) n, MAX(length(trim(text))) maxlen,
       SUM(length(trim(text))>=200) n_substantive
FROM turns
WHERE compression_status IN ('pending','retry_wait') AND memory_domain='agent_interaction'
GROUP BY session_id ORDER BY MIN(timestamp) ASC LIMIT 15;
```

实测：最旧端 15 个会话的 `maxlen` 只有 23，把 **135 条 ≥200 字符**的实质内容堵在身后。

### 修复：信息量优先 + 可回退（锚点专用）

```python
_MIN_INFORMATIVE_CHARS = 40
_INFORMATIVE_TURN_SQL = "length(trim(coalesce(text, ''))) >= %d" % _MIN_INFORMATIVE_CHARS
```

`fetch_one_session(conditions, params, *, anchor_conditions=None)` 让锚点查询用
`[*eligible_conditions, _INFORMATIVE_TURN_SQL]`，**批次查询仍用原 conditions**；
三级回退（沿用既有 `low_relevance_fallback` 模式）：

1. 合格 + 相关性 + 信息量 → 命中即返回
2. 合格 + 相关性（记 `low_information_fallback=True`）
3. 合格（记 `low_relevance_fallback=True`）

**关键陷阱**：信息量条件**只能加在锚点查询上**。第一版把它加进了批次查询，
结果「批次里含任意短 turn 就整批被排除」，一次性打挂 13 个既有用例
（`test_tier2_compress_*` / `test_compression_quality_gate_*`）。
锚点专用 = 换一个会话，批次内容不变。

新增字段要走完三处，漏一处就是 `TypeError: ... unexpected keyword argument`：
`CandidateBatch` → `BridgeResult`（`run_cycle` 的 metadata 是 `**metadata` 展开进 `BridgeResult`）→ `to_dict()`。

回退链的意义：低信息 turn **仍可达**（不会被永久隔离），只是让位给实质内容。
测试要同时断言两件事：实质会话赢得锚点（`low_information_fallback is False`），
以及移除实质内容后回退到低信息会话（`low_information_fallback is True`）。
测试里读写 memory.db 必须用 `open_memory_sqlite()`，直接 `sqlite3.connect()` 会报
`no such module: vec0`。

### 指标修正：不要用 MAX(compressed) 判断恢复

**`MAX(turns.timestamp WHERE compression_status='compressed')` 在按最旧优先排空时不前移**，
即使压缩已经成功——新压缩的 turn 比历史最大时间戳更旧。实测压缩已生效（计数 816→820）
而 MAX 仍停在 08-27，极易误判为「没修好」。

正确判据组合：

- `compressed` 行数增长（如 816→820）
- `compressed_memories` 计数增长（如 411→416）
- 最近 scope `status=compressed` 且 `failed_checks=[]`
- `last_succeeded_at` 非空

### 修复后的下一个阻塞：min_event_coverage=1.0 的全或无门禁

锚点修好、选到实质内容后，可能出现新形态：**提取完全正常但整批被拒**：

```
event_count=4  valid_event_count=4  backlink_completeness=1.0
source_support=0.737  identifier_fidelity=1.0  polarity_consistency=1.0
compression_ratio=0.171
event_coverage=0.684   ← 阈值 min_event_coverage = 1.0  → quality_rejected
failed_checks=['event_coverage','validated_event_coverage']
```

`event_coverage = 被事件覆盖的 turn 数 / candidate_count`，阈值默认 **1.0**，
即要求批次内**每个 turn** 都被事件引用。19 条里天然有若干条不含可提取内容 → 整批拒绝。
小批次（4 条 / 783 字符）coverage=1.0 就能成功 → **成功率结构性取决于批次大小与同质性**。

阈值是配置项：`Mem/src/memai/application/config.py` 的
`tier2_min_event_coverage`（默认 1.0），但 `config.yaml` 未设置 → 运行在默认值。

三个方向（属记忆生命周期策略，需产品决策，**不要擅自降低门禁**）：

- (a) 调低配置阈值 —— 成本最低，但削弱质量语义（本 skill 明确不建议）
- (b) **部分提交**：已验证通过的 event 落库，未覆盖的 turn 留在 pending 下轮再组批 ——
  不降门槛、不丢数据，代价是打破"全或无"契约，需配套测试（推荐）
- (c) coverage 分母改为"含可提取内容的 turn" —— 语义最准但需先定义"可提取"

### 聚合语义：部分成功**已经**不会被记成全局失败（别再当缺陷修）

`_tier2_bridge_cycle` 已实现：有 scope 成功 → `consecutive_failures=0`、
`last_succeeded_at` 更新、`state=degraded`；只有
`successful_scope_count == 0` 才 `_record_tier2_bridge_failure`。
实测 `scope_count=2, successful=0, failed=2` 时报 `failed` 是**正确**的。
先看 `successful_scope_count` 再判断，不要凭 `state` 直接下结论。

另外：**单次 `/tier2/compress` 不更新这套聚合**（只有全量 cycle 会），
所以手动压缩成功后 `last_succeeded_at` 仍可能是 null——别据此判断失败。

### 全量周期怎么跑（修正：run-all-rules 可用）

`POST /compressed/run-all-rules` 返回 `{"status":"accepted","started_at":null}` 是**异步排队**，
不是被跳过：等 ~45s 后看 `/health` 的 `maintenance.requested_run` 变成 `completed`，
`last_tier2_bridge_result` 就会出现 `scope_count/successful_scope_count/failed_scope_count`
和每个 scope 的 `quality_evidence`。它比单 scope 调用更适合验证聚合语义。

## 候选池取证（决定策略前必做）

不要凭「反正都是垃圾」就一刀切。实测 568 条 pending 的构成：

```bash
python -c "
import sqlite3,pathlib,collections
db=pathlib.Path.home()/'.VoidCube/runtime/memory/memory.db'
c=sqlite3.connect(db); c.row_factory=sqlite3.Row
rows=c.execute(\"select speaker,text,tags from turns where compression_status in ('pending','retry_wait')\").fetchall()
print('total',len(rows))
lens=sorted(len(r['text'] or '') for r in rows)
print('len min/median/p90/max',lens[0],lens[len(lens)//2],lens[int(len(lens)*0.9)],lens[-1])
for lo,hi in [(0,20),(21,60),(61,200),(201,800),(801,10**9)]:
    print(f'  {lo}-{hi}:',sum(1 for x in lens if lo<=x<=hi))
print('exact dup:',collections.Counter((r['text'] or '').strip() for r in rows).most_common(5))
print('tags:',collections.Counter((r['tags'] or '') for r in rows).most_common(5))
c.close()"
```

实测结论：池子**异质**——既有 `OK`×15、`Nothing to save.`×4、`你好` 这类无信息噪声（≤20 字符约 110 条），
也有上百条 ≥800 字符的技术分析/代码审查结论（**本该压缩却卡住**）。
另外约 63 条是完全相同的技能自审提示模板（`Review the conversation above and consider saving or updating a skill`）、
15 条是同一个「分析项目代码质量问题」提示。**注意**：这类自审模板带 `evaluation` 标签，
已被 `_NON_EVALUATION_TURN_SQL` 排除，**不消耗压缩周期**，只让 `pending` 计数虚高——
别把它们当成"堵住流水线"的原因（实测真正堵住流水线的是**未打标签的短会话**，见上文锚点章节）。

所以「短文本一律跳过」是错解法；正确方向是让状态机收敛 + 对低信息 turn 做分类。

### 两个容易误报的点

- **`candidate_count` 很小不是 bug。** 候选选择是**按 session 分批**的（`BridgeResult` 的
  `metadata.session_id`），所以即使 `eligible_candidate_count` 有几百，
  单批 `candidate_count` 也可能是 2。别把它当缺陷报。
- **带 `evaluation` 标签的 turn 永远不进候选。** `_NON_EVALUATION_TURN_SQL` 会排除
  `tags` 含 `evaluation` 的 turn（实测 141 条），但它们仍留在 `pending`，
  造成**指标噪声**而非功能缺陷。要清理属产品决策（归档 / 隔离 / 保留），
  且改造前注意上面那条 CHECK 约束。

## 验证

```bash
set PYTHONPATH=src; .venv/Scripts/python.exe -m pytest tests/test_memory_health_signals.py -q
.venv/Scripts/python.exe -m compileall -q Mem/src/memai src/voidcube
git diff --check
```

`tests/test_memory_health_signals.py` 里可复用的驱动模式（不需要真 LLM）：

```python
svc = _make_service(tmp_path)
svc._llm_healthy = True
await svc.create_session(SessionCreate(session_id="no-events", metadata={}))
await svc.add_turn("no-events", TurnCreate(speaker="user", text="OK", metadata={}))

class _EmptyPipeline:
    def ingest(self, turns):
        return SimpleNamespace(events=[], scenes=[], arcs=[], epochs=[], profile_memories=[])

svc._build_compression_pipeline = lambda: _EmptyPipeline()
first = await svc.tier2_compress(Tier2CompressRequest(min_relevance=0.0, force_oldest=True))
assert first["status"] == "no_events_generated"
again = await svc.tier2_compress(Tier2CompressRequest(min_relevance=0.0, force_oldest=True))
assert again["status"] == "no_candidates"      # 退避生效，不再被立刻重选
# 再断言 DB: retry_count=1, retry_after 非空, status='retry_wait'
```

已有的 `test_quality_rejection_stops_retrying_after_three_attempts` 演示了
3 次 → `(3, None, "quality_quarantined")` 的终态。

## 端到端验证（会改数据，先备份）

```bash
# 1) 在线备份（不要直接复制运行中的 WAL 库）
python -c "
import sqlite3,pathlib,time
src=pathlib.Path.home()/'.VoidCube/runtime/memory/memory.db'
dst=src.parent/'backups'/f'memory-pre-zero-event-retry-{time.strftime(\"%Y%m%dT%H%M%S\")}.db'
dst.parent.mkdir(parents=True,exist_ok=True)
s=sqlite3.connect(str(src)); d=sqlite3.connect(str(dst)); s.backup(d)
print(dst.name, d.execute('PRAGMA integrity_check').fetchone()[0]); s.close(); d.close()"
```

```bash
# 2) 触发一次真实压缩周期（重启服务加载新代码后）
python -c "
import urllib.request,json
req=urllib.request.Request('http://localhost:6001/tier2/compress',
  data=json.dumps({'owner_id':'local-user','workspace_id':'VoidCube',
                   'memory_domain':'agent_interaction','memory_actor':'api_a'}).encode(),
  headers={'Content-Type':'application/json'},method='POST')
d=json.loads(urllib.request.urlopen(req,timeout=300).read().decode('utf-8','replace'))
print({k:d.get(k) for k in ['status','turns_processed','candidate_count','compression_method']})
print('failed_checks',(d.get('quality_evidence') or {}).get('failed_checks'))"
```

修复生效的判据（实测）：抽样的 turn 变为 `retry_wait` + `retry_count=1` +
`retry_after` 约 +1h；`pending` 计数下降（570→559）；`eligible_candidate_count` 下降（251→242）；
`retry_wait` 从 0 条变为若干条。

**`POST /compressed/run-all-rules` 是异步的**：返回 `accepted` + `started_at:null` 只表示已排队，
等 ~45s 看 `maintenance.requested_run.status == "completed"` 再读 `last_tier2_bridge_result`。
它适合验证**聚合语义**；要验证单条链路是否产出事件，用单 scope 的 `/tier2/compress` 更直接。

## 坑

- **改行为会撞到既有断言。** `test_tier2_compress_keeps_turns_uncompressed_when_no_events_generated`
  和 `test_standalone_bridge_keeps_turns_uncompressed_when_no_events_generated` 原先断言
  `compression_status == "pending"`，恰好固化了被修复的缺陷。它们真正要保护的不变量是
  「未压缩、未归档、不丢数据」——保留这两条断言，只把状态改成 `retry_wait` 并加注释。
- **不能靠循环调用 `tier2_compress` 三次来验证隔离。** 第一次就会写 `retry_after`（+1h），
  之后同一批变成 `no_candidates`。要到 quarantine 就直接
  `bridge._record_quality_rejection([turn])`（对齐既有测试）。
- **`state` 仍会显示 `failed / no_events_generated`**，即使修复已生效。这是诚实的信号：
  本批确实没产出事件。修复解决的是「永不收敛」，不是「让指标变绿」。
  若要让指标区分「管道坏了」和「本批不值得记忆」，需要新增结果类型——属产品决策。
- **别为了变绿去放宽质量门禁或降低 `min_relevance`。** 那会把低价值内容写进长期记忆。
- `_MAX_QUALITY_RETRIES = 3`，退避 `2**(n-1)` 小时（1h/2h/4h）。
- 服务端口与重启方式见 `voidcube-supervisor-fix`（重启必须拆成 stop/start 两条命令并以 PID 变化确认）。

## 相关

- 记忆系统整体诊断：`system/memory-system-diagnostics`
- 记忆维护扫掠：`general/memory-maintenance-sweep`
- 上游诱因（流 stale 导致回合中断）：`devops/voidcube-llm-stream-stale-robustness`
