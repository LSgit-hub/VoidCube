---
name: voidcube-session-transcript-divergence
description: 诊断和修复 VoidCube 会话 transcript 与 SQLite 已提交前缀分叉（日志 "local transcript diverges from committed prefix"）。含取证脚本、危害评估与“先备份再重建基线”的安全修复模式
category: devops
created: 2026-09-10
updated: 2026-09-10
---

# VoidCube 会话 transcript 分叉（local transcript diverges from committed prefix）

## 症状

`~/.VoidCube/logs/errors.log` 出现：

```
WARNING voidcube.infrastructure.persistence.session_runtime:
  Session DB batch append failed: local transcript diverges from committed prefix for <session_id>
```

同一 session 会**反复**出现同一条，且该回合之后的内容再也没有落库。

## 触发条件

内存中的会话历史与 SQLite 权威记录结构不一致，常见于：

- 上下文压缩后在内存重写了历史，但没有走 `replace_transcript`（例如回合被流中断打断）
- 单轮上下文极大（曾见 `context=~234,755 tokens`）+ `Stream stale for 300s` 强制关闭请求，回合异常终止

## 为什么必须修

`SessionPersistence.flush_to_db` 要求「内存 transcript 的前 `flush_sequence` 条」与已提交前缀 hash 一致。
一旦分叉且没有恢复路径，该会话**永久无法落库**：每轮只打一条 WARNING，后续对话静默丢失，直到用户换个新会话。

## 取证步骤（只读，先别改数据）

### 1. 确认分叉记录

```bash
python -c "from pathlib import Path
lines=(Path.home()/'.VoidCube/logs/errors.log').read_text(encoding='utf-8',errors='replace').splitlines()
hits=[x for x in lines if 'diverges from committed prefix' in x]
print('total',len(hits))
print(hits[-1] if hits else 'NONE')"
```

### 2. 对比 DB 与 JSON 镜像（判断是瞬时态还是持久损坏）

```bash
python -c "import json,pathlib,sqlite3,hashlib
sid='<session-id>'
db=pathlib.Path.home()/'.VoidCube/state.db'
c=sqlite3.connect(str(db)); c.row_factory=sqlite3.Row
r=c.execute('select flush_sequence,transcript_revision,transcript_hash from sessions where id=?',(sid,)).fetchone()
print('session',dict(r) if r else None)
print('committed_rows',c.execute('select count(*) from messages where session_id=?',(sid,)).fetchone()[0])
print('seq_range',c.execute('select min(sequence_no),max(sequence_no) from messages where session_id=?',(sid,)).fetchone()[:])
p=pathlib.Path.home()/'.VoidCube/sessions'/f'session_{sid}.json'
print('mirror_exists',p.exists(), p.stat().st_size if p.exists() else 0)
c.close()"
```

判读要点：

- `flush_sequence == committed_rows`，且 JSON 镜像条数与 DB 一致、两侧 `transcript_hash` 相同
  → 分叉是**瞬时内存态**造成的，落库的最后一个版本是自洽的；损失的是分叉之后的回合。
- 若 DB 与镜像条数/哈希仍不一致 → 更严重，先别动，扩大取证。

`transcript_hash` 只对语义字段取 hash：`role, content, tool_call_id, tool_calls, tool_name,
finish_reason, reasoning, reasoning_details, action_refs, attachments`。
`content` 差异（例如 user 消息被改写）就会导致 hash 不同。

## 关键代码位置

- `src/voidcube/infrastructure/persistence/session_runtime.py`
  - `SessionPersistence.flush_to_db`：分叉检测与恢复入口
  - `_recover_transcript_divergence` / `_write_divergence_snapshot`：恢复实现
- `src/voidcube/infrastructure/persistence/session_db.py`
  - `SessionSequenceConflictError`（可重试的竞态）
  - `SessionTranscriptDivergenceError`（需要重建基线的分叉，携带 `diagnostics`）
  - `transcript_hash` / `get_transcript_snapshot` / `append_messages_batch` / `replace_messages`
- 上层调用方：`src/voidcube/runtime/agent/turn_finalization.py`（`persist_session` 的失败只记入
  `result["finalization"]["persistence"]`，不会自动新建会话 → 所以必须在持久层自愈）

## 修复模式（可复用的设计套路）

不要只把 WARNING 升级成 ERROR 了事，那仍然会永久丢数据。正确套路是「**可观测 + 非破坏性自愈 + 可关闭**」：

1. **专用异常类型**：`SessionTranscriptDivergenceError(SessionSequenceConflictError)`，携带
   `diagnostics`（flush_sequence / committed_messages / local_messages / revision / 两侧 hash）。
   与普通游标冲突区分开，便于观测计数。
2. **分叉时不直接抛**：先尝试恢复；恢复不成立才抛结构化异常。
3. **恢复前必须落盘恢复点**：把**已提交的** transcript 写成
   `<logs_dir>/session_<sid>.divergence-rev<rev>.json`，保证原始数据可回溯。
4. **用带守卫的 `replace_messages` 重建基线**：传 `expected_revision` + `expected_transcript_hash`。
   若期间有并发写入，守卫失败 → 放弃恢复并抛异常（区分竞态）。
5. **保留严格模式开关**：`allow_divergence_recovery=False` 回到旧行为（拒绝写库、不改历史）。
6. **只在恢复成功时**返回 `EffectOutcome(status="succeeded", details={"divergence_recovery": {...}})`，
   让上层能观测到本次发生了重建。

> 设计取舍：分叉时「SQLite 权威」与「内存是实际对话」无法两全。选择不可破坏历史（先备份）+ 自愈（重建基线），
> 而不是静默丢弃。若业务上更希望拒绝写入，把开关置 False。

## 验证

```bash
set PYTHONPATH=src; .venv/Scripts/python.exe -m pytest tests/test_session_transcript_divergence.py tests/test_session_message_batch.py -q
set PYTHONPATH=src; .venv/Scripts/python.exe -m pytest tests/test_session_goal_persistence.py tests/test_conversation_runtime.py tests/test_agent_session_initialization.py -q
.venv/Scripts/python.exe -m compileall -q src/voidcube
.venv/Scripts/python.exe scripts/python_architecture.py
```

改完要重启服务才生效，然后确认 `errors.log` 不再出现新的分叉记录（历史记录还在，看时间戳）。

### 重启服务：必须拆成两条命令，并用 PID 变化确认

**不要**用 `.venv/Scripts/vc.exe serve stop && .venv/Scripts/vc.exe serve start` 这种一行式重启。

原因（本轮实际踩到）：terminal 工具会**去重完全相同的命令**，返回
`{"error": "duplicate_or_in_flight_action", "state": "succeeded"}`。注意 `state=succeeded` 是**误导性的**——
命令根本没执行。结果就是 PID 完全没变、仍在跑旧代码，而 `curl /health` 依然返回 healthy，
让你以为重启成功了。

正确做法：

```bash
.venv/Scripts/vc.exe serve stop; echo "STOP_RC=$?"
```

```bash
.venv/Scripts/vc.exe serve start; echo "START_RC=$?"; sleep 3; .venv/Scripts/vc.exe serve status
```

然后**以 `run/*.pid` 的数值是否变化为准**，不要只看命令输出：

```bash
python -c "import pathlib
p=pathlib.Path.home()/'.VoidCube/run'
for f in sorted(p.glob('*.pid')): print(f.name, f.read_text(encoding='utf-8',errors='replace').strip())"
```

PID 变了才算真的重启。另外：健康端点不校验代码版本，`healthy` **不能**证明新代码已加载。

## 坑

- **该模块原先没有任何测试**（`flush_to_db` / `SessionPersistence` 无覆盖）。修 bug 前先确认这点，
  新增回归测试是必要交付物，而不是可选项。
- 分叉检测发生在 `for attempt in range(3)` 重试循环**之外层**的 `if` 里；恢复失败要确保
  异常类型能穿过外层 `except` —— 注意 `except SessionTranscriptDivergenceError` 必须排在
  `except Exception` **之前**（且该异常是 `SessionSequenceConflictError` 的子类）。
- 恢复用的 `expected_transcript_hash` 直接取 `snapshot["transcript_hash"]`；该值在
  `get_transcript_snapshot` 里已对空列做了「按 messages 现算」的兜底，不要自己再算一遍。
- 别用「重启服务」当修复手段。分叉是数据问题，重启只会让同一段历史继续分叉。
