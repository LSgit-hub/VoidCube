# 故障排除指南

## 内容
- 内存不足（OOM）错误
- 性能问题
- 模型加载错误
- 网络和连接问题
- 量化问题
- 分布式服务问题
- 调试工具和命令

## 内存不足（OOM）错误

### 症状：模型加载期间 `torch.cuda.OutOfMemoryError`

**原因**：模型 + KV 缓存超过可用 VRAM

**解决方案（按顺序尝试）**：

1. **降低 GPU 内存利用率**：
```bash
vllm serve MODEL --gpu-memory-utilization 0.7  # 尝试 0.7, 0.75, 0.8
```

2. **减少最大序列长度**：
```bash
vllm serve MODEL --max-model-len 4096  # 而非 8192
```

3. **启用量化**：
```bash
vllm serve MODEL --quantization awq  # 4 倍内存减少
```

4. **使用张量并行**（多 GPU）：
```bash
vllm serve MODEL --tensor-parallel-size 2  # 分配到 2 个 GPU
```

5. **减少最大并发序列数**：
```bash
vllm serve MODEL --max-num-seqs 128  # 默认为 256
```

### 症状：推理期间 OOM（非模型加载）

**原因**：生成期间 KV 缓存填满

**解决方案**：

```bash
# 减少 KV 缓存分配
vllm serve MODEL --gpu-memory-utilization 0.85

# 减少批大小
vllm serve MODEL --max-num-seqs 64

# 减少每个请求的最大令牌数
# 在客户端请求中设置：max_tokens=512
```

### 症状：量化模型 OOM

**原因**：量化开销或配置不正确

**解决方案**：
```bash
# 确保量化标志与模型匹配
vllm serve TheBloke/Llama-2-70B-AWQ --quantization awq  # 必须指定

# 尝试不同的 dtype
vllm serve MODEL --quantization awq --dtype float16
```

## 性能问题

### 症状：低吞吐量（<50 请求/秒，预期 >100）

**诊断步骤**：

1. **检查 GPU 利用率**：
```bash
watch -n 1 nvidia-smi
# GPU 利用率应 >80%
```

如果 <80%，增加并发请求：
```bash
vllm serve MODEL --max-num-seqs 512  # 从 256 增加
```

2. **检查是否内存受限**：
```bash
# 如果内存 100% 但 GPU <80%，减少序列长度
vllm serve MODEL --max-model-len 4096
```

3. **启用优化**：
```bash
vllm serve MODEL \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --max-num-seqs 512
```

4. **检查张量并行设置**：
```bash
# 必须使用 2 的幂次 GPU 数
vllm serve MODEL --tensor-parallel-size 4  # 不是 3 或 5
```

### 症状：高 TTFT（首令牌时间 >1 秒）

**原因和解决方案**：

**长提示**：
```bash
vllm serve MODEL --enable-chunked-prefill
```

**无前缀缓存**：
```bash
vllm serve MODEL --enable-prefix-caching  # 用于重复提示
```

**并发请求过多**：
```bash
vllm serve MODEL --max-num-seqs 64  # 减少以优先考虑延迟
```

**模型对单个 GPU 太大**：
```bash
vllm serve MODEL --tensor-parallel-size 2  # 并行化预填充
```

### 症状：令牌生成缓慢（低令牌/秒）

**诊断**：
```bash
# 检查模型大小是否正确
vllm serve MODEL  # 应在日志中看到模型大小

# 检查推测解码
vllm serve MODEL --speculative-model DRAFT_MODEL
```

**对于 H100 GPU**，启用 FP8：
```bash
vllm serve MODEL --quantization fp8
```

## 模型加载错误

### 症状：`OSError: MODEL not found`

**原因**：

1. **模型名称拼写错误**：
```bash
# 在 HuggingFace 上检查确切的模型名称
vllm serve meta-llama/Llama-3-8B-Instruct  # 正确的大小写
```

2. **私有/受限模型**：
```bash
# 先登录 HuggingFace
huggingface-cli login
# 然后运行 vLLM
vllm serve meta-llama/Llama-3-70B-Instruct
```

3. **自定义模型需要信任标志**：
```bash
vllm serve MODEL --trust-remote-code
```

### 症状：`ValueError: Tokenizer not found`

**解决方案**：
```bash
# 先手动下载模型
python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('MODEL')"

# 然后启动 vLLM
vllm serve MODEL
```

### 症状：`ImportError: No module named 'flash_attn'`

**解决方案**：
```bash
# 安装 flash attention
pip install flash-attn --no-build-isolation

# 或禁用 flash attention
vllm serve MODEL --disable-flash-attn
```

## 网络和连接问题

### 症状：查询服务器时 `Connection refused`

**诊断**：

1. **检查服务器是否运行**：
```bash
curl http://localhost:8000/health
```

2. **检查端口绑定**：
```bash
# 绑定到所有接口以进行远程访问
vllm serve MODEL --host 0.0.0.0 --port 8000

# 检查端口是否被占用
lsof -i :8000
```

3. **检查防火墙**：
```bash
# 允许端口通过防火墙
sudo ufw allow 8000
```

### 症状：网络响应时间慢

**解决方案**：

1. **增加超时**：
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="EMPTY",
    timeout=300.0  # 5 分钟超时
)
```

2. **检查网络延迟**：
```bash
ping SERVER_IP  # 本地网络应 <10ms
```

3. **使用连接池**：
```python
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
retries = Retry(total=3, backoff_factor=1)
session.mount('http://', HTTPAdapter(max_retries=retries))
```

## 量化问题

### 症状：`RuntimeError: Quantization format not supported`

**解决方案**：
```bash
# 确保正确的量化方法
vllm serve MODEL --quantization awq  # 对于 AWQ 模型
vllm serve MODEL --quantization gptq  # 对于 GPTQ 模型

# 检查模型卡片的量化类型
```

### 症状：量化后输出质量差

**诊断**：

1. **验证模型正确量化**：
```bash
# 检查模型 config.json 中的 quantization_config
cat ~/.cache/huggingface/hub/models--MODEL/config.json
```

2. **尝试不同的量化方法**：
```bash
# 如果 AWQ 质量有问题，尝试 FP8（仅 H100）
vllm serve MODEL --quantization fp8

# 或使用较不激进的量化
vllm serve MODEL  # 无量化
```

3. **增加温度以获得更好的多样性**：
```python
sampling_params = SamplingParams(temperature=0.8, top_p=0.95)
```

## 分布式服务问题

### 症状：`RuntimeError: Distributed init failed`

**诊断**：

1. **检查环境变量**：
```bash
# 在所有节点上
echo $MASTER_ADDR  # 应相同
echo $MASTER_PORT  # 应相同
echo $RANK  # 每个节点应唯一（0, 1, 2, ...）
echo $WORLD_SIZE  # 应相同（总节点数）
```

2. **检查网络连接**：
```bash
# 从节点 1 到节点 2
ping NODE2_IP
nc -zv NODE2_IP 29500  # 检查端口可访问性
```

3. **检查 NCCL 设置**：
```bash
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0  # 或你的网络接口
vllm serve MODEL --tensor-parallel-size 8
```

### 症状：`NCCL error: unhandled cuda error`

**解决方案**：

```bash
# 设置 NCCL 使用正确的网络接口
export NCCL_SOCKET_IFNAME=eth0  # 替换为你的接口

# 增加超时
export NCCL_TIMEOUT=1800  # 30 分钟

# 强制 P2P 用于调试
export NCCL_P2P_DISABLE=1
```

## 调试工具和命令

### 启用调试日志

```bash
export VLLM_LOGGING_LEVEL=DEBUG
vllm serve MODEL
```

### 监控 GPU 使用

```bash
# 实时 GPU 监控
watch -n 1 nvidia-smi

# 内存分解
nvidia-smi --query-gpu=memory.used,memory.free --format=csv -l 1
```

### 性能分析

```bash
# 内置基准测试
vllm bench throughput \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --num-prompts 100

vllm bench latency \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --batch-size 8
```

### 检查指标

```bash
# Prometheus 指标
curl http://localhost:9090/metrics

# 过滤特定指标
curl http://localhost:9090/metrics | grep vllm_time_to_first_token

# 要监控的关键指标：
# - vllm_time_to_first_token_seconds
# - vllm_time_per_output_token_seconds
# - vllm_num_requests_running
# - vllm_gpu_cache_usage_perc
# - vllm_request_success_total
```

### 测试服务器健康

```bash
# 健康检查
curl http://localhost:8000/health

# 模型信息
curl http://localhost:8000/v1/models

# 测试补全
curl http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "MODEL",
    "prompt": "Hello",
    "max_tokens": 10
  }'
```

### 常见环境变量

```bash
# CUDA 设置
export CUDA_VISIBLE_DEVICES=0,1,2,3  # 限制到特定 GPU

# vLLM 设置
export VLLM_LOGGING_LEVEL=DEBUG
export VLLM_TRACE_FUNCTION=1  # 分析函数
export VLLM_USE_V1=1  # 使用 v1.0 引擎（更快）

# NCCL 设置（分布式）
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0
export NCCL_IB_DISABLE=0  # 启用 InfiniBand
```

### 收集错误报告的诊断信息

```bash
# 系统信息
nvidia-smi
python --version
pip show vllm

# vLLM 版本和配置
vllm --version
python -c "import vllm; print(vllm.__version__)"

# 使用调试日志运行
export VLLM_LOGGING_LEVEL=DEBUG
vllm serve MODEL 2>&1 | tee vllm_debug.log

# 在错误报告中包含：
# - vllm_debug.log
# - nvidia-smi 输出
# - 使用的完整命令
# - 预期 vs 实际行为
```
