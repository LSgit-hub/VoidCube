# DSPy 实际应用示例

使用 DSPy 构建生产系统的实用示例。

## 目录
- RAG 系统
- Agent 系统
- 分类
- 数据处理
- 多阶段流水线

## RAG 系统

### 基础 RAG

```python
import dspy

class BasicRAG(dspy.Module):
    def __init__(self, num_passages=3):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=num_passages)
        self.generate = dspy.ChainOfThought("context, question -> answer")

    def forward(self, question):
        passages = self.retrieve(question).passages
        context = "\n\n".join(passages)
        return self.generate(context=context, question=question)

# 配置检索器（以 Chroma 为例）
from dspy.retrieve.chromadb_rm import ChromadbRM

retriever = ChromadbRM(
    collection_name="my_docs",
    persist_directory="./chroma_db",
    k=3
)
dspy.settings.configure(rm=retriever)

# 使用 RAG
rag = BasicRAG()
result = rag(question="What is DSPy?")
print(result.answer)
```

### 优化 RAG

```python
from dspy.teleprompt import BootstrapFewShot

# 带问答对的训练数据
trainset = [
    dspy.Example(
        question="What is retrieval augmented generation?",
        answer="RAG combines retrieval of relevant documents with generation..."
    ).with_inputs("question"),
    # ... 更多示例
]

# 定义度量
def answer_correctness(example, pred, trace=None):
    # 检查答案是否包含关键信息
    return example.answer.lower() in pred.answer.lower()

# 优化 RAG
optimizer = BootstrapFewShot(metric=answer_correctness)
optimized_rag = optimizer.compile(rag, trainset=trainset)

# 优化的 RAG 在类似问题上表现更好
result = optimized_rag(question="Explain RAG systems")
```

### 多跳 RAG

```python
class MultiHopRAG(dspy.Module):
    """跨文档推理链的 RAG。"""

    def __init__(self):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=3)
        self.generate_query = dspy.ChainOfThought("question -> search_query")
        self.generate_answer = dspy.ChainOfThought("context, question -> answer")

    def forward(self, question):
        # 第一次检索
        query1 = self.generate_query(question=question).search_query
        passages1 = self.retrieve(query1).passages

        # 基于第一次结果生成后续查询
        context1 = "\n".join(passages1)
        query2 = self.generate_query(
            question=f"Based on: {context1}\nFollow-up: {question}"
        ).search_query

        # 第二次检索
        passages2 = self.retrieve(query2).passages

        # 合并所有上下文
        all_context = "\n\n".join(passages1 + passages2)

        # 生成最终答案
        return self.generate_answer(context=all_context, question=question)

# 使用多跳 RAG
multi_rag = MultiHopRAG()
result = multi_rag(question="Who wrote the book that inspired Blade Runner?")
# 跳 1：找到 "Blade Runner 基于..."
# 跳 2：找到该书的作者
```

### 带重排序的 RAG

```python
class RerankedRAG(dspy.Module):
    """带学习重排序的 RAG。"""

    def __init__(self):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=10)  # 获取更多候选
        self.rerank = dspy.Predict("question, passage -> relevance_score: float")
        self.answer = dspy.ChainOfThought("context, question -> answer")

    def forward(self, question):
        # 检索候选
        passages = self.retrieve(question).passages

        # 重排序段落
        scored_passages = []
        for passage in passages:
            score = float(self.rerank(
                question=question,
                passage=passage
            ).relevance_score)
            scored_passages.append((score, passage))

        # 重排序后取前 3 个
        top_passages = [p for _, p in sorted(scored_passages, reverse=True)[:3]]
        context = "\n\n".join(top_passages)

        # 从重排序上下文生成答案
        return self.answer(context=context, question=question)
```

## Agent 系统

### ReAct Agent

```python
from dspy.predict import ReAct

# 定义工具
def search_wikipedia(query: str) -> str:
    """搜索维基百科获取信息。"""
    import wikipedia
    try:
        return wikipedia.summary(query, sentences=3)
    except:
        return "No results found"

def calculate(expression: str) -> str:
    """安全计算数学表达式。"""
    try:
        # 使用安全 eval
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except:
        return "Invalid expression"

def search_web(query: str) -> str:
    """搜索网络。"""
    # 你的网络搜索实现
    return results

# 创建 agent 签名
class ResearchAgent(dspy.Signature):
    """使用可用工具回答问题。"""
    question = dspy.InputField()
    answer = dspy.OutputField()

# 创建 ReAct agent
agent = ReAct(ResearchAgent, tools=[search_wikipedia, calculate, search_web])

# Agent 决定使用哪些工具
result = agent(question="What is the population of France divided by 10?")
# Agent：
# 1. 思考："需要法国人口"
# 2. 行动：search_wikipedia("France population")
# 3. 思考："得到 6700 万，需要除法"
# 4. 行动：calculate("67000000 / 10")
# 5. 返回："6,700,000"
```

### 多 Agent 系统

```python
class MultiAgentSystem(dspy.Module):
    """针对不同任务的专门 agent 系统。"""

    def __init__(self):
        super().__init__()

        # 路由 agent
        self.router = dspy.Predict("question -> agent_type: str")

        # 专门 agent
        self.research_agent = ReAct(
            ResearchAgent,
            tools=[search_wikipedia, search_web]
        )
        self.math_agent = dspy.ProgramOfThought("problem -> answer")
        self.reasoning_agent = dspy.ChainOfThought("question -> answer")

    def forward(self, question):
        # 路由到合适的 agent
        agent_type = self.router(question=question).agent_type

        if agent_type == "research":
            return self.research_agent(question=question)
        elif agent_type == "math":
            return self.math_agent(problem=question)
        else:
            return self.reasoning_agent(question=question)

# 使用多 agent 系统
mas = MultiAgentSystem()
result = mas(question="What is 15% of the GDP of France?")
# 路由到 research_agent 获取 GDP，然后到 math_agent 进行计算
```

## 分类

### 二分类器

```python
class SentimentClassifier(dspy.Module):
    def __init__(self):
        super().__init__()
        self.classify = dspy.Predict("text -> sentiment: str")

    def forward(self, text):
        return self.classify(text=text)

# 训练数据
trainset = [
    dspy.Example(text="I love this!", sentiment="positive").with_inputs("text"),
    dspy.Example(text="Terrible experience", sentiment="negative").with_inputs("text"),
    # ... 更多示例
]

# 优化
def accuracy(example, pred, trace=None):
    return example.sentiment == pred.sentiment

optimizer = BootstrapFewShot(metric=accuracy, max_bootstrapped_demos=5)
classifier = SentimentClassifier()
optimized_classifier = optimizer.compile(classifier, trainset=trainset)

# 使用分类器
result = optimized_classifier(text="This product is amazing!")
print(result.sentiment)  # "positive"
```

### 多类分类器

```python
class TopicClassifier(dspy.Module):
    def __init__(self):
        super().__init__()
        self.classify = dspy.ChainOfThought(
            "text -> category: str, confidence: float"
        )

    def forward(self, text):
        result = self.classify(text=text)
        return dspy.Prediction(
            category=result.category,
            confidence=float(result.confidence)
        )

# 在签名中定义类别
class TopicSignature(dspy.Signature):
    """将文本分类为：technology, sports, politics, entertainment 之一。"""
    text = dspy.InputField()
    category = dspy.OutputField(desc="one of: technology, sports, politics, entertainment")
    confidence = dspy.OutputField(desc="0.0 to 1.0")

classifier = dspy.ChainOfThought(TopicSignature)
result = classifier(text="The Lakers won the championship")
print(result.category)  # "sports"
print(result.confidence)  # 0.95
```

### 层级分类器

```python
class HierarchicalClassifier(dspy.Module):
    """两阶段分类：粗粒度然后细粒度。"""

    def __init__(self):
        super().__init__()
        self.coarse = dspy.Predict("text -> broad_category: str")
        self.fine_tech = dspy.Predict("text -> tech_subcategory: str")
        self.fine_sports = dspy.Predict("text -> sports_subcategory: str")

    def forward(self, text):
        # 阶段 1：粗类别
        broad = self.coarse(text=text).broad_category

        # 阶段 2：基于粗类别的细分类
        if broad == "technology":
            fine = self.fine_tech(text=text).tech_subcategory
        elif broad == "sports":
            fine = self.fine_sports(text=text).sports_subcategory
        else:
            fine = "other"

        return dspy.Prediction(broad_category=broad, fine_category=fine)
```

## 数据处理

### 文本摘要

```python
class AdaptiveSummarizer(dspy.Module):
    """将文本摘要到目标长度。"""

    def __init__(self):
        super().__init__()
        self.summarize = dspy.ChainOfThought("text, target_length -> summary")

    def forward(self, text, target_length="3 sentences"):
        return self.summarize(text=text, target_length=target_length)

# 使用摘要器
summarizer = AdaptiveSummarizer()
long_text = "..." # 长文章

short_summary = summarizer(long_text, target_length="1 sentence")
medium_summary = summarizer(long_text, target_length="3 sentences")
detailed_summary = summarizer(long_text, target_length="1 paragraph")
```

### 信息提取

```python
from pydantic import BaseModel, Field

class PersonInfo(BaseModel):
    name: str = Field(description="Full name")
    age: int = Field(description="Age in years")
    occupation: str = Field(description="Job title")
    location: str = Field(description="City and country")

class ExtractPerson(dspy.Signature):
    """从文本中提取人物信息。"""
    text = dspy.InputField()
    person: PersonInfo = dspy.OutputField()

extractor = dspy.TypedPredictor(ExtractPerson)

text = "Dr. Jane Smith, 42, is a neuroscientist at Stanford University in Palo Alto, California."
result = extractor(text=text)

print(result.person.name)       # "Dr. Jane Smith"
print(result.person.age)        # 42
print(result.person.occupation) # "neuroscientist"
print(result.person.location)   # "Palo Alto, California"
```

### 批量处理

```python
class BatchProcessor(dspy.Module):
    """高效处理大数据集。"""

    def __init__(self):
        super().__init__()
        self.process = dspy.Predict("text -> processed_text")

    def forward(self, texts):
        # 批量处理提高效率
        return self.process.batch([{"text": t} for t in texts])

# 处理 1000 个文档
processor = BatchProcessor()
results = processor(texts=large_dataset)

# 结果按顺序返回
for original, result in zip(large_dataset, results):
    print(f"{original} -> {result.processed_text}")
```

## 多阶段流水线

### 文档处理流水线

```python
class DocumentPipeline(dspy.Module):
    """多阶段文档处理。"""

    def __init__(self):
        super().__init__()
        self.extract = dspy.Predict("document -> key_points")
        self.classify = dspy.Predict("key_points -> category")
        self.summarize = dspy.ChainOfThought("key_points, category -> summary")
        self.tag = dspy.Predict("summary -> tags")

    def forward(self, document):
        # 阶段 1：提取要点
        key_points = self.extract(document=document).key_points

        # 阶段 2：分类
        category = self.classify(key_points=key_points).category

        # 阶段 3：摘要
        summary = self.summarize(
            key_points=key_points,
            category=category
        ).summary

        # 阶段 4：生成标签
        tags = self.tag(summary=summary).tags

        return dspy.Prediction(
            key_points=key_points,
            category=category,
            summary=summary,
            tags=tags
        )
```

### 质量控制流水线

```python
class QualityControlPipeline(dspy.Module):
    """生成输出并验证质量。"""

    def __init__(self):
        super().__init__()
        self.generate = dspy.ChainOfThought("prompt -> output")
        self.verify = dspy.Predict("output -> is_valid: bool, issues: str")
        self.improve = dspy.ChainOfThought("output, issues -> improved_output")

    def forward(self, prompt, max_iterations=3):
        output = self.generate(prompt=prompt).output

        for _ in range(max_iterations):
            # 验证输出
            verification = self.verify(output=output)

            if verification.is_valid:
                return dspy.Prediction(output=output, iterations=_ + 1)

            # 基于问题改进
            output = self.improve(
                output=output,
                issues=verification.issues
            ).improved_output

        return dspy.Prediction(output=output, iterations=max_iterations)
```

## 生产技巧

### 1. 缓存提高性能

```python
from functools import lru_cache

class CachedRAG(dspy.Module):
    def __init__(self):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=3)
        self.generate = dspy.ChainOfThought("context, question -> answer")

    @lru_cache(maxsize=1000)
    def forward(self, question):
        passages = self.retrieve(question).passages
        context = "\n".join(passages)
        return self.generate(context=context, question=question).answer
```

### 2. 错误处理

```python
class RobustModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.process = dspy.ChainOfThought("input -> output")

    def forward(self, input):
        try:
            result = self.process(input=input)
            return result
        except Exception as e:
            # 记录错误
            print(f"Error processing {input}: {e}")
            # 返回回退
            return dspy.Prediction(output="Error: could not process input")
```

### 3. 监控

```python
class MonitoredModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.process = dspy.ChainOfThought("input -> output")
        self.call_count = 0
        self.errors = 0

    def forward(self, input):
        self.call_count += 1

        try:
            result = self.process(input=input)
            return result
        except Exception as e:
            self.errors += 1
            raise

    def get_stats(self):
        return {
            "calls": self.call_count,
            "errors": self.errors,
            "error_rate": self.errors / max(self.call_count, 1)
        }
```

### 4. A/B 测试

```python
class ABTestModule(dspy.Module):
    """运行两个变体并比较。"""

    def __init__(self, variant_a, variant_b):
        super().__init__()
        self.variant_a = variant_a
        self.variant_b = variant_b
        self.a_calls = 0
        self.b_calls = 0

    def forward(self, input, variant="a"):
        if variant == "a":
            self.a_calls += 1
            return self.variant_a(input=input)
        else:
            self.b_calls += 1
            return self.variant_b(input=input)

# 比较两个优化器
baseline = dspy.ChainOfThought("question -> answer")
optimized = BootstrapFewShot(...).compile(baseline, trainset=trainset)

ab_test = ABTestModule(variant_a=baseline, variant_b=optimized)

# 50% 路由到每个
import random
variant = "a" if random.random() < 0.5 else "b"
result = ab_test(input=question, variant=variant)
```

## 完整示例：客服机器人

```python
import dspy
from dspy.teleprompt import BootstrapFewShot

class CustomerSupportBot(dspy.Module):
    """完整的客服系统。"""

    def __init__(self):
        super().__init__()

        # 分类意图
        self.classify_intent = dspy.Predict("message -> intent: str")

        # 专门处理器
        self.technical_handler = dspy.ChainOfThought("message, history -> response")
        self.billing_handler = dspy.ChainOfThought("message, history -> response")
        self.general_handler = dspy.Predict("message, history -> response")

        # 检索相关文档
        self.retrieve = dspy.Retrieve(k=3)

        # 对话历史
        self.history = []

    def forward(self, message):
        # 分类意图
        intent = self.classify_intent(message=message).intent

        # 检索相关文档
        docs = self.retrieve(message).passages
        context = "\n".join(docs)

        # 将上下文添加到历史
        history_str = "\n".join(self.history)
        full_message = f"Context: {context}\n\nMessage: {message}"

        # 路由到合适的处理器
        if intent == "technical":
            response = self.technical_handler(
                message=full_message,
                history=history_str
            ).response
        elif intent == "billing":
            response = self.billing_handler(
                message=full_message,
                history=history_str
            ).response
        else:
            response = self.general_handler(
                message=full_message,
                history=history_str
            ).response

        # 更新历史
        self.history.append(f"User: {message}")
        self.history.append(f"Bot: {response}")

        return dspy.Prediction(response=response, intent=intent)

# 训练数据
trainset = [
    dspy.Example(
        message="My account isn't working",
        intent="technical",
        response="I'd be happy to help. What error are you seeing?"
    ).with_inputs("message"),
    # ... 更多示例
]

# 定义度量
def response_quality(example, pred, trace=None):
    # 检查响应是否有帮助
    if len(pred.response) < 20:
        return 0.0
    if example.intent != pred.intent:
        return 0.3
    return 1.0

# 优化
optimizer = BootstrapFewShot(metric=response_quality)
bot = CustomerSupportBot()
optimized_bot = optimizer.compile(bot, trainset=trainset)

# 生产使用
optimized_bot.save("models/support_bot_v1.json")

# 之后，加载并使用
loaded_bot = CustomerSupportBot()
loaded_bot.load("models/support_bot_v1.json")
response = loaded_bot(message="I can't log in")
```

## 资源

- **文档**：https://dspy.ai
- **示例仓库**：https://github.com/stanfordnlp/dspy/tree/main/examples
- **Discord**：https://discord.gg/XCGy2WDCQB
