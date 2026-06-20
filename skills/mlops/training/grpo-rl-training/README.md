# GRPO/RL 训练技能

**使用 TRL 进行群组相对策略优化的专家级指导**

## 📁 技能结构

```
grpo-rl-training/
├── SKILL.md                              # 主技能文档（首先阅读此文件）
├── README.md                             # 本文件
├── templates/
│   └── basic_grpo_training.py            # 生产就绪的训练模板
└── examples/
    └── reward_functions_library.py       # 20+ 奖励函数示例
```

## 🚀 快速开始

1. **阅读 SKILL.md** - 包含所有概念和模式的综合指南
2. **复制 `templates/basic_grpo_training.py`** - 从可工作的代码开始
3. **浏览 `examples/reward_functions_library.py`** - 为你的任务选择奖励函数
4. **根据用例修改** - 调整数据集、奖励和配置

## 💡 内容概览

### SKILL.md（主文档）
- 核心 GRPO 概念和算法基础
- 完整实现工作流（数据集 → 奖励 → 训练 → 部署）
- 10+ 奖励函数示例及代码
- 超参数调优指南
- 训练洞察（损失行为、指标、调试）
- 故障排除指南
- 生产最佳实践

### 模板
- **basic_grpo_training.py**：最小化、生产就绪的训练脚本
  - 使用 Qwen 2.5 1.5B Instruct
  - 3 个奖励函数（格式 + 正确性）
  - LoRA 用于高效训练
  - 完整文档，可直接运行

### 示例
- **reward_functions_library.py**：20+ 经实战测试的奖励函数
  - 正确性奖励（精确匹配、模糊匹配、数值、代码执行）
  - 格式奖励（XML、JSON、严格/宽松）
  - 长度奖励（理想长度、最小/最大）
  - 风格奖励（推理质量、引用、重复惩罚）
  - 组合奖励（多目标优化）
  - 常见任务的预设集合

## 📖 代理使用指南

当此技能加载到你的代理上下文中时：

1. **实现前始终先阅读 SKILL.md**
2. **从简单开始** - 使用基于长度的奖励验证设置
3. **增量构建** - 一次添加一个奖励函数
4. **参考示例** - 从 reward_functions_library.py 复制模式
5. **监控训练** - 观察奖励指标（而非损失！）

## 🎯 常见用例

| 任务类型 | 推荐奖励 | 模板 |
|-----------|---------------------|----------|
| 数学推理 | `MATH_REASONING_REWARDS` 预设 | basic_grpo_training.py |
| 代码生成 | `CODE_GENERATION_REWARDS` 预设 | 修改模板中的数据集 |
| 摘要 | `SUMMARIZATION_REWARDS` 预设 | 调整提示 + 奖励 |
| 问答 | `QA_REWARDS` 预设 | 使用模糊匹配 + 引用 |

## ⚠️ 关键提醒

- **训练期间损失上升** - 这是正常的（它是 KL 散度）
- **使用 3-5 个奖励函数** - 单一奖励经常失败
- **训练前测试奖励** - 独立调试每个函数
- **监控 reward_std** - 应保持 > 0.1（避免模式崩塌）
- **从 num_generations=4-8 开始** - 如果 GPU 允许则扩展

## 🔗 外部资源

- [TRL 文档](https://huggingface.co/docs/trl)
- [DeepSeek R1 论文](https://arxiv.org/abs/2501.12948)
- [Open R1 实现](https://github.com/huggingface/open-r1)
- [Unsloth（快 2-3 倍）](https://docs.unsloth.ai/)

## 📝 版本

**v1.0.0** - 初始发布（2025 年 1 月）

## 👨‍💻 维护者

Orchestra Research
如有问题或改进建议，请访问 https://orchestra.com

---

**许可证：** MIT
**最后更新：** 2025 年 1 月
