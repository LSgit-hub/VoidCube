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
另外 63 条是完全相同的技能自审提示模板，15 条是同一个「分析项目代码质量问题」提示。

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

**别用 `POST /compressed/run-all-rules` 做验证**——它会被 cadence 机制跳过，不能证明链路。

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
