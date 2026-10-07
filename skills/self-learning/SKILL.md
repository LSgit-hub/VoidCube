---
name: self-learning
description: 为 VoidCube 制定有边界的技术研究和学习计划，搜索可靠资料，评估对当前项目的价值，并把结论整理为可追溯的知识记录。用户要求学习新技术、调研方案或维护学习记录时使用。
version: 2.0.0
author: VoidCube
license: MIT
metadata:
  VoidCube:
    tags: [learning, research, knowledge, memory, technology]
    related_skills: [github-repo-management, code-review, memory-maintenance-sweep]
---

# VoidCube 技术学习

把学习任务变成可验证的研究记录，而不是无限收集链接。每次学习先明确主题、时间预算、与 VoidCube 的关系和停止条件，再收集少量高质量来源，最后输出结论、证据、限制和下一步实验。

## 研究流程

1. **定义问题**：说明要回答的技术问题、当前代码入口、目标用户和不在范围内的内容。
2. **查找来源**：优先官方文档、规范、源码、发布说明和可复现的基准；记录 URL、版本、发布日期和访问时间。
3. **验证事实**：把关键 API、配置、依赖和性能结论与当前仓库或一个最小实验对照。未经验证的内容标记为推测。
4. **评估价值**：从与 VoidCube 的相关性、成熟度、集成成本、性能收益、维护风险和长期可迁移性评分，而不是只看热度。
5. **形成结论**：明确推荐采用、保留观察、仅作参考或不采用，并列出支持证据和反例。

## 记录格式

知识记录应存放在用户配置的 VoidCube 知识目录或用户指定路径；不要默认写入仓库。推荐使用 Markdown：

```markdown
# 技术主题

## 问题
- 要解决的具体问题
- 与 VoidCube 的关联入口

## 结论
- 推荐：采用 / 观察 / 参考 / 不采用
- 原因与限制

## 证据
- [来源](https://example.com)（版本、日期、关键事实）
- 本地代码、测试或实验结果

## 下一步
- 一个可验证的小实验或明确的延期条件
```

如果用户要求写入长期记忆，先把结论压缩为稳定事实、适用范围和来源，再调用 `mem_remember`；研究过程、临时想法和未经验证的推测不要写入长期记忆。

## VoidCube 工具边界

根据当前会话可用工具选择执行方式：

- 网页资料：使用 `web_search`、`web_extract` 或浏览器工具；没有这些工具时说明限制，不虚构搜索结果。
- GitHub 资料：使用 GitHub 技能和 `gh` CLI；没有认证时可以分析公开 URL，但不要伪造仓库状态。
- 本地验证：使用 `file_read`、`file_write`、`terminal` 和项目虚拟环境，遵守根目录 `AGENTS.md`。
- 项目事实：优先读取当前 `src/`、`Mem/src/`、`plugins/`、`desktop/` 和现有测试。

不要假设存在 `/self-learning` 命令、`github_search_repos`、`web_search_tool`、`web_extract_tool` 或未注册的计划任务。需要持续学习时，使用 VoidCube 已有的调度和记忆工具，并明确写入路径与所有权。

## 输出要求

每次研究结束输出：问题范围、已执行的搜索/实验、关键证据、价值判断、未验证事项和下一步。报告执行过的命令和测试，不把搜索摘要或模型推断写成实测结果。
