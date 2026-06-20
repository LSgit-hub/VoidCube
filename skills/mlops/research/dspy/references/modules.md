# DSPy 模块

DSPy 内置模块完整指南，用于语言模型编程。

## 模块基础

DSPy 模块是受 PyTorch NN 模块启发的可组合构建块：
- 具有可学习参数（提示、少样本示例）
- 可使用 Python 控制流组合
- 泛化处理任意签名
- 可用 DSPy 优化器优化

### 基础模块模式

```python
import dspy

class CustomModule(dspy.Module):
    def __init__(self):
        super().__init__()
        # 初始化子模块
        self.predictor = dspy.Predict("input -> output")

    def forward(self, input):
        # 模块逻辑
        result = self.predictor(input=input)
        return result
```

## 核心模块

### dspy.Predict

**基础预测模块** - 进行 LM 调用，无推理步骤。

```python
# 内联签名
qa = dspy.Predict("question -> answer")
result = qa(question="What is 2+2?")

# 类签名
class QA(dspy.Signature):
    """简洁回答问题。"""
    question = dspy.InputField()
    answer = dspy.OutputField(desc="short, factual answer")

qa = dspy.Predict(QA)
result = qa(question="What is the capital of France?")
print(result.answer)  # "Paris"
```

**何时使用：**
- 简单、直接的预测
- 不需要推理步骤
- 需要快速响应

### dspy.ChainOfThought

**逐步推理** - 在答案前生成理由。

**参数：**
- `signature`：任务签名
- `rationale_field`：自定义推理字段（可选）
- `rationale_field_type`：推理类型（默认：`str`）

```python
# 基础用法
cot = dspy.ChainOfThought("question -> answer")
result = cot(question="If I have 5 apples and give away 2, how many remain?")
print(result.rationale)  # "Let's think step by step..."
print(result.answer)     # "3"

# 自定义推理字段
cot = dspy.ChainOfThought(
    signature="problem -> solution",
    rationale_field=dspy.OutputField(
        prefix="Reasoning: Let's break this down step by step to"
    )
)
```

**何时使用：**
- 复杂推理任务
- 数学应用题
- 逻辑推导
- 质量 > 速度

**性能：**
- 比 Predict 慢约 2 倍
- 推理任务准确率显著提高

### dspy.ProgramOfThought

**基于代码的推理** - 生成并执行 Python 代码。

```python
pot = dspy.ProgramOfThought("question -> answer")

result = pot(question="What is 15% of 240?")
# 内部生成：answer = 240 * 0.15
# 执行代码并返回结果
print(result.answer)  # 36.0

result = pot(question="If a train travels 60 mph for 2.5 hours, how far does it go?")
# 生成：distance = 60 * 2.5
print(result.answer)  # 150.0
```

**何时使用：**
- 算术计算
- 符号数学
- 数据转换
- 确定性计算

**优势：**
- 比文本数学更可靠
- 处理复杂计算
- 透明（显示生成的代码）

### dspy.ReAct

**推理 + 行动** - 迭代使用工具的 Agent。

```python
from dspy.predict import ReAct

# 定义工具
def search_wikipedia(query: str) -> str:
    """搜索维基百科获取信息。"""
    # 你的搜索实现
    return search_results

def calculate(expression: str) -> float:
    """计算数学表达式。"""
    return eval(expression)

# 创建 ReAct agent
class ResearchQA(dspy.Signature):
    """使用可用工具回答问题。"""
    question = dspy.InputField()
    answer = dspy.OutputField()

react = ReAct(ResearchQA, tools=[search_wikipedia, calculate])

# Agent 决定使用哪些工具
result = react(question="How old was Einstein when he published special relativity?")
# 内部：
# 1. 思考："需要出生年份和发表年份"
# 2. 行动：search_wikipedia("Albert Einstein")
# 3. 行动：search_wikipedia("Special relativity 1905")
# 4. 行动：calculate("1905 - 1879")
# 5. 返回："26 years old"
```

**何时使用：**
- 多步研究任务
- 使用工具的 Agent
- 复杂信息检索
- 需要多次 API 调用的任务

**最佳实践：**
- 保持工具描述清晰具体
- 限制 5-7 个工具（太多会混乱）
- 在文档字符串中提供工具使用示例

### dspy.MultiChainComparison

**生成多个输出并比较** - 自一致性模式。

```python
mcc = dspy.MultiChainComparison("question -> answer", M=5)

result = mcc(question="What is the capital of France?")
# 生成 5 个候选答案
# 比较并选择最一致的
print(result.answer)  # "Paris"
print(result.candidates)  # 所有 5 个生成的答案
```

**参数：**
- `M`：生成的候选数量（默认：5）
- `temperature`：采样温度以增加多样性

**何时使用：**
- 高风险决策
- 模糊问题
- 单一答案可能不可靠时

**权衡：**
- 慢 M 倍（M 次并行调用）
- 模糊任务准确率更高

### dspy.majority

**多次预测的多数投票。**

```python
from dspy.primitives import majority

# 生成多次预测
predictor = dspy.Predict("question -> answer")
predictions = [predictor(question="What is 2+2?") for _ in range(5)]

# 多数投票
answer = majority([p.answer for p in predictions])
print(answer)  # "4"
```

**何时使用：**
- 组合多个模型输出
- 减少预测方差
- 集成方法

## 高级模块

### dspy.TypedPredictor

**使用 Pydantic 模型的结构化输出。**

```python
from pydantic import BaseModel, Field

class PersonInfo(BaseModel):
    name: str = Field(description="Full name")
    age: int = Field(description="Age in years")
    occupation: str = Field(description="Current job")

class ExtractPerson(dspy.Signature):
    """从文本中提取人物信息。"""
    text = dspy.InputField()
    person: PersonInfo = dspy.OutputField()

extractor = dspy.TypedPredictor(ExtractPerson)
result = extractor(text="John Doe is a 35-year-old software engineer.")

print(result.person.name)       # "John Doe"
print(result.person.age)        # 35
print(result.person.occupation) # "software engineer"
```

**优势：**
- 类型安全
- 自动验证
- JSON schema 生成
- IDE 自动补全

### dspy.Retry

**带验证的自动重试。**

```python
from dspy.primitives import Retry

def validate_number(example, pred, trace=None):
    """验证输出是数字。"""
    try:
        float(pred.answer)
        return True
    except ValueError:
        return False

# 验证失败时最多重试 3 次
qa = Retry(
    dspy.ChainOfThought("question -> answer"),
    validate=validate_number,
    max_retries=3
)

result = qa(question="What is 15% of 80?")
# 如果首次尝试返回非数字，自动重试
```

### dspy.Assert

**断言驱动优化。**

```python
import dspy
from dspy.primitives.assertions import assert_transform_module, backtrack_handler

class ValidatedQA(dspy.Module):
    def __init__(self):
        super().__init__()
        self.qa = dspy.ChainOfThought("question -> answer: float")

    def forward(self, question):
        answer = self.qa(question=question).answer

        # 断言答案是数字
        dspy.Assert(
            isinstance(float(answer), float),
            "Answer must be a number",
            backtrack=backtrack_handler
        )

        return dspy.Prediction(answer=answer)
```

**优势：**
- 优化期间捕获错误
- 引导 LM 生成有效输出
- 比事后过滤更好

## 模块组合

### 顺序流水线

```python
class Pipeline(dspy.Module):
    def __init__(self):
        super().__init__()
        self.stage1 = dspy.Predict("input -> intermediate")
        self.stage2 = dspy.ChainOfThought("intermediate -> output")

    def forward(self, input):
        intermediate = self.stage1(input=input).intermediate
        output = self.stage2(intermediate=intermediate).output
        return dspy.Prediction(output=output)
```

### 条件逻辑

```python
class ConditionalModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.router = dspy.Predict("question -> category: str")
        self.simple_qa = dspy.Predict("question -> answer")
        self.complex_qa = dspy.ChainOfThought("question -> answer")

    def forward(self, question):
        category = self.router(question=question).category

        if category == "simple":
            return self.simple_qa(question=question)
        else:
            return self.complex_qa(question=question)
```

### 并行执行

```python
class ParallelModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.approach1 = dspy.ChainOfThought("question -> answer")
        self.approach2 = dspy.ProgramOfThought("question -> answer")

    def forward(self, question):
        # 运行两种方法
        answer1 = self.approach1(question=question).answer
        answer2 = self.approach2(question=question).answer

        # 比较或组合结果
        if answer1 == answer2:
            return dspy.Prediction(answer=answer1, confidence="high")
        else:
            return dspy.Prediction(answer=answer1, confidence="low")
```

## 批量处理

所有模块都支持批量处理以提高效率：

```python
cot = dspy.ChainOfThought("question -> answer")

questions = [
    "What is 2+2?",
    "What is 3+3?",
    "What is 4+4?"
]

# 一次处理所有
results = cot.batch([{"question": q} for q in questions])

for result in results:
    print(result.answer)
```

## 保存和加载

```python
# 保存模块
qa = dspy.ChainOfThought("question -> answer")
qa.save("models/qa_v1.json")

# 加载模块
loaded_qa = dspy.ChainOfThought("question -> answer")
loaded_qa.load("models/qa_v1.json")
```

**保存内容：**
- 少样本示例
- 提示指令
- 模块配置

**不保存内容：**
- 模型权重（DSPy 默认不微调）
- LM 提供者配置

## 模块选择指南

| 任务 | 模块 | 原因 |
|------|--------|--------|
| 简单分类 | Predict | 快速、直接 |
| 数学应用题 | ProgramOfThought | 可靠计算 |
| 逻辑推理 | ChainOfThought | 步骤更好 |
| 多步研究 | ReAct | 工具使用 |
| 高风险决策 | MultiChainComparison | 自一致性 |
| 结构化提取 | TypedPredictor | 类型安全 |
| 模糊问题 | MultiChainComparison | 多视角 |

## 性能技巧

1. **从 Predict 开始**，仅在需要时添加推理
2. **使用批量处理**处理多个输入
3. **缓存预测**用于重复查询
4. **使用 `track_usage=True` 分析 token 使用**
5. **原型后优化**使用 teleprompter

## 常见模式

### 模式：检索 + 生成

```python
class RAG(dspy.Module):
    def __init__(self, k=3):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=k)
        self.generate = dspy.ChainOfThought("context, question -> answer")

    def forward(self, question):
        context = self.retrieve(question).passages
        return self.generate(context=context, question=question)
```

### 模式：验证循环

```python
class VerifiedQA(dspy.Module):
    def __init__(self):
        super().__init__()
        self.answer = dspy.ChainOfThought("question -> answer")
        self.verify = dspy.Predict("question, answer -> is_correct: bool")

    def forward(self, question, max_attempts=3):
        for _ in range(max_attempts):
            answer = self.answer(question=question).answer
            is_correct = self.verify(question=question, answer=answer).is_correct

            if is_correct:
                return dspy.Prediction(answer=answer)

        return dspy.Prediction(answer="Unable to verify answer")
```

### 模式：多轮对话

```python
class DialogAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.respond = dspy.Predict("history, user_message -> assistant_message")
        self.history = []

    def forward(self, user_message):
        history_str = "\n".join(self.history)
        response = self.respond(history=history_str, user_message=user_message)

        self.history.append(f"User: {user_message}")
        self.history.append(f"Assistant: {response.assistant_message}")

        return response
```
