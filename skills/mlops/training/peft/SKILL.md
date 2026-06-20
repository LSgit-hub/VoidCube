---
name: peft-fine-tuning
description: 使用LoRA、QLoRA和25+方法进行LLM参数高效微调。当在有限GPU内存下微调大模型(7B-70B)、需要训练<1%参数且精度损失最小,或多适配器服务时使用。HuggingFace官方库,与transformers生态系统集成。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [peft>=0.13.0, transformers>=4.45.0, torch>=2.0.0, bitsandbytes>=0.43.0]
metadata:
  VoidCube:
    tags: [Fine-Tuning, PEFT, LoRA, QLoRA, Parameter-Efficient, Adapters, Low-Rank, Memory Optimization, Multi-Adapter]

---

# PEFT (参数高效微调)

使用LoRA、QLoRA和25+适配器方法,通过训练<1%参数微调LLM。

## 何时使用PEFT

**使用PEFT/LoRA的场景:**
- 在消费级GPU(RTX 4090、A100)上微调7B-70B模型
- 需要训练<1%参数(6MB适配器 vs 14GB完整模型)
- 希望使用多个任务特定适配器快速迭代
- 从一个基础模型部署多个微调变体

**使用QLoRA (PEFT + 量化)的场景:**
- 在单个24GB GPU上微调70B模型
- 内存是主要约束
- 可以接受相比全量微调约5%的质量权衡

**使用全量微调的场景:**
- 训练小模型(<1B参数)
- 需要最高质量且有计算预算
- 显著领域迁移需要更新所有权重

## 快速开始

### 安装

```bash
# 基本安装
pip install peft

# 带量化支持(推荐)
pip install peft bitsandbytes

# 完整栈
pip install peft transformers accelerate bitsandbytes datasets
```

### LoRA微调(标准)

```python
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
from peft import get_peft_model, LoraConfig, TaskType
from datasets import load_dataset

# 加载基础模型
model_name = "meta-llama/Llama-3.1-8B"
model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype="auto", device_map="auto")
tokenizer = AutoTokenizer.from_pretrained(model_name)
tokenizer.pad_token = tokenizer.eos_token

# LoRA配置
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=16,                          # 秩(8-64,更高=更大容量)
    lora_alpha=32,                 # 缩放因子(通常为2*r)
    lora_dropout=0.05,             # 用于正则化的Dropout
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],  # 注意力层
    bias="none"                    # 不训练偏置
)

# 应用LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()
# 输出: trainable params: 13,631,488 || all params: 8,043,307,008 || trainable%: 0.17%

# 准备数据集
dataset = load_dataset("databricks/databricks-dolly-15k", split="train")

def tokenize(example):
    text = f"### Instruction:\n{example['instruction']}\n\n### Response:\n{example['response']}"
    return tokenizer(text, truncation=True, max_length=512, padding="max_length")

tokenized = dataset.map(tokenize, remove_columns=dataset.column_names)

# 训练
training_args = TrainingArguments(
    output_dir="./lora-llama",
    num_train_epochs=3,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    fp16=True,
    logging_steps=10,
    save_strategy="epoch"
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized,
    data_collator=lambda data: {"input_ids": torch.stack([f["input_ids"] for f in data]),
                                 "attention_mask": torch.stack([f["attention_mask"] for f in data]),
                                 "labels": torch.stack([f["input_ids"] for f in data])}
)

trainer.train()

# 仅保存适配器(6MB vs 16GB)
model.save_pretrained("./lora-llama-adapter")
```

### QLoRA微调(内存高效)

```python
from transformers import AutoModelForCausalLM, BitsAndBytesConfig
from peft import get_peft_model, LoraConfig, prepare_model_for_kbit_training

# 4位量化配置
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",           # NormalFloat4(LLM最佳)
    bnb_4bit_compute_dtype="bfloat16",   # 用bf16计算
    bnb_4bit_use_double_quant=True       # 嵌套量化
)

# 加载量化模型
model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-3.1-70B",
    quantization_config=bnb_config,
    device_map="auto"
)

# 准备训练(启用梯度检查点)
model = prepare_model_for_kbit_training(model)

# QLoRA的LoRA配置
lora_config = LoraConfig(
    r=64,                              # 70B用更高秩
    lora_alpha=128,
    lora_dropout=0.1,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    bias="none",
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, lora_config)
# 70B模型现在可以放在单个24GB GPU上!
```

## LoRA参数选择

### 秩(r) - 容量vs效率

| 秩 | 可训练参数 | 内存 | 质量 | 用例 |
|------|-----------------|--------|---------|----------|
| 4 | ~3M | 最小 | 较低 | 简单任务,原型 |
| **8** | ~7M | 低 | 好 | **推荐起点** |
| **16** | ~14M | 中等 | 较好 | **通用微调** |
| 32 | ~27M | 较高 | 高 | 复杂任务 |
| 64 | ~54M | 高 | 最高 | 领域适配,70B模型 |

### Alpha (lora_alpha) - 缩放因子

```python
# 经验法则: alpha = 2 * rank
LoraConfig(r=16, lora_alpha=32)  # 标准
LoraConfig(r=16, lora_alpha=16)  # 保守(更低学习率效果)
LoraConfig(r=16, lora_alpha=64)  # 激进(更高学习率效果)
```

### 按架构的目标模块

```python
# Llama / Mistral / Qwen
target_modules = ["q_proj", "v_proj", "k_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

# GPT-2 / GPT-Neo
target_modules = ["c_attn", "c_proj", "c_fc"]

# Falcon
target_modules = ["query_key_value", "dense", "dense_h_to_4h", "dense_4h_to_h"]

# BLOOM
target_modules = ["query_key_value", "dense", "dense_h_to_4h", "dense_4h_to_h"]

# 自动检测所有线性层
target_modules = "all-linear"  # PEFT 0.6.0+
```

## 加载和合并适配器

### 加载训练好的适配器

```python
from peft import PeftModel, AutoPeftModelForCausalLM
from transformers import AutoModelForCausalLM

# 选项1: 用PeftModel加载
base_model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.1-8B")
model = PeftModel.from_pretrained(base_model, "./lora-llama-adapter")

# 选项2: 直接加载(推荐)
model = AutoPeftModelForCausalLM.from_pretrained(
    "./lora-llama-adapter",
    device_map="auto"
)
```

### 将适配器合并到基础模型

```python
# 合并以部署(无适配器开销)
merged_model = model.merge_and_unload()

# 保存合并模型
merged_model.save_pretrained("./llama-merged")
tokenizer.save_pretrained("./llama-merged")

# 推送到Hub
merged_model.push_to_hub("username/llama-finetuned")
```

### 多适配器服务

```python
from peft import PeftModel

# 用第一个适配器加载基础模型
model = AutoPeftModelForCausalLM.from_pretrained("./adapter-task1")

# 加载额外适配器
model.load_adapter("./adapter-task2", adapter_name="task2")
model.load_adapter("./adapter-task3", adapter_name="task3")

# 运行时切换适配器
model.set_adapter("task1")  # 使用task1适配器
output1 = model.generate(**inputs)

model.set_adapter("task2")  # 切换到task2
output2 = model.generate(**inputs)

# 禁用适配器(使用基础模型)
with model.disable_adapter():
    base_output = model.generate(**inputs)
```

## PEFT方法比较

| 方法 | 可训练% | 内存 | 速度 | 最适合 |
|--------|------------|--------|-------|----------|
| **LoRA** | 0.1-1% | 低 | 快 | 通用微调 |
| **QLoRA** | 0.1-1% | 很低 | 中等 | 内存受限 |
| AdaLoRA | 0.1-1% | 低 | 中等 | 自动秩选择 |
| IA3 | 0.01% | 最小 | 最快 | 少样本适配 |
| Prefix Tuning | 0.1% | 低 | 中等 | 生成控制 |
| Prompt Tuning | 0.001% | 最小 | 快 | 简单任务适配 |
| P-Tuning v2 | 0.1% | 低 | 中等 | NLU任务 |

### IA3 (最小参数)

```python
from peft import IA3Config

ia3_config = IA3Config(
    target_modules=["q_proj", "v_proj", "k_proj", "down_proj"],
    feedforward_modules=["down_proj"]
)
model = get_peft_model(model, ia3_config)
# 仅训练0.01%的参数!
```

### Prefix Tuning

```python
from peft import PrefixTuningConfig

prefix_config = PrefixTuningConfig(
    task_type="CAUSAL_LM",
    num_virtual_tokens=20,      # 前置token
    prefix_projection=True       # 使用MLP投影
)
model = get_peft_model(model, prefix_config)
```

## 集成模式

### 与TRL (SFTTrainer)

```python
from trl import SFTTrainer, SFTConfig
from peft import LoraConfig

lora_config = LoraConfig(r=16, lora_alpha=32, target_modules="all-linear")

trainer = SFTTrainer(
    model=model,
    args=SFTConfig(output_dir="./output", max_seq_length=512),
    train_dataset=dataset,
    peft_config=lora_config,  # 直接传递LoRA配置
)
trainer.train()
```

### 与Axolotl (YAML配置)

```yaml
# axolotl config.yaml
adapter: lora
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
lora_target_modules:
  - q_proj
  - v_proj
  - k_proj
  - o_proj
lora_target_linear: true  # 目标所有线性层
```

### 与vLLM (推理)

```python
from vllm import LLM
from vllm.lora.request import LoRARequest

# 加载带LoRA支持的基础模型
llm = LLM(model="meta-llama/Llama-3.1-8B", enable_lora=True)

# 用适配器服务
outputs = llm.generate(
    prompts,
    lora_request=LoRARequest("adapter1", 1, "./lora-adapter")
)
```

## 性能基准

### 内存使用(Llama 3.1 8B)

| 方法 | GPU内存 | 可训练参数 |
|--------|-----------|------------------|
| 全量微调 | 60+ GB | 8B (100%) |
| LoRA r=16 | 18 GB | 14M (0.17%) |
| QLoRA r=16 | 6 GB | 14M (0.17%) |
| IA3 | 16 GB | 800K (0.01%) |

### 训练速度(A100 80GB)

| 方法 | Tokens/秒 | vs全量FT |
|--------|-----------|------------|
| 全量FT | 2,500 | 1x |
| LoRA | 3,200 | 1.3x |
| QLoRA | 2,100 | 0.84x |

### 质量(MMLU基准)

| 模型 | 全量FT | LoRA | QLoRA |
|-------|---------|------|-------|
| Llama 2-7B | 45.3 | 44.8 | 44.1 |
| Llama 2-13B | 54.8 | 54.2 | 53.5 |

## 常见问题

### 训练时CUDA OOM

```python
# 解决方案1: 启用梯度检查点
model.gradient_checkpointing_enable()

# 解决方案2: 减小批大小 + 增加累积
TrainingArguments(
    per_device_train_batch_size=1,
    gradient_accumulation_steps=16
)

# 解决方案3: 使用QLoRA
from transformers import BitsAndBytesConfig
bnb_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4")
```

### 适配器未应用

```python
# 验证适配器是否激活
print(model.active_adapters)  # 应显示适配器名称

# 检查可训练参数
model.print_trainable_parameters()

# 确保模型处于训练模式
model.train()
```

### 质量退化

```python
# 增加秩
LoraConfig(r=32, lora_alpha=64)

# 目标更多模块
target_modules = "all-linear"

# 使用更多训练数据和轮次
TrainingArguments(num_train_epochs=5)

# 降低学习率
TrainingArguments(learning_rate=1e-4)
```

## 最佳实践

1. **从r=8-16开始**,质量不足时增加
2. **使用alpha = 2 * rank**作为起点
3. **目标注意力+MLP层**获得最佳质量/效率
4. **启用梯度检查点**节省内存
5. **频繁保存适配器**(小文件,易回滚)
6. **在留出数据上评估**后再合并
7. **在消费级硬件上对70B+模型使用QLoRA**

## 参考

- **[高级用法](references/advanced-usage.md)** - DoRA、LoftQ、秩稳定化、自定义模块
- **[故障排除](references/troubleshooting.md)** - 常见错误、调试、优化

## 资源

- **GitHub**: https://github.com/huggingface/peft
- **文档**: https://huggingface.co/docs/peft
- **LoRA论文**: arXiv:2106.09685
- **QLoRA论文**: arXiv:2305.14314
- **模型**: https://huggingface.co/models?library=peft
