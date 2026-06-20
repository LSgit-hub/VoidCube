# 奖励建模

使用 TRL 为 RLHF 流水线训练奖励模型指南。

## 概述

奖励模型基于人类偏好对补全评分。用于：
- PPO 训练（RL 反馈）
- GRPO 在线 RL
- 补全排序

## 基础训练

```python
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from trl import RewardTrainer, RewardConfig
from datasets import load_dataset

# 加载模型（num_labels=1 用于单一奖励分数）
model = AutoModelForSequenceClassification.from_pretrained(
    "Qwen/Qwen2.5-0.5B-Instruct",
    num_labels=1
)
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")

# 加载偏好数据集（chosen/rejected 对）
dataset = load_dataset("trl-lib/ultrafeedback_binarized", split="train")

# 配置
config = RewardConfig(
    output_dir="Qwen2.5-Reward",
    per_device_train_batch_size=2,
    num_train_epochs=1,
    learning_rate=1e-5
)

# 训练
trainer = RewardTrainer(
    model=model,
    args=config,
    processing_class=tokenizer,
    train_dataset=dataset
)
trainer.train()
```

## 数据集格式

必需字段：
```json
{
  "prompt": "问题或指令",
  "chosen": "更好的回答",
  "rejected": "更差的回答"
}
```

## Bradley-Terry 损失

默认损失函数：
```
loss = -log(sigmoid(reward_chosen - reward_rejected))
```

学习使 chosen 分数 > rejected 分数。

## 使用奖励模型

### 推理

```python
from transformers import pipeline

# 加载训练好的奖励模型
reward_pipe = pipeline("text-classification", model="Qwen2.5-Reward")

# 对补全评分
texts = ["Good answer", "Bad answer"]
scores = reward_pipe(texts)
print(scores)  # 分数越高 = 越好
```

### 在 PPO 中

```python
from trl import PPOTrainer, PPOConfig

config = PPOConfig(
    reward_model_path="Qwen2.5-Reward"  # 使用训练好的奖励模型
)

trainer = PPOTrainer(
    model=policy_model,
    config=config,
    # 奖励模型自动加载
)
```

## 超参数

| 模型大小 | 学习率 | 批量大小 | 轮数 |
|------------|---------------|------------|--------|
| <1B | 2e-5 | 4-8 | 1-2 |
| 1-7B | 1e-5 | 2-4 | 1 |
| 7-13B | 5e-6 | 1-2 | 1 |

## 评估

检查奖励分离：
```python
# chosen 应该比 rejected 分数高
chosen_rewards = model(**chosen_inputs).logits
rejected_rewards = model(**rejected_inputs).logits

accuracy = (chosen_rewards > rejected_rewards).float().mean()
print(f"Accuracy: {accuracy:.2%}")  # 目标：>80%
```

## 参考文献

- InstructGPT 论文：https://arxiv.org/abs/2203.02155
- TRL 文档：https://huggingface.co/docs/trl/reward_trainer
