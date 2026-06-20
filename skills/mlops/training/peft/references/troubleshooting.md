# PEFT 故障排除指南

## 安装问题

### bitsandbytes CUDA 错误

**错误**：`CUDA Setup failed despite GPU being available`

**修复**：
```bash
# 检查 CUDA 版本
nvcc --version

# 安装匹配的 bitsandbytes
pip uninstall bitsandbytes
pip install bitsandbytes --no-cache-dir

# 或为特定 CUDA 从源码编译
git clone https://github.com/TimDettmers/bitsandbytes.git
cd bitsandbytes
CUDA_VERSION=118 make cuda11x  # 根据你的 CUDA 调整
pip install .
```

### Triton 导入错误

**错误**：`ModuleNotFoundError: No module named 'triton'`

**修复**：
```bash
# 安装 triton（仅 Linux）
pip install triton

# Windows：不支持 Triton，使用 CUDA 后端
# 设置环境变量禁用 triton
export CUDA_VISIBLE_DEVICES=0
```

### PEFT 版本冲突

**错误**：`AttributeError: 'LoraConfig' object has no attribute 'use_dora'`

**修复**：
```bash
# 升级到最新 PEFT
pip install peft>=0.13.0 --upgrade

# 检查版本
python -c "import peft; print(peft.__version__)"
```

## 训练问题

### CUDA 内存不足

**错误**：`torch.cuda.OutOfMemoryError: CUDA out of memory`

**解决方案**：

1. **启用梯度检查点**：
```python
from peft import prepare_model_for_kbit_training
model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
```

2. **减小批量大小**：
```python
TrainingArguments(
    per_device_train_batch_size=1,
    gradient_accumulation_steps=16  # 保持有效批量大小
)
```

3. **使用 QLoRA**：
```python
from transformers import BitsAndBytesConfig

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True
)
model = AutoModelForCausalLM.from_pretrained(model_name, quantization_config=bnb_config)
```

4. **降低 LoRA 秩**：
```python
LoraConfig(r=8)  # 而不是 r=16 或更高
```

5. **目标更少模块**：
```python
target_modules=["q_proj", "v_proj"]  # 而不是 all-linear
```

### 损失不下降

**问题**：训练损失保持平稳或增加。

**解决方案**：

1. **检查学习率**：
```python
# 从更低开始
TrainingArguments(learning_rate=1e-4)  # 不是 2e-4 或更高
```

2. **验证适配器是否激活**：
```python
model.print_trainable_parameters()
# 应该显示 >0 可训练参数

# 检查适配器是否应用
print(model.peft_config)
```

3. **检查数据格式**：
```python
# 验证分词
sample = dataset[0]
decoded = tokenizer.decode(sample["input_ids"])
print(decoded)  # 应该看起来正确
```

4. **增加秩**：
```python
LoraConfig(r=32, lora_alpha=64)  # 更多容量
```

### NaN 损失

**错误**：`Loss is NaN`

**修复**：
```python
# 使用 bf16 而不是 fp16
TrainingArguments(bf16=True, fp16=False)

# 或启用损失缩放
TrainingArguments(fp16=True, fp16_full_eval=True)

# 降低学习率
TrainingArguments(learning_rate=5e-5)

# 检查数据问题
for batch in dataloader:
    if torch.isnan(batch["input_ids"].float()).any():
        print("NaN in input!")
```

### 适配器未训练

**问题**：`trainable params: 0` 或模型未更新。

**修复**：
```python
# 验证 LoRA 应用于正确模块
for name, module in model.named_modules():
    if "lora" in name.lower():
        print(f"Found LoRA: {name}")

# 检查 target_modules 是否匹配模型架构
from peft.utils import TRANSFORMERS_MODELS_TO_LORA_TARGET_MODULES_MAPPING
print(TRANSFORMERS_MODELS_TO_LORA_TARGET_MODULES_MAPPING.get(model.config.model_type))

# 确保模型处于训练模式
model.train()

# 检查 requires_grad
for name, param in model.named_parameters():
    if param.requires_grad:
        print(f"Trainable: {name}")
```

## 加载问题

### 适配器加载失败

**错误**：`ValueError: Can't find adapter weights`

**修复**：
```python
# 检查适配器文件是否存在
import os
print(os.listdir("./adapter-path"))
# 应该包含：adapter_config.json, adapter_model.safetensors

# 用正确结构加载
from peft import PeftModel, PeftConfig

# 检查配置
config = PeftConfig.from_pretrained("./adapter-path")
print(config)

# 先加载基础模型
base_model = AutoModelForCausalLM.from_pretrained(config.base_model_name_or_path)
model = PeftModel.from_pretrained(base_model, "./adapter-path")
```

### 基础模型不匹配

**错误**：`RuntimeError: size mismatch`

**修复**：
```python
# 确保基础模型与适配器匹配
from peft import PeftConfig

config = PeftConfig.from_pretrained("./adapter-path")
print(f"Base model: {config.base_model_name_or_path}")

# 加载完全相同的基础模型
base_model = AutoModelForCausalLM.from_pretrained(config.base_model_name_or_path)
```

### Safetensors vs PyTorch 格式

**错误**：`ValueError: We couldn't connect to 'https://huggingface.co'`

**修复**：
```python
# 强制本地加载
model = PeftModel.from_pretrained(
    base_model,
    "./adapter-path",
    local_files_only=True
)

# 或指定格式
model.save_pretrained("./adapter", safe_serialization=True)  # safetensors
model.save_pretrained("./adapter", safe_serialization=False)  # pytorch
```

## 推理问题

### 生成慢

**问题**：推理比预期慢很多。

**解决方案**：

1. **合并适配器以部署**：
```python
merged_model = model.merge_and_unload()
# 推理时无适配器开销
```

2. **使用优化推理引擎**：
```python
from vllm import LLM
llm = LLM(model="./merged-model", dtype="half")
```

3. **启用 Flash Attention**：
```python
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    attn_implementation="flash_attention_2"
)
```

### 输出质量问题

**问题**：微调模型产生更差输出。

**解决方案**：

1. **检查无适配器评估**：
```python
with model.disable_adapter():
    base_output = model.generate(**inputs)
# 与适配器输出比较
```

2. **评估时降低温度**：
```python
model.generate(**inputs, temperature=0.1, do_sample=False)
```

3. **用更多数据重训**：
```python
# 增加训练样本
# 使用更高质量数据
# 训练更多轮
```

### 错误适配器激活

**问题**：模型使用错误适配器或无适配器。

**修复**：
```python
# 检查激活适配器
print(model.active_adapters)

# 显式设置适配器
model.set_adapter("your-adapter-name")

# 列出所有适配器
print(model.peft_config.keys())
```

## QLoRA 特定问题

### 量化错误

**错误**：`RuntimeError: mat1 and mat2 shapes cannot be multiplied`

**修复**：
```python
# 确保计算 dtype 匹配
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.bfloat16,  # 匹配模型 dtype
    bnb_4bit_quant_type="nf4"
)

# 用正确 dtype 加载
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    quantization_config=bnb_config,
    torch_dtype=torch.bfloat16
)
```

### QLoRA OOM

**错误**：即使 4 位量化也 OOM。

**修复**：
```python
# 启用双重量化
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_use_double_quant=True  # 进一步减少内存
)

# 使用卸载
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    quantization_config=bnb_config,
    device_map="auto",
    max_memory={0: "20GB", "cpu": "100GB"}
)
```

### QLoRA 合并失败

**错误**：`RuntimeError: expected scalar type BFloat16 but found Float`

**修复**：
```python
# 合并前反量化
from peft import PeftModel

# 用更高精度加载以合并
base_model = AutoModelForCausalLM.from_pretrained(
    base_model_name,
    torch_dtype=torch.float16,  # 未量化
    device_map="auto"
)

# 加载适配器
model = PeftModel.from_pretrained(base_model, "./qlora-adapter")

# 现在合并
merged = model.merge_and_unload()
```

## 多适配器问题

### 适配器冲突

**错误**：`ValueError: Adapter with name 'default' already exists`

**修复**：
```python
# 使用唯一名称
model.load_adapter("./adapter1", adapter_name="task1")
model.load_adapter("./adapter2", adapter_name="task2")

# 或删除现有
model.delete_adapter("default")
```

### 混合精度适配器

**错误**：用不同 dtype 训练的适配器。

**修复**：
```python
# 转换适配器精度
model = PeftModel.from_pretrained(base_model, "./adapter")
model = model.to(torch.bfloat16)

# 或用特定 dtype 加载
model = PeftModel.from_pretrained(
    base_model,
    "./adapter",
    torch_dtype=torch.bfloat16
)
```

## 性能优化

### 内存分析

```python
import torch

def print_memory():
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1e9
        reserved = torch.cuda.memory_reserved() / 1e9
        print(f"Allocated: {allocated:.2f}GB, Reserved: {reserved:.2f}GB")

# 训练期间分析
print_memory()  # 之前
model.train()
loss = model(**batch).loss
loss.backward()
print_memory()  # 之后
```

### 速度分析

```python
import time
import torch

def benchmark_generation(model, tokenizer, prompt, n_runs=5):
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    # 预热
    model.generate(**inputs, max_new_tokens=10)
    torch.cuda.synchronize()

    # 基准测试
    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        outputs = model.generate(**inputs, max_new_tokens=100)
        torch.cuda.synchronize()
        times.append(time.perf_counter() - start)

    tokens = outputs.shape[1] - inputs.input_ids.shape[1]
    avg_time = sum(times) / len(times)
    print(f"Speed: {tokens/avg_time:.2f} tokens/sec")

# 比较适配器 vs 合并
benchmark_generation(adapter_model, tokenizer, "Hello")
benchmark_generation(merged_model, tokenizer, "Hello")
```

## 获取帮助

1. **检查 PEFT GitHub Issues**：https://github.com/huggingface/peft/issues
2. **HuggingFace 论坛**：https://discuss.huggingface.co/
3. **PEFT 文档**：https://huggingface.co/docs/peft

### 调试模板

报告问题时，包括：

```python
# 系统信息
import peft
import transformers
import torch

print(f"PEFT: {peft.__version__}")
print(f"Transformers: {transformers.__version__}")
print(f"PyTorch: {torch.__version__}")
print(f"CUDA: {torch.version.cuda}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")

# 配置
print(model.peft_config)
model.print_trainable_parameters()
```
