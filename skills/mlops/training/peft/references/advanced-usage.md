# PEFT 高级用法指南

## 高级 LoRA 变体

### DoRA（权重分解低秩适应）

DoRA 将权重分解为幅度和方向分量，通常比标准 LoRA 效果更好：

```python
from peft import LoraConfig

dora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    use_dora=True,  # 启用 DoRA
    task_type="CAUSAL_LM"
)

model = get_peft_model(model, dora_config)
```

**何时使用 DoRA**：
- 在指令遵循任务上始终优于 LoRA
- 由于幅度向量，内存稍高（~10%）
- 最适合质量关键的微调

### AdaLoRA（自适应秩）

根据重要性自动调整每层秩：

```python
from peft import AdaLoraConfig

adalora_config = AdaLoraConfig(
    init_r=64,              # 初始秩
    target_r=16,            # 目标平均秩
    tinit=200,              # 预热步数
    tfinal=1000,            # 最终剪枝步数
    deltaT=10,              # 秩更新频率
    beta1=0.85,
    beta2=0.85,
    orth_reg_weight=0.5,    # 正交正则化
    target_modules=["q_proj", "v_proj"],
    task_type="CAUSAL_LM"
)
```

**优势**：
- 为重要层分配更多秩
- 可在保持质量的同时减少总参数
- 适合探索最优秩分布

### LoRA+（非对称学习率）

A 和 B 矩阵使用不同学习率：

```python
from peft import LoraConfig

# LoRA+ 对 B 矩阵使用更高学习率
lora_plus_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules="all-linear",
    use_rslora=True,  # 秩稳定 LoRA（相关技术）
)

# LoRA+ 的手动实现
from torch.optim import AdamW

# 分组参数
lora_A_params = [p for n, p in model.named_parameters() if "lora_A" in n]
lora_B_params = [p for n, p in model.named_parameters() if "lora_B" in n]

optimizer = AdamW([
    {"params": lora_A_params, "lr": 1e-4},
    {"params": lora_B_params, "lr": 1e-3},  # B 高 10 倍
])
```

### rsLoRA（秩稳定 LoRA）

缩放 LoRA 输出以稳定不同秩的训练：

```python
lora_config = LoraConfig(
    r=64,
    lora_alpha=64,
    use_rslora=True,  # 启用秩稳定缩放
    target_modules="all-linear"
)
```

**何时使用**：
- 实验不同秩时
- 帮助在不同秩值间保持一致行为
- 推荐用于 r > 32

## LoftQ（LoRA 微调感知量化）

初始化 LoRA 权重以补偿量化误差：

```python
from peft import LoftQConfig, LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, BitsAndBytesConfig

# LoftQ 配置
loftq_config = LoftQConfig(
    loftq_bits=4,              # 量化位数
    loftq_iter=5,              # 交替优化迭代次数
)

# 带 LoftQ 初始化的 LoRA 配置
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules="all-linear",
    init_lora_weights="loftq",
    loftq_config=loftq_config,
    task_type="CAUSAL_LM"
)

# 加载量化模型
bnb_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4")
model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-3.1-8B",
    quantization_config=bnb_config
)

model = get_peft_model(model, lora_config)
```

**相比标准 QLoRA 的优势**：
- 量化后更好的初始质量
- 更快收敛
- 基准测试上准确率高 ~1-2%

## 自定义模块目标

### 目标特定层

```python
# 仅目标第一和最后 transformer 层
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["model.layers.0.self_attn.q_proj",
                    "model.layers.0.self_attn.v_proj",
                    "model.layers.31.self_attn.q_proj",
                    "model.layers.31.self_attn.v_proj"],
    layers_to_transform=[0, 31]  # 替代方法
)
```

### 层模式匹配

```python
# 仅目标 0-10 层
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules="all-linear",
    layers_to_transform=list(range(11)),  # 0-10 层
    layers_pattern="model.layers"
)
```

### 排除特定层

```python
lora_config = LoraConfig(
    r=16,
    target_modules="all-linear",
    modules_to_save=["lm_head"],  # 完整训练这些（非 LoRA）
)
```

## 嵌入和 LM Head 训练

### 用 LoRA 训练嵌入

```python
from peft import LoraConfig

# 包含嵌入
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj", "embed_tokens"],  # 包含嵌入
    modules_to_save=["lm_head"],  # 完整训练 lm_head
)
```

### 用 LoRA 扩展词表

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import get_peft_model, LoraConfig

# 添加新 token
tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-3.1-8B")
new_tokens = ["<custom_token_1>", "<custom_token_2>"]
tokenizer.add_tokens(new_tokens)

# 调整模型嵌入
model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.1-8B")
model.resize_token_embeddings(len(tokenizer))

# 配置 LoRA 训练新嵌入
lora_config = LoraConfig(
    r=16,
    target_modules="all-linear",
    modules_to_save=["embed_tokens", "lm_head"],  # 完整训练这些
)

model = get_peft_model(model, lora_config)
```

## 多适配器模式

### 适配器组合

```python
from peft import PeftModel

# 加载带多个适配器的模型
model = AutoPeftModelForCausalLM.from_pretrained("./base-adapter")
model.load_adapter("./style-adapter", adapter_name="style")
model.load_adapter("./task-adapter", adapter_name="task")

# 组合适配器（加权和）
model.add_weighted_adapter(
    adapters=["style", "task"],
    weights=[0.7, 0.3],
    adapter_name="combined",
    combination_type="linear"  # 或 "cat", "svd"
)

model.set_adapter("combined")
```

### 适配器堆叠

```python
# 堆叠适配器（顺序应用）
model.add_weighted_adapter(
    adapters=["base", "domain", "task"],
    weights=[1.0, 1.0, 1.0],
    adapter_name="stacked",
    combination_type="cat"  # 拼接适配器输出
)
```

### 动态适配器切换

```python
import torch

class MultiAdapterModel:
    def __init__(self, base_model_path, adapter_paths):
        self.model = AutoPeftModelForCausalLM.from_pretrained(adapter_paths[0])
        for name, path in adapter_paths[1:].items():
            self.model.load_adapter(path, adapter_name=name)

    def generate(self, prompt, adapter_name="default"):
        self.model.set_adapter(adapter_name)
        return self.model.generate(**self.tokenize(prompt))

    def generate_ensemble(self, prompt, adapters, weights):
        """使用加权适配器集成生成"""
        outputs = []
        for adapter, weight in zip(adapters, weights):
            self.model.set_adapter(adapter)
            logits = self.model(**self.tokenize(prompt)).logits
            outputs.append(weight * logits)
        return torch.stack(outputs).sum(dim=0)
```

## 内存优化

### 带 LoRA 的梯度检查点

```python
from peft import prepare_model_for_kbit_training

# 启用梯度检查点
model = prepare_model_for_kbit_training(
    model,
    use_gradient_checkpointing=True,
    gradient_checkpointing_kwargs={"use_reentrant": False}
)
```

### 训练的 CPU 卸载

```python
from accelerate import Accelerator

accelerator = Accelerator(
    mixed_precision="bf16",
    gradient_accumulation_steps=8,
    cpu_offload=True  # 卸载优化器状态到 CPU
)

model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)
```

### 带 LoRA 的内存高效注意力

```python
from transformers import AutoModelForCausalLM

# 组合 Flash Attention 2 和 LoRA
model = AutoModelForCausalLM.from_pretrained(
    "meta-llama/Llama-3.1-8B",
    attn_implementation="flash_attention_2",
    torch_dtype=torch.bfloat16
)

# 应用 LoRA
model = get_peft_model(model, lora_config)
```

## 推理优化

### 合并以部署

```python
# 将适配器权重合并到基础模型
merged_model = model.merge_and_unload()

# 量化合并模型用于推理
from transformers import BitsAndBytesConfig

bnb_config = BitsAndBytesConfig(load_in_4bit=True)
quantized_model = AutoModelForCausalLM.from_pretrained(
    "./merged-model",
    quantization_config=bnb_config
)
```

### 导出为不同格式

```python
# 导出为 GGUF (llama.cpp)
# 先合并，再转换
merged_model.save_pretrained("./merged-model")

# 使用 llama.cpp 转换器
# python convert-hf-to-gguf.py ./merged-model --outfile model.gguf

# 导出为 ONNX
from optimum.onnxruntime import ORTModelForCausalLM

ort_model = ORTModelForCausalLM.from_pretrained(
    "./merged-model",
    export=True
)
ort_model.save_pretrained("./onnx-model")
```

### 批量适配器推理

```python
from vllm import LLM
from vllm.lora.request import LoRARequest

# 初始化带 LoRA 支持
llm = LLM(
    model="meta-llama/Llama-3.1-8B",
    enable_lora=True,
    max_lora_rank=64,
    max_loras=4  # 最大并发适配器
)

# 使用不同适配器批量处理
requests = [
    ("prompt1", LoRARequest("adapter1", 1, "./adapter1")),
    ("prompt2", LoRARequest("adapter2", 2, "./adapter2")),
    ("prompt3", LoRARequest("adapter1", 1, "./adapter1")),
]

outputs = llm.generate(
    [r[0] for r in requests],
    lora_request=[r[1] for r in requests]
)
```

## 训练配方

### 指令调优配方

```python
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    lora_dropout=0.05,
    target_modules="all-linear",
    bias="none",
    task_type="CAUSAL_LM"
)

training_args = TrainingArguments(
    output_dir="./output",
    num_train_epochs=3,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    lr_scheduler_type="cosine",
    warmup_ratio=0.03,
    bf16=True,
    logging_steps=10,
    save_strategy="steps",
    save_steps=100,
    eval_strategy="steps",
    eval_steps=100,
)
```

### 代码生成配方

```python
lora_config = LoraConfig(
    r=32,              # 代码用更高秩
    lora_alpha=64,
    lora_dropout=0.1,
    target_modules=["q_proj", "v_proj", "k_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    bias="none",
    task_type="CAUSAL_LM"
)

training_args = TrainingArguments(
    learning_rate=1e-4,        # 代码用更低学习率
    num_train_epochs=2,
    max_seq_length=2048,       # 更长序列
)
```

### 对话/聊天配方

```python
from trl import SFTTrainer

lora_config = LoraConfig(
    r=16,
    lora_alpha=16,  # 聊天用 alpha = r
    lora_dropout=0.05,
    target_modules="all-linear"
)

# 使用聊天模板
def format_chat(example):
    messages = [
        {"role": "user", "content": example["instruction"]},
        {"role": "assistant", "content": example["response"]}
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False)

trainer = SFTTrainer(
    model=model,
    peft_config=lora_config,
    train_dataset=dataset.map(format_chat),
    max_seq_length=1024,
)
```

## 调试和验证

### 验证适配器应用

```python
# 检查哪些模块应用了 LoRA
for name, module in model.named_modules():
    if hasattr(module, "lora_A"):
        print(f"LoRA applied to: {name}")

# 打印详细配置
print(model.peft_config)

# 检查适配器状态
print(f"Active adapters: {model.active_adapters}")
print(f"Trainable: {sum(p.numel() for p in model.parameters() if p.requires_grad)}")
```

### 与基础模型比较

```python
# 使用适配器生成
model.set_adapter("default")
adapter_output = model.generate(**inputs)

# 不使用适配器生成
with model.disable_adapter():
    base_output = model.generate(**inputs)

print(f"Adapter: {tokenizer.decode(adapter_output[0])}")
print(f"Base: {tokenizer.decode(base_output[0])}")
```

### 监控训练指标

```python
from transformers import TrainerCallback

class LoRACallback(TrainerCallback):
    def on_log(self, args, state, control, logs=None, **kwargs):
        if "loss" in logs:
            # 记录适配器特定指标
            model = kwargs["model"]
            lora_params = sum(p.numel() for n, p in model.named_parameters()
                            if "lora" in n and p.requires_grad)
            print(f"Step {state.global_step}: loss={logs['loss']:.4f}, lora_params={lora_params}")
```
