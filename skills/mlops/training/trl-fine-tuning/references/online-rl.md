# 在线 RL 方法

PPO、GRPO、RLOO 和 OnlineDPO 在线强化学习指南。

## 概述

在线 RL 在训练期间生成补全并基于奖励进行优化。

## PPO（近端策略优化）

LLM 对齐的经典 RL 算法。

### 基础用法

```bash
python -m trl.scripts.ppo \
    --model_name_or_path Qwen/Qwen2.5-0.5B-Instruct \
    --reward_model_path reward-model \
    --dataset_name trl-internal-testing/descriptiveness-sentiment-trl-style \
    --output_dir model-ppo \
    --learning_rate 3e-6 \
    --per_device_train_batch_size 64 \
    --total_episodes 10000 \
    --num_ppo_epochs 4 \
    --kl_coef 0.05
```

### 关键参数

- `kl_coef`：KL 惩罚（0.05-0.2）
- `num_ppo_epochs`：每批次轮数（2-4）
- `cliprange`：PPO 裁剪（0.1-0.3）
- `vf_coef`：价值函数系数（0.1）

## GRPO（组相对策略优化）

内存高效的在线 RL。

### 基础用法

```python
from trl import GRPOTrainer, GRPOConfig
from datasets import load_dataset

# 定义奖励函数
def reward_func(completions, **kwargs):
    return [len(set(c.split())) for c in completions]

config = GRPOConfig(
    output_dir="model-grpo",
    num_generations=4,  # 每个提示的补全数
    max_new_tokens=128
)

trainer = GRPOTrainer(
    model="Qwen/Qwen2-0.5B-Instruct",
    reward_funcs=reward_func,
    args=config,
    train_dataset=load_dataset("trl-lib/tldr", split="train")
)
trainer.train()
```

### 关键参数

- `num_generations`：2-8 个补全
- `max_new_tokens`：64-256
- 学习率：1e-5 到 1e-4

## 内存比较

| 方法 | 内存（7B） | 速度 | 用例 |
|--------|-------------|-------|----------|
| PPO | 40GB | 中 | 最大控制 |
| GRPO | 24GB | 快 | **内存受限** |
| OnlineDPO | 28GB | 快 | 无奖励模型 |

## 参考文献

- PPO 论文：https://arxiv.org/abs/1707.06347
- GRPO 论文：https://arxiv.org/abs/2402.03300
- TRL 文档：https://huggingface.co/docs/trl/
