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

## 关键事实（2026-09-22 探针实测 + 2026-10-07 复测，动手前必读）

1. **memory 侧实现已不在 `memory_manager.py`**：`build_memory_context_block()` 与 `_sanitize_and_filter_context()` 现在位于 `src/voidcube/application/memory_context.py`（`memory_manager.py` 这个文件已不存在，历史文档里的引用会误导后续会话）。
2. **`mode="index"` 现已启用（2026-10-07）**：`runner.py` 的调用点已传 `mode="index"`；两种模式**共享同一强制包裹**
   （`## Skills (mandatory)` 加载规则 + `skill_manage` 提醒 + `<available_skills>` 围栏，见 `_format_skills_prompt()`），
   差别只在描述渲染：`full` 逐字全量、`index` 走关键事实 4 的有界渲染。回退只需去掉该参数。
   **切换前必须确认包裹不丢**（旧版 index 只输出清单行、会丢掉加载规则）：回归测试
   `test_index_mode_keeps_mandatory_envelope_and_trigger_clause`。
3. **实测体积差已是 47%，不再是 18%**：79 个技能时 `full` 16397 字符 / `index` 8656 字符。
   历史数字（full 8605 / index 7052，只省 18%）已失效：描述提取的 60 字硬截断被移除后 full 侧变长，
   而 index 侧改成了“头部 + 触发子句尾段”的有界渲染。引用体积数字前先按下面的命令复测。
4. **索引行截断已修，别再照旧描述**：`_build_skills_index_line()` 现在走 `_index_description()`——先经
   `_first_sentence()`（句点感知，不在 `llama.cpp` / `subprocess.run` 这类技术词内切），再取 head 预算
   70 字符 + 尾段保留“何时使用”触发子句（15 个触发词），head 与尾段做重叠去重，整行按
   `_INDEX_LINE_BUDGET=170` 回裁。实测含触发语的 44 个技能在 index 行里**全部可见（丢失 44 → 0）**。
   历史缺陷（`desc.split(".")[0]` + 80 字符硬截 → 中文触发条件整体不可见）已不存在。
5. **描述提取已从 60 字硬截改为 1024 上限**：`catalog.extract_skill_description` 用
   `MAX_DESCRIPTION_LENGTH=1024` 全量保留（多数技能把“……时使用”写在描述末尾，60 字硬截会让技能在
   提示词索引里不可发现）。注意：改这类“派生逻辑”后必须 bump `registry.DERIVED_METADATA_VERSION`，
   否则增量刷新按文件 mtime/size 复用旧派生值、修复对存量记录永不生效（实测第一次验证时 142/158 条仍是旧值）。

## 核心问题与修复方案

### 1. Skill 索引两级加载

**问题**：`build_skills_system_prompt()` 每次注入全量 skill name+description。

**实现**（`src/voidcube/runtime/agent/prompt_builder.py`）：
- `_build_skills_index_line()` / `_build_skills_index()` 生成轻量索引
- `build_skills_system_prompt(mode="index")` 返回「名称 + 有界描述」（首句头部 + 触发子句尾段，见关键事实 4）；
  `mode="full"` 只是函数默认值，`runner.py` 传的是 `index`（见关键事实 2）
- 两模式共用包裹：`_SKILLS_PROMPT_HEADER` / `_SKILLS_PROMPT_FOOTER` / `_format_skills_prompt()`，其中含"必须用 skill_view 加载"
  的强制规则，**绝不能为了省 token 丢掉**
- 缓存 key 含 `mode`，full/index 独立缓存；主数据源是 registry（SQLite），失败时降级为文件系统扫描

**使用**：
```python
skills_prompt = build_skills_system_prompt(
    available_tools=self.valid_tool_names,
    available_toolsets=avail_toolsets,
    mode="index",  # 默认 "full"
)
```

**收益边界（2026-10-07 实测，已落地）**：`runner.py` 已传 `mode="index"`，现网省 **40%**（full 16397 / index 9856 字符，
含约 1.3k 字符的强制包裹；≈2k tokens），触发子句 44/44 可见。
口径提醒：index 侧现在也带包裹，所以历史那句"省 47%"是**无包裹**口径，复测一律以 `_format_skills_prompt()` 之后的值为准。
排查顺序：先确认包裹完整（回归测试），再确认触发子句可见，最后才看体积。

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
- 2026-10-07: 复测更新——描述提取的 60 字硬截已移除（MAX_DESCRIPTION_LENGTH=1024 + DERIVED_METADATA_VERSION 强制重解析）；索引行改为“head + 触发子句尾段”有界渲染（触发子句 index 可见率 44 → 0 丢失）；体积数字更新为 full 16397 / index 8656（省 47%）
- 2026-09-22: 修正失效引用（memory_manager.py → memory_context.py，行号改为 grep 定位）；补关键事实（index 模式默认未启用、实测只省 18%、中文截断失效）；补 bundled 三方一致要求
- 2026-10-07: index 模式已在 runner 落地（含强制包裹共享）+ 本技能同步现状（省 40%、包裹不可丢、回归测试名）。
