---
name: context-optimization
category: devops
description: 诊断和修复 VoidCube agent 运行时上下文膨胀问题——skill 索引两级加载、memory prefetch 去噪+min_score过滤、use_prior_content中文标记、prefetch诊断日志。
---

# Context Optimization — VoidCube Agent 上下文瘦身

诊断和修复 VoidCube agent 运行时上下文膨胀问题，包括 skill 索引、memory prefetch、tool schema 和 fallback 标记。

## 触发条件

- 用户报告"上下文太大"/"token 消耗高"/"响应慢"
- 分析 system prompt 组成
- 优化 memory prefetch 质量
- 修复 use_prior_content fallback 的用户体验

## 核心问题与修复方案

### 1. Skill 索引两级加载

**问题**：`build_skills_system_prompt()` 每次注入全量 skill name+description，40+ skills 占 3000-6000 chars。

**修复**（`prompt_builder.py`）：
- 新增 `_build_skills_index()` 和 `_build_skills_index_line()` 生成轻量索引
- `build_skills_system_prompt(mode="index")` 返回仅名称+首句摘要（~300 chars）
- 缓存 key 包含 mode，full/index 独立缓存
- 描述截断到第一句或 80 chars

**使用**：
```python
# 首轮用 index 模式
skills_prompt = build_skills_system_prompt(
    available_tools=self.valid_tool_names,
    available_toolsets=avail_toolsets,
    mode="index",  # 或 "full"
)
```

### 2. Memory Prefetch 去噪 + min_score 过滤

**问题**：prefetch 返回的 JSON 包含 trace_id、score、recall_status 等噪声字段；无质量门槛导致低分记忆注入上下文。

**修复**（`memory_manager.py`）：
- `_sanitize_memory_context()`: 剥离元数据，只保留 summary/title/timestamp/speaker/tier/count
- `_sanitize_and_filter_context()`: 合并去噪 + min_score 过滤
- `build_memory_context_block(min_score=0.5)`: 低于门槛的结果静默丢弃
- 兼容 `{data: {results: [...]}}` 嵌套结构和 `{results: [...]}` 扁平结构

**预期效果**：约 61% token 节省（377→150 chars）。

### 3. use_prior_content Fallback 中文标记

**问题**：streaming 中断后，系统用上一轮内容作为 final response，用户误以为是完整回复。

**修复**（`response_disposition.py`）：
- Status 提示改为：`⚠️ 响应不完整 — 工具调用后流式传输中断，显示之前的消息（工具可能未执行）`
- Final response 前缀：`[响应在工具调用前中断——显示的是尝试调用工具之前的预览内容，上方列出的工具可能未执行。]`

### 4. Prefetch 诊断日志

**位置**：`runner.py` ~4089 行

```python
logger.info("Memory prefetch: %d chars / ~%d tokens (fenced block: %d chars)")
```

每次 session 启动时记录，用于评估 min_score 阈值是否需要调整。

## 实施注意事项

1. **Tool schema 分组懒加载**：完整实现需要修改 `_chat_request_config()`、检测 tool_call、维护已加载状态，改动面大。当前通过配置 `enabled_toolsets`/`disabled_toolsets` 已有基础支持，优先做 skill 索引优化。

2. **向后兼容**：`build_skills_system_prompt(mode="full")` 是默认值，现有调用无需修改。`build_memory_context_block()` 新增的 `min_score` 参数有默认值 0.5。

3. **测试要求**：
   - `pytest tests/test_skill_registry.py` — skill 相关
   - `pytest tests/test_memory_manager.py` — memory 相关
   - `pytest tests/test_response_disposition.py` — fallback 相关

4. **Worktree vs 主仓库**：改动需同步到 `~/.VoidCube/runtime/body/slots/slot-A/worktree/` 才能在新 session 生效。主仓库路径 `F:/My_code/VScode_py/VoidCube/`。

## 相关文件

- `src/voidcube/runtime/agent/prompt_builder.py` — skill 索引
- `src/voidcube/application/memory_manager.py` — memory 去噪+过滤
- `src/voidcube/domain/agent/response_disposition.py` — fallback 标记
- `src/voidcube/runtime/agent/runner.py` — prefetch 日志