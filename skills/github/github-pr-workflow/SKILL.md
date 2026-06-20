---
name: github-pr-workflow
description: 完整的pull request生命周期 — 创建分支、提交变更、打开PR、监控CI状态、自动修复失败并合并。使用gh CLI或回退到git + GitHub REST API通过curl。
version: 1.1.0
author: Voidcube Agent
license: MIT
metadata:
  VoidCube:
    tags: [GitHub, Pull-Requests, CI/CD, Git, Automation, Merge]
    related_skills: [github-auth, github-code-review]
---

# GitHub Pull Request工作流

管理PR生命周期的完整指南。每节先展示`gh`方式,然后是无`gh`机器的`git` + `curl`回退。

## 前置条件

- 已通过GitHub认证(见`github-auth`技能)
- 在有GitHub远程的git仓库内

### 快速认证检测

```bash
# 确定整个工作流使用哪种方法
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  AUTH="gh"
else
  AUTH="git"
  # 确保有API调用的令牌
  if [ -z "$GITHUB_TOKEN" ]; then
    if [ -f ~/.VoidCube/.env ] && grep -q "^GITHUB_TOKEN=" ~/.VoidCube/.env; then
      GITHUB_TOKEN=$(grep "^GITHUB_TOKEN=" ~/.VoidCube/.env | head -1 | cut -d= -f2 | tr -d '\n\r')
    elif grep -q "github.com" ~/.git-credentials 2>/dev/null; then
      GITHUB_TOKEN=$(grep "github.com" ~/.git-credentials 2>/dev/null | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|')
    fi
  fi
fi
echo "使用: $AUTH"
```

### 从Git远程提取所有者/仓库

许多`curl`命令需要`owner/repo`。从git远程提取:

```bash
# 适用于HTTPS和SSH远程URL
REMOTE_URL=$(git remote get-url origin)
OWNER_REPO=$(echo "$REMOTE_URL" | sed -E 's|.*github\.com[:/]||; s|\.git$||')
OWNER=$(echo "$OWNER_REPO" | cut -d/ -f1)
REPO=$(echo "$OWNER_REPO" | cut -d/ -f2)
echo "所有者: $OWNER, 仓库: $REPO"
```

---

## 1. 分支创建

这部分是纯`git` — 两种方式相同:

```bash
# 确保最新
git fetch origin
git checkout main && git pull origin main

# 创建并切换到新分支
git checkout -b feat/add-user-authentication
```

分支命名约定:
- `feat/description` — 新功能
- `fix/description` — bug修复
- `refactor/description` — 代码重构
- `docs/description` — 文档
- `ci/description` — CI/CD变更

## 2. 提交

使用智能体的文件工具(`write_file`、`patch`)进行变更,然后提交:

```bash
# 暂存特定文件
git add src/auth.py src/models/user.py tests/test_auth.py

# 用约定式提交消息提交
git commit -m "feat: 添加基于JWT的用户认证

- 添加登录/注册端点
- 添加带密码哈希的User模型
- 为受保护路由添加认证中间件
- 为认证流程添加单元测试"
```

提交消息格式(约定式提交):
```
type(scope): 简短描述

如需更长解释。72字符换行。
```

类型: `feat`、`fix`、`refactor`、`docs`、`test`、`ci`、`chore`、`perf`

## 3. 推送并创建PR

### 推送分支(两种方式相同)

```bash
git push -u origin HEAD
```

### 创建PR

**使用gh:**

```bash
gh pr create \
  --title "feat: 添加基于JWT的用户认证" \
  --body "## 摘要
- 添加登录和注册API端点
- JWT令牌生成和验证

## 测试计划
- [ ] 单元测试通过

Closes #42"
```

选项: `--draft`、`--reviewer user1,user2`、`--label "enhancement"`、`--base develop`

**使用git + curl:**

```bash
BRANCH=$(git branch --show-current)

curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/$OWNER/$REPO/pulls \
  -d "{
    \"title\": \"feat: 添加基于JWT的用户认证\",
    \"body\": \"## 摘要\n添加登录和注册API端点。\n\nCloses #42\",
    \"head\": \"$BRANCH\",
    \"base\": \"main\"
  }"
```

响应JSON包含PR`number` — 保存以备后续命令使用。

要创建为草稿,在JSON正文中添加`"draft": true`。

## 4. 监控CI状态

### 检查CI状态

**使用gh:**

```bash
# 一次性检查
gh pr checks

# 监视直到所有检查完成(每10秒轮询)
gh pr checks --watch
```

**使用git + curl:**

```bash
# 获取当前分支最新提交SHA
SHA=$(git rev-parse HEAD)

# 查询组合状态
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/commits/$SHA/status \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f\"总体: {data['state']}\")
for s in data.get('statuses', []):
    print(f\"  {s['context']}: {s['state']} - {s.get('description', '')}\")"

# 也检查GitHub Actions检查运行(单独端点)
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/commits/$SHA/check-runs \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for cr in data.get('check_runs', []):
    print(f\"  {cr['name']}: {cr['status']} / {cr['conclusion'] or 'pending'}\")"
```

### 轮询直到完成(git + curl)

```bash
# 简单轮询循环 — 每30秒检查,最多10分钟
SHA=$(git rev-parse HEAD)
for i in $(seq 1 20); do
  STATUS=$(curl -s \
    -H "Authorization: token $GITHUB_TOKEN" \
    https://api.github.com/repos/$OWNER/$REPO/commits/$SHA/status \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
  echo "检查 $i: $STATUS"
  if [ "$STATUS" = "success" ] || [ "$STATUS" = "failure" ] || [ "$STATUS" = "error" ]; then
    break
  fi
  sleep 30
done
```

## 5. 自动修复CI失败

当CI失败时,诊断并修复。此循环适用于任一认证方法。

### 步骤1:获取失败详情

**使用gh:**

```bash
# 列出此分支最近的工作流运行
gh run list --branch $(git branch --show-current) --limit 5

# 查看失败日志
gh run view <RUN_ID> --log-failed
```

**使用git + curl:**

```bash
BRANCH=$(git branch --show-current)

# 列出此分支的工作流运行
curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/$OWNER/$REPO/actions/runs?branch=$BRANCH&per_page=5" \
  | python3 -c "
import sys, json
runs = json.load(sys.stdin)['workflow_runs']
for r in runs:
    print(f\"运行 {r['id']}: {r['name']} - {r['conclusion'] or r['status']}\")"

# 获取失败作业日志(下载为zip,解压,读取)
RUN_ID=<run_id>
curl -s -L \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/actions/runs/$RUN_ID/logs \
  -o /tmp/ci-logs.zip
cd /tmp && unzip -o ci-logs.zip -d ci-logs && cat ci-logs/*.txt
```

### 步骤2:修复并推送

识别问题后,使用文件工具(`patch`、`write_file`)修复:

```bash
git add <修复的文件>
git commit -m "fix: 解决<检查名>中的CI失败"
git push
```

### 步骤3:验证

使用上面第4节的命令重新检查CI状态。

### 自动修复循环模式

当被要求自动修复CI时,遵循此循环:

1. 检查CI状态 → 识别失败
2. 读取失败日志 → 理解错误
3. 使用`read_file` + `patch`/`write_file` → 修复代码
4. `git add . && git commit -m "fix: ..." && git push`
5. 等待CI → 重新检查状态
6. 如仍失败则重复(最多3次,然后询问用户)

## 6. 合并

**使用gh:**

```bash
# Squash合并 + 删除分支(功能分支最干净)
gh pr merge --squash --delete-branch

# 启用自动合并(所有检查通过时合并)
gh pr merge --auto --squash --delete-branch
```

**使用git + curl:**

```bash
PR_NUMBER=<number>

# 通过API合并PR(squash)
curl -s -X PUT \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER/merge \
  -d "{
    \"merge_method\": \"squash\",
    \"commit_title\": \"feat: 添加用户认证 (#$PR_NUMBER)\"
  }"

# 合并后删除远程分支
BRANCH=$(git branch --show-current)
git push origin --delete $BRANCH

# 本地切换回main
git checkout main && git pull origin main
git branch -d $BRANCH
```

合并方法: `"merge"`(合并提交)、`"squash"`、`"rebase"`

### 启用自动合并(curl)

```bash
# 自动合并需要仓库在设置中启用。
# 这使用GraphQL API,因为REST不支持自动合并。
PR_NODE_ID=$(curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$OWNER/$REPO/pulls/$PR_NUMBER \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['node_id'])")

curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/graphql \
  -d "{\"query\": \"mutation { enablePullRequestAutoMerge(input: {pullRequestId: \\\"$PR_NODE_ID\\\", mergeMethod: SQUASH}) { clientMutationId } }\"}"
```

## 7. 完整工作流示例

```bash
# 1. 从干净的main开始
git checkout main && git pull origin main

# 2. 分支
git checkout -b fix/login-redirect-bug

# 3. (智能体用文件工具进行代码变更)

# 4. 提交
git add src/auth/login.py tests/test_login.py
git commit -m "fix: 修正登录后重定向URL

保留?next=参数,而不是总是重定向到/Dashboard。"

# 5. 推送
git push -u origin HEAD

# 6. 创建PR(根据可用性选择gh或curl)
# ... (见第3节)

# 7. 监控CI(见第4节)

# 8. 绿色时合并(见第6节)
```

## 有用的PR命令参考

| 操作 | gh | git + curl |
|--------|-----|-----------|
| 列出我的PR | `gh pr list --author @me` | `curl -s -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/repos/$OWNER/$REPO/pulls?state=open"` |
| 查看PR差异 | `gh pr diff` | `git diff main...HEAD`(本地)或`curl -H "Accept: application/vnd.github.diff" ...` |
| 添加评论 | `gh pr comment N --body "..."` | `curl -X POST .../issues/N/comments -d '{"body":"..."}'` |
| 请求审查 | `gh pr edit N --add-reviewer user` | `curl -X POST .../pulls/N/requested_reviewers -d '{"reviewers":["user"]}'` |
| 关闭PR | `gh pr close N` | `curl -X PATCH .../pulls/N -d '{"state":"closed"}'` |
| 检出他人的PR | `gh pr checkout N` | `git fetch origin pull/N/head:pr-N && git checkout pr-N` |
