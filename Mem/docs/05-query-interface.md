# 查询接口 v1

## 1. 目的

本文档定义下游系统如何查询记忆层。该接口围绕时间优先检索、结构相关性和感知证据的返回格式构建。

## 2. 检索理念

查询层应能回答以下问题：
- 特定时间发生了什么，
- 某一时期有哪些主线，
- 一个主题如何演变，
- 哪些脉络当前处于活跃或休眠状态，
- 一份摘要源自何处。

接口默认不应重放原始对话记录。

## 3. 核心查询类型

### `point_query`
返回特定时间点或其附近发生的事情。

### `range_query`
返回一个时间区间内的主要发展。

### `theme_evolution`
返回一个主题、实体或项目随时间发展的历史。

### `active_arcs`
返回当前活跃、停滞或休眠的主要脉络。

### `chapter_summary`
返回一个较大历史时期的纪元层级概览。

### `evidence_trace`
返回记忆对象或摘要背后的证据链。

## 4. 规范请求结构

```json
{
  "query_type": "range_query",
  "time_start": "2026-01-01T00:00:00Z",
  "time_end": "2026-03-31T23:59:59Z",
  "topic": "memory-system",
  "entity": "user",
  "status_filter": ["active", "dormant"],
  "detail_level": "brief|standard|deep",
  "include_evidence": true,
  "include_superseded": false,
  "max_results": 10
}
```

## 5. 查询参数

- `query_type`：支持的查询模式之一。
- `time_start`、`time_end`：可选时间边界。
- `topic`：可选主题过滤器。
- `entity`：可选实体过滤器。
- `status_filter`：允许的生命周期状态。
- `detail_level`：输出密度控制。
- `include_evidence`：是否公开证据引用。
- `include_superseded`：是否允许过时版本。
- `max_results`：结果数量上限。

## 6. 检索流水线

默认检索流程为：
1. 时间过滤，
2. 结构过滤，
3. 语义扩展，
4. 排序，
5. 压缩为响应。

### 阶段 1：时间过滤
- 识别相关时间区间；
- 必要时针对大致范围或重叠范围略微扩展区间。

### 阶段 2：结构过滤
- 范围摘要优先使用脉络；
- 局部情节优先使用场景；
- 大型篇章查询优先使用纪元。

### 阶段 3：语义扩展
- 使用主题、实体和语义相似度，找回精确过滤器遗漏的相关候选项。

### 阶段 4：排序
- 按时间匹配度、结构相关性、重要性、语义匹配度和新近程度排序。

### 阶段 5：压缩为响应
- 为请求的详细程度生成最小的连贯回答结构。

## 7. 排序公式

建议的 v1 排序公式：

```text
final_rank =
  0.35 * temporal_overlap +
  0.25 * structural_relevance +
  0.20 * importance +
  0.10 * semantic_similarity +
  0.10 * recency
```

## 8. 规范响应结构

### Retention review

`POST /compressed/retention-review` 是只读维护报告接口，用于审查长期记忆的
休眠和遗忘候选，不执行状态变更或删除。实际自动维护由后台规则推进：先写入
`purge_candidate`，观察期后逻辑 `purged`，再在审计保留期后物理清理。

请求字段：

```json
{
  "owner_id": "local-user",
  "workspace_id": "default",
  "memory_actor": "api_a",
  "source_domains": ["agent_interaction"],
  "reference_time": "2026-09-01T00:00:00Z",
  "include_protected": true,
  "limit": 50
}
```

响应中：

- `dormant_candidates` 只包含可考虑休眠的 `Arc`；
- `purge_candidates` 只包含可考虑遗忘的低价值 `Event` / `Scene`；
- `protected` 说明未入选 purge 的保护原因；
- `dry_run` 必须为 `true`，表示该审查接口不会改变数据库。

### 范围摘要响应

```json
{
  "result_type": "range_summary",
  "observed": ["..."],
  "main_arcs": ["..."],
  "side_arcs": ["..."],
  "turning_points": ["..."],
  "open_questions": ["..."],
  "evidence_refs": ["scene_001", "arc_003"],
  "confidence": 0.88
}
```

### 时间点查询响应

```json
{
  "result_type": "point_summary",
  "events": ["..."],
  "local_scene": "...",
  "related_arc": "...",
  "evidence_refs": ["event_014"],
  "confidence": 0.91
}
```

### 主题演变响应

```json
{
  "result_type": "theme_evolution",
  "theme": "memory-system",
  "timeline": [
    {
      "time": "2026-03",
      "shift": "从宽泛构想转向正式设计"
    }
  ],
  "active_state": "active",
  "major_turning_points": ["scene_001"],
  "evidence_refs": ["arc_001"],
  "confidence": 0.87
}
```

### 证据轨迹响应

```json
{
  "result_type": "evidence_trace",
  "target_id": "arc_001",
  "summary": "...",
  "support_chain": [
    "arc_001",
    "scene_001",
    "event_001",
    "turn_011"
  ]
}
```

## 9. 详细程度

### `brief`
- 优先使用一至三条脉络层级陈述；
- 尽量少公开证据。

### `standard`
- 包含核心结构、转折点和活跃问题。

### `deep`
- 包含低层级支持信息，以及更多从事件到场景的链条。

## 10. 已取代记录的处理

默认行为：
- 从标准检索中排除已取代记录，
- 简明回答中只包含当前有效视图，
- 仅在 `include_superseded = true` 或审计模式下公开已取代材料。

## 11. 查询安全规则

在以下情况下，系统应拒绝给出过度断言的答案：
- 时间边界过于模糊，
- 请求的主题缺少纵向证据，
- 所有相关结果均已被取代且尚未解决，
- 证据过于稀疏，无法支持结构化回答。

在这些情况下，接口应返回部分结果及不确定性说明。

## 12. 最小 v1 API 表面

最小的实用集合为：
- `point_query`
- `range_query`
- `theme_evolution`
- `active_arcs`
- `evidence_trace`

纪元生成稳定后，可以添加 `chapter_summary`。
