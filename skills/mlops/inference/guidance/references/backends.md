# 后端配置指南

使用不同 LLM 后端配置 Guidance 的完整指南。

## 目录
- 基于 API 的模型（Anthropic、OpenAI）
- 本地模型（Transformers、llama.cpp）
- 后端比较
- 性能调优
- 高级配置

## 基于 API 的模型

### Anthropic Claude

#### 基本设置

```python
from guidance import models

# 使用环境变量
lm = models.Anthropic("claude-sonnet-4-5-20250929")
# 从环境读取 ANTHROPIC_API_KEY

# 显式 API 密钥
lm = models.Anthropic(
    model="claude-sonnet-4-5-20250929",
    api_key="your-api-key-here"
)
```

#### 可用模型

```python
# Claude 3.5 Sonnet（最新，推荐）
lm = models.Anthropic("claude-sonnet-4-5-20250929")

# Claude 3.7 Sonnet（快速，性价比高）
lm = models.Anthropic("claude-sonnet-3.7-20250219")

# Claude 3 Opus（能力最强）
lm = models.Anthropic("claude-3-opus-20240229")

# Claude 3.5 Haiku（最快，最便宜）
lm = models.Anthropic("claude-3-5-haiku-20241022")
```

#### 配置选项

```python
lm = models.Anthropic(
    model="claude-sonnet-4-5-20250929",
    api_key="your-api-key",
    max_tokens=4096,           # 最大生成 token 数
    temperature=0.7,            # 采样温度 (0-1)
    top_p=0.9,                  # 核采样
    timeout=30,                 # 请求超时（秒）
    max_retries=3              # 重试失败请求
)
```

#### 使用上下文管理器

```python
from guidance import models, system, user, assistant, gen

lm = models.Anthropic("claude-sonnet-4-5-20250929")

with system():
    lm += "You are a helpful assistant."

with user():
    lm += "What is the capital of France?"

with assistant():
    lm += gen(max_tokens=50)

print(lm)
```

### OpenAI

#### 基本设置

```python
from guidance import models

# 使用环境变量
lm = models.OpenAI("gpt-4o")
# 从环境读取 OPENAI_API_KEY

# 显式 API 密钥
lm = models.OpenAI(
    model="gpt-4o",
    api_key="your-api-key-here"
)
```

#### 可用模型

```python
# GPT-4o（最新，多模态）
lm = models.OpenAI("gpt-4o")

# GPT-4o Mini（快速，性价比高）
lm = models.OpenAI("gpt-4o-mini")

# GPT-4 Turbo
lm = models.OpenAI("gpt-4-turbo")

# GPT-3.5 Turbo（最便宜）
lm = models.OpenAI("gpt-3.5-turbo")
```

#### 配置选项

```python
lm = models.OpenAI(
    model="gpt-4o-mini",
    api_key="your-api-key",
    max_tokens=2048,
    temperature=0.7,
    top_p=1.0,
    frequency_penalty=0.0,
    presence_penalty=0.0,
    timeout=30
)
```

#### 聊天格式

```python
from guidance import models, gen

lm = models.OpenAI("gpt-4o-mini")

# OpenAI 使用聊天格式
lm += [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is 2+2?"}
]

# 生成响应
lm += gen(max_tokens=50)
```

### Azure OpenAI

```python
from guidance import models

lm = models.AzureOpenAI(
    model="gpt-4o",
    azure_endpoint="https://your-resource.openai.azure.com/",
    api_key="your-azure-api-key",
    api_version="2024-02-15-preview",
    deployment_name="your-deployment-name"
)
```

## 本地模型

### Transformers（Hugging Face）

#### 基本设置

```python
from guidance.models import Transformers

# 从 Hugging Face 加载模型
lm = Transformers("microsoft/Phi-4-mini-instruct")
```

#### GPU 配置

```python
# 使用 GPU
lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cuda"
)

# 使用特定 GPU
lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cuda:0"  # GPU 0
)

# 使用 CPU
lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cpu"
)
```

#### 高级配置

```python
lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cuda",
    torch_dtype="float16",      # 使用 FP16（更快，更少内存）
    load_in_8bit=True,          # 8 位量化
    max_memory={0: "20GB"},     # GPU 内存限制
    offload_folder="./offload"  # 如需要则卸载到磁盘
)
```

#### 热门模型

```python
# Phi-4（Microsoft）
lm = Transformers("microsoft/Phi-4-mini-instruct")
lm = Transformers("microsoft/Phi-3-medium-4k-instruct")

# Llama 3（Meta）
lm = Transformers("meta-llama/Llama-3.1-8B-Instruct")
lm = Transformers("meta-llama/Llama-3.1-70B-Instruct")

# Mistral（Mistral AI）
lm = Transformers("mistralai/Mistral-7B-Instruct-v0.3")
lm = Transformers("mistralai/Mixtral-8x7B-Instruct-v0.1")

# Qwen（阿里巴巴）
lm = Transformers("Qwen/Qwen2.5-7B-Instruct")

# Gemma（Google）
lm = Transformers("google/gemma-2-9b-it")
```

#### 生成配置

```python
lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cuda"
)

# 配置生成
from guidance import gen

result = lm + gen(
    max_tokens=100,
    temperature=0.7,
    top_p=0.9,
    top_k=50,
    repetition_penalty=1.1
)
```

### llama.cpp

#### 基本设置

```python
from guidance.models import LlamaCpp

# 加载 GGUF 模型
lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=4096  # 上下文窗口
)
```

#### GPU 配置

```python
# 使用 GPU 加速
lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=4096,
    n_gpu_layers=35,  # 将 35 层卸载到 GPU
    n_threads=8       # 剩余层的 CPU 线程
)

# 完全 GPU 卸载
lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=4096,
    n_gpu_layers=-1  # 卸载所有层
)
```

#### 高级配置

```python
lm = LlamaCpp(
    model_path="/path/to/llama-3.1-8b-instruct.Q4_K_M.gguf",
    n_ctx=8192,          # 上下文窗口（token）
    n_gpu_layers=35,     # GPU 层数
    n_threads=8,         # CPU 线程
    n_batch=512,         # 提示处理的批次大小
    use_mmap=True,       # 内存映射模型文件
    use_mlock=False,     # 将模型锁定在 RAM 中
    seed=42,             # 随机种子
    verbose=False        # 抑制详细输出
)
```

#### 量化模型

```python
# Q4_K_M（4 位，大多数情况推荐）
lm = LlamaCpp("/path/to/model.Q4_K_M.gguf")

# Q5_K_M（5 位，质量更好）
lm = LlamaCpp("/path/to/model.Q5_K_M.gguf")

# Q8_0（8 位，高质量）
lm = LlamaCpp("/path/to/model.Q8_0.gguf")

# F16（16 位浮点，最高质量）
lm = LlamaCpp("/path/to/model.F16.gguf")
```

#### 热门 GGUF 模型

```python
# Llama 3.1
lm = LlamaCpp("llama-3.1-8b-instruct.Q4_K_M.gguf")

# Mistral
lm = LlamaCpp("mistral-7b-instruct-v0.3.Q4_K_M.gguf")

# Phi-4
lm = LlamaCpp("phi-4-mini-instruct.Q4_K_M.gguf")
```

## 后端比较

### 功能矩阵

| 功能 | Anthropic | OpenAI | Transformers | llama.cpp |
|------|-----------|--------|--------------|-----------|
| 约束生成 | ✅ 完整 | ✅ 完整 | ✅ 完整 | ✅ 完整 |
| Token 修复 | ✅ 是 | ✅ 是 | ✅ 是 | ✅ 是 |
| 流式传输 | ✅ 是 | ✅ 是 | ✅ 是 | ✅ 是 |
| GPU 支持 | 不适用 | 不适用 | ✅ 是 | ✅ 是 |
| 量化 | 不适用 | 不适用 | ✅ 是 | ✅ 是 |
| 成本 | $$$ | $$$ | 免费 | 免费 |
| 延迟 | 低 | 低 | 中等 | 低 |
| 设置难度 | 简单 | 简单 | 中等 | 中等 |

### 性能特征

**Anthropic Claude：**
- **延迟**：200-500ms（API 调用）
- **吞吐量**：受 API 速率限制限制
- **成本**：每 1M 输入 token $3-15
- **最适合**：生产系统、高质量输出

**OpenAI：**
- **延迟**：200-400ms（API 调用）
- **吞吐量**：受 API 速率限制限制
- **成本**：每 1M 输入 token $0.15-30
- **最适合**：成本敏感的生产环境、gpt-4o-mini

**Transformers：**
- **延迟**：50-200ms（本地推理）
- **吞吐量**：依赖 GPU（10-100 token/秒）
- **成本**：仅硬件成本
- **最适合**：隐私敏感、大批量、实验

**llama.cpp：**
- **延迟**：30-150ms（本地推理）
- **吞吐量**：依赖硬件（20-150 token/秒）
- **成本**：仅硬件成本
- **最适合**：边缘部署、Apple Silicon、CPU 推理

### 内存需求

**Transformers (FP16)：**
- 7B 模型：~14GB GPU VRAM
- 13B 模型：~26GB GPU VRAM
- 70B 模型：~140GB GPU VRAM（多 GPU）

**llama.cpp (Q4_K_M)：**
- 7B 模型：~4.5GB RAM
- 13B 模型：~8GB RAM
- 70B 模型：~40GB RAM

**优化提示：**
- 使用量化模型（Q4_K_M）以降低内存
- 使用 GPU 卸载以加快推理
- 对较小模型（<7B）使用 CPU 推理

## 性能调优

### API 模型（Anthropic、OpenAI）

#### 降低延迟

```python
from guidance import models, gen

lm = models.Anthropic("claude-sonnet-4-5-20250929")

# 使用较低的 max_tokens（更快响应）
lm += gen(max_tokens=100)  # 而不是 1000

# 使用流式传输（感知延迟降低）
for chunk in lm.stream(gen(max_tokens=500)):
    print(chunk, end="", flush=True)
```

#### 降低成本

```python
# 使用更便宜的模型
lm = models.Anthropic("claude-3-5-haiku-20241022")  # 对比 Sonnet
lm = models.OpenAI("gpt-4o-mini")  # 对比 gpt-4o

# 减少上下文大小
# - 保持提示简洁
# - 避免大型少样本示例
# - 使用 max_tokens 限制
```

### 本地模型（Transformers、llama.cpp）

#### 优化 GPU 使用

```python
from guidance.models import Transformers

# 使用 FP16 获得 2 倍加速
lm = Transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    torch_dtype="float16"
)

# 使用 8 位量化获得 4 倍内存减少
lm = Transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    load_in_8bit=True
)

# 使用 flash attention（需要 flash-attn 包）
lm = Transformers(
    "meta-llama/Llama-3.1-8B-Instruct",
    device="cuda",
    use_flash_attention_2=True
)
```

#### 优化 llama.cpp

```python
from guidance.models import LlamaCpp

# 最大化 GPU 层数
lm = LlamaCpp(
    model_path="/path/to/model.Q4_K_M.gguf",
    n_gpu_layers=-1  # 所有层在 GPU 上
)

# 优化批次大小
lm = LlamaCpp(
    model_path="/path/to/model.Q4_K_M.gguf",
    n_batch=512,     # 更大批次 = 更快提示处理
    n_gpu_layers=-1
)

# 使用 Metal（Apple Silicon）
lm = LlamaCpp(
    model_path="/path/to/model.Q4_K_M.gguf",
    n_gpu_layers=-1,  # 使用 Metal GPU 加速
    use_mmap=True
)
```

#### 批量处理

```python
# 高效处理多个请求
requests = [
    "What is 2+2?",
    "What is the capital of France?",
    "What is photosynthesis?"
]

# 错误：顺序处理
for req in requests:
    lm = Transformers("microsoft/Phi-4-mini-instruct")
    lm += req + gen(max_tokens=50)

# 正确：重用已加载的模型
lm = Transformers("microsoft/Phi-4-mini-instruct")
for req in requests:
    lm += req + gen(max_tokens=50)
```

## 高级配置

### 自定义模型配置

```python
from transformers import AutoTokenizer, AutoModelForCausalLM
from guidance.models import Transformers

# 加载自定义模型
tokenizer = AutoTokenizer.from_pretrained("your-model")
model = AutoModelForCausalLM.from_pretrained(
    "your-model",
    device_map="auto",
    torch_dtype="float16"
)

# 与 Guidance 一起使用
lm = Transformers(model=model, tokenizer=tokenizer)
```

### 环境变量

```bash
# API 密钥
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."

# Transformers 缓存
export HF_HOME="/path/to/cache"
export TRANSFORMERS_CACHE="/path/to/cache"

# GPU 选择
export CUDA_VISIBLE_DEVICES=0,1  # 使用 GPU 0 和 1
```

### 调试

```python
# 启用详细日志
import logging
logging.basicConfig(level=logging.DEBUG)

# 检查后端信息
lm = models.Anthropic("claude-sonnet-4-5-20250929")
print(f"Model: {lm.model_name}")
print(f"Backend: {lm.backend}")

# 检查 GPU 使用情况（Transformers）
lm = Transformers("microsoft/Phi-4-mini-instruct", device="cuda")
print(f"Device: {lm.device}")
print(f"Memory allocated: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
```

## 资源

- **Anthropic 文档**：https://docs.anthropic.com
- **OpenAI 文档**：https://platform.openai.com/docs
- **Hugging Face 模型**：https://huggingface.co/models
- **llama.cpp**：https://github.com/ggerganov/llama.cpp
- **GGUF 模型**：https://huggingface.co/models?library=gguf
