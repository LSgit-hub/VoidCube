# 性能优化

## 内容
- PagedAttention 解析
- 连续批处理机制
- 前缀缓存策略
- 推测解码设置
- 基准测试结果和比较
- 性能调优指南

## PagedAttention 解析

**传统注意力问题**：
- KV 缓存存储在连续内存中
- 由于碎片化浪费约 50% GPU 内存
- 无法为不同序列长度动态重新分配

**PagedAttention 解决方案**：
- 将 KV 缓存划分为固定大小的块（类似操作系统虚拟内存）
- 从空闲块队列动态分配
- 跨序列共享块（用于前缀缓存）

**内存节省示例**：
```
传统方式：70B 模型需要 160GB KV 缓存 → 8x A100 上 OOM
PagedAttention：70B 模型需要 80GB KV 缓存 → 适合 4x A100
```

**配置**：
```bash
# 块大小（默认：16 个令牌）
vllm serve MODEL --block-size 16

# GPU 块数量（自动计算）
# 由 --gpu-memory-utilization 控制
vllm serve MODEL --gpu-memory-utilization 0.9
```

## 连续批处理机制

**传统批处理**：
- 等待批次中所有序列完成
- 等待最长序列时 GPU 空闲
- 低 GPU 利用率（~40-60%）

**连续批处理**：
- 当槽位可用时添加新请求
- 在同一批次中混合预填充（新请求）和解码（进行中）
- 高 GPU 利用率（>90%）

**吞吐量提升**：
```
传统批处理：50 请求/秒 @ 50% GPU 利用率
连续批处理：200 请求/秒 @ 90% GPU 利用率
= 4 倍吞吐量提升
```

**调优参数**：
```bash
# 最大并发序列数（更高 = 更多批处理）
vllm serve MODEL --max-num-seqs 256

# 预填充/解码调度（默认自动平衡）
# 无需手动调优
```

## 前缀缓存策略

为常见提示前缀重用已计算的 KV 缓存。

**用例**：
- 跨请求重复的系统提示
- 每个提示中的少样本示例
- 具有重叠块的 RAG 上下文

**节省示例**：
```
提示：[系统：500 令牌] + [用户：100 令牌]

无缓存：每次请求计算 600 令牌
有缓存：一次计算 500 令牌，然后每次请求 100 令牌
= TTFT 快 83%
```

**启用前缀缓存**：
```bash
vllm serve MODEL --enable-prefix-caching
```

**自动前缀检测**：
- vLLM 自动检测常见前缀
- 无需代码更改
- 与 OpenAI 兼容 API 配合工作

**缓存命中率监控**：
```bash
curl http://localhost:9090/metrics | grep cache_hit
# vllm_cache_hit_rate: 0.75  (75% 命中率)
```

## 推测解码设置

使用较小的"草稿"模型提议令牌，较大模型验证。

**速度提升**：
```
标准方式：每次前向传播生成 1 个令牌
推测解码：每次前向传播生成 3-5 个令牌
= 2-3 倍更快生成
```

**工作原理**：
1. 草稿模型提议 K 个令牌（快速）
2. 目标模型并行验证所有 K 个令牌（一次传播）
3. 接受验证的令牌，从第一个拒绝处重新开始

**使用独立草稿模型设置**：
```bash
vllm serve meta-llama/Llama-3-70B-Instruct \
  --speculative-model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
  --num-speculative-tokens 5
```

**使用 n-gram 草稿设置**（无独立模型）：
```bash
vllm serve MODEL \
  --speculative-method ngram \
  --num-speculative-tokens 3
```

**何时使用**：
- 输出长度 > 100 令牌
- 草稿模型比目标小 5-10 倍
- 可接受 2-3% 准确性权衡

## 基准测试结果

**vLLM vs HuggingFace Transformers**（Llama 3 8B，A100）：
```
指标                  | HF Transformers | vLLM   | 提升
------------------------|-----------------|--------|------------
吞吐量（请求/秒）    | 12              | 280    | 23 倍
TTFT（毫秒）              | 850             | 120    | 7 倍
令牌/秒             | 45              | 2,100  | 47 倍
GPU 内存（GB）        | 28              | 16     | 少 1.75 倍
```

**vLLM vs TensorRT-LLM**（Llama 2 70B，4x A100）：
```
指标                  | TensorRT-LLM | vLLM   | 备注
------------------------|--------------|--------|------------------
吞吐量（请求/秒）    | 320          | 285    | TRT 快 12%
设置复杂度        | 高         | 低    | vLLM 更简单
仅 NVIDIA            | 是          | 否     | vLLM 多平台
量化支持    | FP8, INT8    | AWQ/GPTQ/FP8 | vLLM 更多选项
```

## 性能调优指南

**步骤 1：测量基线**

```bash
# 安装基准测试工具
pip install locust

# 运行基线基准测试
vllm bench throughput \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --num-prompts 1000

# 记录：吞吐量、TTFT、令牌/秒
```

**步骤 2：调优内存利用率**

```bash
# 尝试不同值：0.7, 0.85, 0.9, 0.95
vllm serve MODEL --gpu-memory-utilization 0.9
```

更高 = 更多批次容量 = 更高吞吐量，但有 OOM 风险。

**步骤 3：调优并发**

```bash
# 尝试值：128, 256, 512, 1024
vllm serve MODEL --max-num-seqs 256
```

更高 = 更多批处理机会，但可能增加延迟。

**步骤 4：启用优化**

```bash
vllm serve MODEL \
  --enable-prefix-caching \     # 用于重复提示
  --enable-chunked-prefill \    # 用于长提示
  --gpu-memory-utilization 0.9 \
  --max-num-seqs 512
```

**步骤 5：重新基准测试并比较**

目标提升：
- 吞吐量：+30-100%
- TTFT：-20-50%
- GPU 利用率：>85%

**常见性能问题**：

**低吞吐量（<50 请求/秒）**：
- 增加 `--max-num-seqs`
- 启用 `--enable-prefix-caching`
- 检查 GPU 利用率（应 >80%）

**高 TTFT（>1 秒）**：
- 启用 `--enable-chunked-prefill`
- 如可能减少 `--max-model-len`
- 检查模型是否对 GPU 来说太大

**OOM 错误**：
- 将 `--gpu-memory-utilization` 减少到 0.7
- 减少 `--max-model-len`
- 使用量化（`--quantization awq`）
