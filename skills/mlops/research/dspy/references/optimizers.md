# DSPy 优化器（Teleprompter）

DSPy 优化算法完整指南，用于改进提示和模型权重。

## 什么是优化器？

DSPy 优化器（称为 "teleprompter"）自动改进你的模块：
- **从训练数据合成少样本示例**
- **通过搜索提出更好的指令**
- **微调模型权重**（可选）

**核心思想**：不用手动调整提示，定义度量并让 DSPy 优化。

## 优化器选择指南

| 优化器 | 最佳用途 | 速度 | 质量 | 所需数据 |
|-----------|----------|-------|---------|-------------|
| BootstrapFewShot | 通用 | 快 | 好 | 10-50 示例 |
| MIPRO | 指令调优 | 中 | 优秀 | 50-200 示例 |
| BootstrapFinetune | 微调 | 慢 | 优秀 | 100+ 示例 |
| COPRO | 提示优化 | 中 | 好 | 20-100 示例 |
| KNNFewShot | 快速基线 | 很快 | 一般 | 10+ 示例 |

## 核心优化器

### BootstrapFewShot

**最流行的优化器** - 从训练数据生成少样本演示。

**工作原理：**
1. 获取你的训练示例
2. 使用你的模块生成预测
3. 选择高质量预测（基于度量）
4. 将这些作为未来提示中的少样本示例

**参数：**
- `metric`：评分预测的函数（必需）
- `max_bootstrapped_demos`：最大生成演示数（默认：4）
- `max_labeled_demos`：最大使用标注示例数（默认：16）
- `max_rounds`：优化迭代次数（默认：1）
- `metric_threshold`：接受的最小分数（可选）

```python
import dspy
from dspy.teleprompt import BootstrapFewShot

# 定义度量
def validate_answer(example, pred, trace=None):
    """如果预测匹配金标准答案则返回 True。"""
    return example.answer.lower() == pred.answer.lower()

# 训练数据
trainset = [
    dspy.Example(question="What is 2+2?", answer="4").with_inputs("question"),
    dspy.Example(question="What is 3+5?", answer="8").with_inputs("question"),
    dspy.Example(question="What is 10-3?", answer="7").with_inputs("question"),
]

# 创建模块
qa = dspy.ChainOfThought("question -> answer")

# 优化
optimizer = BootstrapFewShot(
    metric=validate_answer,
    max_bootstrapped_demos=3,
    max_rounds=2
)

optimized_qa = optimizer.compile(qa, trainset=trainset)

# 现在 optimized_qa 已学习少样本示例！
result = optimized_qa(question="What is 5+7?")
```

**最佳实践：**
- 从 10-50 个训练示例开始
- 使用覆盖边缘情况的多样化示例
- 大多数任务设置 `max_bootstrapped_demos=3-5`
- 提高质量可增加 `max_rounds=2-3`

**何时使用：**
- 首先尝试的优化器
- 你有 10+ 标注示例
- 想要快速改进
- 通用任务

### MIPRO（最重要的提示优化）

**最先进的优化器** - 迭代搜索更好的指令。

**工作原理：**
1. 生成候选指令
2. 在验证集上测试每个
3. 选择表现最好的指令
4. 迭代进一步优化

**参数：**
- `metric`：评估度量（必需）
- `num_candidates`：每次迭代尝试的指令数（默认：10）
- `init_temperature`：采样温度（默认：1.0）
- `verbose`：显示进度（默认：False）

```python
from dspy.teleprompt import MIPRO

# 定义更细致的度量
def answer_quality(example, pred, trace=None):
    """评分答案质量 0-1。"""
    if example.answer.lower() in pred.answer.lower():
        return 1.0
    # 相似答案的部分分
    return 0.5 if len(set(example.answer.split()) & set(pred.answer.split())) > 0 else 0.0

# 更大的训练集（MIPRO 受益于更多数据）
trainset = [...]  # 50-200 示例
valset = [...]    # 20-50 示例

# 创建模块
qa = dspy.ChainOfThought("question -> answer")

# 用 MIPRO 优化
optimizer = MIPRO(
    metric=answer_quality,
    num_candidates=10,
    init_temperature=1.0,
    verbose=True
)

optimized_qa = optimizer.compile(
    student=qa,
    trainset=trainset,
    valset=valset,  # MIPRO 使用单独的验证集
    num_trials=100   # 更多试验 = 更好质量
)
```

**最佳实践：**
- 使用 50-200 个训练示例
- 分离验证集（20-50 示例）
- 运行 100-200 次试验获得最佳结果
- 通常需要 10-30 分钟

**何时使用：**
- 你有 50+ 标注示例
- 想要最先进的性能
- 愿意等待优化
- 复杂推理任务

### BootstrapFinetune

**微调模型权重** - 创建微调的训练数据集。

**工作原理：**
1. 生成合成训练数据
2. 以微调格式导出数据
3. 你单独微调模型
4. 加载回微调后的模型

**参数：**
- `metric`：评估度量（必需）
- `max_bootstrapped_demos`：生成的演示数（默认：4）
- `max_rounds`：数据生成轮数（默认：1）

```python
from dspy.teleprompt import BootstrapFinetune

# 训练数据
trainset = [...]  # 推荐 100+ 示例

# 定义度量
def validate(example, pred, trace=None):
    return example.answer == pred.answer

# 创建模块
qa = dspy.ChainOfThought("question -> answer")

# 生成微调数据
optimizer = BootstrapFinetune(metric=validate)
optimized_qa = optimizer.compile(qa, trainset=trainset)

# 导出训练数据到文件
# 然后使用你的 LM 提供者 API 进行微调

# 微调后，加载你的模型：
finetuned_lm = dspy.OpenAI(model="ft:gpt-3.5-turbo:your-model-id")
dspy.settings.configure(lm=finetuned_lm)
```

**最佳实践：**
- 使用 100+ 训练示例
- 在留出测试集上验证
- 监控过拟合
- 先与基于提示的方法比较

**何时使用：**
- 你有 100+ 示例
- 延迟关键（微调模型更快）
- 任务狭窄且明确
- 提示优化不够

### COPRO（坐标提示优化）

**通过无梯度搜索优化提示。**

**工作原理：**
1. 生成提示变体
2. 评估每个变体
3. 选择最佳提示
4. 迭代优化

```python
from dspy.teleprompt import COPRO

# 训练数据
trainset = [...]

# 定义度量
def metric(example, pred, trace=None):
    return example.answer == pred.answer

# 创建模块
qa = dspy.ChainOfThought("question -> answer")

# 用 COPRO 优化
optimizer = COPRO(
    metric=metric,
    breadth=10,  # 每次迭代的候选数
    depth=3      # 优化轮数
)

optimized_qa = optimizer.compile(qa, trainset=trainset)
```

**何时使用：**
- 想要提示优化
- 有 20-100 示例
- MIPRO 太慢

### KNNFewShot

**简单 k 近邻** - 为每个查询选择相似示例。

**工作原理：**
1. 嵌入所有训练示例
2. 对每个查询，找到 k 个最相似示例
3. 将这些作为少样本演示

```python
from dspy.teleprompt import KNNFewShot

trainset = [...]

# 不需要度量 - 只选择相似示例
optimizer = KNNFewShot(k=3)
optimized_qa = optimizer.compile(qa, trainset=trainset)

# 对每个查询，使用 trainset 中 3 个最相似示例
```

**何时使用：**
- 快速基线
- 有多样化训练示例
- 相似性是有用性的好代理

## 编写度量

度量是评分预测的函数。它们对优化至关重要。

### 二制度量

```python
def exact_match(example, pred, trace=None):
    """如果预测完全匹配金标准则返回 True。"""
    return example.answer == pred.answer

def contains_answer(example, pred, trace=None):
    """如果预测包含金标准答案则返回 True。"""
    return example.answer.lower() in pred.answer.lower()
```

### 连续度量

```python
def f1_score(example, pred, trace=None):
    """预测和金标准之间的 F1 分数。"""
    pred_tokens = set(pred.answer.lower().split())
    gold_tokens = set(example.answer.lower().split())

    if not pred_tokens:
        return 0.0

    precision = len(pred_tokens & gold_tokens) / len(pred_tokens)
    recall = len(pred_tokens & gold_tokens) / len(gold_tokens)

    if precision + recall == 0:
        return 0.0

    return 2 * (precision * recall) / (precision + recall)

def semantic_similarity(example, pred, trace=None):
    """预测和金标准之间的嵌入相似度。"""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')

    emb1 = model.encode(example.answer)
    emb2 = model.encode(pred.answer)

    similarity = cosine_similarity(emb1, emb2)
    return similarity
```

### 多因素度量

```python
def comprehensive_metric(example, pred, trace=None):
    """组合多个因素。"""
    score = 0.0

    # 正确性（50%）
    if example.answer.lower() in pred.answer.lower():
        score += 0.5

    # 简洁性（25%）
    if len(pred.answer.split()) <= 20:
        score += 0.25

    # 引用（25%）
    if "source:" in pred.answer.lower():
        score += 0.25

    return score
```

### 使用 Trace 调试

```python
def metric_with_trace(example, pred, trace=None):
    """使用 trace 进行调试的度量。"""
    is_correct = example.answer == pred.answer

    if trace is not None and not is_correct:
        # 记录失败以分析
        print(f"Failed on: {example.question}")
        print(f"Expected: {example.answer}")
        print(f"Got: {pred.answer}")

    return is_correct
```

## 评估最佳实践

### 训练/验证/测试划分

```python
# 划分数据
trainset = data[:100]   # 70%
valset = data[100:120]  # 15%
testset = data[120:]    # 15%

# 在训练集上优化
optimized = optimizer.compile(module, trainset=trainset)

# 优化期间验证（用于 MIPRO）
optimized = optimizer.compile(module, trainset=trainset, valset=valset)

# 在测试集上评估
from dspy.evaluate import Evaluate
evaluator = Evaluate(devset=testset, metric=metric)
score = evaluator(optimized)
```

### 交叉验证

```python
from sklearn.model_selection import KFold

kfold = KFold(n_splits=5)
scores = []

for train_idx, val_idx in kfold.split(data):
    trainset = [data[i] for i in train_idx]
    valset = [data[i] for i in val_idx]

    optimized = optimizer.compile(module, trainset=trainset)
    score = evaluator(optimized, devset=valset)
    scores.append(score)

print(f"Average score: {sum(scores) / len(scores):.2f}")
```

### 比较优化器

```python
results = {}

for opt_name, optimizer in [
    ("baseline", None),
    ("fewshot", BootstrapFewShot(metric=metric)),
    ("mipro", MIPRO(metric=metric)),
]:
    if optimizer is None:
        module_opt = module
    else:
        module_opt = optimizer.compile(module, trainset=trainset)

    score = evaluator(module_opt, devset=testset)
    results[opt_name] = score

print(results)
# {'baseline': 0.65, 'fewshot': 0.78, 'mipro': 0.85}
```

## 高级模式

### 自定义优化器

```python
from dspy.teleprompt import Teleprompter

class CustomOptimizer(Teleprompter):
    def __init__(self, metric):
        self.metric = metric

    def compile(self, student, trainset, **kwargs):
        # 你的优化逻辑
        # 返回优化后的 student 模块
        return student
```

### 多阶段优化

```python
# 阶段 1：引导少样本
stage1 = BootstrapFewShot(metric=metric, max_bootstrapped_demos=3)
optimized1 = stage1.compile(module, trainset=trainset)

# 阶段 2：指令调优
stage2 = MIPRO(metric=metric, num_candidates=10)
optimized2 = stage2.compile(optimized1, trainset=trainset, valset=valset)

# 最终优化模块
final_module = optimized2
```

### 集成优化

```python
class EnsembleModule(dspy.Module):
    def __init__(self, modules):
        super().__init__()
        self.modules = modules

    def forward(self, question):
        predictions = [m(question=question).answer for m in self.modules]
        # 投票或平均
        return dspy.Prediction(answer=max(set(predictions), key=predictions.count))

# 优化多个模块
opt1 = BootstrapFewShot(metric=metric).compile(module, trainset=trainset)
opt2 = MIPRO(metric=metric).compile(module, trainset=trainset)
opt3 = COPRO(metric=metric).compile(module, trainset=trainset)

# 集成
ensemble = EnsembleModule([opt1, opt2, opt3])
```

## 优化工作流

### 1. 从基线开始

```python
# 无优化
baseline = dspy.ChainOfThought("question -> answer")
baseline_score = evaluator(baseline, devset=testset)
print(f"Baseline: {baseline_score}")
```

### 2. 尝试 BootstrapFewShot

```python
# 快速优化
fewshot = BootstrapFewShot(metric=metric, max_bootstrapped_demos=3)
optimized = fewshot.compile(baseline, trainset=trainset)
fewshot_score = evaluator(optimized, devset=testset)
print(f"Few-shot: {fewshot_score} (+{fewshot_score - baseline_score:.2f})")
```

### 3. 如果有更多数据，尝试 MIPRO

```python
# 最先进优化
mipro = MIPRO(metric=metric, num_candidates=10)
optimized_mipro = mipro.compile(baseline, trainset=trainset, valset=valset)
mipro_score = evaluator(optimized_mipro, devset=testset)
print(f"MIPRO: {mipro_score} (+{mipro_score - baseline_score:.2f})")
```

### 4. 保存最佳模型

```python
if mipro_score > fewshot_score:
    optimized_mipro.save("models/best_model.json")
else:
    optimized.save("models/best_model.json")
```

## 常见陷阱

### 1. 对训练数据过拟合

```python
# ❌ 错误：太多演示
optimizer = BootstrapFewShot(max_bootstrapped_demos=20)  # 过拟合！

# ✅ 正确：适度演示
optimizer = BootstrapFewShot(max_bootstrapped_demos=3-5)
```

### 2. 度量与任务不匹配

```python
# ❌ 错误：细致任务用二制度量
def bad_metric(example, pred, trace=None):
    return example.answer == pred.answer  # 太严格！

# ✅ 正确：分级度量
def good_metric(example, pred, trace=None):
    return f1_score(example.answer, pred.answer)  # 允许部分分
```

### 3. 训练数据不足

```python
# ❌ 错误：数据太少
trainset = data[:5]  # 不够！

# ✅ 正确：足够数据
trainset = data[:50]  # 更好
```

### 4. 无验证集

```python
# ❌ 错误：在测试集上优化
optimizer.compile(module, trainset=testset)  # 作弊！

# ✅ 正确：正确划分
optimizer.compile(module, trainset=trainset, valset=valset)
evaluator(optimized, devset=testset)
```

## 性能技巧

1. **从简单开始**：先用 BootstrapFewShot
2. **使用代表性数据**：覆盖边缘情况
3. **监控过拟合**：在留出集上验证
4. **迭代度量**：根据失败优化
5. **保存检查点**：不要丢失进度
6. **与基线比较**：测量改进
7. **测试多个优化器**：找到最佳匹配

## 资源

- **论文**："DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines"
- **GitHub**：https://github.com/stanfordnlp/dspy
- **Discord**：https://discord.gg/XCGy2WDCQB
