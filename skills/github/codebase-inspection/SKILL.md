---
name: codebase-inspection
description: 使用pygount检查和分析代码库,进行LOC计数、语言分解和代码与注释比率。当被要求检查代码行数、仓库大小、语言组成或代码库统计时使用。
version: 1.0.0
author: Voidcube Agent
license: MIT
metadata:
  VoidCube:
    tags: [LOC, Code Analysis, pygount, Codebase, Metrics, Repository]
    related_skills: [github-repo-management]
---

# 使用pygount进行代码库检查

使用`pygount`分析仓库的代码行数、语言分解、文件计数和代码与注释比率。

## 使用场景

- 用户询问LOC(代码行数)计数
- 用户想要仓库的语言分解
- 用户询问代码库大小或组成
- 用户想要代码与注释比率
- 一般的"这个仓库有多大"问题

## 前置条件

```bash
pip install --break-system-packages pygount 2>/dev/null || pip install pygount
```

## 1. 基本摘要(最常用)

获取完整的语言分解,包括文件计数、代码行和注释行:

```bash
cd /path/to/repo
pygount --format=summary \
  --folders-to-skip=".git,node_modules,venv,.venv,__pycache__,.cache,dist,build,.next,.tox,.eggs,*.egg-info" \
  .
```

**重要:** 始终使用`--folders-to-skip`排除依赖/构建目录,否则pygount会爬取它们并花费很长时间或挂起。

## 2. 常见文件夹排除

根据项目类型调整:

```bash
# Python项目
--folders-to-skip=".git,venv,.venv,__pycache__,.cache,dist,build,.tox,.eggs,.mypy_cache"

# JavaScript/TypeScript项目
--folders-to-skip=".git,node_modules,dist,build,.next,.cache,.turbo,coverage"

# 通用排除
--folders-to-skip=".git,node_modules,venv,.venv,__pycache__,.cache,dist,build,.next,.tox,vendor,third_party"
```

## 3. 按特定语言过滤

```bash
# 仅统计Python文件
pygount --suffix=py --format=summary .

# 仅统计Python和YAML
pygount --suffix=py,yaml,yml --format=summary .
```

## 4. 详细的逐文件输出

```bash
# 默认格式显示每个文件的分解
pygount --folders-to-skip=".git,node_modules,venv" .

# 按代码行排序(通过sort管道)
pygount --folders-to-skip=".git,node_modules,venv" . | sort -t$'\t' -k1 -nr | head -20
```

## 5. 输出格式

```bash
# 摘要表(默认推荐)
pygount --format=summary .

# JSON输出用于程序化使用
pygount --format=json .

# 管道友好: 语言、文件数、代码、文档、空行、字符串
pygount --format=summary . 2>/dev/null
```

## 6. 解读结果

摘要表列:
- **Language** — 检测到的编程语言
- **Files** — 该语言的文件数
- **Code** — 实际代码行(可执行/声明式)
- **Comment** — 注释或文档行
- **%** — 总计百分比

特殊伪语言:
- `__empty__` — 空文件
- `__binary__` — 二进制文件(图像、编译文件等)
- `__generated__` — 自动生成的文件(启发式检测)
- `__duplicate__` — 内容相同的文件
- `__unknown__` — 无法识别的文件类型

## 陷阱

1. **始终排除.git、node_modules、venv** — 没有`--folders-to-skip`,pygount会爬取所有内容,可能需要几分钟或在大型依赖树上挂起。
2. **Markdown显示0代码行** — pygount将所有Markdown内容分类为注释,而非代码。这是预期行为。
3. **JSON文件显示低代码计数** — pygount可能保守地计算JSON行。要准确计算JSON行数,直接使用`wc -l`。
4. **大型monorepo** — 对于非常大的仓库,考虑使用`--suffix`针对特定语言而不是扫描所有内容。
