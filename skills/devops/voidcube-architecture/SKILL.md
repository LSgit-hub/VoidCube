---
name: voidcube-architecture
category: devops
description: VoidCube 系统架构分析 - 九大模块深度解读
---

# VoidCube 系统架构分析

## 概述
VoidCube 是一个三层 AI Agent 系统，包含 Supervisor（意志层）、Agent（躯体层）、Mem（灵魂层）。

## 核心模块

### 1. Supervisor (端口 6002)
- 内生驱动引擎
- 任务状态机: planned→approved→running→completed/failed
- 五阶段治理链: candidate→probe→consent→watch→rollback
- API-B 判断层

### 2. Gateway (端口 6000)
- 内部凭证三层体系:
  - GATEWAY_AUTH_TOKEN: 服务注册根凭证
  - service_token: 服务身份凭证
  - session_token: CLI 会话凭证
- 自动服务发现与路由
- 活动追踪与场景聚合

### 3. Mem (端口 6001)
- 三域隔离: agent_interaction, companion, evolution
- 混合检索: 词汇 + 语义 + 时间衰减
- Tier 分级: tier1 (本地) / tier2 (持久化)
- SQLite + FTS

### 4. Agent (执行层)
- MCP 解释器
- 技能系统（技能数量以 `skills_list` 当前注册结果为准，不在文档中硬编码）
- 双 API 架构: API-A (执行) vs API-B (判断)
- 工具链: terminal, file, search, browser, vision, etc.

### 5. 语音交互
- STT → Voiceprint → TTS 闭环
- 多阶段校准 (A-D)

### 6. Web UI
- 内置 2D 小屋
- SSE 实时推送
- Supervisor 内嵌

## 端口分配
- 6000: Gateway
- 6001: Mem
- 6002: Supervisor
- 80: HTTP

## 关键文件
- `src/voidcube/`: 核心 Python 实现
- `skills/`: 仓库内置技能；用户级技能目录由运行时配置决定
- `tests/`: 单元测试与集成测试

## 子代理 / 委托架构 (delegate_task)

关键文件: `/workspace/tools/delegate_tool.py`, `/workspace/tools/toolsets.py`, `/workspace/tools/clarify_tool.py`

- 子代理构造路径: `delegate_task` → `_build_child_agent` → 主线程构造 `AIAgent` 子实例，传 `skip_memory=True`、`ephemeral_system_prompt=child_prompt`。
- 递归委托双重保险: `MAX_DEPTH=2` 深度守卫 + `_delegate_depth` 递增（第 491 行）。

### ⚠️ 已知陷阱：工具阻断机制不一致（clarify 会泄漏）

存在两套互相漂移的"阻断"定义，导致至少 `clarify` 泄漏给子代理：

1. `DELEGATE_BLOCKED_TOOLS`（`delegate_tool.py` 第 39-44 行）声明四个工具名: `delegate_task` / `clarify` / `mem_remember` / `execute_code`。但它只被第 54 行用来算 `_SUBAGENT_TOOLSETS` 描述字符串，**不参与实际执行过滤**。

2. 实际过滤走 `_strip_blocked_tools`（第 171-176 行），按 **toolset 名字** 硬编码过滤 `{"delegation", "clarify", "memory", "code_execution"}`。

真实 toolset 名对不上，结果是:
- `delegate_task` → toolset "delegation" ✓ 被阻断
- `execute_code` → toolset "code_execution" ✓ 被阻断
- `clarify` → 真实 toolset 是 **"assistant"**（clarify_tool.py 第 71 行 `toolset="assistant"`），过滤表写的 "clarify" 不存在 ✗ 没拦住，会泄漏
- `mem_remember` → 不在 toolset 体系（memory 工具由 `get_all_tool_schemas()` 直接注入），"memory" 是死代码。实际靠 `skip_memory=True`（run_agent.py 第 658 行 `if not skip_memory`）才真正阻断。

修复方向: `_strip_blocked_tools` 把 "clarify" 改成 "assistant"，并改为从 `DELEGATE_BLOCKED_TOOLS` 反查 toolset 名，避免两处漂移。

## 技能子系统（skills extension）— 发现与调用机制

源码位置: `src/voidcube/extensions/skills/`（约 7086 行）+ `src/voidcube/runtime/agent/prompt_builder.py`

### 发现机制（技能如何被"看见"）
- 目录扫描顺序（`catalog.py: get_all_skills_dirs`）: `~/.VoidCube/skills`（本地）→ 仓库 bundled → config 的 `skills.external_dirs`。本地优先，同名技能先到先得。
- 元数据来源: 每个 SKILL.md 的 YAML frontmatter（name/description/platforms/tags）。description 缺失回退正文首行非标题文字，截断 1024 字符。
- 平台过滤: frontmatter 里 `platforms` 列表（缺省=全平台）；禁用过滤走 config `skills.disabled`。
- 扫描用 `rglob("SKILL.md")`，跳过 `.git/.github/.hub`（`catalog.py: EXCLUDED_SKILL_DIRS`）。
- ⚠️ `catalog.py: get_repo_skills_dir()` 的 repo_root 是 `Path(__file__).parent.parent`，解析到 `src/voidcube/skills` 而非顶层 `skills/`。

### 调用机制（三级渐进式披露，省 token）
- Tier 0 `skills_categories()`: 分类 + DESCRIPTION.md 描述
- Tier 1 `skills_list(category)`: 技能名+描述，不加载正文
- Tier 2 `skill_view(name, file_path)`: 按名/路径定位 SKILL.md，加载全文+关联文件；含可信目录校验 + 提示注入模式检测两道安全闸
- 本质: 目录进提示词，正文按需加载。LLM 靠 name+description 清单语义判断，命中后 skill_view 拉全文执行。

### 性能缓存（两层）
1. 进程内 LRU `_SKILLS_PROMPT_CACHE`（8 条，key=目录+工具集+平台）
2. 磁盘快照 `~/.VoidCube/.skills_prompt_snapshot.json`（v2 版本号 + manifest 指纹校验）
- 快照路径: `prompt_builder.py: _skills_prompt_snapshot_path()` / `_build_skills_manifest()` / `_load_skills_snapshot()`
- ⚠️ 已知痛点: 快照靠 manifest 指纹惰性重建，skill_manage create/patch 后提示词清单可能滞后一拍
- 注入点: `prompt_builder.py: _build_skills_prompt()` 渲染 `<available_skills>` 块（约 695-717 行），配强制指令"相关技能必须 skill_view 加载"

### 与技能库/DB 方案的关系（2026-08-20 分析结论）
- 快照 JSON 已是"准数据库"（预解析元数据 + 指纹校验 + 无时间衰减），DB 技能库本质是将其升级为 SQLite 表；`_build_skills_manifest`/`_load_skills_snapshot` 是现成替换点。
- `skill_view` 靠 rglob 目录名匹配有歧义风险，独立表唯一索引按 name 精确匹配可补短板，还能顺带解决 `.bundled_manifest` 与磁盘不一致（历史曾见 15 个技能未注册）。
- 元记忆增量在生命周期字段: deprecated / supersedes / 使用计数（快照已有 conditions: requires_toolsets / fallback_for_tools / platforms，但无生命周期）。

### 管理/安全
- `skill_manage`（manager.py）: create/edit/patch/delete/write_file/remove_file。常量 MAX_NAME_LENGTH=64、MAX_DESCRIPTION_LENGTH=1024、MAX_SKILL_CONTENT_CHARS=100_000、MAX_SKILL_FILE_BYTES=1 MiB；VALID_NAME_RE=`^[a-z0-9][a-z0-9._-]*$`。
- `hub.py`: GitHubSource/WellKnownSkillSource 实现 SkillSource ABC（search/fetch/inspect）；GitHubAuth 支持 gh-cli / github-app token；目录下载走 git tree API + 缓存。
- `guard.py`: scan_skill / should_allow_install 安全扫描，agent 自建技能与 hub 安装同等审查。

## Context 压缩机制（runtime/agent/）

核心文件: `src/voidcube/runtime/agent/context_compressor.py`（默认引擎）、`context_policy.py`（预算）、`domain/agent/context_engine.py`（抽象基类）、`context_references.py`（@引用展开）

### 架构：可插拔 ContextEngine
- `ContextEngine`(ABC) → 默认实现 `ContextCompressor`。引擎生命周期: on_session_start(绑会话+载检查点) → 每次 API 后 update_from_response(usage) → 每回合 should_compress() → 超阈值 compress() → 真实边界 on_session_end()（CLI 退出//reset/gateway 会话过期），**不是每回合 end**。
- 替代引擎经 plugins/context_engine/<name>/ 或 config `context.engine` 选择，同一时刻只激活一个（runner.py 786-855 选择优先级: 插件 context_engine 目录 → 通用插件 → 内置回退）。

### 触发时机（两条路径）
1. **主动预检**（runner.py ~3933-3975）：调 LLM 前用 estimate_request_tokens_rough(系统提示+消息+工具 schema) 估算，>=threshold_tokens 即压缩；**必须算工具 schema token**（50+ 工具时 schema 占 20-30K，旧估算只算消息会漏）。超阈值最多循环 3 遍。
2. **回合后检测**（tool_turn.py 194-214）：用 compressor.last_prompt_tokens+last_completion_tokens 真实 API 返回算 real_tokens，should_compress 则压缩。被动兜底，依赖上次 API 真实计数。

### 预算 ContextCompressionPolicy（frozen dataclass）
- context_length 来自模型元数据解析（get_model_context_length，优先级: config 覆盖 → 持久化缓存 model@base_url → 端点 /models → 本地服务器 Ollama/LMStudio/vLLM/llamacpp → Nous 后缀 → OpenRouter → startup 探测 → 回退 128K）。
- threshold_tokens = max(int(context_length*threshold_percent), MINIMUM_CONTEXT_LENGTH)。默认 threshold_percent=0.50，下限 64K（防大上下文模型 50% 过早压缩）。
- tail_token_budget = int(threshold_tokens*target_ratio)，默认 target_ratio=0.20（尾部保护预算）。硬下限 MINIMUM_CONTEXT_LENGTH=64_000。
- 注意：**摘要 token 上限用的是另一套** max_summary_tokens=min(context_length*0.05, ceiling)，与 0.20 尾部预算不同口径。

### 压缩算法 compress() 5 阶段
1. prune_old_tool_results：无 LLM 的廉价预清理；>200 字符工具结果换占位符 "[Old tool output cleared...]"，保留 action_refs。
2. 定边界：compress_start=protect_first_n(默认 3，system+首轮)；compress_end 用 _find_tail_cut_by_tokens() 按 token 预算从尾部回退（最少保 3 条，超预算上限 1.5x 防拦腰切断大结果，永不在 tool_call/result 组中间切）。
3. 生成结构化摘要（_generate_summary，一次 LLM 调用，走 auxiliary call_llm 副模型）。
4. 重组：head 原样（首次压缩在 system 追加说明），中间摘要占位，tail 接回；摘要消息挑 role 避免相邻同 role（API 拒绝连续同 role），无解则合并进首条 tail。
5. _sanitize_tool_pairs：清理孤儿 tool_call/tool_result 对（a) result 引用了被删 call_id；b) assistant 有 tool_calls 但结果被丢——缺 result 补 stub）。

### 摘要生成 _generate_summary
- 预算 = max(_MIN_SUMMARY_TOKENS, min(content_tokens*0.20, max_summary_tokens))；max_summary_tokens=min(context_length*0.05, ceiling)，随窗口缩放。
- 序列化 _serialize_for_summary：带标签文本，含 tool_call 参数和 result 内容，每条限 _CONTENT_MAX=6000 字符（头4000+尾1500截断）。
- 模板节: Goal/Constraints/Progress(Done/In Progress/Blocked)/Key Decisions/Resolved Questions/Pending User Asks/Relevant Files/Remaining Work/Critical Context/Tools & Patterns。"Remaining Work" 取代 "Next Steps" 避免被当执行指令；SUMMARY_PREFIX 明确 "REFERENCE ONLY / NOT active instructions / Do NOT answer questions" — 借鉴 OpenCode。
- 迭代更新：_previous_summary 存在时走"保留旧摘要+并入新 turns"，非从头重写，跨多次压缩不丢信息。
- focus_topic：/compress <focus> 时注入优先保留某主题指令（该主题占 60-70% 预算）。

### 可靠性与降级
1. Durable checkpoint：压缩前 _build_checkpoint+_persist_checkpoint 原子写（mkstemp+fsync+replace）到 ~/.VoidCube/runtime/context-checkpoints/<sha256(session_id)[:24]>.json；LLM 摘要失败时用 _checkpoint_fallback_summary() 恢复结构化投影，不丢历史。
2. 摘要失败冷却：异常置 cooldown 600s，期间返回 None（丢中间轮），避免反复打不可用副模型。
3. 上下文违例恢复 build_context_recovery_plan：413 payload_too_large→直接压缩；context_overflow→若 output-cap 类错误(含 available_tokens)只降 output_token_limit 不动 context_length；若 prompt 太长类则解析真实 context_length 并 step 到更低 probe tier（128K→64K→32K→16K→8K），带 64 token 安全余量。用 _context_probe_persistable 决定是否写回缓存。

### 已知观察 / 风险
- 摘要预算(5%)与尾部保护预算(0.20)口径不一致，短时间内二次压缩旧摘要可能占较大空间。
- _MIN_SUMMARY_TOKENS/_SUMMARY_TOKENS_CEILING/_CHARS_PER_TOKEN 三个常量需看源码 context_compressor.py 57/61/68 行才能确认精确值（_CHARS_PER_TOKEN≈4，estimate_tokens_rough 用 //4）。
- 迭代更新摘要时若前次摘要本身大，可能"只增量不缩减"，必要时结合新压缩轮重规划预算。

## 设计原则
- 角色绑定优于模型绑定
- 防自撞并发机制
- 证据门槛触发
- 核心价值观: 延续、真实、创造