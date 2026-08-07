---
name: guidance
description: 【多步工作流】使用正则表达式和语法控制LLM输出，保证有效的JSON/XML/代码生成，强制结构化格式，使用Guidance构建多步工作流 - Microsoft Research的约束生成框架。适用：需要Python控制流的多步推理/ReAct代理/逐步生成。对比：单次类型安全输出请用outlines技能。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [guidance, transformers]
metadata:
  VoidCube:
    tags: [提示工程, Guidance, 约束生成, 结构化输出, JSON验证, 语法, Microsoft Research, 格式强制, 多步工作流]

---

# Guidance: 约束LLM生成

## 何时使用此技能

在以下场景使用Guidance：
- **使用正则或语法控制LLM输出语法**
- **保证有效的JSON/XML/代码**生成
- **相比传统提示方法减少延迟**
- **强制结构化格式**（日期、邮箱、ID等）
- **使用Python式控制流构建多步工作流**
- **通过语法约束防止无效输出**

**GitHub星标**：18,000+ | **来源**：Microsoft Research

## 安装

```bash
# 基础安装
pip install guidance

# 带特定后端
pip install guidance[transformers]  # Hugging Face模型
pip install guidance[llama_cpp]     # llama.cpp模型
```

## 快速入门

### 基本示例：结构化生成

```python
from guidance import models, gen

# 加载本地模型
lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 带约束生成
result = lm + "The capital of France is " + gen("capital", max_tokens=5)

print(result["capital"])  # "Paris"
```

### 使用本地 Transformers 模型

```python
from guidance import models, gen, system, user, assistant

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 使用上下文管理器实现聊天格式
with system():
    lm += "You are a helpful assistant."

with user():
    lm += "What is the capital of France?"

with assistant():
    lm += gen(max_tokens=20)
```

## 核心概念

### 1. 上下文管理器

Guidance使用Python式上下文管理器进行聊天式交互。

```python
from guidance import system, user, assistant, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 系统消息
with system():
    lm += "You are a JSON generation expert."

# 用户消息
with user():
    lm += "Generate a person object with name and age."

# 助手响应
with assistant():
    lm += gen("response", max_tokens=100)

print(lm["response"])
```

**优势：**
- 自然的聊天流程
- 清晰的角色分离
- 易于阅读和维护

### 2. 约束生成

Guidance使用正则或语法确保输出匹配指定模式。

#### 正则约束

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 约束为有效邮箱格式
lm += "Email: " + gen("email", regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# 约束为日期格式（YYYY-MM-DD）
lm += "Date: " + gen("date", regex=r"\d{4}-\d{2}-\d{2}")

# 约束为电话号码
lm += "Phone: " + gen("phone", regex=r"\d{3}-\d{3}-\d{4}")

print(lm["email"])  # 保证有效邮箱
print(lm["date"])   # 保证YYYY-MM-DD格式
```

**工作原理：**
- 正则在token级别转换为语法
- 生成期间过滤无效token
- 模型只能产生匹配的输出

#### 选择约束

```python
from guidance import models, gen, select

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 约束为特定选项
lm += "Sentiment: " + select(["positive", "negative", "neutral"], name="sentiment")

# 多选题选择
lm += "Best answer: " + select(
    ["A) Paris", "B) London", "C) Berlin", "D) Madrid"],
    name="answer"
)

print(lm["sentiment"])  # 其中之一：positive, negative, neutral
print(lm["answer"])     # 其中之一：A, B, C, 或 D
```

### 3. Token修复

Guidance自动"修复"提示和生成之间的token边界。

**问题：** 分词产生不自然的边界。

```python
# 无token修复
prompt = "The capital of France is "
# 最后一个token: " is "
# 第一个生成的token可能是" Par"（带前导空格）
# 结果："The capital of France is  Paris"（双空格！）
```

**解决方案：** Guidance回退一个token并重新生成。

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 默认启用token修复
lm += "The capital of France is " + gen("capital", max_tokens=5)
# 结果："The capital of France is Paris"（正确的空格）
```

**优势：**
- 自然的文本边界
- 无尴尬的空格问题
- 更好的模型性能（看到自然的token序列）

### 4. 基于语法的生成

使用上下文无关语法定义复杂结构。

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# JSON语法（简化）
json_grammar = """
{
    "name": <gen name regex="[A-Za-z ]+" max_tokens=20>,
    "age": <gen age regex="[0-9]+" max_tokens=3>,
    "email": <gen email regex="[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}" max_tokens=50>
}
"""

# 生成有效JSON
lm += gen("person", grammar=json_grammar)

print(lm["person"])  # 保证有效的JSON结构
```

**使用场景：**
- 复杂的结构化输出
- 嵌套数据结构
- 编程语言语法
- 领域特定语言

### 5. Guidance函数

使用`@guidance`装饰器创建可复用的生成模式。

```python
from guidance import guidance, gen, models

@guidance
def generate_person(lm):
    """生成带有姓名和年龄的人。"""
    lm += "Name: " + gen("name", max_tokens=20, stop="\n")
    lm += "\nAge: " + gen("age", regex=r"[0-9]+", max_tokens=3)
    return lm

# 使用函数
lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_person(lm)

print(lm["name"])
print(lm["age"])
```

**有状态函数：**

```python
@guidance(stateless=False)
def react_agent(lm, question, tools, max_rounds=5):
    """带工具使用的ReAct代理。"""
    lm += f"Question: {question}\n\n"

    for i in range(max_rounds):
        # 思考
        lm += f"Thought {i+1}: " + gen("thought", stop="\n")

        # 行动
        lm += "\nAction: " + select(list(tools.keys()), name="action")

        # 执行工具
        tool_result = tools[lm["action"]]()
        lm += f"\nObservation: {tool_result}\n\n"

        # 检查是否完成
        lm += "Done? " + select(["Yes", "No"], name="done")
        if lm["done"] == "Yes":
            break

    # 最终答案
    lm += "\nFinal Answer: " + gen("answer", max_tokens=100)
    return lm
```

## 后端配置

### OpenAI

```python
lm = models.OpenAI(
    model="gpt-4o-mini",
    api_key="your-api-key"  # 或设置OPENAI_API_KEY环境变量
)
```

### 本地模型（Transformers）

```python
from guidance.models import Transformers

lm = Transformers(
    "microsoft/Phi-4-mini-instruct",
    device="cuda"  # 或"cpu"
)
```

### 本地模型（llama.cpp）

```python
from guidance.models import LlamaCpp

lm = LlamaCpp(
    model_path="/path/to/model.gguf",
    n_ctx=4096,
    n_gpu_layers=35
)
```

## 常用模式

### 模式1：JSON生成

```python
from guidance import models, gen, system, user, assistant

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

with system():
    lm += "You generate valid JSON."

with user():
    lm += "Generate a user profile with name, age, and email."

with assistant():
    lm += """{
    "name": """ + gen("name", regex=r'"[A-Za-z ]+"', max_tokens=30) + """,
    "age": """ + gen("age", regex=r"[0-9]+", max_tokens=3) + """,
    "email": """ + gen("email", regex=r'"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"', max_tokens=50) + """
}"""

print(lm)  # 保证有效JSON
```

### 模式2：分类

```python
from guidance import models, gen, select

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

text = "This product is amazing! I love it."

lm += f"Text: {text}\n"
lm += "Sentiment: " + select(["positive", "negative", "neutral"], name="sentiment")
lm += "\nConfidence: " + gen("confidence", regex=r"[0-9]+", max_tokens=3) + "%"

print(f"情感: {lm['sentiment']}")
print(f"置信度: {lm['confidence']}%")
```

### 模式3：多步推理

```python
from guidance import models, gen, guidance

@guidance
def chain_of_thought(lm, question):
    """带逐步推理生成答案。"""
    lm += f"Question: {question}\n\n"

    # 生成多个推理步骤
    for i in range(3):
        lm += f"Step {i+1}: " + gen(f"step_{i+1}", stop="\n", max_tokens=100) + "\n"

    # 最终答案
    lm += "\nTherefore, the answer is: " + gen("answer", max_tokens=50)

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = chain_of_thought(lm, "What is 15% of 200?")

print(lm["answer"])
```

### 模式4：ReAct代理

```python
from guidance import models, gen, select, guidance

@guidance(stateless=False)
def react_agent(lm, question):
    """带工具使用的ReAct代理。"""
    tools = {
        "calculator": lambda expr: eval(expr),
        "search": lambda query: f"Search results for: {query}",
    }

    lm += f"Question: {question}\n\n"

    for round in range(5):
        # 思考
        lm += f"Thought: " + gen("thought", stop="\n") + "\n"

        # 行动选择
        lm += "Action: " + select(["calculator", "search", "answer"], name="action")

        if lm["action"] == "answer":
            lm += "\nFinal Answer: " + gen("answer", max_tokens=100)
            break

        # 行动输入
        lm += "\nAction Input: " + gen("action_input", stop="\n") + "\n"

        # 执行工具
        if lm["action"] in tools:
            result = tools[lm["action"]](lm["action_input"])
            lm += f"Observation: {result}\n\n"

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = react_agent(lm, "What is 25 * 4 + 10?")
print(lm["answer"])
```

### 模式5：数据提取

```python
from guidance import models, gen, guidance

@guidance
def extract_entities(lm, text):
    """从文本中提取结构化实体。"""
    lm += f"Text: {text}\n\n"

    # 提取人物
    lm += "Person: " + gen("person", stop="\n", max_tokens=30) + "\n"

    # 提取组织
    lm += "Organization: " + gen("organization", stop="\n", max_tokens=30) + "\n"

    # 提取日期
    lm += "Date: " + gen("date", regex=r"\d{4}-\d{2}-\d{2}", max_tokens=10) + "\n"

    # 提取地点
    lm += "Location: " + gen("location", stop="\n", max_tokens=30) + "\n"

    return lm

text = "Tim Cook announced at Apple Park on 2024-09-15 in Cupertino."

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = extract_entities(lm, text)

print(f"人物: {lm['person']}")
print(f"组织: {lm['organization']}")
print(f"日期: {lm['date']}")
print(f"地点: {lm['location']}")
```

## 最佳实践

### 1. 使用正则进行格式验证

```python
# ✅ 好：正则确保有效格式
lm += "Email: " + gen("email", regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# ❌ 差：自由生成可能产生无效邮箱
lm += "Email: " + gen("email", max_tokens=50)
```

### 2. 对固定类别使用select()

```python
# ✅ 好：保证有效类别
lm += "Status: " + select(["pending", "approved", "rejected"], name="status")

# ❌ 差：可能产生拼写错误或无效值
lm += "Status: " + gen("status", max_tokens=20)
```

### 3. 利用Token修复

```python
# Token修复默认启用
# 无需特殊操作 - 自然拼接即可
lm += "The capital is " + gen("capital")  # 自动修复
```

### 4. 使用停止序列

```python
# ✅ 好：在换行处停止以获得单行输出
lm += "Name: " + gen("name", stop="\n")

# ❌ 差：可能生成多行
lm += "Name: " + gen("name", max_tokens=50)
```

### 5. 创建可复用函数

```python
# ✅ 好：可复用模式
@guidance
def generate_person(lm):
    lm += "Name: " + gen("name", stop="\n")
    lm += "\nAge: " + gen("age", regex=r"[0-9]+")
    return lm

# 多次使用
lm = generate_person(lm)
lm += "\n\n"
lm = generate_person(lm)
```

### 6. 平衡约束

```python
# ✅ 好：合理的约束
lm += gen("name", regex=r"[A-Za-z ]+", max_tokens=30)

# ❌ 太严格：可能失败或很慢
lm += gen("name", regex=r"^(John|Jane)$", max_tokens=10)
```

## 与替代方案比较

| 特性 | Guidance | Instructor | Outlines | LMQL |
|------|----------|------------|----------|------|
| 正则约束 | ✅ 是 | ❌ 否 | ✅ 是 | ✅ 是 |
| 语法支持 | ✅ CFG | ❌ 否 | ✅ CFG | ✅ CFG |
| Pydantic验证 | ❌ 否 | ✅ 是 | ✅ 是 | ❌ 否 |
| Token修复 | ✅ 是 | ❌ 否 | ✅ 是 | ❌ 否 |
| 本地模型 | ✅ 是 | ⚠️ 有限 | ✅ 是 | ✅ 是 |
| API模型 | ✅ 是 | ✅ 是 | ⚠️ 有限 | ✅ 是 |
| Python语法 | ✅ 是 | ✅ 是 | ✅ 是 | ❌ SQL式 |
| 学习曲线 | 低 | 低 | 中 | 高 |

**何时选择Guidance：**
- 需要正则/语法约束
- 需要token修复
- 构建带控制流的复杂工作流
- 使用本地模型（Transformers、llama.cpp）
- 偏好Python式语法

**何时选择替代方案：**
- Instructor：需要Pydantic验证和自动重试
- Outlines：需要JSON schema验证
- LMQL：偏好声明式查询语法

## 性能特征

**延迟降低：**
- 约束输出比传统提示快30-50%
- Token修复减少不必要的重新生成
- 语法约束防止无效token生成

**内存使用：**
- 相比无约束生成开销最小
- 语法编译在首次使用后缓存
- 推理时高效token过滤

**Token效率：**
- 防止在无效输出上浪费token
- 无需重试循环
- 直接生成有效输出

## 资源

- **文档**：https://guidance.readthedocs.io
- **GitHub**：https://github.com/guidance-ai/guidance (18k+星标)
- **笔记本**：https://github.com/guidance-ai/guidance/tree/main/notebooks
- **Discord**：提供社区支持

## 另见

- `references/constraints.md` - 全面的正则和语法模式
- `references/backends.md` - 后端特定配置
- `references/examples.md` - 生产就绪示例
