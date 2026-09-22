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

## 关键事实（2026-09-22 探针实测，动手前必读）

1. **memory 侧实现已不在 `memory_manager.py`**：`build_memory_context_block()` 与 `_sanitize_and_filter_context()` 现在位于 `src/voidcube/application/memory_context.py`（`memory_manager.py` 这个文件已不存在，历史文档里的引用会误导后续会话）。
2. **`mode="index"` 默认不启用**：`runner.py` 的调用点没有传 `mode` 参数，现网走的是 `full`。启用与否是产品决策，不是已经生效的优化。
3. **实测体积差只有 18%**：79 个技能时 `full` 8605 字符 / `index` 7052 字符。历史文档里的"大幅压缩""约 300 字符"在中文技能库上不成立。
4. **截断逻辑对中文基本失效**：`_build_skills_index_line()` 用 `desc.split(".")[0]` 取首句、超过 80 字符才硬截断；中文描述几乎不含 ASCII 句号，于是退化为"80 个汉字"的硬截断，信息量远大于英文 80 字符。

## 核心问题与修复方案

### 1. Skill 索引两级加载

**问题**：`build_skills_system_prompt()` 每次注入全量 skill name+description。

**实现**（`src/voidcube/runtime/agent/prompt_builder.py`）：
- `_build_skills_index_line()` / `_build_skills_index()` 生成轻量索引
- `build_skills_system_prompt(mode="index")` 返回仅名称+首句摘要；`mode="full"` 为默认
- 缓存 key 含 `mode`，full/index 独立缓存；主数据源是 registry（SQLite），失败时降级为文件系统扫描

**使用**：
```python
skills_prompt = build_skills_system_prompt(
    available_tools=self.valid_tool_names,
    available_toolsets=avail_toolsets,
    mode="index",  # 默认 "full"
)
```

**收益边界**：实测省 18%（见关键事实 3）。要真正瘦身应改截断逻辑（按 "——"、"，" 等中文分隔符切分或按字节截断），而不是只切模式。

### 2. Memory Prefetch 去噪 + min_score 过滤

**实现**（`src/voidcube/application/memory_context.py`）：
- `sanitize_context()`：剥离内部元数据
- `_sanitize_and_filter_context(raw, min_score=0.5)`：去噪 + 分数过滤，兼容 `{data:{results:[...]}}` 与 `{results:[...]}` 两种结构
- `build_memory_context_block(raw_context, min_score=0.5)`：包 `<memory-context>` 围栏，低于门槛的结果静默丢弃

**实测行为**（2026-09-22 探针断言）：高分保留、`normalized_score < 0.5` 被过滤、无 score 字段的放行、trace_id/query_plan 等噪声字段剥离、空输入与"全部低于阈值"返回空串。

### 3. use_prior_content Fallback 中文标记

**实现**（`src/voidcube/domain/agent/response_disposition.py`）：
- status：`⚠️ 响应不完整 — 工具调用后流式传输中断，显示之前的消息（工具可能未执行）`
- final 前缀：`[响应在工具调用前中断——显示的是尝试调用工具之前的预览内容，上方列出的工具可能未执行。]`

### 4. Prefetch 诊断日志

`runner.py` 内的 `logger.info("Memory prefetch: %d chars / ~%d tokens ...")`，每次 session 启动记录一次，用于评估 min_score 阈值。
**行号会漂**：2026-09-22 实测在 4379 行，历史文档写的 4089 已失效 —— 一律用 `grep -n` 定位，不要信历史行号。

## 实施注意事项

1. **Tool schema 分组懒加载**：完整实现需要修改 `_chat_request_config()`、检测 tool_call、维护已加载状态，改动面大。优先用配置 `enabled_toolsets`/`disabled_toolsets`。
2. **向后兼容**：`mode="full"` 是默认值，`build_memory_context_block()` 的 `min_score` 有默认值 0.5，现有调用无需修改。
3. **测试要求**：
   - `pytest tests/test_skill_registry.py`（技能索引/registry 改动，AGENTS.md 规则 5 强制）
   - `pytest tests/test_prompt_builder_skills_cache.py tests/test_skills_sync_contract.py`
   - `pytest tests/test_response_disposition.py`
   - memory 侧用例在 `Mem/tests`（`-k memory`）；`tests/` 下没有对应 memory_manager 的测试文件
4. **bundled 技能必须三方一致**：本技能在仓库与运行时各有一份，改完必须 `repo == runtime == manifest`（否则 sync 会永久判为 user-modified，仓库更新不再下发）。

## 相关文件

- `src/voidcube/runtime/agent/prompt_builder.py` — skill 索引（函数名 `build_skills_system_prompt`，无下划线前缀）
- `src/voidcube/application/memory_context.py` — memory 去噪 + min_score 过滤
- `src/voidcube/domain/agent/response_disposition.py` — fallback 标记
- `src/voidcube/runtime/agent/runner.py` — prefetch 日志与调用点

## 变更记录

- 2026-09-08: 创建
- 2026-09-22: 修正失效引用（memory_manager.py → memory_context.py，行号改为 grep 定位）；补关键事实（index 模式默认未启用、实测只省 18%、中文截断失效）；补 bundled 三方一致要求
