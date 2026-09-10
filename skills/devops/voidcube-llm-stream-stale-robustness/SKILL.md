---
name: voidcube-llm-stream-stale-robustness
description: 诊断和修复 VoidCube LLM 流式传输的卡死与误判——"Stream stale"/"read operation timed out"/"Partial stream delivered; returning terminal stub"/"Empty response ... No fallback available"。含 stale 看门狗与 httpx 读超时的阈值倒置、一次性看门狗等根因模式
category: devops
created: 2026-09-10
updated: 2026-09-10
---

# VoidCube LLM 流式传输 stale / 超时健壮性

## 症状（`~/.VoidCube/logs/errors.log`）

```
WARNING voidcube.infrastructure.llm.transport_runtime:
  Stream stale for 300s (threshold 300s); model=... context=~234,755 tokens. Closing request client.
WARNING ...: Streaming failed after partial delivery, not retrying: The read operation timed out
WARNING ...: Partial stream delivered; returning terminal stub: ...
WARNING voidcube.domain.agent.response_disposition:
  Empty response (no content or reasoning) after 3 retries. No fallback available. model=... provider=...
ERROR   root: API call failed after 3 retries. HTTP 502: ...
```

外层表现：一轮对话“卡很久后返回空回复”。

## 架构：谁在监控谁

`src/voidcube/infrastructure/llm/transport_runtime.py` 里的 `ChatTransport.stream()`：

- **worker 线程**跑 `stream_once()`（真正的 HTTP 流），每收到一个 chunk 就刷新 `last_chunk_time`。
- **调用线程（主线程）**在 `while thread.is_alive()` 里轮询，充当 **stale 看门狗**：超过阈值就
  `_close_slot()`（关掉请求客户端）+ `_replace_primary()`（重建连接池）+ 发 status 提示。
- 失败重试在 worker 内：`for attempt in range(max_retries + 1)`。

## 两个根因模式（都是本轮实测确认的）

### 模式 1：stale 看门狗是一次性的

旧实现用 `stale_abort_requested` 布尔量，一旦置 True **永不复位**。后果：**只有第一次尝试受保护**。
第一次被击杀后进入重试，若重试又挂住，看门狗不再介入，只能等整体请求超时
（`VOIDCUBE_API_TIMEOUT`，默认 1800s）——表现为十几分钟毫无响应。

修复：按「每次尝试」重新武装。worker 每次进入 `stream_once` 自增 `attempt_index`，
看门狗记录 `stale_kill_attempt`，仅当 `stale_kill_attempt != attempt_index` 时才判定 stale。
日志带 `attempt=N`（击杀序号），并在本次调用多次击杀时补一条汇总 warning 便于观测。

### 模式 2：httpx 读超时低于 stale 阈值（阈值倒置）

- 读超时默认 **120s**（`VOIDCUBE_STREAM_READ_TIMEOUT`）；
- stale 阈值随上下文自适应：**180s** 基准，>50K tokens → 240s，>100K tokens → 300s。

于是大上下文下正常的长停顿（模型长时间思考后才出首个 token）先撞上 `httpx.ReadTimeout`，
**绕过**看门狗的诊断与连接池重建，直接走 `Partial stream delivered; returning terminal stub`，
再叠加 `Empty response ... No fallback available`。日志里能同时看到两个阈值数字就是铁证
（`Stream stale for 180s` 与默认 read=120s）。

修复：把读超时抬到 stale 阈值**之上**留余量，仍以请求预算 `base_timeout` 为上限：

```python
@staticmethod
def _align_stream_read_timeout(read_timeout, stale_timeout, base_timeout):
    if not math.isfinite(stale_timeout):      # 本地端点返回 inf
        return read_timeout
    return max(read_timeout, min(base_timeout, stale_timeout + 30.0))
```

原则：**stale 看门狗是「流已死」的权威判定**（它带诊断 + 主动关客户端 + 重建连接池），
I/O 层读超时退化为高于它的兜底。显式传入的小 `timeout` 仍是硬上限，不得被突破。

## 环境变量速查

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VOIDCUBE_STREAM_STALE_TIMEOUT` | 180 | stale 基准阈值（秒）；本地端点自动变为 inf |
| `VOIDCUBE_STREAM_READ_TIMEOUT` | 120 | httpx per-read 超时；本地端点自动抬到 base |
| `VOIDCUBE_STREAM_RETRIES` | 2 | 流式失败重试次数（总尝试 = +1） |
| `VOIDCUBE_API_TIMEOUT` | 1800 | 整体请求超时，也是读超时的上限 |

`estimate_context_tokens()` 很粗糙（`sum(len(str(v)) for v in messages) // 4`），只用于选档，别当精确值。

## 排查步骤（只读）

```bash
# 1. stale / terminal stub / fallback 各出现多少次、最后一次是什么
python -c "from pathlib import Path
lines=(Path.home()/'.VoidCube/logs/errors.log').read_text(encoding='utf-8',errors='replace').splitlines()
for pat in ['Stream stale','terminal stub','No fallback available','read operation timed out','HTTP 502']:
    hits=[x for x in lines if pat in x]
    print(pat, len(hits), '|', hits[-1][:200] if hits else 'NONE')"
```

```bash
# 2. 判定是否阈值倒置：把日志里的 threshold 与当前 read timeout 对照
grep -o 'Stream stale for [0-9]*s (threshold [0-9]*s)' ~/.VoidCube/logs/errors.log | tail -5
```

判读：
- 多档 threshold（180/240/300）说明上下文规模在变化，正常；
- 若同时出现 `The read operation timed out`，基本可判定读超时先于看门狗触发 → 模式 2。

## 验证

```bash
set PYTHONPATH=src; .venv/Scripts/python.exe -m pytest tests/test_chat_transport.py -q
set PYTHONPATH=src; .venv/Scripts/python.exe -m pytest tests/test_response_disposition.py tests/test_conversation_runtime.py tests/test_tool_turn.py -q
.venv/Scripts/python.exe -m compileall -q src/voidcube
.venv/Scripts/python.exe scripts/python_architecture.py
```

改完要重启服务才生效，**且必须确认命令真的执行了**：把
`.venv/Scripts/vc.exe serve stop` 与 `.venv/Scripts/vc.exe serve start` 拆成两条独立命令，
并以 `~/.VoidCube/run/*.pid` 数值变化为判据（terminal 会去重相同命令；
健康端点不校验代码版本，`healthy` 不能证明新代码已加载）。详见 `voidcube-supervisor-fix`。

## 坑

- **改行为会撞到既有断言。** `tests/test_chat_transport.py` 原有
  `assert lifecycle.replaced == ["stale_stream_pool_cleanup"]` 精确固化了“只击杀一次”的旧行为。
  重新武装后阈值为 0 时每次尝试都会被判 stale，该断言必然失败。
  正确处理是**改成 `in` 断言并在测试里写注释说明语义变了**，而不是为了迁就测试而放弃修复。
- **读超时对齐别突破请求预算。** `min(base_timeout, ...)` 那一层不能省，否则显式传入的小
  `timeout` 会被抬高，破坏 `test_stream_transport_respects_per_request_timeout` 这类契约。
- `math` 需新增 import；本地端点时 `stale_timeout` 为 `float('inf')`，必须先用
  `math.isfinite` 短路，否则 `inf + 30` 参与 `min/max` 会得出反直觉结果。
- **这两个模式修不了的部分**：`No fallback available` 是供应商级备用凭据/配置能力缺口，
  不是传输层缺陷；上游 `HTTP 502` 属外部故障。别把它们当成传输层 bug 去改。

## 相关

- 会话分叉（stale 导致回合中断的下游后果）：`devops/voidcube-session-transcript-divergence`
- 服务重启与 PID 判读：`devops/voidcube-supervisor-fix`
