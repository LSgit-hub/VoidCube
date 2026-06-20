# 全面 JSON 生成指南

使用 Pydantic 模型和 JSON schema 与 Outlines 进行 JSON 生成的完整指南。

## 目录
- Pydantic 模型
- JSON Schema 支持
- 高级模式
- 嵌套结构
- 复杂类型
- 验证
- 性能优化

## Pydantic 模型

### 基本模型

```python
from pydantic import BaseModel
import outlines

class User(BaseModel):
    name: str
    age: int
    email: str

model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")
generator = outlines.generate.json(model, User)

user = generator("Generate user: Alice, 25, alice@example.com")
print(user.name)   # "Alice"
print(user.age)    # 25
print(user.email)  # "alice@example.com"
```

### 字段约束

```python
from pydantic import BaseModel, Field

class Product(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(gt=0, description="Price in USD")
    discount: float = Field(ge=0, le=100, description="Discount percentage")
    quantity: int = Field(ge=0, description="Available quantity")
    sku: str = Field(pattern=r"^[A-Z]{3}-\d{6}$")

model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")
generator = outlines.generate.json(model, Product)

product = generator("Generate product: iPhone 15, $999")
# 所有字段保证满足约束
```

**可用约束：**
- `min_length`, `max_length`：字符串长度
- `gt`, `ge`, `lt`, `le`：数值比较
- `multiple_of`：数值必须是指定值的倍数
- `pattern`：字符串的正则模式
- `min_items`, `max_items`：列表长度

### 可选字段

```python
from typing import Optional

class Article(BaseModel):
    title: str  # 必需
    author: Optional[str] = None  # 可选
    published_date: Optional[str] = None  # 可选
    tags: list[str] = []  # 默认空列表
    view_count: int = 0  # 默认值

generator = outlines.generate.json(model, Article)

# 即使可选字段缺失也能生成
article = generator("Title: Introduction to AI")
print(article.author)  # None（未提供）
print(article.tags)    # []（默认）
```

### 默认值

```python
class Config(BaseModel):
    debug: bool = False
    max_retries: int = 3
    timeout: float = 30.0
    log_level: str = "INFO"

# 未指定时生成器使用默认值
generator = outlines.generate.json(model, Config)
config = generator("Generate config with debug enabled")
print(config.debug)  # True（来自提示）
print(config.timeout)  # 30.0（默认）
```

## 枚举和字面量

### 枚举字段

```python
from enum import Enum

class Status(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

class Application(BaseModel):
    applicant_name: str
    status: Status  # 必须是枚举值之一
    submitted_date: str

generator = outlines.generate.json(model, Application)
app = generator("Generate application for John Doe")

print(app.status)  # Status.PENDING（或枚举值之一）
print(type(app.status))  # <enum 'Status'>
```

### 字面量类型

```python
from typing import Literal

class Task(BaseModel):
    title: str
    priority: Literal["low", "medium", "high", "critical"]
    status: Literal["todo", "in_progress", "done"]
    assigned_to: str

generator = outlines.generate.json(model, Task)
task = generator("Create high priority task: Fix bug")

print(task.priority)  # "low", "medium", "high", "critical" 之一
```

### 多选字段

```python
class Survey(BaseModel):
    question: str
    answer: Literal["strongly_disagree", "disagree", "neutral", "agree", "strongly_agree"]
    confidence: Literal["low", "medium", "high"]

generator = outlines.generate.json(model, Survey)
survey = generator("Rate: 'I enjoy using this product'")
```

## 嵌套结构

### 嵌套模型

```python
class Address(BaseModel):
    street: str
    city: str
    state: str
    zip_code: str
    country: str = "USA"

class Person(BaseModel):
    name: str
    age: int
    email: str
    address: Address  # 嵌套模型

model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")
generator = outlines.generate.json(model, Person)

prompt = """
Extract person:
Name: Alice Johnson
Age: 28
Email: alice@example.com
Address: 123 Main St, Boston, MA, 02101
"""

person = generator(prompt)
print(person.name)  # "Alice Johnson"
print(person.address.city)  # "Boston"
print(person.address.state)  # "MA"
```

### 深度嵌套

```python
class Coordinates(BaseModel):
    latitude: float
    longitude: float

class Location(BaseModel):
    name: str
    coordinates: Coordinates

class Event(BaseModel):
    title: str
    date: str
    location: Location

generator = outlines.generate.json(model, Event)
event = generator("Generate event: Tech Conference in San Francisco")

print(event.title)  # "Tech Conference"
print(event.location.name)  # "San Francisco"
print(event.location.coordinates.latitude)  # 37.7749
```

### 嵌套模型列表

```python
class Item(BaseModel):
    name: str
    quantity: int
    price: float

class Order(BaseModel):
    order_id: str
    customer: str
    items: list[Item]  # 嵌套模型列表
    total: float

generator = outlines.generate.json(model, Order)

prompt = """
Generate order for John:
- 2x Widget ($10 each)
- 3x Gadget ($15 each)
Order ID: ORD-001
"""

order = generator(prompt)
print(f"Order ID: {order.order_id}")
for item in order.items:
    print(f"- {item.quantity}x {item.name} @ ${item.price}")
print(f"Total: ${order.total}")
```

## 复杂类型

### 联合类型

```python
from typing import Union

class TextContent(BaseModel):
    type: Literal["text"]
    content: str

class ImageContent(BaseModel):
    type: Literal["image"]
    url: str
    caption: str

class Post(BaseModel):
    title: str
    content: Union[TextContent, ImageContent]  # 任一类型

generator = outlines.generate.json(model, Post)

# 可以生成文本或图像内容
post = generator("Generate blog post with image")
if post.content.type == "text":
    print(post.content.content)
elif post.content.type == "image":
    print(post.content.url)
```

### 列表和数组

```python
class Article(BaseModel):
    title: str
    authors: list[str]  # 字符串列表
    tags: list[str]
    sections: list[dict[str, str]]  # 字典列表
    related_ids: list[int]

generator = outlines.generate.json(model, Article)
article = generator("Generate article about AI")

print(article.authors)  # ["Alice", "Bob"]
print(article.tags)  # ["AI", "Machine Learning", "Technology"]
```

### 字典

```python
class Metadata(BaseModel):
    title: str
    properties: dict[str, str]  # 字符串键和值
    counts: dict[str, int]  # 字符串键，整数值
    settings: dict[str, Union[str, int, bool]]  # 混合值类型

generator = outlines.generate.json(model, Metadata)
meta = generator("Generate metadata")

print(meta.properties)  # {"author": "Alice", "version": "1.0"}
print(meta.counts)  # {"views": 1000, "likes": 50}
```

### Any 类型（谨慎使用）

```python
from typing import Any

class FlexibleData(BaseModel):
    name: str
    structured_field: str
    flexible_field: Any  # 可以是任何类型

# 注意：Any 降低类型安全性，仅在必要时使用
generator = outlines.generate.json(model, FlexibleData)
```

## JSON Schema 支持

### 直接使用 Schema

```python
import outlines

model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")

# 定义 JSON schema
schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0, "maximum": 120},
        "email": {"type": "string", "format": "email"}
    },
    "required": ["name", "age", "email"]
}

# 从 schema 生成
generator = outlines.generate.json(model, schema)
result = generator("Generate person: Alice, 25, alice@example.com")

print(result)  # 匹配 schema 的有效 JSON
```

### 从 Pydantic 获取 Schema

```python
class User(BaseModel):
    name: str
    age: int
    email: str

# 从 Pydantic 模型获取 JSON schema
schema = User.model_json_schema()
print(schema)
# {
#   "type": "object",
#   "properties": {
#     "name": {"type": "string"},
#     "age": {"type": "integer"},
#     "email": {"type": "string"}
#   },
#   "required": ["name", "age", "email"]
# }

# 两种方法等效：
generator1 = outlines.generate.json(model, User)
generator2 = outlines.generate.json(model, schema)
```

## 高级模式

### 条件字段

```python
class Order(BaseModel):
    order_type: Literal["standard", "express"]
    delivery_date: str
    express_fee: Optional[float] = None  # 仅用于快递订单

generator = outlines.generate.json(model, Order)

# 快递订单
order1 = generator("Create express order for tomorrow")
print(order1.express_fee)  # 25.0

# 标准订单
order2 = generator("Create standard order")
print(order2.express_fee)  # None
```

### 递归模型

```python
from typing import Optional, List

class TreeNode(BaseModel):
    value: str
    children: Optional[List['TreeNode']] = None

# 启用前向引用
TreeNode.model_rebuild()

generator = outlines.generate.json(model, TreeNode)
tree = generator("Generate file tree with subdirectories")

print(tree.value)  # "root"
print(tree.children[0].value)  # "subdir1"
```

### 带验证的模型

```python
from pydantic import field_validator

class DateRange(BaseModel):
    start_date: str
    end_date: str

    @field_validator('end_date')
    def end_after_start(cls, v, info):
        """确保 end_date 在 start_date 之后。"""
        if 'start_date' in info.data:
            from datetime import datetime
            start = datetime.strptime(info.data['start_date'], '%Y-%m-%d')
            end = datetime.strptime(v, '%Y-%m-%d')
            if end < start:
                raise ValueError('end_date must be after start_date')
        return v

generator = outlines.generate.json(model, DateRange)
# 生成后进行验证
```

## 多对象

### 生成对象列表

```python
class Person(BaseModel):
    name: str
    age: int

class Team(BaseModel):
    team_name: str
    members: list[Person]

generator = outlines.generate.json(model, Team)

team = generator("Generate engineering team with 5 members")
print(f"Team: {team.team_name}")
for member in team.members:
    print(f"- {member.name}, {member.age}")
```

### 批量生成

```python
def generate_batch(prompts: list[str], schema: type[BaseModel]):
    """为多个提示生成结构化输出。"""
    model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")
    generator = outlines.generate.json(model, schema)

    results = []
    for prompt in prompts:
        result = generator(prompt)
        results.append(result)

    return results

class Product(BaseModel):
    name: str
    price: float

prompts = [
    "Product: iPhone 15, $999",
    "Product: MacBook Pro, $2499",
    "Product: AirPods, $179"
]

products = generate_batch(prompts, Product)
for product in products:
    print(f"{product.name}: ${product.price}")
```

## 性能优化

### 缓存生成器

```python
from functools import lru_cache

@lru_cache(maxsize=10)
def get_generator(model_name: str, schema_hash: int):
    """缓存生成器以重用。"""
    model = outlines.models.transformers(model_name)
    return outlines.generate.json(model, schema)

# 首次调用：创建生成器
gen1 = get_generator("microsoft/Phi-3-mini-4k-instruct", hash(User))

# 第二次调用：返回缓存的生成器（快！）
gen2 = get_generator("microsoft/Phi-3-mini-4k-instruct", hash(User))
```

### 批量处理

```python
# 高效处理多个项目
model = outlines.models.transformers("microsoft/Phi-3-mini-4k-instruct")
generator = outlines.generate.json(model, User)

texts = ["User: Alice, 25", "User: Bob, 30", "User: Carol, 35"]

# 重用生成器（模型保持加载）
users = [generator(text) for text in texts]
```

### 最小化 Schema 复杂度

```python
# ✅ 好：简单、扁平结构（更快）
class SimplePerson(BaseModel):
    name: str
    age: int
    city: str

# ⚠️ 较慢：深度嵌套
class ComplexPerson(BaseModel):
    personal_info: PersonalInfo
    address: Address
    employment: Employment
    # ... 多层嵌套
```

## 错误处理

### 处理缺失字段

```python
from pydantic import ValidationError

class User(BaseModel):
    name: str
    age: int
    email: str

try:
    user = generator("Generate user")  # 可能不包含所有字段
except ValidationError as e:
    print(f"Validation error: {e}")
    # 优雅处理
```

### 使用可选字段的回退

```python
class RobustUser(BaseModel):
    name: str  # 必需
    age: Optional[int] = None  # 可选
    email: Optional[str] = None  # 可选

# 即使数据不完整也更可能成功
user = generator("Generate user: Alice")
print(user.name)  # "Alice"
print(user.age)  # None（未提供）
```

## 最佳实践

### 1. 使用具体类型

```python
# ✅ 好：具体类型
class Product(BaseModel):
    name: str
    price: float  # 不是 Any 或 str
    quantity: int  # 不是 str
    in_stock: bool  # 不是 int

# ❌ 坏：泛型类型
class Product(BaseModel):
    name: Any
    price: str  # 应该是 float
    quantity: str  # 应该是 int
```

### 2. 添加描述

```python
# ✅ 好：清晰描述
class Article(BaseModel):
    title: str = Field(description="Article title, 10-100 characters")
    content: str = Field(description="Main article content in paragraphs")
    tags: list[str] = Field(description="List of relevant topic tags")

# 描述帮助模型理解预期输出
```

### 3. 使用约束

```python
# ✅ 好：带约束
class Age(BaseModel):
    value: int = Field(ge=0, le=120, description="Age in years")

# ❌ 坏：无约束
class Age(BaseModel):
    value: int  # 可能是负数或 > 120
```

### 4. 优先使用枚举而非字符串

```python
# ✅ 好：固定集合使用枚举
class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Task(BaseModel):
    priority: Priority  # 保证有效

# ❌ 坏：自由形式字符串
class Task(BaseModel):
    priority: str  # 可能是 "urgent", "ASAP", "!!" 等
```

### 5. 测试模型

```python
# 测试模型按预期工作
def test_product_model():
    product = Product(
        name="Test Product",
        price=19.99,
        quantity=10,
        in_stock=True
    )
    assert product.price == 19.99
    assert isinstance(product, Product)

# 在生产使用前运行测试
```

## 资源

- **Pydantic 文档**：https://docs.pydantic.dev
- **JSON Schema**：https://json-schema.org
- **Outlines GitHub**：https://github.com/outlines-dev/outlines
