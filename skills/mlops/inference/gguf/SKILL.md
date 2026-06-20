---
name: gguf-quantization
description: GGUF格式和llama.cpp量化，用于高效的CPU/GPU推理。在消费级硬件、Apple Silicon上部署模型，或需要灵活的2-8位量化且无GPU要求时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [llama-cpp-python>=0.2.0]
metadata:
  VoidCube:
    tags: [GGUF, 量化, llama.cpp, CPU推理, Apple Silicon, 模型压缩, 优化]

---

# GGUF - llama.cpp的量化格式

GGUF（GPT-Generated Unified Format）是llama.cpp的标准文件格式，支持在CPU、Apple Silicon和GPU上进行高效推理，并提供灵活的量化选项。

## 何时使用GGUF

**使用GGUF的场景：**
- 在消费级硬件（笔记本、台式机）上部署
- 在Apple Silicon（M1/M2/M3）上运行，支持Metal加速
- 需要CPU推理，无需GPU
- 需要灵活量化（Q2_K到Q8_0）
- 使用本地AI工具（LM Studio、Ollama、text-generation-webui）

**核心优势：**
- **通用硬件**：支持CPU、Apple Silicon、NVIDIA、AMD
- **无需Python运行时**：纯C/C++推理
- **灵活量化**：2-8位，多种方法（K-quants）
- **生态系统支持**：LM Studio、Ollama、koboldcpp等
- **imatrix**：重要性矩阵，提升低位质量

**替代方案：**
- **AWQ/GPTQ**：在NVIDIA GPU上通过校准实现最大精度
- **HQQ**：HuggingFace的快速无校准量化
- **bitsandbytes**：与transformers库简单集成
- **TensorRT-LLM**：生产级NVIDIA部署，最大速度

## 快速入门

### 安装

```bash
# 克隆llama.cpp
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp

# 构建（CPU）
make

# 使用CUDA构建（NVIDIA）
make GGML_CUDA=1

# 使用Metal构建（Apple Silicon）
make GGML_METAL=1

# 安装Python绑定（可选）
pip install llama-cpp-python
```

### 将模型转换为GGUF

```bash
# 安装依赖
pip install -r requirements.txt

# 将HuggingFace模型转换为GGUF（FP16）
python convert_hf_to_gguf.py ./path/to/model --outfile model-f16.gguf

# 或指定输出类型
python convert_hf_to_gguf.py ./path/to/model \
    --outfile model-f16.gguf \
    --outtype f16
```

### 量化模型

```bash
# 基本量化为Q4_K_M
./llama-quantize model-f16.gguf model-q4_k_m.gguf Q4_K_M

# 使用重要性矩阵量化（质量更好）
./llama-imatrix -m model-f16.gguf -f calibration.txt -o model.imatrix
./llama-quantize --imatrix model.imatrix model-f16.gguf model-q4_k_m.gguf Q4_K_M
```

### 运行推理

```bash
# CLI推理
./llama-cli -m model-q4_k_m.gguf -p "Hello, how are you?"

# 交互模式
./llama-cli -m model-q4_k_m.gguf --interactive

# 使用GPU卸载
./llama-cli -m model-q4_k_m.gguf -ngl 35 -p "Hello!"
```

## 量化类型

### K-quant方法（推荐）

| 类型 | 位数 | 大小（7B） | 质量 | 使用场景 |
|------|------|-----------|------|----------|
| Q2_K | 2.5 | ~2.8 GB | 低 | 极限压缩 |
| Q3_K_S | 3.0 | ~3.0 GB | 低-中 | 内存受限 |
| Q3_K_M | 3.3 | ~3.3 GB | 中 | 平衡 |
| Q4_K_S | 4.0 | ~3.8 GB | 中-高 | 良好平衡 |
| Q4_K_M | 4.5 | ~4.1 GB | 高 | **推荐默认** |
| Q5_K_S | 5.0 | ~4.6 GB | 高 | 注重质量 |
| Q5_K_M | 5.5 | ~4.8 GB | 很高 | 高质量 |
| Q6_K | 6.0 | ~5.5 GB | 优秀 | 接近原始 |
| Q8_0 | 8.0 | ~7.2 GB | 最佳 | 最大质量 |

### 传统方法

| 类型 | 描述 |
|------|------|
| Q4_0 | 4位，基础 |
| Q4_1 | 4位带delta |
| Q5_0 | 5位，基础 |
| Q5_1 | 5位带delta |

**建议**：使用K-quant方法（Q4_K_M、Q5_K_M）获得最佳质量/大小比。

## 转换工作流

### 工作流1：HuggingFace到GGUF

```bash
# 1. 下载模型
huggingface-cli download meta-llama/Llama-3.1-8B --local-dir ./llama-3.1-8b

# 2. 转换为GGUF（FP16）
python convert_hf_to_gguf.py ./llama-3.1-8b \
    --outfile llama-3.1-8b-f16.gguf \
    --outtype f16

# 3. 量化
./llama-quantize llama-3.1-8b-f16.gguf llama-3.1-8b-q4_k_m.gguf Q4_K_M

# 4. 测试
./llama-cli -m llama-3.1-8b-q4_k_m.gguf -p "Hello!" -n 50
```

### 工作流2：使用重要性矩阵（质量更好）

```bash
# 1. 转换为GGUF
python convert_hf_to_gguf.py ./model --outfile model-f16.gguf

# 2. 创建校准文本（多样化样本）
cat > calibration.txt << 'EOF'
The quick brown fox jumps over the lazy dog.
Machine learning is a subset of artificial intelligence.
Python is a popular programming language.
# 添加更多多样化文本样本...
EOF

# 3. 生成重要性矩阵
./llama-imatrix -m model-f16.gguf \
    -f calibration.txt \
    --chunk 512 \
    -o model.imatrix \
    -ngl 35  # 如果有GPU层

# 4. 使用imatrix量化
./llama-quantize --imatrix model.imatrix \
    model-f16.gguf \
    model-q4_k_m.gguf \
    Q4_K_M
```

### 工作流3：多种量化

```bash
#!/bin/bash
MODEL="llama-3.1-8b-f16.gguf"
IMATRIX="llama-3.1-8b.imatrix"

# 生成一次imatrix
./llama-imatrix -m $MODEL -f wiki.txt -o $IMATRIX -ngl 35

# 创建多种量化
for QUANT in Q4_K_M Q5_K_M Q6_K Q8_0; do
    OUTPUT="llama-3.1-8b-${QUANT,,}.gguf"
    ./llama-quantize --imatrix $IMATRIX $MODEL $OUTPUT $QUANT
    echo "已创建: $OUTPUT ($(du -h $OUTPUT | cut -f1))"
done
```

## Python使用

### llama-cpp-python

```python
from llama_cpp import Llama

# 加载模型
llm = Llama(
    model_path="./model-q4_k_m.gguf",
    n_ctx=4096,          # 上下文窗口
    n_gpu_layers=35,     # GPU卸载（0表示仅CPU）
    n_threads=8          # CPU线程
)

# 生成
output = llm(
    "What is machine learning?",
    max_tokens=256,
    temperature=0.7,
    stop=["</s>", "\n\n"]
)
print(output["choices"][0]["text"])
```

### 聊天补全

```python
from llama_cpp import Llama

llm = Llama(
    model_path="./model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35,
    chat_format="llama-3"  # 或 "chatml", "mistral"等
)

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is Python?"}
]

response = llm.create_chat_completion(
    messages=messages,
    max_tokens=256,
    temperature=0.7
)
print(response["choices"][0]["message"]["content"])
```

### 流式输出

```python
from llama_cpp import Llama

llm = Llama(model_path="./model-q4_k_m.gguf", n_gpu_layers=35)

# 流式输出token
for chunk in llm(
    "Explain quantum computing:",
    max_tokens=256,
    stream=True
):
    print(chunk["choices"][0]["text"], end="", flush=True)
```

## 服务器模式

### 启动OpenAI兼容服务器

```bash
# 启动服务器
./llama-server -m model-q4_k_m.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -ngl 35 \
    -c 4096

# 或使用Python绑定
python -m llama_cpp.server \
    --model model-q4_k_m.gguf \
    --n_gpu_layers 35 \
    --host 0.0.0.0 \
    --port 8080
```

### 使用OpenAI客户端

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="local-model",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=256
)
print(response.choices[0].message.content)
```

## 硬件优化

### Apple Silicon（Metal）

```bash
# 使用Metal构建
make clean && make GGML_METAL=1

# 使用Metal加速运行
./llama-cli -m model.gguf -ngl 99 -p "Hello"

# Python使用Metal
llm = Llama(
    model_path="model.gguf",
    n_gpu_layers=99,     # 卸载所有层
    n_threads=1          # Metal处理并行
)
```

### NVIDIA CUDA

```bash
# 使用CUDA构建
make clean && make GGML_CUDA=1

# 使用CUDA运行
./llama-cli -m model.gguf -ngl 35 -p "Hello"

# 指定GPU
CUDA_VISIBLE_DEVICES=0 ./llama-cli -m model.gguf -ngl 35
```

### CPU优化

```bash
# 使用AVX2/AVX512构建
make clean && make

# 使用最优线程运行
./llama-cli -m model.gguf -t 8 -p "Hello"

# Python CPU配置
llm = Llama(
    model_path="model.gguf",
    n_gpu_layers=0,      # 仅CPU
    n_threads=8,         # 匹配物理核心
    n_batch=512          # 提示处理的批次大小
)
```

## 与工具集成

### Ollama

```bash
# 创建Modelfile
cat > Modelfile << 'EOF'
FROM ./model-q4_k_m.gguf
TEMPLATE """{{ .System }}
{{ .Prompt }}"""
PARAMETER temperature 0.7
PARAMETER num_ctx 4096
EOF

# 创建Ollama模型
ollama create mymodel -f Modelfile

# 运行
ollama run mymodel "Hello!"
```

### LM Studio

1. 将GGUF文件放在`~/.cache/lm-studio/models/`
2. 打开LM Studio并选择模型
3. 配置上下文长度和GPU卸载
4. 开始推理

### text-generation-webui

```bash
# 放在models文件夹
cp model-q4_k_m.gguf text-generation-webui/models/

# 使用llama.cpp加载器启动
python server.py --model model-q4_k_m.gguf --loader llama.cpp --n-gpu-layers 35
```

## 最佳实践

1. **使用K-quants**：Q4_K_M提供最佳质量/大小平衡
2. **使用imatrix**：Q4及以下始终使用重要性矩阵
3. **GPU卸载**：尽可能多地卸载层，受VRAM限制
4. **上下文长度**：从4096开始，按需增加
5. **线程数**：匹配物理CPU核心，而非逻辑核心
6. **批次大小**：增加n_batch以加快提示处理

## 常见问题

**模型加载缓慢：**
```bash
# 使用mmap加快加载
./llama-cli -m model.gguf --mmap
```

**内存不足：**
```bash
# 减少GPU层
./llama-cli -m model.gguf -ngl 20  # 从35减少

# 或使用更小的量化
./llama-quantize model-f16.gguf model-q3_k_m.gguf Q3_K_M
```

**低位质量差：**
```bash
# Q4及以下始终使用imatrix
./llama-imatrix -m model-f16.gguf -f calibration.txt -o model.imatrix
./llama-quantize --imatrix model.imatrix model-f16.gguf model-q4_k_m.gguf Q4_K_M
```

## 参考资料

- **[高级用法](references/advanced-usage.md)** - 批处理、推测解码、自定义构建
- **[故障排除](references/troubleshooting.md)** - 常见问题、调试、基准测试

## 资源

- **仓库**：https://github.com/ggml-org/llama.cpp
- **Python绑定**：https://github.com/abetlen/llama-cpp-python
- **预量化模型**：https://huggingface.co/TheBloke
- **GGUF转换器**：https://huggingface.co/spaces/ggml-org/gguf-my-repo
- **许可证**：MIT
