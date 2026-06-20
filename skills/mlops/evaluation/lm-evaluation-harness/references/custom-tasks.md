# 自定义任务

在 lm-evaluation-harness 中创建领域特定评估任务的完整指南。

## 概述

自定义任务允许你在自己的数据集和指标上评估模型。任务使用 YAML 配置文件定义，可选使用 Python 工具处理复杂逻辑。

**为什么要创建自定义任务**：
- 在专有/领域特定数据上评估
- 测试现有基准未涵盖的特定能力
- 为内部模型创建评估流水线
- 复现研究实验

## 快速开始

### 最小自定义任务

创建 `my_tasks/simple_qa.yaml`：

```yaml
task: simple_qa
dataset_path: data/simple_qa.jsonl
output_type: generate_until
doc_to_text: "Question: {{question}}\nAnswer:"
doc_to_target: "{{answer}}"
metric_list:
  - metric: exact_match
    aggregation: mean
    higher_is_better: true
```

**运行**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks simple_qa \
  --include_path my_tasks/
```

## 任务配置参考

### 基本字段

```yaml
# 任务标识
task: my_custom_task           # 唯一任务名称（必需）
task_alias: "My Task"          # 显示名称
tag:                           # 分组标签
  - custom
  - domain_specific

# 数据集配置
dataset_path: data/my_data.jsonl  # HuggingFace 数据集或本地路径
dataset_name: default             # 子集名称（如适用）
training_split: train
validation_split: validation
test_split: test

# 评估配置
output_type: generate_until    # 或 loglikelihood, multiple_choice
num_fewshot: 5                 # 少样本示例数量
batch_size: auto               # 批大小

# 提示模板（Jinja2）
doc_to_text: "Question: {{question}}"
doc_to_target: "{{answer}}"

# 指标
metric_list:
  - metric: exact_match
    aggregation: mean
    higher_is_better: true

# 元数据
metadata:
  version: 1.0
```

### 输出类型

**`generate_until`**：自由生成
```yaml
output_type: generate_until
generation_kwargs:
  max_gen_toks: 256
  until:
    - "\n"
    - "."
  temperature: 0.0
```

**`loglikelihood`**：计算目标的对数概率
```yaml
output_type: loglikelihood
# 用于困惑度、分类
```

**`multiple_choice`**：从选项中选择
```yaml
output_type: multiple_choice
doc_to_choice: "{{choices}}"  # 选项列表
```

## 数据格式

### 本地 JSONL 文件

`data/my_data.jsonl`：
```json
{"question": "What is 2+2?", "answer": "4"}
{"question": "Capital of France?", "answer": "Paris"}
```

**任务配置**：
```yaml
dataset_path: data/my_data.jsonl
dataset_kwargs:
  data_files:
    test: data/my_data.jsonl
```

### HuggingFace 数据集

```yaml
dataset_path: squad
dataset_name: plain_text
test_split: validation
```

### CSV 文件

`data/my_data.csv`：
```csv
question,answer,category
What is 2+2?,4,math
Capital of France?,Paris,geography
```

**任务配置**：
```yaml
dataset_path: data/my_data.csv
dataset_kwargs:
  data_files:
    test: data/my_data.csv
```

## 提示工程

### 简单模板

```yaml
doc_to_text: "Question: {{question}}\nAnswer:"
doc_to_target: "{{answer}}"
```

### 条件逻辑

```yaml
doc_to_text: |
  {% if context %}
  Context: {{context}}
  {% endif %}
  Question: {{question}}
  Answer:
```

### 多项选择

```yaml
doc_to_text: |
  Question: {{question}}
  A. {{choices[0]}}
  B. {{choices[1]}}
  C. {{choices[2]}}
  D. {{choices[3]}}
  Answer:

doc_to_target: "{{ 'ABCD'[answer_idx] }}"
doc_to_choice: ["A", "B", "C", "D"]
```

### 少样本格式化

```yaml
fewshot_delimiter: "\n\n"        # 示例之间
target_delimiter: " "            # 问题和答案之间
doc_to_text: "Q: {{question}}"
doc_to_target: "A: {{answer}}"
```

## 自定义 Python 函数

对于复杂逻辑，在 `utils.py` 中使用 Python 函数。

### 创建 `my_tasks/utils.py`

```python
def process_docs(dataset):
    """预处理文档。"""
    def _process(doc):
        # 自定义预处理
        doc["question"] = doc["question"].strip().lower()
        return doc

    return dataset.map(_process)

def doc_to_text(doc):
    """自定义提示格式化。"""
    context = doc.get("context", "")
    question = doc["question"]

    if context:
        return f"Context: {context}\nQuestion: {question}\nAnswer:"
    return f"Question: {question}\nAnswer:"

def doc_to_target(doc):
    """自定义目标提取。"""
    return doc["answer"].strip().lower()

def aggregate_scores(items):
    """自定义指标聚合。"""
    correct = sum(1 for item in items if item == 1.0)
    total = len(items)
    return correct / total if total > 0 else 0.0
```

### 在任务配置中使用

```yaml
task: my_custom_task
dataset_path: data/my_data.jsonl

# 使用 Python 函数
process_docs: !function utils.process_docs
doc_to_text: !function utils.doc_to_text
doc_to_target: !function utils.doc_to_target

metric_list:
  - metric: exact_match
    aggregation: !function utils.aggregate_scores
    higher_is_better: true
```

## 实际示例

### 示例 1：领域问答任务

**目标**：评估医学问答。

`medical_qa/medical_qa.yaml`：
```yaml
task: medical_qa
dataset_path: data/medical_qa.jsonl
output_type: generate_until
num_fewshot: 3

doc_to_text: |
  Medical Question: {{question}}
  Context: {{context}}
  Answer (be concise):

doc_to_target: "{{answer}}"

generation_kwargs:
  max_gen_toks: 100
  until:
    - "\n\n"
  temperature: 0.0

metric_list:
  - metric: exact_match
    aggregation: mean
    higher_is_better: true
  - metric: !function utils.medical_f1
    aggregation: mean
    higher_is_better: true

filter_list:
  - name: lowercase
    filter:
      - function: lowercase
      - function: remove_whitespace

metadata:
  version: 1.0
  domain: medical
```

`medical_qa/utils.py`：
```python
from sklearn.metrics import f1_score
import re

def medical_f1(predictions, references):
    """医学术语的自定义 F1。"""
    pred_terms = set(extract_medical_terms(predictions[0]))
    ref_terms = set(extract_medical_terms(references[0]))

    if not pred_terms and not ref_terms:
        return 1.0
    if not pred_terms or not ref_terms:
        return 0.0

    tp = len(pred_terms & ref_terms)
    fp = len(pred_terms - ref_terms)
    fn = len(ref_terms - pred_terms)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0

    return 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

def extract_medical_terms(text):
    """提取医学术语。"""
    # 自定义逻辑
    return re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)*\b', text)
```

### 示例 2：代码评估

`code_eval/python_challenges.yaml`：
```yaml
task: python_challenges
dataset_path: data/python_problems.jsonl
output_type: generate_until
num_fewshot: 0

doc_to_text: |
  Write a Python function to solve:
  {{problem_statement}}

  Function signature:
  {{function_signature}}

doc_to_target: "{{canonical_solution}}"

generation_kwargs:
  max_gen_toks: 512
  until:
    - "\n\nclass"
    - "\n\ndef"
  temperature: 0.2

metric_list:
  - metric: !function utils.execute_code
    aggregation: mean
    higher_is_better: true

process_results: !function utils.process_code_results

metadata:
  version: 1.0
```

`code_eval/utils.py`：
```python
import subprocess
import json

def execute_code(predictions, references):
    """针对测试用例执行生成的代码。"""
    generated_code = predictions[0]
    test_cases = json.loads(references[0])

    try:
        # 使用测试用例执行代码
        for test_input, expected_output in test_cases:
            result = execute_with_timeout(generated_code, test_input, timeout=5)
            if result != expected_output:
                return 0.0
        return 1.0
    except Exception:
        return 0.0

def execute_with_timeout(code, input_data, timeout=5):
    """安全地执行代码并设置超时。"""
    # 使用 subprocess 和 timeout 实现
    pass

def process_code_results(doc, results):
    """处理代码执行结果。"""
    return {
        "passed": results[0] == 1.0,
        "generated_code": results[1]
    }
```

### 示例 3：指令遵循

`instruction_eval/instruction_eval.yaml`：
```yaml
task: instruction_following
dataset_path: data/instructions.jsonl
output_type: generate_until
num_fewshot: 0

doc_to_text: |
  Instruction: {{instruction}}
  {% if constraints %}
  Constraints: {{constraints}}
  {% endif %}
  Response:

doc_to_target: "{{expected_response}}"

generation_kwargs:
  max_gen_toks: 256
  temperature: 0.7

metric_list:
  - metric: !function utils.check_constraints
    aggregation: mean
    higher_is_better: true
  - metric: !function utils.semantic_similarity
    aggregation: mean
    higher_is_better: true

process_docs: !function utils.add_constraint_checkers
```

`instruction_eval/utils.py`：
```python
from sentence_transformers import SentenceTransformer, util

model = SentenceTransformer('all-MiniLM-L6-v2')

def check_constraints(predictions, references):
    """检查响应是否满足约束。"""
    response = predictions[0]
    constraints = json.loads(references[0])

    satisfied = 0
    total = len(constraints)

    for constraint in constraints:
        if verify_constraint(response, constraint):
            satisfied += 1

    return satisfied / total if total > 0 else 1.0

def verify_constraint(response, constraint):
    """验证单个约束。"""
    if constraint["type"] == "length":
        return len(response.split()) >= constraint["min_words"]
    elif constraint["type"] == "contains":
        return constraint["keyword"] in response.lower()
    # 添加更多约束类型
    return True

def semantic_similarity(predictions, references):
    """计算语义相似度。"""
    pred_embedding = model.encode(predictions[0])
    ref_embedding = model.encode(references[0])
    return float(util.cos_sim(pred_embedding, ref_embedding))

def add_constraint_checkers(dataset):
    """将约束解析为可验证格式。"""
    def _parse(doc):
        # 将约束字符串解析为结构化格式
        doc["parsed_constraints"] = parse_constraints(doc.get("constraints", ""))
        return doc
    return dataset.map(_parse)
```

## 高级功能

### 输出过滤

```yaml
filter_list:
  - name: extract_answer
    filter:
      - function: regex
        regex_pattern: "Answer: (.*)"
        group: 1
      - function: lowercase
      - function: strip_whitespace
```

### 多指标

```yaml
metric_list:
  - metric: exact_match
    aggregation: mean
    higher_is_better: true
  - metric: f1
    aggregation: mean
    higher_is_better: true
  - metric: bleu
    aggregation: mean
    higher_is_better: true
```

### 任务组

创建 `my_tasks/_default.yaml`：
```yaml
group: my_eval_suite
task:
  - simple_qa
  - medical_qa
  - python_challenges
```

**运行整个套件**：
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks my_eval_suite \
  --include_path my_tasks/
```

## 测试你的任务

### 验证配置

```bash
# 测试任务加载
lm_eval --tasks my_custom_task --include_path my_tasks/ --limit 0

# 在 5 个样本上运行
lm_eval --model hf \
  --model_args pretrained=gpt2 \
  --tasks my_custom_task \
  --include_path my_tasks/ \
  --limit 5
```

### 调试模式

```bash
lm_eval --model hf \
  --model_args pretrained=gpt2 \
  --tasks my_custom_task \
  --include_path my_tasks/ \
  --limit 1 \
  --log_samples  # 保存输入/输出样本
```

## 最佳实践

1. **从简单开始**：先用最小配置测试
2. **版本化任务**：使用 `metadata.version`
3. **记录指标**：在注释中解释自定义指标
4. **用多个模型测试**：确保鲁棒性
5. **在已知示例上验证**：包含健全性检查
6. **谨慎使用过滤器**：可能隐藏错误
7. **处理边界情况**：空字符串、缺失字段

## 常见模式

### 分类任务

```yaml
output_type: loglikelihood
doc_to_text: "Text: {{text}}\nLabel:"
doc_to_target: " {{label}}"  # 空格前缀很重要！
metric_list:
  - metric: acc
    aggregation: mean
```

### 困惑度评估

```yaml
output_type: loglikelihood_rolling
doc_to_text: "{{text}}"
metric_list:
  - metric: perplexity
    aggregation: perplexity
```

### 排序任务

```yaml
output_type: loglikelihood
doc_to_text: "Query: {{query}}\nPassage: {{passage}}\nRelevant:"
doc_to_target: [" Yes", " No"]
metric_list:
  - metric: acc
    aggregation: mean
```

## 故障排除

**"任务未找到"**：检查 `--include_path` 和任务名称

**结果为空**：验证 `doc_to_text` 和 `doc_to_target` 模板

**指标错误**：确保指标名称正确（exact_match，不是 exact-match）

**过滤器问题**：使用 `--log_samples` 测试过滤器

**Python 函数未找到**：检查 `!function module.function_name` 语法

## 参考资料

- 任务系统：EleutherAI/lm-evaluation-harness 文档
- 示例任务：`lm_eval/tasks/` 目录
- TaskConfig：`lm_eval/api/task.py`
