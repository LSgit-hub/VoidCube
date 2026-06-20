# 基准测试指南

lm-evaluation-harness 中所有 60 多个评估任务的完整指南，包括它们测量的内容以及如何解释结果。

## 概述

lm-evaluation-harness 包含 60 多个基准测试，涵盖：
- 语言理解（MMLU、GLUE）
- 数学推理（GSM8K、MATH）
- 代码生成（HumanEval、MBPP）
- 指令遵循（IFEval、AlpacaEval）
- 长上下文理解（LongBench）
- 多语言能力（AfroBench、NorEval）
- 推理（BBH、ARC）
- 真实性（TruthfulQA）

**列出所有任务**：
```bash
lm_eval --tasks list
```

## 主要基准测试

### MMLU（大规模多任务语言理解）

**测量内容**：跨 57 个学科的广泛知识（STEM、人文、社会科学、法律）。

**任务变体**：
- `mmlu`：原始的 57 学科基准测试
- `mmlu_pro`：更具挑战性的版本，侧重推理问题
- `mmlu_prox`：多语言扩展

**格式**：多项选择（4 个选项）

**示例**：
```
Question: What is the capital of France?
A. Berlin
B. Paris
C. London
D. Madrid
Answer: B
```

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --num_fewshot 5
```

**解释**：
- 随机：25%（概率）
- GPT-3 (175B)：43.9%
- GPT-4：86.4%
- 人类专家：~90%

**适用于**：评估一般知识和领域专业知识。

### GSM8K（小学数学 8K）

**测量内容**：小学水平应用题的数学推理。

**任务变体**：
- `gsm8k`：基础任务
- `gsm8k_cot`：使用思维链提示
- `gsm_plus`：带有扰动的对抗性变体

**格式**：自由生成，提取数字答案

**示例**：
```
Question: A baker made 200 cookies. He sold 3/5 of them in the morning and 1/4 of the remaining in the afternoon. How many cookies does he have left?
Answer: 60
```

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks gsm8k \
  --num_fewshot 5
```

**解释**：
- 随机：~0%
- GPT-3 (175B)：17.0%
- GPT-4：92.0%
- Llama 2 70B：56.8%

**适用于**：测试多步推理和算术。

### HumanEval

**测量内容**：从文档字符串生成 Python 代码（功能正确性）。

**任务变体**：
- `humaneval`：标准基准测试
- `humaneval_instruct`：用于指令微调模型

**格式**：代码生成，基于执行的评估

**示例**：
```python
def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
```

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks humaneval \
  --batch_size 1
```

**解释**：
- 随机：0%
- GPT-3 (175B)：0%
- Codex：28.8%
- GPT-4：67.0%
- Code Llama 34B：53.7%

**适用于**：评估代码生成能力。

### BBH（BIG-Bench Hard）

**测量内容**：23 个具有挑战性的推理任务，模型此前未能超越人类。

**类别**：
- 逻辑推理
- 数学应用题
- 社会理解
- 算法推理

**格式**：多项选择和自由生成

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks bbh \
  --num_fewshot 3
```

**解释**：
- 随机：~25%
- GPT-3 (175B)：33.9%
- PaLM 540B：58.3%
- GPT-4：86.7%

**适用于**：测试高级推理能力。

### IFEval（指令遵循评估）

**测量内容**：遵循特定、可验证指令的能力。

**指令类型**：
- 格式约束（例如，"用 3 句话回答"）
- 长度约束（例如，"至少使用 100 个词"）
- 内容约束（例如，"包含单词 'banana'"）
- 结构约束（例如，"使用项目符号"）

**格式**：自由生成，基于规则的验证

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-chat-hf \
  --tasks ifeval \
  --batch_size auto
```

**解释**：
- 测量：指令遵循度（而非质量）
- GPT-4：86% 指令遵循
- Claude 2：84%

**适用于**：评估聊天/指令模型。

### GLUE（通用语言理解评估）

**测量内容**：跨 9 个任务的自然语言理解。

**任务**：
- `cola`：语法可接受性
- `sst2`：情感分析
- `mrpc`：释义检测
- `qqp`：问题对
- `stsb`：语义相似度
- `mnli`：自然语言推理
- `qnli`：问答 NLI
- `rte`：识别文本蕴含
- `wnli`：Winograd 模式

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=bert-base-uncased \
  --tasks glue \
  --num_fewshot 0
```

**解释**：
- BERT Base：78.3（GLUE 分数）
- RoBERTa Large：88.5
- 人类基线：87.1

**适用于**：仅编码器模型，微调基线。

### LongBench

**测量内容**：长上下文理解（4K-32K 令牌）。

**21 个任务涵盖**：
- 单文档问答
- 多文档问答
- 摘要
- 少样本学习
- 代码补全
- 合成任务

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks longbench \
  --batch_size 1
```

**解释**：
- 测试上下文利用
- 许多模型在超过 4K 令牌时困难
- GPT-4 Turbo：54.3%

**适用于**：评估长上下文模型。

## 其他基准测试

### TruthfulQA

**测量内容**：模型倾向于真实而非生成听起来合理的虚假内容。

**格式**：多项选择，4-5 个选项

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks truthfulqa_mc2 \
  --batch_size auto
```

**解释**：
- 较大的模型通常得分更低（更有说服力的谎言）
- GPT-3：58.8%
- GPT-4：59.0%
- 人类：~94%

### ARC（AI2 推理挑战）

**测量内容**：小学科学问题。

**变体**：
- `arc_easy`：较简单的问题
- `arc_challenge`：需要推理的较难问题

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks arc_challenge \
  --num_fewshot 25
```

**解释**：
- ARC-Easy：大多数模型 >80%
- ARC-Challenge 随机：25%
- GPT-4：96.3%

### HellaSwag

**测量内容**：关于日常情境的常识推理。

**格式**：选择最合理的续写

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks hellaswag \
  --num_fewshot 10
```

**解释**：
- 随机：25%
- GPT-3：78.9%
- Llama 2 70B：85.3%

### WinoGrande

**测量内容**：通过代词解析的常识推理。

**示例**：
```
The trophy doesn't fit in the brown suitcase because _ is too large.
A. the trophy
B. the suitcase
```

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks winogrande \
  --num_fewshot 5
```

### PIQA

**测量内容**：物理常识推理。

**示例**："To clean a keyboard, use compressed air or..."

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks piqa
```

## 多语言基准测试

### AfroBench

**测量内容**：跨 64 种非洲语言的性能。

**15 个任务**：NLU、文本生成、知识、问答、数学推理

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks afrobench
```

### NorEval

**测量内容**：挪威语语言理解（9 个任务类别）。

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=NbAiLab/nb-gpt-j-6B \
  --tasks noreval
```

## 领域特定基准测试

### MATH

**测量内容**：高中竞赛数学问题。

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks math \
  --num_fewshot 4
```

**解释**：
- 非常具有挑战性
- GPT-4：42.5%
- Minerva 540B：33.6%

### MBPP（基本 Python 问题）

**测量内容**：从自然语言描述进行 Python 编程。

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks mbpp \
  --batch_size 1
```

### DROP

**测量内容**：需要离散推理的阅读理解。

**命令**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks drop
```

## 基准测试选择指南

### 通用模型

运行此套件：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag,arc_challenge,truthfulqa_mc2 \
  --num_fewshot 5
```

### 代码模型

```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks humaneval,mbpp \
  --batch_size 1
```

### 聊天/指令模型

```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-chat-hf \
  --tasks ifeval,mmlu,gsm8k_cot \
  --batch_size auto
```

### 长上下文模型

```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-3.1-8B \
  --tasks longbench \
  --batch_size 1
```

## 解释结果

### 理解指标

**准确率**：正确答案的百分比（最常见）

**精确匹配（EM）**：需要精确字符串匹配（严格）

**F1 分数**：平衡精确率和召回率

**BLEU/ROUGE**：文本生成相似度

**Pass@k**：生成 k 个样本时的通过百分比

### 典型分数范围

| 模型大小 | MMLU | GSM8K | HumanEval | HellaSwag |
|----------|------|-------|-----------|-----------|
| 7B | 40-50% | 10-20% | 5-15% | 70-80% |
| 13B | 45-55% | 20-35% | 15-25% | 75-82% |
| 70B | 60-70% | 50-65% | 35-50% | 82-87% |
| GPT-4 | 86% | 92% | 67% | 95% |

### 警示信号

- **所有任务都是随机概率**：模型未正确训练
- **生成任务精确为 0%**：可能是格式/解析问题
- **运行间差异巨大**：检查种子/采样设置
- **在所有任务上优于 GPT-4**：可能是数据污染

## 最佳实践

1. **始终报告少样本设置**：0 样本、5 样本等
2. **运行多个种子**：报告均值 ± 标准差
3. **检查数据污染**：在训练数据中搜索基准测试示例
4. **与已发表基线比较**：验证你的设置
5. **报告所有超参数**：模型、批大小、最大令牌数、温度

## 参考资料

- 任务列表：`lm_eval --tasks list`
- 任务 README：`lm_eval/tasks/README.md`
- 论文：参见各个基准测试论文
