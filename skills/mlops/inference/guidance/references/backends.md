# 后端配置指南

Guidance 可使用本地 Transformers、llama.cpp 或 OpenAI 后端。本项目示例优先使用本地模型，避免把特定云厂商当成默认路径。

## Transformers

### 安装

```bash
pip install "guidance[transformers]" torch transformers accelerate
```

### 基本配置

```python
from guidance import models

lm = models.Transformers(
    "Qwen/Qwen2.5-7B-Instruct",
    device="cuda",
)
```

内存不足时可改用更小的模型或量化权重。CPU 环境将 `device` 改为 `cpu`。

### 使用已加载模型

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from guidance.models import Transformers

model_id = "Qwen/Qwen2.5-7B-Instruct"
tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    device_map="auto",
    torch_dtype="auto",
)

lm = Transformers(model=model, tokenizer=tokenizer)
```

## llama.cpp

### 安装

```bash
pip install "guidance[llama_cpp]"
```

### 基本配置

```python
from guidance.models import LlamaCpp

lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=4096,
    n_gpu_layers=35,
)
```

`n_gpu_layers=0` 表示纯 CPU 推理。显存足够时逐步提高该值，以减少延迟。

## OpenAI

```python
from guidance import models

lm = models.OpenAI(
    model="gpt-4o-mini",
    api_key="your-api-key",
)
```

API 密钥也可通过 `OPENAI_API_KEY` 提供。不要把密钥写入仓库、日志或示例输出。

## 选择后端

| 后端 | 隐私 | 硬件要求 | 运维成本 | 适合场景 |
|------|------|----------|----------|----------|
| Transformers | 数据留在本地 | 通常需要 GPU | 中 | 批量任务、实验、离线环境 |
| llama.cpp | 数据留在本地 | CPU 或 GPU | 低 | 边缘部署、量化模型 |
| OpenAI | 数据发送到远端 | 无本地模型硬件 | 低 | 快速接入、托管推理 |

## 性能调优

### 控制生成量

```python
from guidance import gen

lm += gen("answer", max_tokens=100)
```

只分配任务实际需要的 `max_tokens`，并用 `stop`、正则或语法约束尽早终止生成。

### Transformers 显存

```python
lm = models.Transformers(
    "Qwen/Qwen2.5-7B-Instruct",
    device="cuda",
    torch_dtype="float16",
)
```

优先调整模型大小和量化等级。不要通过缩短业务必需的输入来掩盖上下文不足。

### llama.cpp 上下文

```python
lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=8192,
    n_batch=512,
    n_gpu_layers=35,
)
```

`n_ctx` 会直接影响内存占用；应按任务上限配置，而不是无条件取最大值。

## 环境变量

```bash
export OPENAI_API_KEY="sk-..."
export HF_HOME="/path/to/cache"
export CUDA_VISIBLE_DEVICES=0
```

## 排障

- 导入失败：确认安装了所选后端对应的 extra。
- CUDA 内存不足：减小模型、上下文或批大小，或改用量化 GGUF。
- 生成不符合格式：检查正则/语法是否覆盖目标文本，并先用最小输入复现。
- 远端超时：降低并发和生成上限，并为幂等请求设置有限重试。

## 资源

- Guidance：https://github.com/guidance-ai/guidance
- Hugging Face 模型：https://huggingface.co/models
- llama.cpp：https://github.com/ggerganov/llama.cpp
