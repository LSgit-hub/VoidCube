# GGUF 量化指南

GGUF 量化格式和模型转换完整指南。

## 量化概述

**GGUF**（GPT-Generated Unified Format）- llama.cpp 模型的标准格式。

### 格式比较

| 格式 | 困惑度 | 大小 (7B) | Token/秒 | 备注 |
|--------|------------|-----------|------------|-------|
| FP16 | 5.9565（基线） | 13.0 GB | 15 tok/s | 原始质量 |
| Q8_0 | 5.9584 (+0.03%) | 7.0 GB | 25 tok/s | 几乎无损 |
| **Q6_K** | 5.9642 (+0.13%) | 5.5 GB | 30 tok/s | 最佳质量/大小比 |
| **Q5_K_M** | 5.9796 (+0.39%) | 4.8 GB | 35 tok/s | 平衡 |
| **Q4_K_M** | 6.0565 (+1.68%) | 4.1 GB | 40 tok/s | **推荐** |
| Q4_K_S | 6.1125 (+2.62%) | 3.9 GB | 42 tok/s | 更快，质量较低 |
| Q3_K_M | 6.3184 (+6.07%) | 3.3 GB | 45 tok/s | 仅限小模型 |
| Q2_K | 6.8673 (+15.3%) | 2.7 GB | 50 tok/s | 不推荐 |

**推荐**：使用 **Q4_K_M** 获得最佳质量和速度平衡。

## 转换模型

### HuggingFace 到 GGUF

```bash
# 1. 下载 HuggingFace 模型
huggingface-cli download meta-llama/Llama-2-7b-chat-hf \
    --local-dir models/llama-2-7b-chat/

# 2. 转换为 FP16 GGUF
python convert_hf_to_gguf.py \
    models/llama-2-7b-chat/ \
    --outtype f16 \
    --outfile models/llama-2-7b-chat-f16.gguf

# 3. 量化为 Q4_K_M
./llama-quantize \
    models/llama-2-7b-chat-f16.gguf \
    models/llama-2-7b-chat-Q4_K_M.gguf \
    Q4_K_M
```

### 批量量化

```bash
# 量化为多种格式
for quant in Q4_K_M Q5_K_M Q6_K Q8_0; do
    ./llama-quantize \
        model-f16.gguf \
        model-${quant}.gguf \
        $quant
done
```

## K-量化方法

**K-quant** 使用混合精度以获得更好质量：
- 注意力权重：更高精度
- 前馈权重：更低精度

**变体**：
- `_S`（Small）：更快，质量较低
- `_M`（Medium）：平衡（推荐）
- `_L`（Large）：更好质量，更大尺寸

**示例**：`Q4_K_M`
- `Q4`：4 位量化
- `K`：混合精度方法
- `M`：中等质量

## 质量测试

```bash
# 计算困惑度（质量指标）
./llama-perplexity \
    -m model.gguf \
    -f wikitext-2-raw/wiki.test.raw \
    -c 512

# 困惑度越低 = 质量越好
# 基线 (FP16): ~5.96
# Q4_K_M: ~6.06 (+1.7%)
# Q2_K: ~6.87 (+15.3% - 退化太多)
```

## 用例指南

### 通用目的（聊天机器人、助手）
```
Q4_K_M - 最佳平衡
Q5_K_M - 如果有额外内存
```

### 代码生成
```
Q5_K_M 或 Q6_K - 更高精度有助于代码
```

### 创意写作
```
Q4_K_M - 足够的质量
Q3_K_M - 可接受用于草稿生成
```

### 技术/医学
```
Q6_K 或 Q8_0 - 最大精度
```

### 边缘设备（树莓派）
```
Q2_K 或 Q3_K_S - 适应有限内存
```

## 模型大小缩放

### 7B 参数模型

| 格式 | 大小 | 所需内存 |
|--------|------|------------|
| Q2_K | 2.7 GB | 5 GB |
| Q3_K_M | 3.3 GB | 6 GB |
| Q4_K_M | 4.1 GB | 7 GB |
| Q5_K_M | 4.8 GB | 8 GB |
| Q6_K | 5.5 GB | 9 GB |
| Q8_0 | 7.0 GB | 11 GB |

### 13B 参数模型

| 格式 | 大小 | 所需内存 |
|--------|------|------------|
| Q2_K | 5.1 GB | 8 GB |
| Q3_K_M | 6.2 GB | 10 GB |
| Q4_K_M | 7.9 GB | 12 GB |
| Q5_K_M | 9.2 GB | 14 GB |
| Q6_K | 10.7 GB | 16 GB |

### 70B 参数模型

| 格式 | 大小 | 所需内存 |
|--------|------|------------|
| Q2_K | 26 GB | 32 GB |
| Q3_K_M | 32 GB | 40 GB |
| Q4_K_M | 41 GB | 48 GB |
| Q4_K_S | 39 GB | 46 GB |
| Q5_K_M | 48 GB | 56 GB |

**70B 推荐**：使用 Q3_K_M 或 Q4_K_S 以适应消费级硬件。

## 查找预量化模型

**TheBloke** 在 HuggingFace 上：
- https://huggingface.co/TheBloke
- 大多数模型提供所有 GGUF 格式
- 无需转换

**示例**：
```bash
# 下载预量化的 Llama 2-7B
huggingface-cli download \
    TheBloke/Llama-2-7B-Chat-GGUF \
    llama-2-7b-chat.Q4_K_M.gguf \
    --local-dir models/
```

## 重要性矩阵（imatrix）

**是什么**：用于提高量化质量的校准数据。

**好处**：
- Q4 困惑度改善 10-20%
- Q3 及更低必需

**用法**：
```bash
# 1. 生成重要性矩阵
./llama-imatrix \
    -m model-f16.gguf \
    -f calibration-data.txt \
    -o model.imatrix

# 2. 使用 imatrix 量化
./llama-quantize \
    --imatrix model.imatrix \
    model-f16.gguf \
    model-Q4_K_M.gguf \
    Q4_K_M
```

**校准数据**：
- 使用领域特定文本（例如，代码模型用代码）
- 约 100MB 代表性文本
- 更高质量数据 = 更好量化

## 故障排除

**模型输出乱码**：
- 量化太激进（Q2_K）
- 尝试 Q4_K_M 或 Q5_K_M
- 验证模型转换正确

**内存不足**：
- 使用更低量化（Q4_K_S 而不是 Q5_K_M）
- 卸载更少层到 GPU（`-ngl`）
- 使用更小上下文（`-c 2048`）

**推理慢**：
- 更高量化使用更多计算
- Q8_0 比 Q4_K_M 慢得多
- 考虑速度与质量权衡
