---
name: unsloth
description: [文档待补全] Unsloth 快速微调参考入口；当前仅提供官方文档索引，不应作为已验证训练流程使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [unsloth, torch, transformers, trl, datasets, peft]
metadata:
  VoidCube:
    tags: [Fine-Tuning, Unsloth, Fast Training, LoRA, QLoRA, Memory-Efficient, Optimization, Llama, Mistral, Gemma, Qwen]

---

# Unsloth技能

当前文件是官方文档索引，不是经过本地验证的完整训练指南。不要根据本技能直接推断版本兼容性、显存收益或可运行参数。

## 当前状态

- 未提供经过验证的最小 SFT/LoRA 配置。
- 未提供版本锁定、GPU/CUDA 矩阵或失败回退方案。
- 需要执行训练时，优先使用 `fine-tuning-with-trl` 与 `peft`，并把 Unsloth 作为明确指定的优化后端。

## 何时使用此技能

此技能应在以下情况触发：
- 使用unsloth工作时
- 询问unsloth功能或API时
- 实现unsloth解决方案时
- 调试unsloth代码时
- 学习unsloth最佳实践时

## 快速参考

### 常见模式

*快速参考模式将在你使用技能时添加。*

## 参考文件

此技能在 `references/` 中包含综合文档：

- **llms-txt.md** - Llms-Txt文档

需要详细信息时使用 `view` 读取特定参考文件。

## 使用此技能

### 初学者
从getting_started或tutorials参考文件开始了解基础概念。

### 特定功能
使用适当的分类参考文件（api、guides等）获取详细信息。

### 代码示例
上方的快速参考部分包含从官方文档提取的常见模式。

## 资源

### references/
从官方来源提取的组织化文档。这些文件包含：
- 详细解释
- 带语言标注的代码示例
- 原始文档链接
- 用于快速导航的目录

### scripts/
在此添加常见自动化任务的辅助脚本。

### assets/
在此添加模板、样板代码或示例项目。

## 备注

- 此技能从官方文档自动生成
- 参考文件保留了源文档的结构和示例
- 代码示例包含语言检测以获得更好的语法高亮
- 快速参考模式从文档中的常见用例提取

## 更新

要用更新的文档刷新此技能：
1. 使用相同配置重新运行爬虫
2. 技能将用最新信息重建

<!-- Trigger re-upload 1763621536 -->
