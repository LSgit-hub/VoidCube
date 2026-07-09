# Mem LLM-First 架构改进方案

> 2026-07 状态说明：当前 Mem / API-B 的唯一保存与解析入口是 `memory.llm.*`。例如 DeepSeek 必须通过 `memory.llm.provider=deepseek` 与 `memory.llm.api_key_env=DEEPSEEK_API_KEY` 配置，不从 API-A、主 CLI Provider 或其他 provider key 回退推断。

## 1. 问题诊断

当前 Mem 系统的根本缺陷不是"缺少 LLM 调用"，而是**架构设计上 LLM 被定位为"可选的锦上添花"——系统设计默认不需要 LLM 就能完整运行，LLM 只是一个 if-api-key-exists 的后补**。

### 1.1 当前反模式

旧实现曾经出现过的反模式：

```python
provider = config["memory"]["llm"]["provider"]
api_key_env = config["memory"]["llm"]["api_key_env"]
api_key = get_env_value(api_key_env)
if provider != "deepseek" or api_key_env != "DEEPSEEK_API_KEY":
    return wrong_slot_or_wrong_provider()
```

**后果**：API-B 配置不清晰时，系统会在没有 Mem LLM 的情况下"安静地变傻"——压缩用关键词正则、升级用 `[L2]` 前缀、内生驱动器截取文本前 80 字符、治理完全靠布尔代数。用户无从知晓系统正在以极低质量运行。

### 1.2 哪些环节必须由 LLM 驱动

| 环节 | 当前状态 | 应该是什么 |
|------|---------|-----------|
| **对话→事件提取** | HeuristicEventExtractionBackend(关键词正则) 是默认 | `LLMEventExtractionBackend` **必须是默认**，无 LLM 时**排队等待**而非静默降级 |
| **场景/弧线/纪元摘要** | HeuristicScholarBackend(模板填充) 是默认 | `LLMScholarBackend` **必须是默认** |
| **生命周期升级摘要** | 机械 `[L2]` 前缀复制原文 | LLM 重摘要**必须执行**，否则**跳过本轮升级** |
| **清退终审** | 无 LLM 就默认 purge | LLM 不可用时**保留不删**（宁可保留不可误删） |
| **内生驱动器** | 截取文本前80字符 | LLM 分析记忆上下文**必须执行**，否则候选人 utility 标记为 0.3 |
| **治理裁决** | 纯布尔代数 | LLM 推理器在模糊决策时**必须参与**，否则标记 `llm_unavailable_risk` |
| **语义搜索** | n-gram 哈希伪嵌入 | LLM embedding **必须是主路径** |

## 2. LLM-First 架构设计

### 2.1 核心原则

```
┌──────────────────────────────────────────────────────────────┐
│                    LLM-First 原则                              │
│                                                              │
│  1. LLM 是核心引擎，不是可选插件                                │
│     - LLM Provider 是第一公民配置，启动时验证连通性              │
│     - 无 LLM 时系统进入"降级模式"，明确通告                      │
│                                                              │
│  2. 关键环节"无 LLM 则等待"，非"无 LLM 则降级"                   │
│     - 压缩/升级/摘要 → 队列化，LLM 恢复后批量处理               │
│     - 治理裁决 → 标记风险，记录缺失                             │
│                                                              │
│  3. 确定性规则仅用于：                                          │
│     - Tier 1 SQLite 存取（字节级操作不需要智能）                 │
│     - 安全边界（rate limit、权限检查、进程启停）                  │
│     - 已明确的可编程规则（衰减公式、年龄阈值）                     │
│                                                              │
│  4. 降级是显式的、可观测的、有告警的                              │
│     - Supervisor UI 显示 LLM 健康状态                          │
│     - 降级运行超过 N 分钟后触发告警                             │
│     - 治理历史中记录每次降级决策                                 │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 LLM Provider 第一公民配置

**现状**：LLM 配置散落在 `os.environ.get()` 调用中，每个模块各自解释。

**改进**：统一的 `LLMProvider` 配置对象，启动时一次性验证。

```python
# systems/memory/llm_provider.py (新建)
@dataclass
class LLMProviderConfig:
    """Mem 的 LLM 引擎配置 — 第一公民，非可选插件。"""
    provider: str = "deepseek"          # deepseek | openai | anthropic | openrouter
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    
    # 健康检查
    health_check_interval: int = 300    # 每 5 分钟检查 LLM 连通性
    max_degraded_minutes: int = 60      # 降级超过 60 分钟 → 告警
    
    # 队列
    queue_max_size: int = 500           # LLM 工作队列最大长度
    queue_retry_interval: int = 120     # 队列重试间隔
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())
```

**启动验证**：

```python
class LLMProvider:
    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self.healthy = False
        self.last_health_check: datetime | None = None
        self.degraded_since: datetime | None = None
        self.work_queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue_max_size)
    
    async def startup_check(self) -> bool:
        """启动时验证 LLM 连通性。失败 → 系统进入降级模式。"""
    
    async def health_loop(self):
        """后台周期检查 LLM 健康，恢复后自动清空工作队列。"""
```

### 2.3 关键环节的 LLM 必须性分级

```
🔴 CRITICAL — 无 LLM 则排队等待，不静默降级：
  ├── 对话压缩 (Tier1→Tier2 bridge)
  ├── 场景/弧线/纪元摘要生成
  └── 生命周期升级重摘要

🟡 IMPORTANT — 无 LLM 则标记低质量，但仍产出：
  ├── 内生驱动器学习主题生成 (utility 降至 0.3)
  ├── 语义搜索 (降级为关键词匹配，结果标记 degraded=true)
  └── 清退终审 (无 LLM 时默认保留不删)

🟢 OPTIONAL — LLM 增强但非必须：
  ├── 嵌入生成 (可降级为 n-gram hash)
  └── 治理推理分析 (确定性路径仍然工作)
```

### 2.4 无 LLM 时的行为契约

| 场景 | 旧行为（静默降级） | 新行为（显式降级） |
|------|-------------------|-------------------|
| Tier 2 压缩 | 静默用关键词正则，产出低质量 Event | **排队**：turns 保持 `compressed_to_tier2=0`，LLM 恢复后批量处理 |
| 生命周期升级 | 机械加 `[L2]` 前缀，复制原文 | **跳过**：本轮不升级，条目保持当前级别等待下次 |
| 清退终审 | 无 LLM→默认 purge | **保留**：标记 `purge_deferred`，等 LLM 恢复后终审 |
| 内生驱动器 | 截取 80 字符当主题 | **标记低质量**：生成但仍产出，utility=0.3 + `llm_degraded: true` |
| 治理裁决 | 纯布尔通过 | **标记风险**：`risk_level` 升一级 + `llm_unavailable: true` |
| 语义搜索 | n-gram hash | **降级搜索**：关键词匹配 + `degraded: true` |

## 3. 具体实现计划

### Phase 1: LLM Provider 基础设施

**新建文件**: `systems/memory/llm_provider.py`

- `LLMProviderConfig` — 统一配置
- `LLMProvider` — 启动验证 + 健康循环 + 工作队列
- `get_llm_provider()` — 全局单例
- CLI 配置入口：`/api -> 3 记忆系统模型配置`。该入口只写 `memory.llm.*`，并把 API-B key 保存到对应的 `memory.llm.api_key_env`（例如 `DEEPSEEK_API_KEY`）。

**修改文件**: `VoidCube_cli/ops/serve.py`
- 启动序列中增加 LLM Provider 初始化
- 启动失败时打印明确警告而非静默继续

### Phase 2: 关键环节改为 LLM-Required

**修改文件**: `systems/memory/memory_service.py`

- `_build_compression_pipeline()` → 删除 heuristic fallback 的"静默"路径
  - LLM 可用 → `LLMEventExtractionBackend` + `LLMScholarBackend`
  - LLM 不可用 → 返回 `None`，调用方 (`_bridge_to_tier2`) 检查并跳过本轮压缩
- `_llm_escalate_summary()` → LLM 不可用时返回 `(None, None)`，调用方跳过升级
- `_llm_purge_review()` → LLM 不可用时默认返回 `True`（保留不删）

- `_compression_loop()` 增加 LLM 健康状态日志
- `tier1_stats` / `rules_status` 端点增加 `llm_healthy` 字段

### Phase 3: 内生驱动器和治理引擎

**修改文件**: `systems/supervisor/endogenous_drive.py`

- `_llm_generate_learning_topics()` 不可用时 → 仍产出候选但 `utility=0.3` + `llm_degraded: true`
- 确定性后备 `_extract_learning_topic()` 保留但标记为降级

**修改文件**: `systems/governor.py`

- `LLMGovernorReasoner` 改名为 `GovernorLLMAdvisor`，职责从"可选插件"升级为"推荐参与"
- `GovernorDecisionEngine.evaluate()` 中：LLM 不可用时，`risk_level` 自动升一级（medium→high, high→critical）

### Phase 4: 可观测性

**修改文件**: `systems/supervisor/ui_runtime.py`

- Supervisor UI 增加 LLM 健康面板：
  - 当前状态：`healthy` / `degraded` / `unavailable`
  - 降级时长
  - 队列积压数量
  - 最近 LLM 错误

**修改文件**: `systems/memory/memory_service.py`

- `GET /llm/health` — LLM 连通性检查
- `GET /llm/queue-status` — 工作队列状态

## 4. 配置文件设计

```yaml
# VoidCube 配置
memory:
  llm:
    provider: deepseek              # deepseek | openai | openrouter | ollama
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY   # 从环境变量读取
    base_url: https://api.deepseek.com/v1
    
    # 健康策略
    health_check_interval: 300      # 健康检查间隔（秒）
    max_degraded_minutes: 60        # 降级超过此时间 → UI 告警
    
    # 队列策略
    queue_enabled: true             # LLM 不可用时是否排队
    queue_max_size: 500             # 最大排队条目数
    queue_retry_interval: 120       # 队列重试间隔（秒）
    
    # 行为策略
    compression_require_llm: true   # 压缩是否强制 LLM（false=静默降级）
    escalation_require_llm: true    # 升级重摘要是否强制 LLM
    purge_require_llm: true         # 清退终审是否强制 LLM
```

## 5. 改造前后对比

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| **LLM 定位** | 可选插件，env var 后补 | 第一公民，启动验证 |
| **默认行为** | 静默降级为启发式 | 显式排队/跳过/标记 |
| **压缩质量** | 关键词正则（无 LLM 时） | LLM 语义理解（主路径） |
| **升级摘要** | `[L2]` 前缀复制原文 | LLM 逐级重摘要 |
| **清退决策** | 无条件 DELETE | LLM 终审 → 保留/清退 |
| **学习主题** | 截取 80 字符 | LLM 分析记忆生成 |
| **可观测性** | 零——用户不知道系统在降级 | 完整——UI 面板 + 告警 + 日志 |
| **配置方式** | `os.environ.get()` 散落各处 | 统一配置文件 + CLI 管理 |

## 6. 实施顺序

```
Phase 1 (1-2天): LLM Provider 基础设施
  └── llm_provider.py + 配置系统 + CLI 入口

Phase 2 (2天): 关键环节 LLM-Required
  └── memory_service 压缩/升级/清退改为 LLM 必须

Phase 3 (1天): 内生驱动器 + 治理引擎
  └── endogenous_drive 降级标记 + governor 风险升级

Phase 4 (1天): 可观测性
  └── UI 面板 + 健康端点 + 降级告警
```
