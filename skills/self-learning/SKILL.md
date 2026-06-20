---
name: self-learning
description: 精妙的自学习系统 - 设定学习时间，自动搜索先进成熟技术，智能判断记忆价值，持续优化学习笔记
version: 1.0.0
author: VoidCube
license: MIT
metadata:
  VoidCube:
    tags: [learning, research, knowledge, memory, technology, GitHub, web-search]
    related_skills: [github-repo-management, github-code-review]
    config:
      default_learning_duration_minutes: 30
      knowledge_base_dir: ".VoidCube/knowledge"
      daily_learning_goal: 1
      priority_topics: ["AI agent", "Python", "LLM", "automation", "machine-learning"]
---

# 自学习系统

精妙的长期可持续自学习技能，帮助你：
- 设定学习时间自动搜索先进成熟技术
- 智能评估技术价值，决定是否记忆
- 持续优化学习笔记，存优去劣
- 在 GitHub 上寻找优秀解决方案

---

## 快速开始

```bash
# 启动学习会话（默认30分钟）
/self-learning start

# 指定学习主题和时长
/self-learning start --topic "AI agent 最新技术" --duration 60

# 继续之前的学习
/self-learning resume

# 查看学习历史
/self-learning history
```

---

## 核心功能

### 1. 学习会话管理

```python
# 创建学习会话
learning_session = {
    "topic": "AI agent 前沿技术",
    "duration_minutes": 45,
    "start_time": "2026-05-24T10:00:00",
    "status": "active"
}

# 保存会话记录
save_learning_session(learning_session)
```

### 2. 智能技术搜索

**网络搜索策略：**
- 搜索最新技术趋势（近3-6个月）
- 优先查找权威来源（官方文档、顶级会议论文）
- 寻找成熟度高、社区活跃的技术
- 验证技术的实用性和可操作性

**搜索关键词示例：**
```
"[topic] 2026 latest trends"
"[topic] best practices 2025-2026"
"[topic] GitHub trending repositories"
"[topic] state-of-the-art"
"[topic] production-ready"
```

### 3. GitHub 优秀方案挖掘

使用 GitHub 搜索工具：

```python
# 搜索高星仓库
github_search_repos(
    query=f"{topic} stars:>500",
    sort="stars",
    per_page=10
)

# 分析热门仓库
for repo in results:
    analyze_repository_quality(repo)
```

**仓库质量评估维度：**
- ⭐ Stars 数量（社区认可度）
- 🔄 最近更新频率（活跃度）
- 👥 Contributors 数量（协作规模）
- 📖 文档完整性
- 🧪 测试覆盖率
- 📦 依赖健康度
- 🐛 Issue 处理率

### 4. 技术价值评估系统

**记忆价值评分（0-100）：**

| 维度 | 权重 | 评估标准 |
|------|------|----------|
| 实用性 | 30% | 是否能立即应用到当前/未来项目 |
| 前沿性 | 20% | 是否代表技术发展方向 |
| 成熟度 | 20% | 稳定性、社区支持、文档完善度 |
| 学习成本 | 15% | 入门难度、学习曲线 |
| 长期价值 | 15% | 技术寿命预期、可迁移性 |

**判断是否记忆：**
- 评分 ≥ 70：**强烈建议记忆** - 存入核心知识库
- 评分 50-69：**可选记忆** - 存入备选知识库
- 评分 < 50：**暂不记忆** - 记录参考链接备查

### 5. 学习笔记管理

**笔记结构：**

```
knowledge/
├── core/                    # 核心知识库（评分≥70）
│   ├── ai-agents.md
│   ├── python-advanced.md
│   └── llm-engineering.md
├── archive/                 # 备选知识库（评分50-69）
│   └── experimental-ml.md
├── references/              # 参考资料（评分<50）
│   └── links.md
└── learning-history/        # 学习历史
    ├── 2026-05-24.md
    └── 2026-05-25.md
```

**笔记模板：**

```markdown
# [技术名称]

## 概述
- 创建时间：YYYY-MM-DD
- 价值评分：XX/100
- 分类：[core|archive]
- 来源：[URLs]

## 核心要点
- 要点1
- 要点2

## 实践指南
### 快速开始
```code
示例代码
```

### 最佳实践
- 实践1

## 相关资源
- [资源1](URL)
```

### 6. 笔记优化与存优去劣

**定期优化流程：**

1. **回顾**：读取历史笔记
2. **对比**：新旧信息对比
3. **更新**：补充新发现、修正过时内容
4. **归档**：将不再重要的内容移至 archive
5. **精炼**：压缩冗余信息，保留精华

---

## 学习流程完整示例

### 步骤1：启动学习会话

```
用户：我想学习最新的 AI agent 技术，学习45分钟
```

**执行：**
- 创建学习会话记录
- 设定倒计时提醒
- 初始化笔记草稿

### 步骤2：多源信息收集

**网络搜索：**
```python
# 搜索最新趋势
web_search_tool("AI agent 2026 latest trends state-of-the-art", limit=5)
web_search_tool("AI agent best practices production 2025-2026", limit=5)

# 提取内容
web_extract_tool(["https://..."], format="markdown")
```

**GitHub 搜索：**
```python
github_search_repos("AI agent framework stars:>1000", sort="updated", per_page=10)
github_search_repos("multi-agent system stars:>500", sort="stars", per_page=8)
```

### 步骤3：信息筛选与评估

对每个发现的技术/仓库进行评分：

```python
tech_eval = {
    "name": "LangGraph",
    "score": 85,
    "dimensions": {
        "practicality": 30,
        "cutting_edge": 18,
        "maturity": 17,
        "learning_cost": 10,
        "long_term_value": 10
    },
    "recommendation": "core",
    "summary": "..."
}
```

### 步骤4：笔记撰写

根据评分结果，为高价值技术撰写详细笔记。

### 步骤5：学习总结与回顾

学习时间结束时：
- 生成本次学习总结报告
- 对比历史笔记，更新已有内容
- 存入对应知识库（core/archive/references）

---

## 预设学习主题

以下是一些高价值的学习主题，可直接使用：

### 编程语言与框架
- Python 3.13+ 新特性与最佳实践
- Rust 异步编程与性能优化
- TypeScript 5+ 高级类型系统

### AI/ML 技术
- LLM Agent 框架（LangGraph, AutoGen, CrewAI）
- 多智能体协作系统设计
- 提示工程进阶技巧
- RAG 系统优化
- 模型微调与量化

### 开发工具与工程
- DevOps 自动化最佳实践
- 现代测试策略
- 系统设计与架构模式
- 性能优化技术

### 前沿领域
- AI 驱动的软件开发
- 多模态 AI 应用
- 边缘计算与物联网
- 量子计算入门

---

## 学习策略建议

### 番茄工作法
- 25分钟专注学习 + 5分钟休息
- 每个番茄钟完成一个明确目标

### 费曼学习法
- 用简单语言解释所学内容
- 找出理解缺口，填补知识漏洞

### 间隔重复
- 1天后回顾笔记
- 1周后再次回顾
- 1月后第三次回顾

### 实践驱动
- 每学一个技术，写一个小示例
- 将学习内容应用到实际项目

---

## 工具清单

本技能配合以下 VoidCube 工具使用：

| 工具 | 用途 |
|------|------|
| `web_search_tool` | 网络搜索最新技术 |
| `web_extract_tool` | 提取网页内容 |
| `github_search_repos` | GitHub 仓库搜索 |
| `memory_tool` | 记忆管理（可选） |
| `file_read` / `file_write` | 笔记文件操作 |
| `skill_view` | 查看本技能参考文件 |

---

## 高级用法

### 批量学习规划

```python
learning_plan = {
    "week_1": [
        {"topic": "LangGraph 入门", "duration": 60},
        {"topic": "AI Agent 设计模式", "duration": 45}
    ],
    "week_2": [...]
}
```

### 主题深度追踪

设置定期追踪特定主题的最新动态：

```python
tracked_topics = [
    "LLM agent 最新论文",
    "Python 性能优化新方法"
]

# 每周自动搜索更新
schedule_weekly_update(tracked_topics)
```

### 知识图谱构建

将学习内容关联起来，形成知识网络：

```
[LangGraph] → [多Agent系统] → [任务分解]
     ↓
[State Machine] → [Workflow Design]
```

---

## 学习效果评估

每次学习会话后，记录：
- 学习时长
- 发现的高价值技术数量
- 新增笔记数量
- 更新笔记数量
- 实践应用计划

定期回顾学习效果，调整学习策略。

---

## 相关技能

- **github-repo-management** - 深入分析和管理 GitHub 仓库
- **github-code-review** - 学习优秀代码库的代码质量

---

## 现在开始

选择一个你感兴趣的主题，启动你的自学习旅程！

```
/self-learning start --topic "你的主题" --duration 30
```
