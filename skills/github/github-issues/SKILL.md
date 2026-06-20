---
name: github-issues
description: 创建、管理、分类和关闭GitHub问题。搜索现有问题、添加标签、分配人员并链接到PR。使用gh CLI或回退到git + GitHub REST API通过curl。
version: 1.1.0
author: Voidcube Agent
license: MIT
metadata:
  VoidCube:
    tags: [GitHub, Issues, Project-Management, Bug-Tracking, Triage]
    related_skills: [github-auth, github-pr-workflow]
---

# GitHub问题管理

创建、搜索、分类和管理GitHub问题。每节先展示`gh`,然后是`curl`回退。

## 前置条件

- 已通过GitHub认证(见`github-auth`技能)
- 在有GitHub远程的git仓库内,或显式指定仓库

### 设置

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

## 1. 查看问题

**使用gh:**

```bash
gh issue list
gh issue list --state open --label "bug"
gh issue list --assignee @me
gh issue list --search "authentication error" --state all
gh issue view 42
```

**使用curl:**

```bash
# 列出开放问题
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/$OWNER/$REPO/issues?state=open&per_page=20" \
  | python3 -c "
import sys, json
for i in json.load(sys.stdin):
    if 'pull_request' not in i:  # GitHub API在/issues中也返回PR
        labels = ', '.join(l['name'] for l in i['labels'])
        print(f\"#{i['number']:5}  {i['state']:6}  {labels:30}  {i['title']}\")"

# 按标签过滤
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/$OWNER/$REPO/issues?state=open&labels=bug&per_page=20" \
  | python3 -c "
import sys, json
for i in json.load(sys.stdin):
    if 'pull_request' not in i:
        print(f\"#{i['number']}  {i['title']}\")"

# 查看特定问题
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42 \
  | python3 -c "
import sys, json
i = json.load(sys.stdin)
labels = ', '.join(l['name'] for l in i['labels'])
assignees = ', '.join(a['login'] for a in i['assignees'])
print(f\"#{i['number']}: {i['title']}\")
print(f\"状态: {i['state']}  标签: {labels}  分配: {assignees}\")
print(f\"作者: {i['user']['login']}  创建: {i['created_at']}\")
print(f\"\n{i['body']}\")"

# 搜索问题
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/search/issues?q=authentication+error+repo:$OWNER/$REPO" \
  | python3 -c "
import sys, json
for i in json.load(sys.stdin)['items']:
    print(f\"#{i['number']}  {i['state']:6}  {i['title']}\")"
```

## 2. 创建问题

**使用gh:**

```bash
gh issue create \
  --title "登录重定向忽略?next=参数" \
  --body "## 描述
登录后,用户总是跳转到/dashboard。

## 复现步骤
1. 登出时导航到/settings
2. 被重定向到/login?next=/settings
3. 登录
4. 实际:重定向到/Dashboard(应去/settings)

## 预期行为
尊重?next=查询参数。" \
  --label "bug,backend" \
  --assignee "username"
```

**使用curl:**

```bash
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues \
  -d '{
    "title": "登录重定向忽略?next=参数",
    "body": "## 描述\n登录后,用户总是跳转到/Dashboard。\n\n## 复现步骤\n1. 登出时导航到/settings\n2. 被重定向到/login?next=/settings\n3. 登录\n4. 实际:重定向到/Dashboard\n\n## 预期行为\n尊重?next=查询参数。",
    "labels": ["bug", "backend"],
    "assignees": ["username"]
  }'
```

### Bug报告模板

```
## Bug描述
<发生了什么>

## 复现步骤
1. <步骤>
2. <步骤>

## 预期行为
<应该发生什么>

## 实际行为
<实际发生了什么>

## 环境
- OS: <操作系统>
- 版本: <版本>
```

### 功能请求模板

```
## 功能描述
<你想要什么>

## 动机
<为什么这有用>

## 提议方案
<如何工作>

## 考虑的替代方案
<其他方法>
```

## 3. 管理问题

### 添加/移除标签

**使用gh:**

```bash
gh issue edit 42 --add-label "priority:high,bug"
gh issue edit 42 --remove-label "needs-triage"
```

**使用curl:**

```bash
# 添加标签
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42/labels \
  -d '{"labels": ["priority:high", "bug"]}'

# 移除标签
curl -s -X DELETE \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42/labels/needs-triage

# 列出仓库可用标签
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/labels \
  | python3 -c "
import sys, json
for l in json.load(sys.stdin):
    print(f\"  {l['name']:30}  {l.get('description', '')}\")"
```

### 分配

**使用gh:**

```bash
gh issue edit 42 --add-assignee username
gh issue edit 42 --add-assignee @me
```

**使用curl:**

```bash
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42/assignees \
  -d '{"assignees": ["username"]}'
```

### 评论

**使用gh:**

```bash
gh issue comment 42 --body "已调查 — 根本原因在认证中间件。正在修复。"
```

**使用curl:**

```bash
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42/comments \
  -d '{"body": "已调查 — 根本原因在认证中间件。正在修复。"}'
```

### 关闭和重新打开

**使用gh:**

```bash
gh issue close 42
gh issue close 42 --reason "not planned"
gh issue reopen 42
```

**使用curl:**

```bash
# 关闭
curl -s -X PATCH \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42 \
  -d '{"state": "closed", "state_reason": "completed"}'

# 重新打开
curl -s -X PATCH \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/issues/42 \
  -d '{"state": "open"}'
```

### 链接问题到PR

当PR合并时,如果正文中包含正确关键词,问题会自动关闭:

```
Closes #42
Fixes #42
Resolves #42
```

从问题创建分支:

**使用gh:**

```bash
gh issue develop 42 --checkout
```

**使用git(手动等效):**

```bash
git checkout main && git pull origin main
git checkout -b fix/issue-42-login-redirect
```

## 4. 问题分类工作流

当被要求分类问题时:

1. **列出未分类问题:**

```bash
# 使用gh
gh issue list --label "needs-triage" --state open

# 使用curl
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/$OWNER/$REPO/issues?labels=needs-triage&state=open" \
  | python3 -c "
import sys, json
for i in json.load(sys.stdin):
    if 'pull_request' not in i:
        print(f\"#{i['number']}  {i['title']}\")"
```

2. **阅读并分类**每个问题(查看详情,理解bug/功能)

3. **应用标签和优先级**(见上面的管理问题)

4. **分配**如果所有者明确

5. **用分类笔记评论**如果需要

## 5. 批量操作

对于批量操作,结合API调用和shell脚本:

**使用gh:**

```bash
# 关闭所有带特定标签的问题
gh issue list --label "wontfix" --json number --jq '.[].number' | \
  xargs -I {} gh issue close {} --reason "not planned"
```

**使用curl:**

```bash
# 列出带标签的问题号,然后关闭每个
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/$OWNER/$REPO/issues?labels=wontfix&state=open" \
  | python3 -c "import sys,json; [print(i['number']) for i in json.load(sys.stdin)]" \
  | while read num; do
    curl -s -X PATCH \
      -H "Authorization: token $GITHUB_TOKEN" \
      https://api.github.com/repos/$OWNER/$REPO/issues/$num \
      -d '{"state": "closed", "state_reason": "not_planned"}'
    echo "已关闭 #$num"
  done
```

## 快速参考表

| 操作 | gh | curl端点 |
|--------|-----|--------------|
| 列出问题 | `gh issue list` | `GET /repos/{o}/{r}/issues` |
| 查看问题 | `gh issue view N` | `GET /repos/{o}/{r}/issues/N` |
| 创建问题 | `gh issue create ...` | `POST /repos/{o}/{r}/issues` |
| 添加标签 | `gh issue edit N --add-label ...` | `POST /repos/{o}/{r}/issues/N/labels` |
| 分配 | `gh issue edit N --add-assignee ...` | `POST /repos/{o}/{r}/issues/N/assignees` |
| 评论 | `gh issue comment N --body ...` | `POST /repos/{o}/{r}/issues/N/comments` |
| 关闭 | `gh issue close N` | `PATCH /repos/{o}/{r}/issues/N` |
| 搜索 | `gh issue list --search "..."` | `GET /search/issues?q=...` |
