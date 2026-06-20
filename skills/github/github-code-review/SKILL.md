---
name: github-code-review
description: 通过分析git diff审查代码变更,在PR上留下内联评论,并执行彻底的推送前审查。使用gh CLI或回退到git + GitHub REST API通过curl。
version: 1.1.0
author: Voidcube Agent
license: MIT
metadata:
  VoidCube:
    tags: [GitHub, Code-Review, Pull-Requests, Git, Quality]
    related_skills: [github-auth, github-pr-workflow]
---

# GitHub代码审查

对推送前的本地变更执行代码审查,或审查GitHub上的开放PR。此技能大部分使用纯`git` — `gh`/`curl`分离仅对PR级交互重要。

## 前置条件

- 已通过GitHub认证(见`github-auth`技能)
- 在git仓库内

### 设置(用于PR交互)

```bash
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  AUTH="gh"
else
  AUTH="git"
  if [ -z "$GITHUB_TOKEN" ]; then
    if [ -f ~/.VoidCube/.env ] && grep -q "^GITHUB_TOKEN=" ~/.VoidCube/.env; then
      GITHUB_TOKEN=$(grep "^GITHUB_TOKEN=" ~/.VoidCube/.env | head -1 | cut -d= -f2 | tr -d '\n\r')
    elif grep -q "github.com" ~/.git-credentials 2>/dev/null; then
      GITHUB_TOKEN=$(grep "github.com" ~/.git-credentials 2>/dev/null | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|')
    fi
  fi
fi

REMOTE_URL=$(git remote get-url origin)
OWNER_REPO=$(echo "$REMOTE_URL" | sed -E 's|.*github\.com[:/]||; s|\.git$||')
OWNER=$(echo "$OWNER_REPO" | cut -d/ -f1)
REPO=$(echo "$OWNER_REPO" | cut -d/ -f2)
```

---

## 1. 审查本地变更(推送前)

这是纯`git` — 到处可用,无需API。

### 获取差异

```bash
# 暂存变更(将要提交的内容)
git diff --staged

# 相对main的所有变更(PR将包含的内容)
git diff main...HEAD

# 仅文件名
git diff main...HEAD --name-only

# 统计摘要(每个文件的插入/删除)
git diff main...HEAD --stat
```

### 审查策略

1. **先看大局:**

```bash
git diff main...HEAD --stat
git log main..HEAD --oneline
```

2. **逐文件审查** — 对变更文件使用`read_file`获取完整上下文,用diff查看变更内容:

```bash
git diff main...HEAD -- src/auth/login.py
```

3. **检查常见问题:**

```bash
# 遗留的调试语句、TODO、console.log
git diff main...HEAD | grep -n "print(\|console\.log\|TODO\|FIXME\|HACK\|XXX\|debugger"

# 意外暂存的大文件
git diff main...HEAD --stat | sort -t'|' -k2 -rn | head -10

# 密钥或凭据模式
git diff main...HEAD | grep -in "password\|secret\|api_key\|token.*=\|private_key"

# 合并冲突标记
git diff main...HEAD | grep -n "<<<<<<\|>>>>>>\|======="
```

4. **向用户呈现结构化反馈。**

### 审查输出格式

审查本地变更时,按此结构呈现发现:

```
## 代码审查摘要

### 严重
- **src/auth.py:45** — SQL注入:用户输入直接传递给查询。
  建议:使用参数化查询。

### 警告
- **src/models/user.py:23** — 密码明文存储。使用bcrypt或argon2。
- **src/api/routes.py:112** — 登录端点无速率限制。

### 建议
- **src/utils/helpers.py:8** — 与`src/core/utils.py:34`逻辑重复。合并。
- **tests/test_auth.py** — 缺少边界情况:过期令牌测试。

### 看起来不错
- 中间件层关注点清晰分离
- 主路径测试覆盖良好
```

---

## 2. 审查GitHub上的Pull Request

### 查看PR详情

**使用gh:**

```bash
gh pr view 123
gh pr diff 123
gh pr diff 123 --name-only
```

**使用git + curl:**

```bash
PR_NUMBER=123

# 获取PR详情
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER \
  | python3 -c "
import sys, json
pr = json.load(sys.stdin)
print(f\"标题: {pr['title']}\")
print(f\"作者: {pr['user']['login']}\")
print(f\"分支: {pr['head']['ref']} -> {pr['base']['ref']}\")
print(f\"状态: {pr['state']}\")
print(f\"正文:\n{pr['body']}\")"

# 列出变更文件
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER/files \
  | python3 -c "
import sys, json
for f in json.load(sys.stdin):
    print(f\"{f['status']:10} +{f['additions']:-4} -{f['deletions']:-4}  {f['filename']}\")"
```

### 本地检出PR进行完整审查

这使用纯`git` — 无需`gh`:

```bash
# 获取PR分支并检出
git fetch origin pull/123/head:pr-123
git checkout pr-123

# 现在可以使用read_file、search_files、运行测试等

# 查看相对基础分支的差异
git diff main...pr-123
```

**使用gh(快捷方式):**

```bash
gh pr checkout 123
```

### 在PR上留下评论

**通用PR评论 — 使用gh:**

```bash
gh pr comment 123 --body "整体看起来不错,下面有一些建议。"
```

**通用PR评论 — 使用curl:**

```bash
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/$PR_NUMBER/comments \
  -d '{"body": "整体看起来不错,下面有一些建议。"}'
```

### 留下内联审查评论

**单个内联评论 — 使用gh(通过API):**

```bash
HEAD_SHA=$(gh pr view 123 --json headRefOid --jq '.headRefOid')

gh api repos/$OWNER/$REPO/pulls/123/comments \
  --method POST \
  -f body="这可以用列表推导式简化。" \
  -f path="src/auth/login.py" \
  -f commit_id="$HEAD_SHA" \
  -f line=45 \
  -f side="RIGHT"
```

**单个内联评论 — 使用curl:**

```bash
# 获取head提交SHA
HEAD_SHA=$(curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['head']['sha'])")

curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments \
  -d "{
    \"body\": \"这可以用列表推导式简化。\",
    \"path\": \"src/auth/login.py\",
    \"commit_id\": \"$HEAD_SHA\",
    \"line\": 45,
    \"side\": \"RIGHT\"
  }"
```

### 提交正式审查(批准/请求变更)

**使用gh:**

```bash
gh pr review 123 --approve --body "LGTM!"
gh pr review 123 --request-changes --body "见内联评论。"
gh pr review 123 --comment --body "一些建议,无阻塞问题。"
```

**使用curl — 原子性提交多评论审查:**

```bash
HEAD_SHA=$(curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['head']['sha'])")

curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER/reviews \
  -d "{
    \"commit_id\": \"$HEAD_SHA\",
    \"event\": \"COMMENT\",
    \"body\": \"Voidcube Agent代码审查\",
    \"comments\": [
      {\"path\": \"src/auth.py\", \"line\": 45, \"body\": \"使用参数化查询防止SQL注入。\"},
      {\"path\": \"src/models/user.py\", \"line\": 23, \"body\": \"存储前用bcrypt哈希密码。\"},
      {\"path\": \"tests/test_auth.py\", \"line\": 1, \"body\": \"添加过期令牌边界情况测试。\"}
    ]
  }"
```

事件值: `"APPROVE"`、`"REQUEST_CHANGES"`、`"COMMENT"`

`line`字段指文件*新*版本中的行号。对于删除行,使用`"side": "LEFT"`。

---

## 3. 审查清单

执行代码审查(本地或PR)时,系统检查:

### 正确性
- 代码是否如其所述?
- 边界情况是否处理(空输入、null、大数据、并发访问)?
- 错误路径是否优雅处理?

### 安全性
- 无硬编码密钥、凭据或API密钥
- 用户输入验证
- 无SQL注入、XSS或路径遍历
- 需要时检查认证/授权

### 代码质量
- 清晰命名(变量、函数、类)
- 无不必要复杂性或过早抽象
- DRY — 无应提取的重复逻辑
- 函数专注(单一职责)

### 测试
- 新代码路径是否测试?
- 主路径和错误情况是否覆盖?
- 测试可读可维护?

### 性能
- 无N+1查询或不必要循环
- 适当缓存
- 异步代码路径无阻塞操作

### 文档
- 公共API已文档化
- 非显而易见逻辑有注释解释"为什么"
- 行为变更时README已更新

---

## 4. 推送前审查工作流

当用户要求你"审查代码"或"推送前检查":

1. `git diff main...HEAD --stat` — 查看变更范围
2. `git diff main...HEAD` — 读取完整差异
3. 对每个变更文件,如需更多上下文使用`read_file`
4. 应用上述清单
5. 以结构化格式呈现发现(严重/警告/建议/看起来不错)
6. 如发现严重问题,在用户推送前提供修复

---

## 5. PR审查工作流(端到端)

当用户要求你"审查PR #N"、"看这个PR"或给你PR URL,遵循此流程:

### 步骤1:设置环境

```bash
source ~/.VoidCube/skills/github/github-auth/scripts/gh-env.sh
# 或运行此技能顶部的内联设置块
```

### 步骤2:收集PR上下文

获取PR元数据、描述和变更文件列表,在深入代码前了解范围。

**使用gh:**
```bash
gh pr view 123
gh pr diff 123 --name-only
gh pr checks 123
```

**使用curl:**
```bash
PR_NUMBER=123

# PR详情(标题、作者、描述、分支)
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GH_OWNER/$GH_REPO/pulls/$PR_NUMBER

# 带行数的变更文件
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GH_OWNER/$GH_REPO/pulls/$PR_NUMBER/files
```

### 步骤3:本地检出PR

这让你完全访问`read_file`、`search_files`和运行测试的能力。

```bash
git fetch origin pull/$PR_NUMBER/head:pr-$PR_NUMBER
git checkout pr-$PR_NUMBER
```

### 步骤4:读取差异并理解变更

```bash
# 相对基础分支的完整差异
git diff main...HEAD

# 或对大型PR逐文件
git diff main...HEAD --name-only
# 然后对每个文件:
git diff main...HEAD -- path/to/file.py
```

对每个变更文件,使用`read_file`查看变更周围的完整上下文 — 仅差异可能遗漏仅周围代码可见的问题。

### 步骤5:本地运行自动检查(如适用)

```bash
# 如有测试套件则运行测试
python -m pytest 2>&1 | tail -20
# 或: npm test、cargo test、go test ./...等

# 如已配置则运行linter
ruff check . 2>&1 | head -30
# 或: eslint、clippy等
```

### 步骤6:应用审查清单(第3节)

遍历每个类别:正确性、安全性、代码质量、测试、性能、文档。

### 步骤7:将审查发布到GitHub

收集发现并作为带内联评论的正式审查提交。

**使用gh:**
```bash
# 如无问题 — 批准
gh pr review $PR_NUMBER --approve --body "Voidcube Agent审查。代码干净 — 测试覆盖良好,无安全问题。"

# 如发现问题 — 请求变更并带内联评论
gh pr review $PR_NUMBER --request-changes --body "发现几个问题 — 见内联评论。"
```

**使用curl — 带多个内联评论的原子审查:**
```bash
HEAD_SHA=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GH_OWNER/$GH_REPO/pulls/$PR_NUMBER \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['head']['sha'])")

# 构建审查JSON — 事件为APPROVE、REQUEST_CHANGES或COMMENT
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GH_OWNER/$GH_REPO/pulls/$PR_NUMBER/reviews \
  -d "{
    \"commit_id\": \"$HEAD_SHA\",
    \"event\": \"REQUEST_CHANGES\",
    \"body\": \"## Voidcube Agent审查\n\n发现2个问题,1个建议。见内联评论。\",
    \"comments\": [
      {\"path\": \"src/auth.py\", \"line\": 45, \"body\": \"🔴 **严重:** 用户输入直接传递给SQL查询 — 使用参数化查询。\"},
      {\"path\": \"src/models.py\", \"line\": 23, \"body\": \"⚠️ **警告:** 密码未哈希存储。\"},
      {\"path\": \"src/utils.py\", \"line\": 8, \"body\": \"💡 **建议:** 这与core/utils.py:34逻辑重复。\"}
    ]
  }"
```

### 步骤8:也发布摘要评论

除内联评论外,留下顶层摘要,让PR作者一目了然。使用`references/review-output-template.md`中的审查输出格式。

**使用gh:**
```bash
gh pr comment $PR_NUMBER --body "$(cat <<'EOF'
## 代码审查摘要

**结论: 请求变更** (2个问题,1个建议)

### 🔴 严重
- **src/auth.py:45** — SQL注入漏洞

### ⚠️ 警告
- **src/models.py:23** — 明文密码存储

### 💡 建议
- **src/utils.py:8** — 逻辑重复,考虑合并

### ✅ 看起来不错
- 清晰的API设计
- 中间件层错误处理良好

---
*Voidcube Agent审查*
EOF
)"
```

### 步骤9:清理

```bash
git checkout main
git branch -D pr-$PR_NUMBER
```

### 决策:批准 vs 请求变更 vs 评论

- **批准** — 无严重或警告级问题,仅轻微建议或全部通过
- **请求变更** — 任何应在合并前修复的严重或警告级问题
- **评论** — 观察和建议,但无阻塞(用于不确定或PR是草稿时)
