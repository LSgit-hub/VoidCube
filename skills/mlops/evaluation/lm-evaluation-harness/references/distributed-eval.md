# 分布式评估

使用数据并行和张量/流水线并行跨多个 GPU 运行评估的指南。

## 概述

分布式评估通过以下方式加速基准测试：
- **数据并行**：在 GPU 之间分配评估样本（每个 GPU 有完整的模型副本）
- **张量并行**：在 GPU 之间分配模型权重（用于大模型）
- **流水线并行**：在 GPU 之间分配模型层（用于超大模型）

**何时使用**：
- 数据并行：模型适合单个 GPU，希望更快评估
- 张量/流水线并行：模型对单个 GPU 来说太大

## HuggingFace 模型（`hf`）

### 数据并行（推荐）

每个 GPU 加载完整的模型副本并处理评估数据的子集。

**单节点（8 GPU）**：
```bash
accelerate launch --multi_gpu --num_processes 8 \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag \
  --batch_size 16
```

**加速比**：接近线性（8 GPU = ~8× 更快）

**内存**：每个 GPU 需要完整模型（7B 模型 ≈ 14GB × 8 = 112GB 总计）

### 张量并行（模型分片）

在 GPU 之间分配模型权重，用于对单个 GPU 来说太大的模型。

**不使用 accelerate 启动器**：
```bash
lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    dtype=bfloat16 \
  --tasks mmlu,gsm8k \
  --batch_size 8
```

**使用 8 GPU**：70B 模型（140GB）/ 8 = 每GPU 17.5GB ✅

**高级分片**：
```bash
lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    device_map_option=auto,\
    max_memory_per_gpu=40GB,\
    max_cpu_memory=100GB,\
    dtype=bfloat16 \
  --tasks mmlu
```

**选项**：
- `device_map_option`：`"auto"`（默认）、`"balanced"`、`"balanced_low_0"`
- `max_memory_per_gpu`：每个 GPU 的最大内存（例如 `"40GB"`）
- `max_cpu_memory`：用于卸载的最大 CPU 内存
- `offload_folder`：磁盘卸载目录

### 组合数据 + 张量并行

对超大模型同时使用两者。

**示例：16 GPU 上的 70B 模型（2 个副本，每个 8 GPU）**：
```bash
accelerate launch --multi_gpu --num_processes 2 \
  -m lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    dtype=bfloat16 \
  --tasks mmlu \
  --batch_size 8
```

**结果**：数据并行带来 2× 加速，张量并行使 70B 模型适合内存

### 使用 `accelerate config` 配置

创建 `~/.cache/huggingface/accelerate/default_config.yaml`：
```yaml
compute_environment: LOCAL_MACHINE
distributed_type: MULTI_GPU
num_machines: 1
num_processes: 8
gpu_ids: all
mixed_precision: bf16
```

**然后运行**：
```bash
accelerate launch -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu
```

## vLLM 模型（`vllm`）

vLLM 提供高度优化的分布式推理。

### 张量并行

**单节点（4 GPU）**：
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=4,\
    dtype=auto,\
    gpu_memory_utilization=0.9 \
  --tasks mmlu,gsm8k \
  --batch_size auto
```

**内存**：70B 模型分配到 4 GPU = 每GPU ~35GB

### 数据并行

**多个模型副本**：
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    data_parallel_size=4,\
    dtype=auto,\
    gpu_memory_utilization=0.8 \
  --tasks hellaswag,arc_challenge \
  --batch_size auto
```

**结果**：4 个模型副本 = 4× 吞吐量

### 组合张量 + 数据并行

**示例：8 GPU = 4 TP × 2 DP**：
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=4,\
    data_parallel_size=2,\
    dtype=auto,\
    gpu_memory_utilization=0.85 \
  --tasks mmlu \
  --batch_size auto
```

**结果**：70B 模型适合（TP=4），2× 加速（DP=2）

### 多节点 vLLM

vLLM 原生不支持多节点。使用 Ray：

```bash
# 启动 Ray 集群
ray start --head --port=6379

# 运行评估
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=8,\
    dtype=auto \
  --tasks mmlu
```

## NVIDIA NeMo 模型（`nemo_lm`）

### 数据复制

**8 GPU 上的 8 个副本**：
```bash
torchrun --nproc-per-node=8 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/model.nemo,\
    devices=8 \
  --tasks hellaswag,arc_challenge \
  --batch_size 32
```

**加速比**：接近线性（8× 更快）

### 张量并行

**4 路张量并行**：
```bash
torchrun --nproc-per-node=4 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/70b_model.nemo,\
    devices=4,\
    tensor_model_parallel_size=4 \
  --tasks mmlu,gsm8k \
  --batch_size 16
```

### 流水线并行

**4 GPU 上的 2 TP × 2 PP**：
```bash
torchrun --nproc-per-node=4 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/model.nemo,\
    devices=4,\
    tensor_model_parallel_size=2,\
    pipeline_model_parallel_size=2 \
  --tasks mmlu \
  --batch_size 8
```

**约束**：`devices = TP × PP`

### 多节点 NeMo

lm-evaluation-harness 目前不支持多节点。

## SGLang 模型（`sglang`）

### 张量并行

```bash
lm_eval --model sglang \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tp_size=4,\
    dtype=auto \
  --tasks gsm8k \
  --batch_size auto
```

### 数据并行（已弃用）

**注意**：SGLang 正在弃用数据并行。请使用张量并行。

```bash
lm_eval --model sglang \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    dp_size=4,\
    dtype=auto \
  --tasks mmlu
```

## 性能比较

### 70B 模型评估（MMLU，5-shot）

| 方法 | GPU | 时间 | 内存/GPU | 备注 |
|------|-----|------|----------|------|
| HF（无并行） | 1 | 8 小时 | 140GB（OOM） | 无法容纳 |
| HF（TP=8） | 8 | 2 小时 | 17.5GB | 较慢，可容纳 |
| HF（DP=8） | 8 | 1 小时 | 140GB（OOM） | 无法容纳 |
| vLLM（TP=4） | 4 | 30 分钟 | 35GB | 快！ |
| vLLM（TP=4, DP=2） | 8 | 15 分钟 | 35GB | 最快 |

### 7B 模型评估（多个任务）

| 方法 | GPU | 时间 | 加速比 |
|------|-----|------|--------|
| HF（单） | 1 | 4 小时 | 1× |
| HF（DP=4） | 4 | 1 小时 | 4× |
| HF（DP=8） | 8 | 30 分钟 | 8× |
| vLLM（DP=8） | 8 | 15 分钟 | 16× |

**结论**：vLLM 在推理方面明显快于 HuggingFace。

## 选择并行策略

### 决策树

```
模型适合单个 GPU？
├─ 是：使用数据并行
│   ├─ HF: accelerate launch --multi_gpu --num_processes N
│   └─ vLLM: data_parallel_size=N（最快）
│
└─ 否：使用张量/流水线并行
    ├─ 模型 < 70B：
    │   └─ vLLM: tensor_parallel_size=4
    ├─ 模型 70-175B：
    │   ├─ vLLM: tensor_parallel_size=8
    │   └─ 或 HF: parallelize=True
    └─ 模型 > 175B：
        └─ 联系框架作者
```

### 内存估算

**经验法则**：
```
内存（GB）= 参数量（B）× 精度（字节）× 1.2（开销）
```

**示例**：
- 7B FP16：7 × 2 × 1.2 = 16.8GB ✅ 适合 A100 40GB
- 13B FP16：13 × 2 × 1.2 = 31.2GB ✅ 适合 A100 40GB
- 70B FP16：70 × 2 × 1.2 = 168GB ❌ 需要 TP=4 或 TP=8
- 70B BF16：70 × 2 × 1.2 = 168GB（与 FP16 相同）

**使用张量并行**：
```
每 GPU 内存 = 总内存 / TP
```

- 4 GPU 上的 70B：168GB / 4 = 每GPU 42GB ✅
- 8 GPU 上的 70B：168GB / 8 = 每GPU 21GB ✅

## 多节点评估

### 使用 SLURM 的 HuggingFace

**提交作业**：
```bash
#!/bin/bash
#SBATCH --nodes=4
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1

srun accelerate launch --multi_gpu \
  --num_processes $((SLURM_NNODES * 8)) \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag \
  --batch_size 16
```

**提交**：
```bash
sbatch eval_job.sh
```

### 手动多节点设置

**在每个节点上运行**：
```bash
accelerate launch \
  --multi_gpu \
  --num_machines 4 \
  --num_processes 32 \
  --main_process_ip $MASTER_IP \
  --main_process_port 29500 \
  --machine_rank $NODE_RANK \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu
```

**环境变量**：
- `MASTER_IP`：rank 0 节点的 IP
- `NODE_RANK`：每个节点为 0、1、2、3

## 最佳实践

### 1. 从小规模开始

先在小样本上测试：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-70b-hf,parallelize=True \
  --tasks mmlu \
  --limit 100  # 仅 100 个样本
```

### 2. 监控 GPU 使用

```bash
# 终端 1：运行评估
lm_eval --model hf ...

# 终端 2：监控
watch -n 1 nvidia-smi
```

观察：
- GPU 利用率 > 90%
- 内存使用稳定
- 所有 GPU 活跃

### 3. 优化批大小

```bash
# 自动批大小（推荐）
--batch_size auto

# 或手动调整
--batch_size 16  # 从这里开始
--batch_size 32  # 如果内存允许则增加
```

### 4. 使用混合精度

```bash
--model_args dtype=bfloat16  # 更快，更少内存
```

### 5. 检查通信

对于数据并行，检查网络带宽：
```bash
# 应该看到 InfiniBand 或高速网络
nvidia-smi topo -m
```

## 故障排除

### "CUDA 内存不足"

**解决方案**：
1. 增加张量并行：
   ```bash
   --model_args tensor_parallel_size=8  # 原为 4
   ```

2. 减小批大小：
   ```bash
   --batch_size 4  # 原为 16
   ```

3. 降低精度：
   ```bash
   --model_args dtype=int8  # 量化
   ```

### "NCCL 错误" 或挂起

**检查**：
1. 所有 GPU 可见：`nvidia-smi`
2. NCCL 已安装：`python -c "import torch; print(torch.cuda.nccl.version())"`
3. 节点间的网络连接

**修复**：
```bash
export NCCL_DEBUG=INFO  # 启用调试日志
export NCCL_IB_DISABLE=0  # 如果可用则使用 InfiniBand
```

### 评估缓慢

**可能原因**：
1. **数据加载瓶颈**：预处理数据集
2. **GPU 利用率低**：增加批大小
3. **通信开销**：降低并行度

**分析**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --limit 100 \
  --log_samples  # 检查时序
```

### GPU 不平衡

**症状**：GPU 0 为 100%，其他为 50%

**解决方案**：使用 `device_map_option=balanced`：
```bash
--model_args parallelize=True,device_map_option=balanced
```

## 示例配置

### 小模型（7B）- 快速评估

```bash
# 8 个 A100，数据并行
accelerate launch --multi_gpu --num_processes 8 \
  -m lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag,arc_challenge \
  --num_fewshot 5 \
  --batch_size 32

# 时间：~30 分钟
```

### 大模型（70B）- vLLM

```bash
# 8 个 H100，张量并行
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=8,\
    dtype=auto,\
    gpu_memory_utilization=0.9 \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto

# 时间：~1 小时
```

### 超大模型（175B+）

**需要专门设置 - 联系框架维护者**

## 参考资料

- HuggingFace Accelerate：https://huggingface.co/docs/accelerate/
- vLLM 文档：https://docs.vllm.ai/
- NeMo 文档：https://docs.nvidia.com/nemo-framework/
- lm-eval 分布式指南：`docs/model_guide.md`
