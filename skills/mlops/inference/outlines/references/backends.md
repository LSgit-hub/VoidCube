# 后端配置指南

使用不同模型后端配置 Outlines 的完整指南。

## 目录
- 本地模型（Transformers、llama.cpp、vLLM）
- API 模型（OpenAI）
- 性能比较
- 配置示例
- 生产部署

## Transformers（Hugging Face）

### 基本设置

```python
import outlines

# 从 Hugging Face 加载模型
model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")

# 与生成器一起使用
generator = outlines.generate.json(model, YourModel)
result = generator("Your prompt")
```

### GPU 配置

```python
# 使用 CUDA GPU
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="cuda"
)

# 使用特定 GPU
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="cuda:0"  # GPU 0
)

# 使用 CPU
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="cpu"
)

# 使用 Apple Silicon MPS
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="mps"
)
```

### 高级配置

```python
# FP16 用于更快推理
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="cuda",
    model_kwargs={
        "torch_dtype": "float16"
    }
)

# 8 位量化（更少内存）
model = outlines.models.transformers(
    "microsoft/Phi-3-mini-4k-instruct",
    device="cuda",
    model_kwargs={
        "load_in_8bit": True,
        "device_map": "auto"
    }
)

# 4 位量化（更少内存）
model = outlines.models.transformers(
    "meta-llama/Llama-3.1-70B-Instruct",
    device="cuda",
    model_kwargs={
        "load_in_4bit": True,
        "device_map": "auto",
        "bnb_4bit_compute_dtype": "float16"
    }
)

# 多 GPU
model = outlines.models.transformers(
    "meta-llama/Llama-3.1-70B-Instruct",
    device="cuda",
    model_kwargs={
        "device_map": "auto",  # 自动 GPU 分配
        "max_memory": {0: "40GB", 1: "40GB"}  # 每 GPU 限制
    }
)
```

### 常用模型

```python
# Phi-4（Microsoft）
model = outlines.models.transformers("microsoft/Phi-4-mini-instruct")
model = outlines.models.transformers("microsoft/Phi-3-medium-4k-instruct")

# Llama 3.1（Meta）
model = outlines.models.transformers("meta-llama/Llama-3.1-8B-Instruct")
model = outlines.models.transformers("meta-llama/Llama-3.1-70B-Instruct")
model = outlines.models.transformers("meta-llama/Llama-3.1-405B-Instruct")

# Mistral（Mistral AI）
model = outlines.models.transformers("mistralai/Mistral-7B-Instruct-v0.3")
model = outlines.models.transformers("mistralai/Mixtral-8x7B-Instruct-v0.1")
model = outlines.models.transformers("mistralai/Mixtral-8x22B-Instruct-v0.1")

# Qwen（阿里巴巴）
model = outlines.models.transformers("Qwen/Qwen2.5-7B-Instruct")
model = outlines.models.transformers("Qwen/Qwen2.5-14B-Instruct")
model = outlines.models.transformers("Qwen/Qwen2.5-72B-Instruct")

# Gemma（Google）
model = outlines.models.transformers("google/gemma-2-9b-it")
model = outlines.models.transformers("google/gemma-2-27b-it")

# Llava（视觉）
model = outlines.models.transformers("llava-hf/llava-v1.6-mistral-7b-hf")
```

### 自定义模型加载

```python
from transformers import AutoTokenizer, AutoModelForCausalLM
import outlines

# 手动加载模型
tokenizer = AutoTokenizer.from_pretrained("your-model")
model_hf = AutoModelForCausalLM.from_pretrained(
    "your-model",
    device_map="auto",
    torch_dtype="float16"
)

# 与 Outlines 一起使用
model = outlines.models.transformers(
    model=model_hf,
    tokenizer=tokenizer
)
```

## llama.cpp

### 基本设置

```python
import outlines

# 加载 GGUF 模型
model = outlines.models.llamacpp(
    "./models/llama-3.1-8b-instruct.Q4_K_M.gguf",
    n_ctx=4096  # 上下文窗口
)

# 与生成器一起使用
generator = outlines.generate.json(model, YourModel)
```

### GPU 配置

```python
# 仅 CPU
model = outlines.models.llamacpp(
    "./models/model.gguf",
    n_ctx=4096,
    n_threads=8  # 使用 8 个 CPU 线程
)

# GPU 卸载（部分）
model = outlines.models.llamacpp(
    "./models/model.gguf",
    n_ctx=4096,
    n_gpu_layers=35,  # 卸载 35 层到 GPU
    n_threads=4       # 剩余层的 CPU 线程
)

# 完全 GPU 卸载
model = outlines.models.llamacpp(
    "./models/model.gguf",
    n_ctx=8192,
    n_gpu_layers=-1  # 所有层在 GPU 上
)
```

### 高级配置

```python
model = outlines.models.llamacpp(
    "./models/llama-3.1-8b.Q4_K_M.gguf",
    n_ctx=8192,          # 上下文窗口（token）
    n_gpu_layers=35,     # GPU 层数
    n_threads=8,         # CPU 线程数
    n_batch=512,         # 提示处理的批量大小
    use_mmap=True,       # 内存映射模型文件（更快加载）
    use_mlock=False,     # 锁定模型在 RAM 中（防止交换）
    seed=42,             # 随机种子以实现可重现性
    verbose=False        # 抑制详细输出
)
```

### 量化格式

```python
# Q4_K_M（4 位，大多数情况推荐）
# - 大小：7B 模型约 4.5GB
# - 质量：良好
# - 速度：快
model = outlines.models.llamacpp("./models/model.Q4_K_M.gguf")

# Q5_K_M（5 位，更好质量）
# - 大小：7B 模型约 5.5GB
# - 质量：很好
# - 速度：比 Q4 稍慢
model = outlines.models.llamacpp("./models/model.Q5_K_M.gguf")

# Q6_K（6 位，高质量）
# - 大小：7B 模型约 6.5GB
# - 质量：优秀
# - 速度：比 Q5 慢
model = outlines.models.llamacpp("./models/model.Q6_K.gguf")

# Q8_0（8 位，接近原始质量）
# - 大小：7B 模型约 8GB
# - 质量：接近 FP16
# - 速度：比 Q6 慢
model = outlines.models.llamacpp("./models/model.Q8_0.gguf")

# F16（16 位浮点，原始质量）
# - 大小：7B 模型约 14GB
# - 质量：原始
# - 速度：最慢
model = outlines.models.llamacpp("./models/model.F16.gguf")
```

### 常用 GGUF 模型

```python
# Llama 3.1
model = outlines.models.llamacpp("llama-3.1-8b-instruct.Q4_K_M.gguf")
model = outlines.models.llamacpp("llama-3.1-70b-instruct.Q4_K_M.gguf")

# Mistral
model = outlines.models.llamacpp("mistral-7b-instruct-v0.3.Q4_K_M.gguf")

# Phi-4
model = outlines.models.llamacpp("phi-4-mini-instruct.Q4_K_M.gguf")

# Qwen
model = outlines.models.llamacpp("qwen2.5-7b-instruct.Q4_K_M.gguf")
```

### Apple Silicon 优化

```python
# 针对 M1/M2/M3 Mac 优化
model = outlines.models.llamacpp(
    "./models/llama-3.1-8b.Q4_K_M.gguf",
    n_ctx=4096,
    n_gpu_layers=-1,  # 使用 Metal GPU 加速
    use_mmap=True,    # 高效内存映射
    n_threads=8       # 使用性能核心
)
```

## vLLM（生产）

### 基本设置

```python
import outlines

# 使用 vLLM 加载模型
model = outlines.models.vllm("meta-llama/Llama-3.1-8B-Instruct")

# 与生成器一起使用
generator = outlines.generate.json(model, YourModel)
```

### 单 GPU

```python
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    gpu_memory_utilization=0.9,  # 使用 90% GPU 内存
    max_model_len=4096          # 最大序列长度
)
```

### 多 GPU

```python
# 张量并行（跨 GPU 分割模型）
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-70B-Instruct",
    tensor_parallel_size=4,  # 使用 4 个 GPU
    gpu_memory_utilization=0.9
)

# 流水线并行（罕见，用于超大模型）
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-405B-Instruct",
    pipeline_parallel_size=8,  # 8-GPU 流水线
    tensor_parallel_size=4     # 4-GPU 张量分割
    # 总计：32 个 GPU
)
```

### 量化

```python
# AWQ 量化（4 位）
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    quantization="awq",
    dtype="float16"
)

# GPTQ 量化（4 位）
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    quantization="gptq"
)

# SqueezeLLM 量化
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    quantization="squeezellm"
)
```

### 高级配置

```python
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    tensor_parallel_size=1,
    gpu_memory_utilization=0.9,
    max_model_len=8192,
    max_num_seqs=256,           # 最大并发序列
    max_num_batched_tokens=8192, # 每批最大 token
    dtype="float16",
    trust_remote_code=True,
    enforce_eager=False,        # 使用 CUDA 图（更快）
    swap_space=4                # CPU 交换空间（GB）
)
```

### 批量处理

```python
# vLLM 针对高吞吐量批量处理优化
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    max_num_seqs=128  # 并行处理 128 个序列
)

generator = outlines.generate.json(model, YourModel)

# 高效处理多个提示
prompts = ["prompt1", "prompt2", ..., "prompt100"]
results = [generator(p) for p in prompts]
# vLLM 自动批处理和优化
```

## OpenAI（有限支持）

### 基本设置

```python
import outlines

# 基本 OpenAI 支持
model = outlines.models.openai("gpt-4o-mini", api_key="your-api-key")

# 与生成器一起使用
generator = outlines.generate.json(model, YourModel)
result = generator("Your prompt")
```

### 配置

```python
model = outlines.models.openai(
    "gpt-4o-mini",
    api_key="your-api-key",  # 或设置 OPENAI_API_KEY 环境变量
    max_tokens=2048,
    temperature=0.7
)
```

### 可用模型

```python
# GPT-4o（最新）
model = outlines.models.openai("gpt-4o")

# GPT-4o Mini（成本效益）
model = outlines.models.openai("gpt-4o-mini")

# GPT-4 Turbo
model = outlines.models.openai("gpt-4-turbo")

# GPT-3.5 Turbo
model = outlines.models.openai("gpt-3.5-turbo")
```

**注意**：与本地模型相比，OpenAI 支持有限。某些高级功能可能无法工作。

## 后端比较

### 功能矩阵

| 功能 | Transformers | llama.cpp | vLLM | OpenAI |
|---------|-------------|-----------|------|--------|
| 结构化生成 | ✅ 完整 | ✅ 完整 | ✅ 完整 | ⚠️ 有限 |
| FSM 优化 | ✅ 是 | ✅ 是 | ✅ 是 | ❌ 否 |
| GPU 支持 | ✅ 是 | ✅ 是 | ✅ 是 | N/A |
| 多 GPU | ✅ 是 | ✅ 是 | ✅ 是 | N/A |
| 量化 | ✅ 是 | ✅ 是 | ✅ 是 | N/A |
| 高吞吐量 | ⚠️ 中等 | ⚠️ 中等 | ✅ 优秀 | ⚠️ API 限制 |
| 设置难度 | 简单 | 中等 | 中等 | 简单 |
| 成本 | 硬件 | 硬件 | 硬件 | API 使用 |

### 性能特征

**Transformers：**
- **延迟**：50-200ms（单请求，GPU）
- **吞吐量**：10-50 tokens/秒（取决于硬件）
- **内存**：每 1B 参数 2-4GB（FP16）
- **最适用于**：开发、小规模部署、灵活性

**llama.cpp：**
- **延迟**：30-150ms（单请求）
- **吞吐量**：20-150 tokens/秒（取决于量化）
- **内存**：每 1B 参数 0.5-2GB（Q4-Q8）
- **最适用于**：CPU 推理、Apple Silicon、边缘部署、低内存

**vLLM：**
- **延迟**：30-100ms（单请求）
- **吞吐量**：100-1000+ tokens/秒（批量处理）
- **内存**：每 1B 参数 2-4GB（FP16）
- **最适用于**：生产、高吞吐量、批量处理、服务

**OpenAI：**
- **延迟**：200-500ms（API 调用）
- **吞吐量**：API 速率限制
- **内存**：N/A（云端）
- **最适用于**：快速原型设计、无基础设施

### 内存需求

**7B 模型：**
- FP16：约 14GB
- 8 位：约 7GB
- 4 位：约 4GB
- Q4_K_M (GGUF)：约 4.5GB

**13B 模型：**
- FP16：约 26GB
- 8 位：约 13GB
- 4 位：约 7GB
- Q4_K_M (GGUF)：约 8GB

**70B 模型：**
- FP16：约 140GB（多 GPU）
- 8 位：约 70GB（多 GPU）
- 4 位：约 35GB（单 A100/H100）
- Q4_K_M (GGUF)：约 40GB

## 性能调优

### Transformers 优化

```python
# 使用 FP16
model = outlines.models.transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    model_kwargs={"torch_dtype": "float16"}
)

# 使用 flash attention（2-4 倍更快）
model = outlines.models.transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    model_kwargs={
        "torch_dtype": "float16",
        "use_flash_attention_2": True
    }
)

# 使用 8 位量化（内存减半）
model = outlines.models.transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    model_kwargs={
        "load_in_8bit": True,
        "device_map": "auto"
    }
)
```

### llama.cpp 优化

```python
# 最大化 GPU 使用
model = outlines.models.llamacpp(
    "./models/model.Q4_K_M.gguf",
    n_gpu_layers=-1,  # 所有层在 GPU 上
    n_ctx=8192,
    n_batch=512       # 更大批量 = 更快
)

# 针对 CPU 优化（Apple Silicon）
model = outlines.models.llamacpp(
    "./models/model.Q4_K_M.gguf",
    n_ctx=4096,
    n_threads=8,      # 使用所有性能核心
    use_mmap=True
)
```

### vLLM 优化

```python
# 高吞吐量
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-8B-Instruct",
    gpu_memory_utilization=0.95,  # 使用 95% GPU
    max_num_seqs=256,             # 高并发
    enforce_eager=False           # 使用 CUDA 图
)

# 多 GPU
model = outlines.models.vllm(
    "meta-llama/Llama-3.1-70B-Instruct",
    tensor_parallel_size=4,  # 4 个 GPU
    gpu_memory_utilization=0.9
)
```

## 生产部署

### Docker 与 vLLM

```dockerfile
FROM vllm/vllm-openai:latest

# 安装 outlines
RUN pip install outlines

# 复制代码
COPY app.py /app/

# 运行
CMD ["python", "/app/app.py"]
```

### 环境变量

```bash
# Transformers 缓存
export HF_HOME="/path/to/cache"
export TRANSFORMERS_CACHE="/path/to/cache"

# GPU 选择
export CUDA_VISIBLE_DEVICES=0,1,2,3

# OpenAI API 密钥
export OPENAI_API_KEY="sk-..."

# 禁用分词器并行警告
export TOKENIZERS_PARALLELISM=false
```

### 模型服务

```python
# 使用 vLLM 的简单 HTTP 服务器
import outlines
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# 启动时加载模型一次
model = outlines.models.vllm("meta-llama/Llama-3.1-8B-Instruct")

class User(BaseModel):
    name: str
    age: int
    email: str

generator = outlines.generate.json(model, User)

@app.post("/extract")
def extract(text: str):
    result = generator(f"Extract user from: {text}")
    return result.model_dump()
```

## 资源

- **Transformers**：https://huggingface.co/docs/transformers
- **llama.cpp**：https://github.com/ggerganov/llama.cpp
- **vLLM**：https://docs.vllm.ai
- **Outlines**：https://github.com/outlines-dev/outlines
