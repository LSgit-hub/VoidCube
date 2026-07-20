# 全面约束模式

Guidance 中的正则约束、基于语法的生成和 token 修复指南。

## 目录
- 正则约束
- 基于语法的生成
- Token 修复
- 选择约束
- 复杂模式
- 性能优化

## 正则约束

### 基本模式

#### 数值约束

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 整数（正数）
lm += "Age: " + gen("age", regex=r"[0-9]+")

# 整数（带负数）
lm += "Temperature: " + gen("temp", regex=r"-?[0-9]+")

# 浮点数（正数）
lm += "Price: $" + gen("price", regex=r"[0-9]+\.[0-9]{2}")

# 浮点数（带负数和可选小数）
lm += "Value: " + gen("value", regex=r"-?[0-9]+(\.[0-9]+)?")

# 百分比（0-100）
lm += "Progress: " + gen("progress", regex=r"(100|[0-9]{1,2})")

# 范围（1-5 星）
lm += "Rating: " + gen("rating", regex=r"[1-5]") + " stars"
```

#### 文本约束

```python
# 仅字母
lm += "Name: " + gen("name", regex=r"[A-Za-z]+")

# 字母带空格
lm += "Full Name: " + gen("full_name", regex=r"[A-Za-z ]+")

# 字母数字
lm += "Username: " + gen("username", regex=r"[A-Za-z0-9_]+")

# 首字母大写的单词
lm += "Title: " + gen("title", regex=r"[A-Z][a-z]+( [A-Z][a-z]+)*")

# 仅小写
lm += "Code: " + gen("code", regex=r"[a-z0-9-]+")

# 特定长度
lm += "ID: " + gen("id", regex=r"[A-Z]{3}-[0-9]{6}")  # 例如，"ABC-123456"
```

#### 日期和时间约束

```python
# 日期（YYYY-MM-DD）
lm += "Date: " + gen("date", regex=r"\d{4}-\d{2}-\d{2}")

# 日期（MM/DD/YYYY）
lm += "Date: " + gen("date_us", regex=r"\d{2}/\d{2}/\d{4}")

# 时间（HH:MM）
lm += "Time: " + gen("time", regex=r"\d{2}:\d{2}")

# 时间（HH:MM:SS）
lm += "Time: " + gen("time_full", regex=r"\d{2}:\d{2}:\d{2}")

# ISO 8601 日期时间
lm += "Timestamp: " + gen(
    "timestamp",
    regex=r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
)

# 年份（YYYY）
lm += "Year: " + gen("year", regex=r"(19|20)\d{2}")

# 月份名称
lm += "Month: " + gen(
    "month",
    regex=r"(January|February|March|April|May|June|July|August|September|October|November|December)"
)
```

#### 联系信息

```python
# 电子邮件
lm += "Email: " + gen(
    "email",
    regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# 电话（美国格式）
lm += "Phone: " + gen("phone", regex=r"\d{3}-\d{3}-\d{4}")

# 电话（国际格式）
lm += "Phone: " + gen("phone_intl", regex=r"\+[0-9]{1,3}-[0-9]{1,14}")

# 邮政编码（美国）
lm += "ZIP: " + gen("zip", regex=r"\d{5}(-\d{4})?")

# 邮政编码（加拿大）
lm += "Postal: " + gen("postal", regex=r"[A-Z]\d[A-Z] \d[A-Z]\d")

# URL
lm += "URL: " + gen(
    "url",
    regex=r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[a-zA-Z0-9._~:/?#\[\]@!$&'()*+,;=-]*)?"
)
```

### 高级模式

#### JSON 字段约束

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 带引号的字符串字段
lm += '"name": ' + gen("name", regex=r'"[A-Za-z ]+"')

# 数值字段（无引号）
lm += '"age": ' + gen("age", regex=r"[0-9]+")

# 布尔字段
lm += '"active": ' + gen("active", regex=r"(true|false)")

# 空字段
lm += '"optional": ' + gen("optional", regex=r"(null|[0-9]+)")

# 字符串数组
lm += '"tags": [' + gen(
    "tags",
    regex=r'"[a-z]+"(, "[a-z]+")*'
) + ']'

# 完整 JSON 对象
lm += """{
    "name": """ + gen("name", regex=r'"[A-Za-z ]+"') + """,
    "age": """ + gen("age", regex=r"[0-9]+") + """,
    "email": """ + gen(
        "email",
        regex=r'"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"'
    ) + """
}"""
```

#### 代码模式

```python
# Python 变量名
lm += "Variable: " + gen("var", regex=r"[a-z_][a-z0-9_]*")

# Python 函数名
lm += "Function: " + gen("func", regex=r"[a-z_][a-z0-9_]*")

# 十六进制颜色代码
lm += "Color: #" + gen("color", regex=r"[0-9A-Fa-f]{6}")

# UUID
lm += "UUID: " + gen(
    "uuid",
    regex=r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

# Git 提交哈希（短）
lm += "Commit: " + gen("commit", regex=r"[0-9a-f]{7}")

# 语义版本
lm += "Version: " + gen("version", regex=r"[0-9]+\.[0-9]+\.[0-9]+")

# IP 地址（IPv4）
lm += "IP: " + gen(
    "ip",
    regex=r"((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
)
```

#### 领域特定模式

```python
# 信用卡号
lm += "Card: " + gen("card", regex=r"\d{4}-\d{4}-\d{4}-\d{4}")

# 社会安全号（美国）
lm += "SSN: " + gen("ssn", regex=r"\d{3}-\d{2}-\d{4}")

# ISBN-13
lm += "ISBN: " + gen("isbn", regex=r"978-\d{1,5}-\d{1,7}-\d{1,7}-\d")

# 车照（美国）
lm += "Plate: " + gen("plate", regex=r"[A-Z]{3}-\d{4}")

# 货币金额
lm += "Amount: $" + gen("amount", regex=r"[0-9]{1,3}(,[0-9]{3})*\.[0-9]{2}")

# 带小数的百分比
lm += "Rate: " + gen("rate", regex=r"[0-9]+\.[0-9]{1,2}%")
```

## 基于语法的生成

### JSON 语法

```python
from guidance import models, gen, guidance

@guidance
def json_object(lm):
    """生成有效的 JSON 对象。"""
    lm += "{\n"

    # 名称字段（必需）
    lm += '    "name": ' + gen("name", regex=r'"[A-Za-z ]+"') + ",\n"

    # 年龄字段（必需）
    lm += '    "age": ' + gen("age", regex=r"[0-9]+") + ",\n"

    # 电子邮件字段（必需）
    lm += '    "email": ' + gen(
        "email",
        regex=r'"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"'
    ) + ",\n"

    # 活跃字段（必需，布尔值）
    lm += '    "active": ' + gen("active", regex=r"(true|false)") + "\n"

    lm += "}"
    return lm

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")
lm = json_object(lm)
print(lm)  # 保证有效的 JSON
```

### 嵌套 JSON 语法

```python
@guidance
def nested_json(lm):
    """生成嵌套 JSON 结构。"""
    lm += "{\n"

    # 用户对象
    lm += '    "user": {\n'
    lm += '        "name": ' + gen("name", regex=r'"[A-Za-z ]+"') + ",\n"
    lm += '        "age": ' + gen("age", regex=r"[0-9]+") + "\n"
    lm += "    },\n"

    # 地址对象
    lm += '    "address": {\n'
    lm += '        "street": ' + gen("street", regex=r'"[A-Za-z0-9 ]+"') + ",\n"
    lm += '        "city": ' + gen("city", regex=r'"[A-Za-z ]+"') + ",\n"
    lm += '        "zip": ' + gen("zip", regex=r'"\d{5}"') + "\n"
    lm += "    }\n"

    lm += "}"
    return lm
```

### 数组语法

```python
@guidance
def json_array(lm, count=3):
    """生成固定数量的 JSON 数组。"""
    lm += "[\n"

    for i in range(count):
        lm += "    {\n"
        lm += '        "id": ' + gen(f"id_{i}", regex=r"[0-9]+") + ",\n"
        lm += '        "name": ' + gen(f"name_{i}", regex=r'"[A-Za-z ]+"') + "\n"
        lm += "    }"
        if i < count - 1:
            lm += ","
        lm += "\n"

    lm += "]"
    return lm
```

### XML 语法

```python
@guidance
def xml_document(lm):
    """生成有效的 XML 文档。"""
    lm += '<?xml version="1.0"?>\n'
    lm += "<person>\n"

    # 名称元素
    lm += "    <name>" + gen("name", regex=r"[A-Za-z ]+") + "</name>\n"

    # 年龄元素
    lm += "    <age>" + gen("age", regex=r"[0-9]+") + "</age>\n"

    # 电子邮件元素
    lm += "    <email>" + gen(
        "email",
        regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    ) + "</email>\n"

    lm += "</person>"
    return lm
```

### CSV 语法

```python
@guidance
def csv_row(lm):
    """生成 CSV 行。"""
    lm += gen("name", regex=r"[A-Za-z ]+") + ","
    lm += gen("age", regex=r"[0-9]+") + ","
    lm += gen("email", regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    return lm

@guidance
def csv_document(lm, rows=5):
    """生成完整的 CSV。"""
    # 标题
    lm += "Name,Age,Email\n"

    # 行
    for i in range(rows):
        lm = csv_row(lm)
        if i < rows - 1:
            lm += "\n"

    return lm
```

## Token 修复

### Token 修复如何工作

**问题：** 分词创建不自然的边界。

```python
# 没有 token 修复的示例
prompt = "The capital of France is "
# 分词：["The", " capital", " of", " France", " is", " "]
# 模型看到的最后一个 token：" "
# 第一个生成的 token 可能包含前导空格：" Paris"
# 结果："The capital of France is  Paris"（双空格）
```

**解决方案：** Guidance 回退并重新生成最后一个 token。

```python
from guidance import models, gen

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 默认启用 token 修复
lm += "The capital of France is " + gen("capital", max_tokens=5)

# 过程：
# 1. 回退到 " is " 之前的 token
# 2. 一起重新生成 " is" + "capital"
# 3. 结果："The capital of France is Paris"（正确）
```

### Token 修复示例

#### 自然延续

```python
# token 修复前
lm += "The function name is get" + gen("rest")
# 可能生成："The function name is get User"（User 前有空格）

# 使用 token 修复
lm += "The function name is get" + gen("rest")
# 生成："The function name is getUser"（正确的驼峰命名）
```

#### 代码生成

```python
# 函数名补全
lm += "def calculate_" + gen("rest", stop="(")
# token 修复确保平滑连接："calculate_total"

# 变量名补全
lm += "my_" + gen("var_name", regex=r"[a-z_]+")
# token 修复确保："my_variable_name"（不是 "my_ variable_name"）
```

#### 领域特定术语

```python
# 医学术语
lm += "The patient has hyper" + gen("condition")
# token 修复帮助："hypertension"（不是 "hyper tension"）

# 技术术语
lm += "Using micro" + gen("tech")
# token 修复帮助："microservices"（不是 "micro services"）
```

### 禁用 Token 修复

```python
# 如需要则禁用 token 修复（罕见）
lm += gen("text", token_healing=False)
```

## 选择约束

### 基本选择

```python
from guidance import models, select

lm = models.Transformers("Qwen/Qwen2.5-7B-Instruct")

# 简单选择
lm += "Status: " + select(["active", "inactive", "pending"], name="status")

# 布尔选择
lm += "Approved: " + select(["Yes", "No"], name="approved")

# 多项选择
lm += "Answer: " + select(
    ["A) Paris", "B) London", "C) Berlin", "D) Madrid"],
    name="answer"
)
```

### 条件选择

```python
from guidance import models, select, gen, guidance

@guidance
def conditional_fields(lm):
    """根据类型有条件生成字段。"""
    lm += "Type: " + select(["person", "company"], name="type")

    if lm["type"] == "person":
        lm += "\nName: " + gen("name", regex=r"[A-Za-z ]+")
        lm += "\nAge: " + gen("age", regex=r"[0-9]+")
    else:
        lm += "\nCompany Name: " + gen("company", regex=r"[A-Za-z ]+")
        lm += "\nEmployees: " + gen("employees", regex=r"[0-9]+")

    return lm
```

### 重复选择

```python
@guidance
def multiple_selections(lm):
    """选择多个项目。"""
    lm += "Select 3 colors:\n"

    colors = ["red", "blue", "green", "yellow", "purple"]

    for i in range(3):
        lm += f"{i+1}. " + select(colors, name=f"color_{i}") + "\n"

    return lm
```

## 复杂模式

### 模式 1：结构化表单

```python
@guidance
def user_form(lm):
    """生成结构化用户表单。"""
    lm += "=== User Registration ===\n\n"

    # 名称（仅字母）
    lm += "Full Name: " + gen("name", regex=r"[A-Za-z ]+", stop="\n") + "\n"

    # 年龄（数值）
    lm += "Age: " + gen("age", regex=r"[0-9]+", max_tokens=3) + "\n"

    # 电子邮件（验证格式）
    lm += "Email: " + gen(
        "email",
        regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        stop="\n"
    ) + "\n"

    # 电话（美国格式）
    lm += "Phone: " + gen("phone", regex=r"\d{3}-\d{3}-\d{4}") + "\n"

    # 账户类型（选择）
    lm += "Account Type: " + select(
        ["Standard", "Premium", "Enterprise"],
        name="account_type"
    ) + "\n"

    # 活跃状态（布尔值）
    lm += "Active: " + select(["Yes", "No"], name="active") + "\n"

    return lm
```

### 模式 2：多实体提取

```python
@guidance
def extract_entities(lm, text):
    """使用约束提取多个实体。"""
    lm += f"Text: {text}\n\n"

    # 人名（字母）
    lm += "Person: " + gen("person", regex=r"[A-Za-z ]+", stop="\n") + "\n"

    # 组织（字母数字带空格）
    lm += "Organization: " + gen(
        "organization",
        regex=r"[A-Za-z0-9 ]+",
        stop="\n"
    ) + "\n"

    # 日期（YYYY-MM-DD 格式）
    lm += "Date: " + gen("date", regex=r"\d{4}-\d{2}-\d{2}") + "\n"

    # 地点（字母带空格）
    lm += "Location: " + gen("location", regex=r"[A-Za-z ]+", stop="\n") + "\n"

    # 金额（货币）
    lm += "Amount: $" + gen("amount", regex=r"[0-9,]+\.[0-9]{2}") + "\n"

    return lm
```

### 模式 3：代码生成

```python
@guidance
def generate_python_function(lm):
    """使用约束生成 Python 函数。"""
    # 函数名（有效 Python 标识符）
    lm += "def " + gen("func_name", regex=r"[a-z_][a-z0-9_]*") + "("

    # 参数名
    lm += gen("param", regex=r"[a-z_][a-z0-9_]*") + "):\n"

    # 文档字符串
    lm += '    """' + gen("docstring", stop='"""', max_tokens=50) + '"""\n'

    # 函数体（约束为有效 Python）
    lm += "    return " + gen("return_value", stop="\n") + "\n"

    return lm
```

### 模式 4：层次数据

```python
@guidance
def org_chart(lm):
    """生成组织结构图。"""
    lm += "Company: " + gen("company", regex=r"[A-Za-z ]+") + "\n\n"

    # CEO
    lm += "CEO: " + gen("ceo", regex=r"[A-Za-z ]+") + "\n"

    # 部门
    for dept in ["Engineering", "Sales", "Marketing"]:
        lm += f"\n{dept} Department:\n"
        lm += "  Head: " + gen(f"{dept.lower()}_head", regex=r"[A-Za-z ]+") + "\n"
        lm += "  Size: " + gen(f"{dept.lower()}_size", regex=r"[0-9]+") + " employees\n"

    return lm
```

## 性能优化

### 最佳实践

#### 1. 使用特定模式

```python
# ✅ 好：特定模式
lm += gen("age", regex=r"[0-9]{1,3}")  # 快

# ❌ 坏：过于宽泛的模式
lm += gen("age", regex=r"[0-9]+")  # 慢
```

#### 2. 限制最大 Token 数

```python
# ✅ 好：合理限制
lm += gen("name", max_tokens=30)

# ❌ 坏：无限制
lm += gen("name")  # 可能永远生成
```

#### 3. 使用停止序列

```python
# ✅ 好：在换行处停止
lm += gen("line", stop="\n")

# ❌ 坏：依赖 max_tokens
lm += gen("line", max_tokens=100)
```

#### 4. 缓存编译的语法

```python
# 语法在首次使用后自动缓存
# 无需手动缓存
@guidance
def reusable_pattern(lm):
    """此语法编译一次并缓存。"""
    lm += gen("email", regex=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    return lm

# 首次调用：编译语法
lm = reusable_pattern(lm)

# 后续调用：使用缓存的语法（快）
lm = reusable_pattern(lm)
```

#### 5. 避免重叠约束

```python
# ✅ 好：清晰约束
lm += gen("age", regex=r"[0-9]+", max_tokens=3)

# ❌ 坏：冲突约束
lm += gen("age", regex=r"[0-9]{2}", max_tokens=10)  # max_tokens 不必要
```

### 性能基准

**正则 vs 自由生成：**
- 简单正则（数字）：比自由生成慢约 1.2 倍
- 复杂正则（电子邮件）：比自由生成慢约 1.5 倍
- 基于语法：比自由生成慢约 2 倍

**但是：**
- 100% 有效输出（自由生成 + 验证约 70%）
- 无需重试循环
- 对于结构化输出，端到端整体更快

**优化提示：**
- 仅对关键字段使用正则
- 对小固定集使用 `select()`（最快）
- 尽可能使用 `stop` 序列（比 max_tokens 快）
- 通过重用函数缓存编译的语法

## 资源

- **Token 修复论文**：https://arxiv.org/abs/2306.17648
- **Guidance 文档**：https://guidance.readthedocs.io
- **GitHub**：https://github.com/guidance-ai/guidance
