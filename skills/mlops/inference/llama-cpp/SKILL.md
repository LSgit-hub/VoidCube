---
name: llama-cpp
description: 使用 llama.cpp/llama-cpp-python 加载、运行和服务已有 GGUF 模型，面向 CPU、Apple Silicon、AMD/Intel GPU 和边缘部署。模型转换与量化请使用 gguf-quantization。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [llama-cpp-python]
metadata:
  VoidCube:
    tags: [推理服务, Llama.cpp, CPU推理, Apple Silicon, 边缘部署, GGUF, 非NVIDIA, AMD GPU, Intel GPU, 嵌入式]

---

# llama.cpp

纯C/C++ LLM推理，依赖最小，针对CPU和非NVIDIA硬件优化。

## 职责边界

- 本技能负责已有 GGUF 文件的加载、推理、服务、上下文和硬件后端配置。
- `gguf-quantization` 负责 Hugging Face 模型转换为 GGUF 以及 Q2/Q4/Q5/Q6/Q8 等量化流程。
- 需要转换或量化时加载 `gguf-quantization`；已有 GGUF 文件直接使用本技能。

## 何时使用llama.cpp

**使用llama.cpp的场景：**
- 在仅CPU机器上运行
- 在Apple Silicon（M1/M2/M3/M4）上部署
- 使用AMD或Intel GPU（无CUDA）
- 边缘部署（树莓派、嵌入式系统）
- 需要简单部署，无需Docker/Python

**替代方案：TensorRT-LLM**
- 拥有NVIDIA GPU（A100/H100）
- 需要最大吞吐量（100K+ tok/s）
- 在数据中心使用CUDA运行

**替代方案：vLLM**
- 拥有NVIDIA GPU
- 需要Python优先API
- 想要PagedAttention

## 快速入门

### 安装

```bash
# macOS/Linux
brew install llama.cpp

# 或从源码构建
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make

# 使用Metal（Apple Silicon）
make LLAMA_METAL=1

# 使用CUDA（NVIDIA）
make LLAMA_CUDA=1

# 使用ROCm（AMD）
make LLAMA_HIP=1
```

### 下载模型

```bash
# 从HuggingFace下载（GGUF格式）
huggingface-cli download \
    TheBloke/Llama-2-7B-Chat-GGUF \
    llama-2-7b-chat.Q4_K_M.gguf \
    --local-dir models/

# 或从HuggingFace转换
python convert_hf_to_gguf.py models/llama-2-7b-chat/
```

### 运行推理

```bash
# 简单聊天
./llama-cli \
    -m models/llama-2-7b-chat.Q4_K_M.gguf \
    -p "Explain quantum computing" \
    -n 256  # 最大token数

# 交互式聊天
./llama-cli \
    -m models/llama-2-7b-chat.Q4_K_M.gguf \
    --interactive
```

### 服务器模式

```bash
# 启动OpenAI兼容服务器
./llama-server \
    -m models/llama-2-7b-chat.Q4_K_M.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -ngl 32  # 卸载32层到GPU

# 客户端请求
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-2-7b-chat",
    "messages": [{"role": "user", "content": "Hello!"}],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

## 量化格式

### GGUF格式概览

| 格式 | 位数 | 大小（7B） | 速度 | 质量 | 使用场景 |
|--------|------|-----------|-------|---------|----------|
| **Q4_K_M** | 4.5 | 4.1 GB | 快 | 好 | **推荐默认** |
| Q4_K_S | 4.3 | 3.9 GB | 更快 | 较低 | 速度关键 |
| Q5_K_M | 5.5 | 4.8 GB | 中 | 更好 | 质量关键 |
| Q6_K | 6.5 | 5.5 GB | 较慢 | 最佳 | 最大质量 |
| Q8_0 | 8.0 | 7.0 GB | 慢 | 优秀 | 最小退化 |
| Q2_K | 2.5 | 2.7 GB | 最快 | 差 | 仅测试 |

### 选择量化

```bash
# 通用（平衡）
Q4_K_M  # 4位，中等质量

# 最大速度（更多退化）
Q2_K or Q3_K_M

# 最大质量（较慢）
Q6_K or Q8_0

# 超大型模型（70B, 405B）
Q3_K_M or Q4_K_S  # 更低位以适应内存
```

## 硬件加速

### Apple Silicon（Metal）

```bash
# 使用Metal构建
make LLAMA_METAL=1

# 使用GPU加速运行（自动）
./llama-cli -m model.gguf -ngl 999  # 卸载所有层

# 性能：M3 Max 40-60 tokens/sec（Llama 2-7B Q4_K_M）
```

### NVIDIA GPU（CUDA）

```bash
# 使用CUDA构建
make LLAMA_CUDA=1

# 卸载层到GPU
./llama-cli -m model.gguf -ngl 35  # 卸载35/40层

# 大型模型的混合CPU+GPU
./llama-cli -m llama-70b.Q4_K_M.gguf -ngl 20  # GPU: 20层, CPU: 其余
```

### AMD GPU（ROCm）

```bash
# 使用ROCm构建
make LLAMA_HIP=1

# 使用AMD GPU运行
./llama-cli -m model.gguf -ngl 999
```

## 常用模式

### 批处理

```bash
# 从文件处理多个提示
cat prompts.txt | ./llama-cli \
    -m model.gguf \
    --batch-size 512 \
    -n 100
```

### 约束生成

```bash
# 使用语法输出JSON
./llama-cli \
    -m model.gguf \
    -p "Generate a person: " \
    --grammar-file grammars/json.gbnf

# 仅输出有效JSON
```

### 上下文大小

```bash
# 增加上下文（默认512）
./llama-cli \
    -m model.gguf \
    -c 4096  # 4K上下文窗口

# 超长上下文（如果模型支持）
./llama-cli -m model.gguf -c 32768  # 32K上下文
```

## 性能基准

### CPU性能（Llama 2-7B Q4_K_M）

| CPU | 线程 | 速度 | 成本 |
|-----|---------|-------|------|
| Apple M3 Max | 16 | 50 tok/s | $0（本地） |
| AMD Ryzen 9 7950X | 32 | 35 tok/s | $0.50/小时 |
| Intel i9-13900K | 32 | 30 tok/s | $0.40/小时 |
| AWS c7i.16xlarge | 64 | 40 tok/s | $2.88/小时 |

### GPU加速（Llama 2-7B Q4_K_M）

| GPU | 速度 | vs CPU | 成本 |
|-----|-------|--------|------|
| NVIDIA RTX 4090 | 120 tok/s | 3-4× | $0（本地） |
| NVIDIA A10 | 80 tok/s | 2-3× | $1.00/小时 |
| AMD MI250 | 70 tok/s | 2× | $2.00/小时 |
| Apple M3 Max（Metal） | 50 tok/s | ~相同 | $0（本地） |

## 支持的模型

**LLaMA系列**：
- Llama 2（7B, 13B, 70B）
- Llama 3（8B, 70B, 405B）
- Code Llama

**Mistral系列**：
- Mistral 7B
- Mixtral 8x7B, 8x22B

**其他**：
- Falcon, BLOOM, GPT-J
- Phi-3, Gemma, Qwen
- LLaVA（视觉）, Whisper（音频）

**查找模型**：https://huggingface.co/models?library=gguf

## 参考资料

- **[量化指南](references/quantization.md)** - GGUF格式、转换、质量比较
- **[服务器部署](references/server.md)** - API端点、Docker、监控
- **[优化](references/optimization.md)** - 性能调优、混合CPU+GPU

## 资源

- **GitHub**：https://github.com/ggerganov/llama.cpp
- **模型**：https://huggingface.co/models?library=gguf
- **Discord**：https://discord.gg/llama-cpp
