# Mem 短长期双层记忆改造方案

## 1. 问题诊断

当前 Mem 系统存在四个核心问题：

| # | 问题 | 根因 |
|---|------|------|
| 1 | **压缩后丢失原始对话详细信息** | 旧设计中 SQLite 的扁平压缩直接对原始对话做 LLM 摘要合并，原文不可恢复 |
| 2 | **没有基于时间轴的历史会话信息** | Tier 1 SQLite 只有扁平的 `memories` 表，无 session/turn 树形结构，无时间轴索引 |
| 3 | **无法追溯具体的对话内容** | Event/Scene/Arc 的 `source_turns` 引用了 turn_id，但 Tier 1 侧没有对应的 turn 存储表 |
| 4 | **压缩是单向的，无法还原** | 压缩后旧条目被直接 DELETE（`memory_service.py:479`），不可逆 |

**根本原因**：Mem 只是一个"从会话不断压缩的流程"，但它缺少一个"先完整记住，再逐步总结"的双层机制。

## 2. 改造目标

建立**短长期双层记忆架构**：

```
Tier 1 (SQLite)：30 天内完整保留 → 时间轴索引 → 精确检索
       ↓ 30 天后
Tier 2 (Mem Pipeline)：Event → Scene → Arc → Epoch 结构化压缩
       ↓ source_turns 反向引用
可追溯：Arc → Scene → Event → turn_id → Tier 1 archive 原文
```

## 3. 核心设计原则

1. **避免重复造轮子**：最大化复用现有组件，只在必要处扩展
2. **Mem Pipeline 零修改**：ChroniclePipeline 及其内部组件不做任何修改
3. **Tier 1 是纯扩展**：在现有 `memory_service.py` 上增加表和路由
4. **渐进式迁移**：新旧并存，逐步切换，不破坏现有治理链路

## 4. 现有组件复用清单

| 现有组件 | 改动量 | 说明 |
|---------|--------|------|
| `Mem/src/memai/pipeline.py` | **零改动** | ChroniclePipeline.ingest() 直接作为 Tier 2 压缩引擎 |
| `Mem/src/memai/schema.py` | **零改动** | TranscriptTurn 作为 Tier 1→Tier 2 数据转换格式 |
| `Mem/src/memai/extraction.py` | **零改动** | EventExtractor / ProfileMemoryExtractor 不变 |
| `Mem/src/memai/scene_builder.py` | **零改动** | SceneBuilder 不变 |
| `Mem/src/memai/arc_binder.py` | **零改动** | ArcBinder 不变 |
| `Mem/src/memai/epoch_builder.py` | **零改动** | EpochBuilder 不变 |
| `Mem/src/memai/query.py` | **小改动** | 扩展 evidence_trace 支持 Tier 1 回查 |
| `Mem/src/memai/maintenance.py` | **零改动** | MemoryMaintenanceEngine 不变 |
| `Mem/src/memai/governance.py` | **零改动** | 治理事件 Schema 不变 |
| `Mem/src/memai/governance_repository.py` | **零改动** | 追加式 JSONL 日志不变 |
| `systems/memory/memory_service.py` | **中等改动** | 扩展 SQLite schema + 新增路由 + Tier 1→Tier 2 桥接 |
| `systems/gateway/internal_gateway.py` | **小改动** | 添加 turn 记录 hook |
| `plugins/memory/mem/governor_bridge.py` | **零改动** | 治理桥接不变 |

**总新增代码量估算**：~500 行（集中在 memory_service.py 的扩展和 Tier 1→Tier 2 bridge），其余全部复用。

## 5. 实现阶段

### Phase 1：扩展 SQLite Schema（Tier 1 基础设施）

**文件**：`systems/memory/memory_service.py`

**改动**：在现有 `memories` 表基础上，新增三张表：

```sql
-- 会话表：每次对话一个 session
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    metadata TEXT           -- JSON: {source, agent_id, task_type, ...}
);

-- 对话轮次表：完整的原始对话
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    speaker TEXT NOT NULL,  -- "user" | "agent" | "system"
    text TEXT NOT NULL,     -- 原始对话全文
    timestamp TEXT NOT NULL,
    relevance_score REAL DEFAULT 1.0,
    decay_factor REAL DEFAULT 0.01,
    tags TEXT,              -- JSON array
    metadata TEXT,          -- JSON: {tool_calls, token_count, ...}
    compressed_to_tier2 INTEGER DEFAULT 0  -- 0=未压缩, 1=已压缩
);

-- 归档表：压缩后的 turn 摘要（可选保留原文）
CREATE TABLE IF NOT EXISTS turns_archive (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    speaker TEXT NOT NULL,
    text_summary TEXT,      -- 原始 text 的前 500 字符摘要
    original_text TEXT,     -- 可选：保留完整原文（配置控制）
    timestamp TEXT NOT NULL,
    compressed_at TEXT NOT NULL,
    event_ids TEXT,         -- JSON array: 对应的 Tier 2 Event ID 列表
    scene_ids TEXT          -- JSON array: 对应的 Tier 2 Scene ID 列表
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_relevance ON turns(relevance_score);
CREATE INDEX IF NOT EXISTS idx_turns_compressed ON turns(compressed_to_tier2);
CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON turns_archive(timestamp);
CREATE INDEX IF NOT EXISTS idx_archive_session ON turns_archive(session_id);
```

**复用**：
- 现有 `_setup_database()` 方法扩展，在同一 SQLite 文件中增加表
- 现有 `_save_to_db()` / `_row_to_entry()` 模式用于新表
- 现有 FastAPI lifespan、decay loop、gateway 注册保持不变

**新增 API 路由**（在 `_setup_routes()` 中注册）：

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/sessions` | 创建新会话 |
| GET | `/sessions/{session_id}` | 获取会话信息（含所有 turns） |
| GET | `/sessions` | 列出所有会话（按时间倒序） |
| POST | `/sessions/{session_id}/turns` | 追加对话轮次 |
| GET | `/sessions/{session_id}/turns` | 获取会话的所有轮次 |
| GET | `/turns?start=&end=` | 按时间范围查询轮次 |
| GET | `/turns/{turn_id}` | 获取单个轮次原文 |
| GET | `/turns/timeline?date=` | 获取指定日期的时间轴视图 |
| POST | `/tier2/compress` | 手动触发 Tier 1→Tier 2 压缩 |

### Phase 2：Gateway 会话记录 Hook

**文件**：`systems/gateway/internal_gateway.py`

**改动**：在处理用户请求和 Agent 响应时，自动将每个 turn 写入 Tier 1 SQLite：

```python
# 在 Gateway 处理每条消息时调用
async def _record_turn_to_tier1(self, session_id, speaker, text, metadata):
    """将对话轮次写入 Tier 1 SQLite，非阻塞"""
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{self.memory_service_url}/sessions/{session_id}/turns",
                json={
                    "speaker": speaker,
                    "text": text,
                    "metadata": metadata,
                },
                timeout=2.0,  # 超时不阻塞主链路
            )
    except Exception:
        pass  # Tier 1 记录失败不影响用户服务
```

**关键约束**：Tier 1 写入是**非阻塞、best-effort**的。用户服务链路不受 Tier 1 存储状态影响。

**复用**：Gateway 已有 `memory_service_url` 配置和 HTTP 调用模式（参考现有的 `governance_task_proxy`），只需增加 `_record_turn_to_tier1()` 调用。

### Phase 3：Tier 1 → Tier 2 桥接器

**新文件**：`systems/memory/tier1_to_tier2_bridge.py`

这是唯一需要新增的核心模块。职责：定期检查 Tier 1 中超过 30 天的 turns，批量送入 Mem Pipeline 压缩。

```python
class Tier1ToTier2Bridge:
    """将 Tier 1 SQLite 中的过期 turns 桥接到 Tier 2 Mem Pipeline。"""

    def __init__(
        self,
        db_path: str,
        pipeline: ChroniclePipeline,       # 复用现有
        retention_days: int = 30,          # 保留窗口，可配置
        batch_size: int = 100,             # 每批处理的 turn 数
        min_relevance_for_compress: float = 0.1,  # 低于此分数的 turn 直接归档不压缩
    ):
        ...

    def find_candidate_turns(self) -> list[dict]:
        """查找超过 retention_days 且未压缩的 turns。"""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        # SQL: SELECT * FROM turns WHERE timestamp < cutoff AND compressed_to_tier2 = 0
        ...

    def bridge_to_tier2(self, turns: list[dict]) -> PipelineResult:
        """将 turns 转为 TranscriptTurn 序列，送入 ChroniclePipeline。"""
        transcript_turns = [
            TranscriptTurn(
                turn_id=t["turn_id"],
                speaker=t["speaker"],
                text=t["text"],
                timestamp=datetime.fromisoformat(t["timestamp"]),
            )
            for t in turns
        ]
        # 直接复用现有 ChroniclePipeline.ingest()
        return self.pipeline.ingest(transcript_turns)

    def archive_processed_turns(self, turns, result: PipelineResult):
        """将已压缩的 turns 移至 archive，写入 Tier 2 反向引用。"""
        # 构建 turn_id → event_ids 映射
        turn_to_events = {}
        for event in result.events:
            for turn_id in event.source_turns:
                turn_to_events.setdefault(turn_id, []).append(event.id)

        for turn in turns:
            event_ids = turn_to_events.get(turn["turn_id"], [])
            # 写入 turns_archive 表
            # 更新 turns 表的 compressed_to_tier2 = 1
            ...

    def run_compression_cycle(self):
        """执行一次完整的 Tier 1→Tier 2 压缩周期。"""
        candidates = self.find_candidate_turns()
        if not candidates:
            return

        # 按 session 分组（保持对话连续性）
        by_session = groupby(candidates, key=lambda t: t["session_id"])

        for session_id, session_turns in by_session.items():
            result = self.bridge_to_tier2(session_turns)
            self.archive_processed_turns(session_turns, result)
            # 将 PipelineResult 合并到现有 mem_state.json
            ...
```

**集成到 memory_service 的 decay loop**：

```python
# 在 memory_service.py 的 _compression_loop() 中增加：
async def _compression_loop(self):
    while True:
        await asyncio.sleep(self.config.compression_interval)
        try:
            # 现有：扁平衰减
            for namespace in list(self._namespace_cache.keys()):
                await self._compress_namespace(namespace)
            # 新增：Tier 1 → Tier 2 桥接
            await self._tier2_bridge_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Background compression skipped", exc_info=True)
```

**复用分析**：
- `ChroniclePipeline` — 零修改，直接 `ingest()`
- `MemoryStateRepository` — 用于合并新的 PipelineResult 到持久化状态
- 现有 decay loop — 仅增加一个调用

### Phase 4：查询增强 — Tier 2 → Tier 1 回查

**文件**：`Mem/src/memai/query.py`（小改动）

**改动**：扩展 `evidence_trace()` 方法，支持从 Tier 2 的 turn_id 反向查询 Tier 1 原文：

```python
def evidence_trace(self, target_id, *, include_superseded=True,
                   resolve_turns=False, tier1_db_path=None):
    """Evidence trace with optional Tier 1 turn resolution."""
    # ... 现有逻辑不变 ...
    if resolve_turns and tier1_db_path:
        # 沿 support_chain 收集所有 turn_id
        # 查询 Tier 1 SQLite turns + turns_archive 表
        # 返回原始对话文本
        chain_with_text = []
        for ref in chain:
            turn_text = self._resolve_turn_text(ref, tier1_db_path)
            chain_with_text.append({"ref": ref, "text": turn_text})
        result["support_chain_with_text"] = chain_with_text
    return result
```

**新增 CLI 命令**：

```bash
# 按时间轴查看某天的完整对话
memai timeline --date 2026-06-01 --db-path ./memory.db

# 追溯某条记忆的证据链（含原文）
memai trace event_abc123 --resolve-turns --db-path ./memory.db

# 查看会话树
memai sessions --db-path ./memory.db
memai session <session_id> --db-path ./memory.db
```

### Phase 5：可配置化与可观测性

**配置项**（在 VoidCube 配置文件中）：

```yaml
memory:
  tier1:
    retention_days: 30          # Tier 1 保留窗口
    max_turns: 10000            # turns 表最大行数（触发强制压缩）
    decay_rate: 0.99            # 每日 relevance 衰减率
    min_relevance: 0.1          # 压缩候选最低分数
    archive_keep_original: true # 归档时是否保留原文
  tier2:
    compression_interval: 3600  # Tier 2 压缩检查周期（秒）
    batch_size: 100             # 每批处理 turn 数
```

**可观测性**（在 Supervisor UI 中增加）：

| 指标 | 来源 | 含义 |
|------|------|------|
| `tier1_total_turns` | SQLite COUNT | Tier 1 当前缓存的 turn 总数 |
| `tier1_oldest_turn` | SQLite MIN(timestamp) | 最早未压缩 turn 的时间 |
| `tier2_total_events` | PipelineResult | Tier 2 结构化 Event 总数 |
| `tier2_total_arcs` | PipelineResult | Tier 2 活跃 Arc 总数 |
| `last_bridge_at` | memory_service | 上次 Tier 1→Tier 2 桥接时间 |
| `bridged_turns_total` | 累计计数 | 累计已桥接的 turn 数 |

## 6. 改造前后对比

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| **30 天内对话** | 进入 SQLite 后即被摘要压缩 | 完整保留在 Tier 1 turns 表，精确可查 |
| **时间轴检索** | 无 | Tier 1 按时间点/段/会话检索原始对话 |
| **原文追溯** | 压缩后原文不可恢复 | source_turns → Tier 1 archive 回查 |
| **压缩方向** | 单向不可逆 | 不可逆但保留反向引用链路 |
| **衰减机制** | relevance_score 降低后即被 DELETE | 衰减仅标记候选，不自动删除 |
| **Mem Pipeline** | 从 JSON 文件 ingest | 改为从 Tier 1 SQLite 批量 ingest，逻辑不变 |
| **Governor Bridge** | 不变 | 不变 |
| **治理审计** | 不变 | 不变，压缩事件自动记录 |

## 7. 实施顺序

```
Phase 1（1-2 天）：扩展 SQLite Schema
  └── 在 memory_service.py 中增加 sessions/turns/turns_archive 表
  └── 新增 API 路由
  └── 单元测试

Phase 2（1 天）：Gateway 会话记录 Hook
  └── internal_gateway.py 增加 _record_turn_to_tier1()
  └── 集成测试：发送消息 → 确认 Tier 1 有记录

Phase 3（2 天）：Tier 1 → Tier 2 桥接器
  └── 新建 systems/memory/tier1_to_tier2_bridge.py
  └── 集成到 memory_service decay loop
  └── 端到端测试：写入 turns → 等待 30 天 → 确认 Tier 2 生成

Phase 4（1 天）：查询增强
  └── 扩展 evidence_trace()
  └── 新增 CLI timeline/sessions 命令

Phase 5（1 天）：配置化与可观测性
  └── 配置文件支持
  └── Supervisor UI 指标面板扩展
```

**总工期估算**：6-7 天。

## 8. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| SQLite 并发写入瓶颈 | Tier 1 写入走 Gateway 异步 HTTP，超时 2s 不阻塞主链路 |
| turns 表膨胀 | 30 天窗口 + max_turns 硬上限 + archive 迁移 |
| Tier 2 Pipeline 处理耗时 | 批量处理 + 异步执行，不影响用户服务 |
| 与现有 mem_state.json 状态冲突 | Tier 2 产出的 PipelineResult 通过 MemoryStateRepository 合并 |
| 旧 memory_service 的 namespaces/memories 表兼容 | 保留现有表不变，新表独立运作，渐进迁移 |

## 9. 不做的

- ❌ 不修改 Mem Pipeline 内部任何组件（ChroniclePipeline、Extractor、Builder、Binder、MaintenanceEngine 全部不动）
- ❌ 不改变现有治理链路（Governor Bridge、GovernanceEventRepository 不动）
- ❌ 不引入新的数据库引擎（坚持 SQLite，不引入 PostgreSQL/Redis）
- ❌ 不引入向量数据库（语义搜索是下一阶段的事）
- ❌ 不修改 Agent 侧的记忆访问接口（Agent 通过 Gateway 访问 Mem，接口不变）

## 10. 成功标准

1. 最近 30 天内的任意对话，可以通过时间轴或会话 ID 精确检索原文
2. 超过 30 天的对话，可以从 Tier 2 的 Arc/Scene/Event 沿 source_turns 追溯到原始 turn_id
3. Tier 1→Tier 2 桥接自动运行，不需要手动触发
4. 所有现有测试（101 个 Mem 测试 + memory_service 测试）继续通过
5. 治理审计日志中自动记录每次 Tier 1→Tier 2 桥接事件
