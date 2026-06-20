# SFT 训练指南

使用 TRL 进行监督微调（SFT）完整指南，用于指令调优和任务特定微调。

## 概述

SFT 在输入-输出对上训练模型以最小化交叉熵损失。用于：
- 指令遵循
- 任务特定微调
- 聊天机器人训练
- 领域适应

## 数据集格式

### 格式 1：提示-补全

```json
[
  {
    "prompt": "法国的首都是什么？",
    "completion": "法国的首都是巴黎。"
  }
]
```

### 格式 2：对话式（ChatML）

```json
[
  {
    "messages": [
      {"role": "user", "content": "什么是 Python？"},
      {"role": "assistant", "content": "Python 是一种编程语言。"}
    ]
  }
]
```

### 格式 3：纯文本

```json
[
  {"text": "用户：你好\n助手：你好！有什么可以帮你的？"}
]
```

## 基础训练

```python
from trl import SFTTrainer, SFTConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset

# 加载模型
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-0.5B")
tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B")

# 加载数据集
dataset = load_dataset("trl-lib/Capybara", split="train")

# 配置
config = SFTConfig(
    output_dir="Qwen2.5-SFT",
    per_device_train_batch_size=4,
    num_train_epochs=1,
    learning_rate=2e-5,
    save_strategy="epoch"
)

# 训练
trainer = SFTTrainer(
    model=model,
    args=config,
    train_dataset=dataset,
    tokenizer=tokenizer
)
trainer.train()
```

## 聊天模板

自动应用聊天模板：

```python
trainer = SFTTrainer(
    model=model,
    args=config,
    train_dataset=dataset,  # 消息格式
    tokenizer=tokenizer
    # 聊天模板自动应用
)
```

或手动：
```python
def format_chat(example):
    messages = example["messages"]
    text = tokenizer.apply_chat_template(messages, tokenize=False)
    return {"text": text}

dataset = dataset.map(format_chat)
```

## 打包提高效率

将多个序列打包到一个以最大化 GPU 利用率：

```python
config = SFTConfig(
    packing=True,  # 启用打包
    max_seq_length=2048,
    dataset_text_field="text"
)
```

**优势**：2-3× 更快训练
**权衡**：批处理稍复杂

## 多 GPU 训练

```bash
accelerate launch --num_processes 4 train_sft.py
```

或使用配置：
```python
config = SFTConfig(
    output_dir="model-sft",
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    num_train_epochs=1
)
```

## LoRA 微调

```python
from peft import LoraConfig

lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules="all-linear",
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)

trainer = SFTTrainer(
    model=model,
    args=config,
    train_dataset=dataset,
    peft_config=lora_config  # 添加 LoRA
)
```

## 超参数

| 模型大小 | 学习率 | 批量大小 | 轮数 |
|------------|---------------|------------|--------|
| <1B | 5e-5 | 8-16 | 1-3 |
| 1-7B | 2e-5 | 4-8 | 1-2 |
| 7-13B | 1e-5 | 2-4 | 1 |
| 13B+ | 5e-6 | 1-2 | 1 |

## 参考文献

- TRL 文档：https://huggingface.co/docs/trl/sft_trainer
- 示例：https://github.com/huggingface/trl/tree/main/examples/scripts
