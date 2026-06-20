# 量化指南

## 内容
- 量化方法比较
- AWQ 设置和使用
- GPTQ 设置和使用
- FP8 量化（H100）
- 模型准备
- 准确性与压缩权衡

## 量化方法比较

| 方法 | 压缩率 | 准确性损失 | 速度 | 最适合 |
|--------|-------------|---------------|-------|----------|
| **AWQ** | 4-bit (75%) | <1% | 快 | 70B 模型，生产环境 |
| **GPTQ** | 4-bit (75%) | 1-2% | 快 | 广泛模型支持 |
| **FP8** | 8-bit (50%) | <0.5% | 最快 | 仅 H100 GPU |
| **SqueezeLLM** | 3-4 bit (75-80%) | 2-3% | 中等 | 极限压缩 |

**推荐**：
- **生产环境**：70B 模型使用 AWQ
- **H100 GPU**：使用 FP8 获得最佳速度
- **最大兼容性**：使用 GPTQ
- **极限压缩**：使用 SqueezeLLM

## AWQ 设置和使用

**AWQ**（激活感知权重量化）在 4-bit 时达到最佳准确性。

**步骤 1：查找预量化模型**

在 HuggingFace 搜索 AWQ 模型：
```bash
# 示例：TheBloke/Llama-2-70B-AWQ
# 示例：TheBloke/Mixtral-8x7B-Instruct-v0.1-AWQ
```

**步骤 2：使用 AWQ 启动**

```bash
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95
```

**内存节省**：
```
Llama 2 70B fp16：140GB VRAM（需要 4x A100）
Llama 2 70B AWQ：35GB VRAM（1x A100 40GB）
= 4 倍内存减少
```

**步骤 3：验证性能**

测试输出是否可接受：
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")

# 测试复杂推理
response = client.chat.completions.create(
    model="TheBloke/Llama-2-70B-AWQ",
    messages=[{"role": "user", "content": "Explain quantum entanglement"}]
)

print(response.choices[0].message.content)
# 验证质量符合你的要求
```

**量化自己的模型**（需要 80GB+ VRAM 的 GPU）：

```python
from awq import AutoAWQForCausalLM
from transformers import AutoTokenizer

model_path = "meta-llama/Llama-2-70b-hf"
quant_path = "llama-2-70b-awq"

# 加载模型
model = AutoAWQForCausalLM.from_pretrained(model_path)
tokenizer = AutoTokenizer.from_pretrained(model_path)

# 量化
quant_config = {"zero_point": True, "q_group_size": 128, "w_bit": 4}
model.quantize(tokenizer, quant_config=quant_config)

# 保存
model.save_quantized(quant_path)
tokenizer.save_pretrained(quant_path)
```

## GPTQ 设置和使用

**GPTQ** 具有最广泛的模型支持和良好的压缩。

**步骤 1：查找 GPTQ 模型**

```bash
# 示例：TheBloke/Llama-2-13B-GPTQ
# 示例：TheBloke/CodeLlama-34B-GPTQ
```

**步骤 2：使用 GPTQ 启动**

```bash
vllm serve TheBloke/Llama-2-13B-GPTQ \
  --quantization gptq \
  --dtype float16
```

**GPTQ 配置选项**：
```bash
# 如需要指定 GPTQ 参数
vllm serve MODEL \
  --quantization gptq \
  --gptq-act-order \  # 激活排序
  --dtype float16
```

**量化自己的模型**：

```python
from auto_gptq import AutoGPTQForCausalLM, BaseQuantizeConfig
from transformers import AutoTokenizer

model_name = "meta-llama/Llama-2-13b-hf"
quantized_name = "llama-2-13b-gptq"

# 加载模型
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoGPTQForCausalLM.from_pretrained(model_name, quantize_config)

# 准备校准数据
calib_data = [...]  # 示例文本列表

# 量化
quantize_config = BaseQuantizeConfig(
    bits=4,
    group_size=128,
    desc_act=True
)
model.quantize(calib_data)

# 保存
model.save_quantized(quantized_name)
```

## FP8 量化（H100）

**FP8**（8-bit 浮点）在 H100 GPU 上提供最佳速度，准确性损失最小。

**要求**：
- H100 或 H800 GPU
- CUDA 12.3+（推荐 12.8）
- Hopper 架构支持

**步骤 1：启用 FP8**

```bash
vllm serve meta-llama/Llama-3-70B-Instruct \
  --quantization fp8 \
  --tensor-parallel-size 2
```

**H100 上的性能提升**：
```
fp16：180 令牌/秒
FP8：320 令牌/秒
= 1.8 倍加速
```

**步骤 2：验证准确性**

FP8 通常有 <0.5% 的准确性下降：
```python
# 运行评估套件
# 在你的任务上比较 FP8 vs FP16
# 验证准确性可接受
```

**动态 FP8 量化**（无需预量化模型）：

```bash
# vLLM 在运行时自动量化
vllm serve MODEL --quantization fp8
# 无需模型准备
```

## 模型准备

**预量化模型（最简单）**：

1. 在 HuggingFace 搜索：`[model name] AWQ` 或 `[model name] GPTQ`
2. 下载或直接使用：`TheBloke/[Model]-AWQ`
3. 使用适当的 `--quantization` 标志启动

**量化自己的模型**：

**AWQ**：
```bash
# 安装 AutoAWQ
pip install autoawq

# 运行量化脚本
python quantize_awq.py --model MODEL --output OUTPUT
```

**GPTQ**：
```bash
# 安装 AutoGPTQ
pip install auto-gptq

# 运行量化脚本
python quantize_gptq.py --model MODEL --output OUTPUT
```

**校准数据**：
- 使用来自目标领域的 128-512 个多样化示例
- 代表生产输入
- 更高质量的校准 = 更好的准确性

## 准确性与压缩权衡

**实证结果**（Llama 2 70B 在 MMLU 基准上）：

| 量化 | 准确性 | 内存 | 速度 | 生产就绪 |
|--------------|----------|--------|-------|------------------|
| FP16（基线） | 100% | 140GB | 1.0x | ✅（如果内存可用） |
| FP8 | 99.5% | 70GB | 1.8x | ✅（仅 H100） |
| AWQ 4-bit | 99.0% | 35GB | 1.5x | ✅（最适合 70B） |
| GPTQ 4-bit | 98.5% | 35GB | 1.5x | ✅（良好兼容性） |
| SqueezeLLM 3-bit | 96.0% | 26GB | 1.3x | ⚠️（检查准确性） |

**何时使用每种方法**：

**无量化（FP16）**：
- 有足够的 GPU 内存
- 需要绝对最佳准确性
- 模型 <13B 参数

**FP8**：
- 使用 H100/H800 GPU
- 需要最佳速度和最小准确性损失
- 生产部署

**AWQ 4-bit**：
- 需要将 70B 模型放入 40GB GPU
- 生产部署
- 可接受 <1% 准确性损失

**GPTQ 4-bit**：
- 需要广泛模型支持
- 不在 H100 上（改用 FP8）
- 可接受 1-2% 准确性损失

**测试策略**：

1. **基线**：在评估集上测量 FP16 准确性
2. **量化**：创建量化版本
3. **评估**：在相同任务上比较量化 vs 基线
4. **决策**：如果下降 < 阈值（通常 1-2%）则接受

**示例评估**：
```python
from evaluate import load_evaluation_suite

# 在 FP16 基线上运行
baseline_score = evaluate(model_fp16, eval_suite)

# 在量化版本上运行
quant_score = evaluate(model_awq, eval_suite)

# 比较
degradation = (baseline_score - quant_score) / baseline_score * 100
print(f"Accuracy degradation: {degradation:.2f}%")

# 决策
if degradation < 1.0:
    print("✅ 量化可用于生产")
else:
    print("⚠️ 检查准确性损失")
```
