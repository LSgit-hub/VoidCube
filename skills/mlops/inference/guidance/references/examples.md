# 生产就绪示例

使用 Guidance 进行结构化生成、代理和工作流的实际示例。

## 目录
- JSON 生成
- 数据提取
- 分类系统
- 代理系统
- 多步工作流
- 代码生成
- 生产提示

## JSON 生成

### 基本 JSON

```python
from guidance import models, gen, guidance

@guidance
def generate_user(lm):
    """生成有效的用户 JSON。"""
    lm += "{\n"
    lm += '  "name": ' + gen("name", regex=r'"[A-Za-z ]+"') + ",\n"
    lm += '  "age": ' + gen("age", regex=r"[0-9]+") + ",\n"
    lm += '  "email": ' + gen(
        "email",
        regex=r'"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"'
    ) + "\n"
    lm += "}"
    return lm

# 使用它
lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm += "Generate a user profile:\n"
lm = generate_user(lm)

print(lm)
# 输出：保证有效的 JSON
```

### 嵌套 JSON

```python
@guidance
def generate_order(lm):
    """生成嵌套订单 JSON。"""
    lm += "{\n"

    # 客户信息
    lm += '  "customer": {\n'
    lm += '    "name": ' + gen("customer_name", regex=r'"[A-Za-z ]+"') + ",\n"
    lm += '    "email": ' + gen(
        "customer_email",
        regex=r'"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"'
    ) + "\n"
    lm += "  },\n"

    # 订单详情
    lm += '  "order": {\n'
    lm += '    "id": ' + gen("order_id", regex=r'"ORD-[0-9]{6}"') + ",\n"
    lm += '    "date": ' + gen("order_date", regex=r'"\d{4}-\d{2}-\d{2}"') + ",\n"
    lm += '    "total": ' + gen("order_total", regex=r"[0-9]+\.[0-9]{2}") + "\n"
    lm += "  },\n"

    # 状态
    lm += '  "status": ' + gen(
        "status",
        regex=r'"(pending|processing|shipped|delivered)"'
    ) + "\n"

    lm += "}"
    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_order(lm)
```

### JSON 数组

```python
@guidance
def generate_user_list(lm, count=3):
    """生成用户 JSON 数组。"""
    lm += "[\n"

    for i in range(count):
        lm += "  {\n"
        lm += '    "id": ' + gen(f"id_{i}", regex=r"[0-9]+") + ",\n"
        lm += '    "name": ' + gen(f"name_{i}", regex=r'"[A-Za-z ]+"') + ",\n"
        lm += '    "active": ' + gen(f"active_{i}", regex=r"(true|false)") + "\n"
        lm += "  }"
        if i < count - 1:
            lm += ","
        lm += "\n"

    lm += "]"
    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_user_list(lm, count=5)
```

### 动态 JSON Schema

```python
import json
from guidance import models, gen, guidance

@guidance
def json_from_schema(lm, schema):
    """生成匹配 schema 的 JSON。"""
    lm += "{\n"

    fields = list(schema["properties"].items())
    for i, (field_name, field_schema) in enumerate(fields):
        lm += f'  "{field_name}": '

        # 处理不同类型
        if field_schema["type"] == "string":
            if "pattern" in field_schema:
                lm += gen(field_name, regex=f'"{field_schema["pattern"]}"')
            else:
                lm += gen(field_name, regex=r'"[^"]+"')
        elif field_schema["type"] == "number":
            lm += gen(field_name, regex=r"[0-9]+(\.[0-9]+)?")
        elif field_schema["type"] == "integer":
            lm += gen(field_name, regex=r"[0-9]+")
        elif field_schema["type"] == "boolean":
            lm += gen(field_name, regex=r"(true|false)")

        if i < len(fields) - 1:
            lm += ","
        lm += "\n"

    lm += "}"
    return lm

# 定义 schema
schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "score": {"type": "number"},
        "active": {"type": "boolean"}
    }
}

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = json_from_schema(lm, schema)
```

## 数据提取

### 从文本提取

```python
from guidance import models, gen, guidance, system, user, assistant

@guidance
def extract_person_info(lm, text):
    """从文本提取结构化信息。"""
    lm += f"Text: {text}\n\n"

    with assistant():
        lm += "Name: " + gen("name", regex=r"[A-Za-z ]+", stop="\n") + "\n"
        lm += "Age: " + gen("age", regex=r"[0-9]+", max_tokens=3) + "\n"
        lm += "Occupation: " + gen("occupation", regex=r"[A-Za-z ]+", stop="\n") + "\n"
        lm += "Email: " + gen(
            "email",
            regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            stop="\n"
        ) + "\n"

    return lm

text = "John Smith is a 35-year-old software engineer. Contact: john@example.com"

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

with system():
    lm += "You extract structured information from text."

with user():
    lm = extract_person_info(lm, text)

print(f"Name: {lm['name']}")
print(f"Age: {lm['age']}")
print(f"Occupation: {lm['occupation']}")
print(f"Email: {lm['email']}")
```

### 多实体提取

```python
@guidance
def extract_entities(lm, text):
    """提取多种实体类型。"""
    lm += f"Analyze: {text}\n\n"

    # 人物实体
    lm += "People:\n"
    for i in range(3):  # 最多 3 个人
        lm += f"- " + gen(f"person_{i}", regex=r"[A-Za-z ]+", stop="\n") + "\n"

    # 组织实体
    lm += "\nOrganizations:\n"
    for i in range(2):  # 最多 2 个组织
        lm += f"- " + gen(f"org_{i}", regex=r"[A-Za-z0-9 ]+", stop="\n") + "\n"

    # 日期
    lm += "\nDates:\n"
    for i in range(2):  # 最多 2 个日期
        lm += f"- " + gen(f"date_{i}", regex=r"\d{4}-\d{2}-\d{2}", stop="\n") + "\n"

    # 地点
    lm += "\nLocations:\n"
    for i in range(2):  # 最多 2 个地点
        lm += f"- " + gen(f"location_{i}", regex=r"[A-Za-z ]+", stop="\n") + "\n"

    return lm

text = """
Tim Cook and Satya Nadella met at Microsoft headquarters in Redmond on 2024-09-15
to discuss the collaboration between Apple and Microsoft. The meeting continued
in Cupertino on 2024-09-20.
"""

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = extract_entities(lm, text)
```

### 批量提取

```python
@guidance
def batch_extract(lm, texts):
    """从多个文本提取。"""
    lm += "Batch Extraction Results:\n\n"

    for i, text in enumerate(texts):
        lm += f"=== Item {i+1} ===\n"
        lm += f"Text: {text}\n"
        lm += "Name: " + gen(f"name_{i}", regex=r"[A-Za-z ]+", stop="\n") + "\n"
        lm += "Sentiment: " + gen(
            f"sentiment_{i}",
            regex=r"(positive|negative|neutral)",
            stop="\n"
        ) + "\n\n"

    return lm

texts = [
    "Alice is happy with the product",
    "Bob is disappointed with the service",
    "Carol has no strong feelings either way"
]

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = batch_extract(lm, texts)
```

## 分类系统

### 情感分析

```python
from guidance import models, select, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

text = "This product is absolutely amazing! Best purchase ever."

lm += f"Text: {text}\n\n"
lm += "Sentiment: " + select(
    ["positive", "negative", "neutral"],
    name="sentiment"
)
lm += "\nConfidence: " + gen("confidence", regex=r"[0-9]{1,3}") + "%\n"
lm += "Reasoning: " + gen("reasoning", stop="\n", max_tokens=50)

print(f"Sentiment: {lm['sentiment']}")
print(f"Confidence: {lm['confidence']}%")
print(f"Reasoning: {lm['reasoning']}")
```

### 多标签分类

```python
@guidance
def classify_article(lm, text):
    """使用多个标签分类文章。"""
    lm += f"Article: {text}\n\n"

    # 主要类别
    lm += "Primary Category: " + select(
        ["Technology", "Business", "Science", "Politics", "Entertainment"],
        name="primary_category"
    ) + "\n"

    # 次要类别（最多 3 个）
    lm += "\nSecondary Categories:\n"
    categories = ["Technology", "Business", "Science", "Politics", "Entertainment"]
    for i in range(3):
        lm += f"{i+1}. " + select(categories, name=f"secondary_{i}") + "\n"

    # 标签
    lm += "\nTags: " + gen("tags", stop="\n", max_tokens=50) + "\n"

    # 目标受众
    lm += "Target Audience: " + select(
        ["General", "Expert", "Beginner"],
        name="audience"
    )

    return lm

article = """
Apple announced new AI features in iOS 18, leveraging machine learning to improve
battery life and performance. The company's stock rose 5% following the announcement.
"""

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = classify_article(lm, article)
```

### 意图分类

```python
@guidance
def classify_intent(lm, message):
    """分类用户意图。"""
    lm += f"User Message: {message}\n\n"

    # 意图
    lm += "Intent: " + select(
        ["question", "complaint", "request", "feedback", "other"],
        name="intent"
    ) + "\n"

    # 紧急程度
    lm += "Urgency: " + select(
        ["low", "medium", "high", "critical"],
        name="urgency"
    ) + "\n"

    # 部门
    lm += "Route To: " + select(
        ["support", "sales", "billing", "technical"],
        name="department"
    ) + "\n"

    # 情感
    lm += "Sentiment: " + select(
        ["positive", "neutral", "negative"],
        name="sentiment"
    )

    return lm

message = "My account was charged twice for the same order. Need help ASAP!"

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = classify_intent(lm, message)

print(f"Intent: {lm['intent']}")
print(f"Urgency: {lm['urgency']}")
print(f"Department: {lm['department']}")
```

## 代理系统

### ReAct 代理

```python
from guidance import models, gen, select, guidance

@guidance(stateless=False)
def react_agent(lm, question, tools, max_rounds=5):
    """使用工具的 ReAct 代理。"""
    lm += f"Question: {question}\n\n"

    for round in range(max_rounds):
        # 思考
        lm += f"Thought {round+1}: " + gen("thought", stop="\n", max_tokens=100) + "\n"

        # 动作选择
        lm += "Action: " + select(
            list(tools.keys()) + ["answer"],
            name="action"
        )

        if lm["action"] == "answer":
            lm += "\n\nFinal Answer: " + gen("answer", max_tokens=200)
            break

        # 动作输入
        lm += "\nAction Input: " + gen("action_input", stop="\n", max_tokens=100) + "\n"

        # 执行工具
        if lm["action"] in tools:
            try:
                result = tools[lm["action"]](lm["action_input"])
                lm += f"Observation: {result}\n\n"
            except Exception as e:
                lm += f"Observation: Error - {str(e)}\n\n"

    return lm

# 定义工具
tools = {
    "calculator": lambda expr: eval(expr),
    "search": lambda query: f"Search results for '{query}': [Mock results]",
    "weather": lambda city: f"Weather in {city}: Sunny, 72°F"
}

# 使用代理
lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = react_agent(lm, "What is (25 * 4) + 10?", tools)

print(lm["answer"])
```

### 多代理系统

```python
@guidance
def coordinator_agent(lm, task):
    """委派给专家的协调器。"""
    lm += f"Task: {task}\n\n"

    # 确定使用哪个专家
    lm += "Specialist: " + select(
        ["researcher", "writer", "coder", "analyst"],
        name="specialist"
    ) + "\n"

    lm += "Reasoning: " + gen("reasoning", stop="\n", max_tokens=100) + "\n"

    return lm

@guidance
def researcher_agent(lm, query):
    """研究专家。"""
    lm += f"Research Query: {query}\n\n"
    lm += "Findings:\n"
    for i in range(3):
        lm += f"{i+1}. " + gen(f"finding_{i}", stop="\n", max_tokens=100) + "\n"
    return lm

@guidance
def writer_agent(lm, topic):
    """写作专家。"""
    lm += f"Topic: {topic}\n\n"
    lm += "Title: " + gen("title", stop="\n", max_tokens=50) + "\n"
    lm += "Content:\n" + gen("content", max_tokens=500)
    return lm

# 协调工作流
task = "Write an article about AI safety"

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = coordinator_agent(lm, task)

specialist = lm["specialist"]
if specialist == "researcher":
    lm = researcher_agent(lm, task)
elif specialist == "writer":
    lm = writer_agent(lm, task)
```

### 带验证的工具使用

```python
@guidance(stateless=False)
def validated_tool_agent(lm, question):
    """带验证工具调用的代理。"""
    tools = {
        "add": lambda a, b: float(a) + float(b),
        "multiply": lambda a, b: float(a) * float(b),
        "divide": lambda a, b: float(a) / float(b) if float(b) != 0 else "Error: Division by zero"
    }

    lm += f"Question: {question}\n\n"

    for i in range(5):
        # 选择工具
        lm += "Tool: " + select(list(tools.keys()) + ["done"], name="tool")

        if lm["tool"] == "done":
            lm += "\nAnswer: " + gen("answer", max_tokens=100)
            break

        # 获取验证的数值参数
        lm += "\nArg1: " + gen("arg1", regex=r"-?[0-9]+(\.[0-9]+)?") + "\n"
        lm += "Arg2: " + gen("arg2", regex=r"-?[0-9]+(\.[0-9]+)?") + "\n"

        # 执行
        result = tools[lm["tool"]](lm["arg1"], lm["arg2"])
        lm += f"Result: {result}\n\n"

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = validated_tool_agent(lm, "What is (10 + 5) * 3?")
```

## 多步工作流

### 思维链

```python
@guidance
def chain_of_thought(lm, question):
    """使用 CoT 的多步推理。"""
    lm += f"Question: {question}\n\n"

    # 生成推理步骤
    lm += "Let me think step by step:\n\n"
    for i in range(4):
        lm += f"Step {i+1}: " + gen(f"step_{i+1}", stop="\n", max_tokens=100) + "\n"

    # 最终答案
    lm += "\nTherefore, the answer is: " + gen("answer", stop="\n", max_tokens=50)

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = chain_of_thought(lm, "If a train travels 60 mph for 2.5 hours, how far does it go?")

print(lm["answer"])
```

### 自一致性

```python
@guidance
def self_consistency(lm, question, num_samples=3):
    """生成多个推理路径并聚合。"""
    lm += f"Question: {question}\n\n"

    answers = []
    for i in range(num_samples):
        lm += f"=== Attempt {i+1} ===\n"
        lm += "Reasoning: " + gen(f"reasoning_{i}", stop="\n", max_tokens=100) + "\n"
        lm += "Answer: " + gen(f"answer_{i}", stop="\n", max_tokens=50) + "\n\n"
        answers.append(lm[f"answer_{i}"])

    # 聚合（简单多数投票）
    from collections import Counter
    most_common = Counter(answers).most_common(1)[0][0]

    lm += f"Final Answer (by majority): {most_common}\n"
    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = self_consistency(lm, "What is 15% of 200?")
```

### 规划与执行

```python
@guidance
def plan_and_execute(lm, goal):
    """规划任务然后执行它们。"""
    lm += f"Goal: {goal}\n\n"

    # 规划阶段
    lm += "Plan:\n"
    num_steps = 4
    for i in range(num_steps):
        lm += f"{i+1}. " + gen(f"plan_step_{i}", stop="\n", max_tokens=100) + "\n"

    # 执行阶段
    lm += "\nExecution:\n\n"
    for i in range(num_steps):
        lm += f"Step {i+1}: {lm[f'plan_step_{i}']}\n"
        lm += "Status: " + select(["completed", "in-progress", "blocked"], name=f"status_{i}") + "\n"
        lm += "Result: " + gen(f"result_{i}", stop="\n", max_tokens=150) + "\n\n"

    # 总结
    lm += "Summary: " + gen("summary", max_tokens=200)

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = plan_and_execute(lm, "Build a REST API for a blog platform")
```

## 代码生成

### Python 函数

```python
@guidance
def generate_python_function(lm, description):
    """从描述生成 Python 函数。"""
    lm += f"Description: {description}\n\n"

    # 函数签名
    lm += "def " + gen("func_name", regex=r"[a-z_][a-z0-9_]*") + "("
    lm += gen("params", regex=r"[a-z_][a-z0-9_]*(, [a-z_][a-z0-9_]*)*") + "):\n"

    # 文档字符串
    lm += '    """' + gen("docstring", stop='"""', max_tokens=100) + '"""\n'

    # 函数体
    lm += "    " + gen("body", stop="\n", max_tokens=200) + "\n"

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_python_function(lm, "Check if a number is prime")

print(lm)
```

### SQL 查询

```python
@guidance
def generate_sql(lm, description):
    """从描述生成 SQL 查询。"""
    lm += f"Description: {description}\n\n"
    lm += "SQL Query:\n"

    # SELECT 子句
    lm += "SELECT " + gen("select_clause", stop=" FROM", max_tokens=100)

    # FROM 子句
    lm += " FROM " + gen("from_clause", stop=" WHERE", max_tokens=50)

    # WHERE 子句（可选）
    lm += " WHERE " + gen("where_clause", stop=";", max_tokens=100) + ";"

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_sql(lm, "Get all users who signed up in the last 30 days")
```

### API 端点

```python
@guidance
def generate_api_endpoint(lm, description):
    """生成 REST API 端点。"""
    lm += f"Description: {description}\n\n"

    # HTTP 方法
    lm += "Method: " + select(["GET", "POST", "PUT", "DELETE"], name="method") + "\n"

    # 路径
    lm += "Path: /" + gen("path", regex=r"[a-z0-9/-]+", stop="\n") + "\n"

    # 请求体（如果是 POST/PUT）
    if lm["method"] in ["POST", "PUT"]:
        lm += "\nRequest Body:\n"
        lm += "{\n"
        lm += '  "field1": ' + gen("field1", regex=r'"[a-z_]+"') + ",\n"
        lm += '  "field2": ' + gen("field2", regex=r'"[a-z_]+"') + "\n"
        lm += "}\n"

    # 响应
    lm += "\nResponse (200 OK):\n"
    lm += "{\n"
    lm += '  "status": "success",\n'
    lm += '  "data": ' + gen("response_data", max_tokens=100) + "\n"
    lm += "}\n"

    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = generate_api_endpoint(lm, "Create a new blog post")
```

## 生产提示

### 错误处理

```python
@guidance
def safe_extraction(lm, text):
    """带回退处理的提取。"""
    try:
        lm += f"Text: {text}\n"
        lm += "Name: " + gen("name", regex=r"[A-Za-z ]+", stop="\n", max_tokens=30)
        return lm
    except Exception as e:
        # 回退到较宽松的提取
        lm += f"Text: {text}\n"
        lm += "Name: " + gen("name", stop="\n", max_tokens=30)
        return lm
```

### 缓存

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def cached_generation(text):
    """缓存 LLM 生成。"""
    lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
    lm += f"Analyze: {text}\n"
    lm += "Sentiment: " + select(["positive", "negative", "neutral"], name="sentiment")
    return lm["sentiment"]

# 首次调用：访问 LLM
result1 = cached_generation("This is great!")

# 第二次调用：返回缓存结果
result2 = cached_generation("This is great!")  # 即时！
```

### 监控

```python
import time

@guidance
def monitored_generation(lm, text):
    """跟踪生成指标。"""
    start_time = time.time()

    lm += f"Text: {text}\n"
    lm += "Analysis: " + gen("analysis", max_tokens=100)

    elapsed = time.time() - start_time

    # 记录指标
    print(f"Generation time: {elapsed:.2f}s")
    print(f"Output length: {len(lm['analysis'])} chars")

    return lm
```

### 批量处理

```python
def batch_process(texts, batch_size=10):
    """批量处理文本。"""
    lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
    results = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]

        for text in batch:
            lm += f"Text: {text}\n"
            lm += "Sentiment: " + select(
                ["positive", "negative", "neutral"],
                name=f"sentiment_{i}"
            ) + "\n\n"

        results.extend([lm[f"sentiment_{i}"] for i in range(len(batch))])

    return results
```

## 资源

- **Guidance Notebooks**：https://github.com/guidance-ai/guidance/tree/main/notebooks
- **Guidance 文档**：https://guidance.readthedocs.io
- **社区示例**：https://github.com/guidance-ai/guidance/discussions
