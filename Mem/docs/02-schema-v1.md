# 模式 v1

## 1. 设计目标

本模式为以时间为中心的外部记忆定义分层记忆模型。其设计支持：
- 从事件到篇章的逐步抽象，
- 显式修订与取代，
- 感知证据的检索，
- 选择性遗忘，
- 稳定跟踪主线与支线。

v1 层级为：

`Event -> Scene -> Arc -> Epoch`

## 2. 共享基础类型

所有记忆单元都继承一个通用基础结构。

```json
{
  "id": "mem_xxx",
  "type": "event|scene|arc|epoch",
  "title": "string",
  "summary": "string",
  "timespan_start": "ISO datetime",
  "timespan_end": "ISO datetime",
  "time_precision": "exact|day|week|month|approx",
  "importance": 0.0,
  "confidence": 0.0,
  "status": "active|dormant|closed|superseded",
  "main_or_side": "main|side|undetermined",
  "topics": ["string"],
  "entities": ["string"],
  "evidence_refs": ["string"],
  "parent_ids": ["string"],
  "child_ids": ["string"],
  "supersedes": ["string"],
  "compression_level": 0,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime",
  "last_reviewed_at": "ISO datetime"
}
```

## 3. 字段语义

- `id`：不可变对象标识符。
- `type`：层级标识符。
- `title`：简短、便于人类阅读的标签。
- `summary`：紧凑的历史描述。
- `timespan_start` 和 `timespan_end`：当前可得的最佳时间边界。
- `time_precision`：时间边界的可靠性与粒度。
- `importance`：预期的长期检索与保留价值。
- `confidence`：对对象准确性的置信度。
- `status`：生命周期状态。
- `main_or_side`：在更广泛叙事中的结构角色。
- `topics`：主题标签。
- `entities`：人物、项目、地点或其他命名实体。
- `evidence_refs`：支持该记录的原始轮次 ID、源片段或低层级对象 ID。
- `parent_ids`：直接上层容器。
- `child_ids`：直接下层成员。
- `supersedes`：同一逻辑记忆单元的先前版本。
- `compression_level`：抽象深度。

## 4. 枚举

### `time_precision`

```text
exact | day | week | month | approx
```

### `status`

```text
active | dormant | closed | superseded
```

### `main_or_side`

```text
main | side | undetermined
```

## 5. 事件

`Event` 是最小的持久记忆单元。它记录有意义的变化，而不是普通话语。

### 事件结构

```json
{
  "id": "event_001",
  "type": "event",
  "title": "决定围绕时间定义记忆系统",
  "summary": "用户明确将该项目定义为面向大模型、以时间为中心的记忆管理器。",
  "timespan_start": "2026-03-22T10:00:00Z",
  "timespan_end": "2026-03-22T10:05:00Z",
  "time_precision": "exact",
  "importance": 0.82,
  "confidence": 0.95,
  "status": "active",
  "main_or_side": "main",
  "topics": ["memory-system", "project-definition"],
  "entities": ["user", "project"],
  "evidence_refs": ["turn_011"],
  "parent_ids": ["scene_001"],
  "child_ids": [],
  "supersedes": [],
  "compression_level": 0,
  "created_at": "2026-03-22T10:05:00Z",
  "updated_at": "2026-03-22T10:05:00Z",
  "last_reviewed_at": "2026-03-22T10:05:00Z",
  "event_kind": "decision",
  "novelty": 0.88,
  "impact_scope": "arc",
  "source_turns": ["turn_011"]
}
```

### 事件专属字段

- `event_kind`：受控事件类别之一。
- `novelty`：事件贡献了多少新信息。
- `impact_scope`：预期的下游影响范围。
- `source_turns`：提取该事件所依据的直接轮次 ID。

### `event_kind`

```text
decision | progress | blocker | shift | completion | conflict | correction
```

### `impact_scope`

```text
local | thread | arc | epoch
```

### 事件规则

- 事件必须表示变化、决定、进展、修正或中断。
- 没有持久影响的随意陈述不应成为事件。
- 每个事件都应可追溯到一个或多个源轮次。

## 6. 场景

`Scene` 将时间接近且主题相关的事件组合在一起。它通常覆盖一天或一周。

### 场景结构

```json
{
  "id": "scene_001",
  "type": "scene",
  "title": "项目框定与角色定义",
  "summary": "在此期间，该项目被框定为一个以时间、压缩和叙事组织为中心的结构化记忆层。",
  "timespan_start": "2026-03-22T00:00:00Z",
  "timespan_end": "2026-03-22T23:59:59Z",
  "time_precision": "day",
  "importance": 0.86,
  "confidence": 0.92,
  "status": "active",
  "main_or_side": "main",
  "topics": ["architecture", "role-definition"],
  "entities": ["user", "project"],
  "evidence_refs": ["turn_009", "turn_011", "turn_013"],
  "parent_ids": ["arc_001"],
  "child_ids": ["event_001", "event_002", "event_003"],
  "supersedes": [],
  "compression_level": 1,
  "created_at": "2026-03-22T23:59:59Z",
  "updated_at": "2026-03-22T23:59:59Z",
  "last_reviewed_at": "2026-03-22T23:59:59Z",
  "scene_goal": "明确项目的概念基础",
  "key_events": ["event_001", "event_002"],
  "local_turning_points": ["event_002"],
  "open_questions": ["长期存储中应保留多少证据？"]
}
```

### 场景专属字段

- `scene_goal`：局部组织目标（如有）。
- `key_events`：对场景摘要至关重要的事件。
- `local_turning_points`：实质性改变场景内部方向的事件。
- `open_questions`：场景结束时仍然活跃的未解决问题。

### 场景规则

- 一个场景必须包含至少一个事件。
- `key_events` 必须是 `child_ids` 的子集。
- `local_turning_points` 必须是 `child_ids` 的子集。
- 场景摘要应表示局部发展，而不是对整个脉络作出判断。

## 7. 脉络

`Arc` 是持久叙事连续性的主要单元。它跨越多个场景，表示一条持续发展的脉络。

### 脉络结构

```json
{
  "id": "arc_001",
  "type": "arc",
  "title": "长期记忆管理器设计主线",
  "summary": "这条脉络跟踪面向大模型、时间优先的外部记忆框架的构建过程，包括角色边界、分层结构和长期压缩逻辑。",
  "timespan_start": "2026-03-22T00:00:00Z",
  "timespan_end": "2026-04-30T23:59:59Z",
  "time_precision": "approx",
  "importance": 0.93,
  "confidence": 0.89,
  "status": "active",
  "main_or_side": "main",
  "topics": ["memory-architecture", "timeline-indexing", "compression"],
  "entities": ["user", "project"],
  "evidence_refs": ["scene_001"],
  "parent_ids": ["epoch_001"],
  "child_ids": ["scene_001"],
  "supersedes": [],
  "compression_level": 2,
  "created_at": "2026-03-22T23:59:59Z",
  "updated_at": "2026-03-22T23:59:59Z",
  "last_reviewed_at": "2026-03-22T23:59:59Z",
  "arc_goal": "定义并最终实现记忆系统的治理框架",
  "arc_state": "active",
  "drivers": ["需要管理超出上下文长度的记忆", "需要保持时间顺序连贯"],
  "obstacles": ["避免浅层人格解读"],
  "milestones": ["scene_001"],
  "turning_points": ["scene_001"]
}
```

### 脉络专属字段

- `arc_goal`：持续目标或组织性问题。
- `arc_state`：当前动态状态。
- `drivers`：推动脉络向前发展的因素。
- `obstacles`：减缓或扭曲脉络的因素。
- `milestones`：重大进展标志。
- `turning_points`：改变脉络轨迹的场景或事件。

### `arc_state`

```text
emerging | active | stalled | dormant | resolved
```

### 脉络规则

- 一条脉络应包含一个或多个场景。
- 主线通常应超过配置的重要性阈值，例如 `0.70`。
- 脉络摘要应描述轨迹，而不是简单列出成员场景。
- 脉络的状态可以随时间变化，而其身份保持不变。

## 8. 纪元

`Epoch` 是 v1 中最高层级的篇章记忆。它表示一个较大的历史阶段。

### 纪元结构

```json
{
  "id": "epoch_001",
  "type": "epoch",
  "title": "概念基础阶段",
  "summary": "本篇章确立了以时间为中心的外部记忆系统的理论基础：分层编年、显式压缩和受证据约束的修订。",
  "timespan_start": "2026-03-01T00:00:00Z",
  "timespan_end": "2026-06-30T23:59:59Z",
  "time_precision": "month",
  "importance": 0.90,
  "confidence": 0.84,
  "status": "active",
  "main_or_side": "main",
  "topics": ["foundation", "memory-theory"],
  "entities": ["user", "project"],
  "evidence_refs": ["arc_001"],
  "parent_ids": [],
  "child_ids": ["arc_001"],
  "supersedes": [],
  "compression_level": 3,
  "created_at": "2026-03-31T23:59:59Z",
  "updated_at": "2026-03-31T23:59:59Z",
  "last_reviewed_at": "2026-03-31T23:59:59Z",
  "epoch_theme": "记忆系统的理论与架构基础",
  "major_arcs": ["arc_001"],
  "chapter_shift": "从一般构想转入正式设计计划",
  "long_term_effects": ["为后续实现与评估确立稳定概念"]
}
```

### 纪元专属字段

- `epoch_theme`：历史时期的定义性主题。
- `major_arcs`：支撑该篇章的脉络。
- `chapter_shift`：该时期内发生了什么变化。
- `long_term_effects`：延续到后续时期的持久影响。

### 纪元规则

- 一个纪元应聚合一条或多条脉络。
- 纪元摘要应强调重大历史变化和持久影响。
- 纪元不是人格标签，而是篇章层级的编年记录。

## 9. 层级约束

- `Event -> Scene -> Arc -> Epoch` 是规范的包含链。
- 高层级必须能够从低层级接受审计。
- `supersedes` 只能指向同类型对象。
- `compression_level` 应按类型保持稳定：

```text
Event = 0
Scene = 1
Arc = 2
Epoch = 3
```

## 10. 存储说明

v1 存储可以采用普通 JSON 文件或以换行分隔的 JSON 记录。存储后端不属于模式契约的一部分。

建议的持久化拆分方式：
- 原始对话记录或源轮次单独存储，
- 结构化记忆单元存储在索引文件或轻量级数据库中，
- 可选择为父子关系与取代关系实体化图边。

## 11. 最低可行合规性

如果一个实现能够做到以下各项，即符合 schema-v1：
- 创建有效的 `Event` 记录，
- 将事件聚合为 `Scene` 记录，
- 将场景绑定为 `Arc` 记录，
- 可选择以较低频率生成 `Epoch` 记录，
- 保留证据引用，
- 支持同类型取代链接。
