---
name: serving-llms-vllm
description: 使用vLLM的PagedAttention和连续批处理高吞吐量服务LLM。部署生产LLM API、优化推理延迟/吞吐量或在有限GPU内存下服务模型时使用。支持OpenAI兼容端点、量化（GPTQ/AWQ/FP8）和张量并行。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [vllm, torch, transformers]
metadata:
  VoidCube:
    tags: [vLLM, 推理服务, PagedAttention, 连续批处理, 高吞吐量, 生产, OpenAI API, 量化, 张量并行]

---

# vLLM - 高性能LLM服务

## 快速入门

vLLM通过PagedAttention（基于块的KV缓存）和连续批处理（混合预填充/解码请求）实现比标准transformers高24倍的吞吐量。

**安装**：
```bash
pip install vllm
```

**基本离线推理**：
```python
from vllm import LLM, SamplingParams

llm = LLM(model="meta-llama/Llama-3-8B-Instruct")
sampling = SamplingParams(temperature=0.7, max_tokens=256)

outputs = llm.generate(["Explain quantum computing"], sampling)
print(outputs[0].outputs[0].text)
```

**OpenAI兼容服务器**：
```bash
vllm serve meta-llama/Llama-3-8B-Instruct

# 使用OpenAI SDK查询
python -c "
from openai import OpenAI
client = OpenAI(base_url='http://localhost:8000/v1', api_key='EMPTY')
print(client.chat.completions.create(
    model='meta-llama/Llama-3-8B-Instruct',
    messages=[{'role': 'user', 'content': 'Hello!'}]
).choices[0].message.content)
"
```

## 常用工作流

### 工作流1：生产API部署

复制此检查清单并跟踪进度：

```
部署进度：
- [ ] 步骤1：配置服务器设置
- [ ] 步骤2：使用有限流量测试
- [ ] 步骤3：启用监控
- [ ] 步骤4：部署到生产
- [ ] 步骤5：验证性能指标
```

**步骤1：配置服务器设置**

根据模型大小选择配置：

```bash
# 单GPU上的7B-13B模型
vllm serve meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --max-model-len 8192 \
  --port 8000

# 使用张量并行的30B-70B模型
vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 4 \
  --gpu-memory-utilization 0.9 \
  --quantization awq \
  --port 8000

# 带缓存和指标的生产环境
vllm serve meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --enable-prefix-caching \
  --enable-metrics \
  --metrics-port 9090 \
  --port 8000 \
  --host 0.0.0.0
```

**步骤2：使用有限流量测试**

生产前运行负载测试：

```bash
# 安装负载测试工具
pip install locust

# 创建带示例请求的test_load.py
# 运行：locust -f test_load.py --host http://localhost:8000
```

验证TTFT（首个token时间）< 500ms且吞吐量 > 100 req/sec。

**步骤3：启用监控**

vLLM在端口9090暴露Prometheus指标：

```bash
curl http://localhost:9090/metrics | grep vllm
```

监控的关键指标：
- `vllm:time_to_first_token_seconds` - 延迟
- `vllm:num_requests_running` - 活跃请求
- `vllm:gpu_cache_usage_perc` - KV缓存利用率

**步骤4：部署到生产**

使用Docker进行一致部署：

```bash
# 在Docker中运行vLLM
docker run --gpus all -p 8000:8000 \
  vllm/vllm-openai:latest \
  --model meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --enable-prefix-caching
```

**步骤5：验证性能指标**

检查部署是否满足目标：
- TTFT < 500ms（短提示）
- 吞吐量 > 目标req/sec
- GPU利用率 > 80%
- 日志中无OOM错误

### 工作流2：离线批推理

用于处理大型数据集，无服务器开销。

复制此检查清单：

```
批处理：
- [ ] 步骤1：准备输入数据
- [ ] 步骤2：配置LLM引擎
- [ ] 步骤3：运行批推理
- [ ] 步骤4：处理结果
```

**步骤1：准备输入数据**

```python
# 从文件加载提示
prompts = []
with open("prompts.txt") as f:
    prompts = [line.strip() for line in f]

print(f"已加载{len(prompts)}个提示")
```

**步骤2：配置LLM引擎**

```python
from vllm import LLM, SamplingParams

llm = LLM(
    model="meta-llama/Llama-3-8B-Instruct",
    tensor_parallel_size=2,  # 使用2个GPU
    gpu_memory_utilization=0.9,
    max_model_len=4096
)

sampling = SamplingParams(
    temperature=0.7,
    top_p=0.95,
    max_tokens=512,
    stop=["</s>", "\n\n"]
)
```

**步骤3：运行批推理**

vLLM自动批处理请求以提高效率：

```python
# 一次调用处理所有提示
outputs = llm.generate(prompts, sampling)

# vLLM内部处理批处理
# 无需手动分块提示
```

**步骤4：处理结果**

```python
# 提取生成的文本
results = []
for output in outputs:
    prompt = output.prompt
    generated = output.outputs[0].text
    results.append({
        "prompt": prompt,
        "generated": generated,
        "tokens": len(output.outputs[0].token_ids)
    })

# 保存到文件
import json
with open("results.jsonl", "w") as f:
    for result in results:
        f.write(json.dumps(result) + "\n")

print(f"已处理{len(results)}个提示")
```

### 工作流3：量化模型服务

在有限GPU内存中容纳大型模型。

```
量化设置：
- [ ] 步骤1：选择量化方法
- [ ] 步骤2：查找或创建量化模型
- [ ] 步骤3：使用量化标志启动
- [ ] 步骤4：验证准确性
```

**步骤1：选择量化方法**

- **AWQ**：最适合70B模型，精度损失最小
- **GPTQ**：广泛模型支持，良好压缩
- **FP8**：在H100 GPU上最快

**步骤2：查找或创建量化模型**

使用HuggingFace的预量化模型：

```bash
# 搜索AWQ模型
# 示例：TheBloke/Llama-2-70B-AWQ
```

**步骤3：使用量化标志启动**

```bash
# 使用预量化模型
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95

# 结果：70B模型在约40GB VRAM中
```

**步骤4：验证准确性**

测试输出是否符合预期质量：

```python
# 比较量化与非量化响应
# 验证任务特定性能未改变
```

## 何时使用 vs 替代方案

**使用vLLM的场景：**
- 部署生产LLM API（100+ req/sec）
- 服务OpenAI兼容端点
- GPU内存有限但需要大型模型
- 多用户应用（聊天机器人、助手）
- 需要低延迟和高吞吐量

**替代方案：**
- **llama.cpp**：CPU/边缘推理，单用户
- **HuggingFace transformers**：研究、原型开发、一次性生成
- **TensorRT-LLM**：仅NVIDIA，需要绝对最大性能
- **Text-Generation-Inference**：已在HuggingFace生态系统中

## 常见问题

**问题：模型加载期间内存不足**

减少内存使用：
```bash
vllm serve MODEL \
  --gpu-memory-utilization 0.7 \
  --max-model-len 4096
```

或使用量化：
```bash
vllm serve MODEL --quantization awq
```

**问题：首个token慢（TTFT > 1秒）**

对重复提示启用前缀缓存：
```bash
vllm serve MODEL --enable-prefix-caching
```

对长提示启用分块预填充：
```bash
vllm serve MODEL --enable-chunked-prefill
```

**问题：模型未找到错误**

对自定义模型使用`--trust-remote-code`：
```bash
vllm serve MODEL --trust-remote-code
```

**问题：吞吐量低（<50 req/sec）**

增加并发序列：
```bash
vllm serve MODEL --max-num-seqs 512
```

使用`nvidia-smi`检查GPU利用率 - 应该 >80%。

**问题：推理比预期慢**

验证张量并行使用2的幂次GPU：
```bash
vllm serve MODEL --tensor-parallel-size 4  # 不是3
```

启用推测解码以加快生成：
```bash
vllm serve MODEL --speculative-model DRAFT_MODEL
```

## 高级主题

**服务器部署模式**：参见[references/server-deployment.md](references/server-deployment.md)了解Docker、Kubernetes和负载均衡配置。

**性能优化**：参见[references/optimization.md](references/optimization.md)了解PagedAttention调优、连续批处理详情和基准结果。

**量化指南**：参见[references/quantization.md](references/quantization.md)了解AWQ/GPTQ/FP8设置、模型准备和精度比较。

**故障排除**：参见[references/troubleshooting.md](references/troubleshooting.md)了解详细错误消息、调试步骤和性能诊断。

## 硬件要求

- **小型模型（7B-13B）**：1x A10（24GB）或A100（40GB）
- **中型模型（30B-40B）**：2x A100（40GB）使用张量并行
- **大型模型（70B+）**：4x A100（40GB）或2x A100（80GB），使用AWQ/GPTQ

支持平台：NVIDIA（主要）、AMD ROCm、Intel GPU、TPU

## 资源

- 官方文档：https://docs.vllm.ai
- GitHub：https://github.com/vllm-project/vllm
- 论文："Efficient Memory Management for Large Language Model Serving with PagedAttention"（SOSP 2023）
- 社区：https://discuss.vllm.ai
