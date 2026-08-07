---
name: grpo-rl-training
description: ⚠️ 已合并至 trl-fine-tuning — 请使用 fine-tuning-with-trl 技能。GRPO/RL 微调的所有功能（奖励函数设计、超参数调优、训练监控）已完整覆盖于 trl-fine-tuning 的 Workflow 3 和 references/online-rl.md。
version: 1.0.0
author: Orchestra Research
license: MIT
deprecated: true
redirect_to: fine-tuning-with-trl
dependencies: [transformers>=4.47.0, trl>=0.14.0, datasets>=3.2.0, peft>=0.14.0, torch]
metadata:
  VoidCube:
    tags: [Post-Training, Reinforcement Learning, GRPO, TRL, RLHF, Reward Modeling, Reasoning, DPO, PPO, Structured Output, deprecated, redirect-to-trl]

---

# ⚠️ 此技能已合并

**GRPO/RL 微调的所有功能已完整合并至 [`fine-tuning-with-trl`](../trl-fine-tuning/SKILL.md) 技能。**

请使用 `trl-fine-tuning`（技能名：`fine-tuning-with-trl`）替代。该技能包含：

- **Workflow 3**：GRPO 内存高效在线 RL 完整指南
- **references/online-rl.md**：PPO、GRPO、RLOO、OnlineDPO 详细配置
- 奖励函数设计、超参数调优、训练监控等所有原 grpo-rl-training 内容

### 快速迁移

| 原 grpo-rl-training | → trl-fine-tuning 对应位置 |
|---------------------|---------------------------|
| 奖励函数设计哲学 | Workflow 3 步骤 1 |
| GRPOConfig 配置 | Workflow 3 步骤 2 |
| GRPOTrainer 训练 | Workflow 3 步骤 3 |
| 超参数调优表 | references/online-rl.md |
| 训练监控/陷阱 | references/online-rl.md |

此文件仅作为重定向保留，未来版本将移除。
