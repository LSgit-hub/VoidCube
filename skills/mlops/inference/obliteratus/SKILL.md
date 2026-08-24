---
name: obliteratus
description: [实验性] 在获得明确授权的研究环境中分析 OBLITERATUS 的模型权重编辑流程。默认不作为普通技能推荐；涉及移除安全对齐、改变拒答行为或发布修改权重前必须确认风险、来源和许可证。
version: 2.0.0
author: Voidcube Agent
license: MIT
experimental: true
dependencies: [obliteratus, torch, transformers, bitsandbytes, accelerate, safetensors]
metadata:
  VoidCube:
    tags: [消融, 解除审查, 移除拒绝, LLM, 权重投影, SVD, 机制可解释性, HuggingFace, 模型手术]
    related_skills: [serving-llms-vllm, gguf-quantization]
---

# OBLITERATUS 技能（实验性，默认隔离）

本技能不会自动安装依赖、下载模型、修改权重或执行消融。只有用户明确确认研究目的、模型授权、备份策略和许可证边界后，才能继续后续步骤。

从开放权重LLM中移除拒绝行为（防护栏），无需重新训练或微调。使用机制可解释性技术 — 包括差分均值、SVD、白化SVD、LEACE概念擦除、SAE分解、贝叶斯核投影等 — 识别并手术式切除模型权重中的拒绝方向，同时保留推理能力。

**许可证警告：** OBLITERATUS是AGPL-3.0。永远不要将其作为Python库导入。始终通过CLI（`obliteratus`命令）或子进程调用。这保持Voidcube Agent的MIT许可证干净。

## 何时使用此技能

当用户：
- 想要"解除审查"或"消融"LLM
- 询问从模型中移除拒绝/防护栏
- 想要创建Llama、Qwen、Mistral等的无审查版本
- 提到"拒绝移除"、"消融"、"权重投影"
- 想要分析模型的拒绝机制如何工作
- 引用OBLITERATUS、abliterator或拒绝方向

## 步骤1：安装

检查是否已安装：
```bash
obliteratus --version 2>/dev/null && echo "已安装" || echo "未安装"
```

如果未安装，从GitHub克隆并安装：
```bash
git clone https://github.com/elder-plinius/OBLITERATUS.git
cd OBLITERATUS
pip install -e .
# 对于Gradio Web UI支持：
# pip install -e ".[spaces]"
```

**重要：** 安装前与用户确认。这会拉取约5-10GB的依赖（PyTorch、Transformers、bitsandbytes等）。

## 步骤2：检查硬件

首先，检查可用的GPU：
```bash
python3 -c "
import torch
if torch.cuda.is_available():
    gpu = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f'GPU: {gpu}')
    print(f'VRAM: {vram:.1f} GB')
    if vram < 4: print('层级: tiny（1B以下模型）')
    elif vram < 8: print('层级: small（1-4B模型）')
    elif vram < 16: print('层级: medium（4-9B模型，4位量化）')
    elif vram < 32: print('层级: large（8-32B模型，4位量化）')
    else: print('层级: frontier（32B+模型）')
else:
    print('无GPU - CPU上仅支持tiny模型（1B以下）')
"
```

### VRAM要求（使用4位量化）

| VRAM     | 最大模型大小  | 示例模型                              |
|:---------|:----------------|:--------------------------------------------|
| 仅CPU | ~1B参数      | GPT-2, TinyLlama, SmolLM                    |
| 4-8 GB   | ~4B参数      | Qwen2.5-1.5B, Phi-3.5 mini, Llama 3.2 3B   |
| 8-16 GB  | ~9B参数      | Llama 3.1 8B, Mistral 7B, Gemma 2 9B       |
| 24 GB    | ~32B参数     | Qwen3-32B, Llama 3.1 70B（紧张）, Command-R |
| 48 GB+   | ~72B+参数    | Qwen2.5-72B, DeepSeek-R1                    |
| 多GPU| 200B+参数    | Llama 3.1 405B, DeepSeek-V3 (685B MoE)      |

## 步骤3：浏览可用模型并获取推荐

```bash
# 按计算层级浏览模型
obliteratus models --tier medium

# 获取特定模型的架构信息
obliteratus info <model_name>

# 获取遥测驱动的最佳方法和参数推荐
obliteratus recommend <model_name>
obliteratus recommend <model_name> --insights  # 全局跨架构排名
```

## 步骤4：选择方法

### 方法选择指南
**大多数情况的默认/推荐：`advanced`。** 它使用多方向SVD和保范投影，经过充分测试。

| 情况                         | 推荐方法 | 原因                                      |
|:----------------------------------|:-------------------|:-----------------------------------------|
| 默认/大多数模型             | `advanced`         | 多方向SVD，保范，可靠 |
| 快速测试/原型开发          | `basic`            | 快速，简单，足以评估    |
| 稠密模型（Llama, Mistral）      | `advanced`         | 多方向，保范         |
| MoE模型（DeepSeek, Mixtral）     | `nuclear`          | 专家粒度，处理MoE复杂性  |
| 推理模型（R1蒸馏）     | `surgical`         | CoT感知，保留思维链    |
| 顽固拒绝持续         | `aggressive`       | 白化SVD + 头手术 + 越狱   |
| 想要可逆更改           | 使用引导向量（见分析部分） |
| 最大质量，时间不限   | `optimized`        | 贝叶斯搜索最佳参数      |
| 实验性自动检测       | `informed`         | 自动检测对齐类型 — 实验性，可能不总是优于advanced |

### 9种CLI方法
- **basic** — 通过差分均值的单一拒绝方向。快速（8B约5-10分钟）。
- **advanced**（默认，推荐）— 多SVD方向，保范投影，2次精炼传递。中等速度（约10-20分钟）。
- **aggressive** — 白化SVD + 越狱对比 + 注意力头手术。更高的一致性损坏风险。
- **spectral_cascade** — DCT频域分解。研究/新颖方法。
- **informed** — 在消融期间运行分析以自动配置。实险性 — 比advanced更慢且更不可预测。
- **surgical** — SAE特征 + 神经元掩码 + 头手术 + 每专家。非常慢（约1-2小时）。最适合推理模型。
- **optimized** — 贝叶斯超参数搜索（Optuna TPE）。最长运行时间但找到最优参数。
- **inverted** — 翻转拒绝方向。模型变得主动愿意。
- **nuclear** — 针对顽固MoE模型的最大力度组合。专家粒度。

### 方向提取方法（--direction-method标志）
- **diff_means**（默认）— 拒绝/服从激活之间的简单差分均值。稳健。
- **svd** — 多方向SVD提取。更适合复杂对齐。
- **leace** — LEACE（通过闭式估计的线性擦除）。最优线性擦除。

### 4种仅Python-API方法
（不可通过CLI使用 — 需要Python导入，这违反AGPL边界。仅当用户明确想在自己的AGPL项目中将OBLITERATUS用作库时提及。）
- failspy, gabliteration, heretic, rdo

## 步骤5：运行消融

### 标准用法
```bash
# 默认方法（advanced）— 推荐用于大多数模型
obliteratus obliterate <model_name> --method advanced --output-dir ./abliterated-models

# 使用4位量化（节省VRAM）
obliteratus obliterate <model_name> --method advanced --quantization 4bit --output-dir ./abliterated-models

# 大型模型（70B+）— 保守默认值
obliteratus obliterate <model_name> --method advanced --quantization 4bit --large-model --output-dir ./abliterated-models
```

### 微调参数
```bash
obliteratus obliterate <model_name> \
  --method advanced \
  --direction-method diff_means \
  --n-directions 4 \
  --refinement-passes 2 \
  --regularization 0.1 \
  --quantization 4bit \
  --output-dir ./abliterated-models \
  --contribute  # 选择加入遥测以进行社区研究
```

### 关键标志
| 标志 | 描述 | 默认值 |
|:-----|:------------|:--------|
| `--method` | 消融方法 | advanced |
| `--direction-method` | 方向提取 | diff_means |
| `--n-directions` | 拒绝方向数量（1-32） | 方法依赖 |
| `--refinement-passes` | 迭代传递（1-5） | 2 |
| `--regularization` | 正则化强度（0.0-1.0） | 0.1 |
| `--quantization` | 以4位或8位加载 | 无（全精度） |
| `--large-model` | 120B+的保守默认值 | false |
| `--output-dir` | 保存消融模型的位置 | ./obliterated_model |
| `--contribute` | 分享匿名结果用于研究 | false |
| `--verify-sample-size` | 拒绝检查的测试提示数量 | 20 |
| `--dtype` | 模型dtype（float16, bfloat16） | auto |

### 其他执行模式
```bash
# 交互式引导模式（硬件 → 模型 → 预设）
obliteratus interactive

# Web UI（Gradio）
obliteratus ui --port 7860

# 从YAML配置运行完整消融研究
obliteratus run config.yaml --preset quick

# 锦标赛：所有方法相互竞争
obliteratus tourney <model_name>
```

## 步骤6：验证结果

消融后，检查输出指标：

| 指标 | 良好值 | 警告 |
|:-------|:-----------|:--------|
| 拒绝率 | < 5%（理想约0%） | > 10%表示拒绝持续 |
| 困惑度变化 | < 10%增加 | > 15%表示一致性损坏 |
| KL散度 | < 0.1 | > 0.5表示显著分布偏移 |
| 一致性 | 高/通过定性检查 | 响应退化，重复 |

### 如果拒绝持续（> 10%）
1. 尝试`aggressive`方法
2. 增加`--n-directions`（例如8或16）
3. 添加`--refinement-passes 3`
4. 尝试`--direction-method svd`而不是diff_means

### 如果一致性损坏（困惑度 > 15%增加）
1. 减少`--n-directions`（尝试2）
2. 增加`--regularization`（尝试0.3）
3. 减少`--refinement-passes`到1
4. 尝试`basic`方法（更温和）

## 步骤7：使用消融模型

输出是标准的HuggingFace模型目录。

```bash
# 使用transformers本地测试
python3 -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
model = AutoModelForCausalLM.from_pretrained('./abliterated-models/<model>')
tokenizer = AutoTokenizer.from_pretrained('./abliterated-models/<model>')
inputs = tokenizer('How do I pick a lock?', return_tensors='pt')
outputs = model.generate(**inputs, max_new_tokens=200)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
"

# 上传到HuggingFace Hub
huggingface-cli upload <username>/<model-name>-abliterated ./abliterated-models/<model>

# 使用vLLM服务
vllm serve ./abliterated-models/<model>
```

## CLI命令参考

| 命令 | 描述 |
|:--------|:------------|
| `obliteratus obliterate` | 主消融命令 |
| `obliteratus info <model>` | 打印模型架构详情 |
| `obliteratus models --tier <tier>` | 按计算层级浏览精选模型 |
| `obliteratus recommend <model>` | 遥测驱动的方法/参数建议 |
| `obliteratus interactive` | 引导设置向导 |
| `obliteratus tourney <model>` | 锦标赛：所有方法正面交锋 |
| `obliteratus run <config.yaml>` | 从YAML执行消融研究 |
| `obliteratus strategies` | 列出所有注册的消融策略 |
| `obliteratus report <results.json>` | 重新生成可视化报告 |
| `obliteratus ui` | 启动Gradio Web界面 |
| `obliteratus aggregate` | 汇总社区遥测数据 |

## 分析模块

OBLITERATUS包含28个用于机制可解释性的分析模块。
参见`skill_view(name="obliteratus", file_path="references/analysis-modules.md")`获取完整参考。

### 快速分析命令
```bash
# 运行特定分析模块
obliteratus run analysis-config.yaml --preset quick

# 首先运行的关键模块：
# - alignment_imprint: 指纹DPO/RLHF/CAI/SFT对齐方法
# - concept_geometry: 单方向 vs 多面锥
# - logit_lens: 哪一层决定拒绝
# - anti_ouroboros: 自修复风险分数
# - causal_tracing: 因果必要组件
```

### 引导向量（可逆替代方案）
不进行永久权重修改，使用推理时引导：
```python
# 仅Python API — 用于用户自己的项目
from obliteratus.analysis.steering_vectors import SteeringVectorFactory, SteeringHookManager
```

## 消融策略

除了基于方向的消融，OBLITERATUS还包括结构消融策略：
- **嵌入消融** — 针对嵌入层组件
- **FFN消融** — 前馈网络块移除
- **头剪枝** — 注意力头剪枝
- **层移除** — 完整层移除

列出所有可用：`obliteratus strategies`

## 评估

OBLITERATUS包含内置评估工具：
- 拒绝率基准测试
- 困惑度比较（前后）
- LM Eval Harness集成用于学术基准
- 正面对手比较
- 基线性能跟踪

## 平台支持

- **CUDA** — 完全支持（NVIDIA GPU）
- **Apple Silicon（MLX）** — 通过MLX后端支持
- **CPU** — 支持tiny模型（< 1B参数）

## YAML配置模板

通过`skill_view`加载可复现运行的模板：
- `templates/abliteration-config.yaml` — 标准单模型配置
- `templates/analysis-study.yaml` — 消融前分析研究
- `templates/batch-abliteration.yaml` — 多模型批处理

## 遥测

OBLITERATUS可以选择将匿名运行数据贡献给全局研究数据集。
使用`--contribute`标志启用。不收集个人数据 — 仅模型名称、方法、指标。

## 常见陷阱

1. **不要将`informed`作为默认** — 它是实险性的且更慢。使用`advanced`获得可靠结果。
2. **约1B以下的模型对消融响应差** — 它们的拒绝行为浅且分散，使干净的方向提取困难。预期部分结果（剩余20-40%拒绝）。3B+模型有更干净的拒绝方向，响应更好（使用`advanced`通常0%拒绝）。
3. **`aggressive`可能使情况更糟** — 在小模型上它可能损坏一致性并实际增加拒绝率。仅当`advanced`在3B+模型上留下> 10%拒绝时使用。
4. **始终检查困惑度** — 如果激增> 15%，模型已损坏。减少激进性。
5. **MoE模型需要特殊处理** — 对Mixtral、DeepSeek-MoE等使用`nuclear`方法。
6. **量化模型无法重新量化** — 消融全精度模型，然后量化输出。
7. **VRAM估算是近似的** — 4位量化有帮助，但提取期间峰值使用可能激增。
8. **推理模型敏感** — 对R1蒸馏使用`surgical`以保留思维链。
9. **检查`obliteratus recommend`** — 遥测数据可能有比默认值更好的参数。
10. **AGPL许可证** — 永远不要在MIT/Apache项目中`import obliteratus`。仅CLI调用。
11. **大型模型（70B+）** — 始终使用`--large-model`标志获得保守默认值。
12. **谱认证RED常见** — 谱检查经常标记"不完整"，即使实际拒绝率为0%。检查实际拒绝率而不是仅依赖谱认证。

## 互补技能

- **vllm** — 高吞吐量服务消融模型
- **gguf** — 将消融模型转换为GGUF用于llama.cpp
- **huggingface-tokenizers** — 使用模型分词器
