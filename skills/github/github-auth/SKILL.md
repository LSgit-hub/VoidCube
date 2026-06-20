---
name: github-auth
description: 使用git(通用可用)或gh CLI为智能体设置GitHub认证。涵盖HTTPS令牌、SSH密钥、凭据助手和gh auth — 带有检测流程自动选择正确方法。
version: 1.1.0
author: Voidcube Agent
license: MIT
metadata:
  VoidCube:
    tags: [GitHub, Authentication, Git, gh-cli, SSH, Setup]
    related_skills: [github-pr-workflow, github-code-review, github-issues, github-repo-management]
---

# GitHub认证设置

此技能设置认证,以便智能体可以处理GitHub仓库、PR、问题和CI。它涵盖两条路径:

- **`git`(始终可用)** — 使用HTTPS个人访问令牌或SSH密钥
- **`gh` CLI(如果已安装)** — 更丰富的GitHub API访问,认证流程更简单

## 检测流程

当用户要求你处理GitHub时,首先运行此检查:

```bash
# 检查可用工具
git --version
gh --version 2>/dev/null || echo "gh未安装"

# 检查是否已认证
gh auth status 2>/dev/null || echo "gh未认证"
git config --global credential.helper 2>/dev/null || echo "无git凭据助手"
```

**决策树:**
1. 如果`gh auth status`显示已认证 → 很好,所有操作使用`gh`
2. 如果`gh`已安装但未认证 → 使用下面的"gh auth"方法
3. 如果`gh`未安装 → 使用下面的"仅git"方法(无需sudo)

---

## 方法1:仅Git认证(无gh,无sudo)

这适用于任何安装了`git`的机器。无需root权限。

### 选项A:HTTPS与个人访问令牌(推荐)

这是最可移植的方法 — 到处可用,无需SSH配置。

**步骤1:创建个人访问令牌**

告诉用户访问: **https://github.com/settings/tokens**

- 点击"Generate new token (classic)"
- 给它一个名称,如"VoidCube-agent"
- 选择范围:
  - `repo`(完整仓库访问 — 读取、写入、推送、PR)
  - `workflow`(触发和管理GitHub Actions)
  - `read:org`(如果处理组织仓库)
- 设置过期时间(90天是个好默认值)
- 复制令牌 — 不会再次显示

**步骤2:配置git存储令牌**

```bash
# 设置凭据助手以缓存凭据
# "store"保存到~/.git-credentials明文(简单、持久)
git config --global credential.helper store

# 现在执行一个触发认证的测试操作 — git会提示输入凭据
# Username: <他们的github用户名>
# Password: <粘贴个人访问令牌,不是他们的GitHub密码>
git ls-remote https://github.com/<他们的用户名>/<任意仓库>.git
```

输入凭据一次后,它们会被保存并在所有未来操作中重用。

**替代方案:cache助手(凭据从内存过期)**

```bash
# 在内存中缓存8小时(28800秒),而不是保存到磁盘
git config --global credential.helper 'cache --timeout=28800'
```

**替代方案:直接在远程URL中设置令牌(每个仓库)**

```bash
# 在远程URL中嵌入令牌(完全避免凭据提示)
git remote set-url origin https://<用户名>:<令牌>@github.com/<所有者>/<仓库>.git
```

**步骤3:配置git身份**

```bash
# 提交必需 — 设置名称和邮箱
git config --global user.name "他们的名字"
git config --global user.email "他们的邮箱@example.com"
```

**步骤4:验证**

```bash
# 测试推送访问(现在应该无需任何提示即可工作)
git ls-remote https://github.com/<他们的用户名>/<任意仓库>.git

# 验证身份
git config --global user.name
git config --global user.email
```

### 选项B:SSH密钥认证

适合偏好SSH或已设置密钥的用户。

**步骤1:检查现有SSH密钥**

```bash
ls -la ~/.ssh/id_*.pub 2>/dev/null || echo "未找到SSH密钥"
```

**步骤2:如需要生成密钥**

```bash
# 生成ed25519密钥(现代、安全、快速)
ssh-keygen -t ed25519 -C "他们的邮箱@example.com" -f ~/.ssh/id_ed25519 -N ""

# 显示公钥供他们添加到GitHub
cat ~/.ssh/id_ed25519.pub
```

告诉用户在以下位置添加公钥: **https://github.com/settings/keys**
- 点击"New SSH key"
- 粘贴公钥内容
- 给它一个标题,如"VoidCube-agent-<机器名>"

**步骤3:测试连接**

```bash
ssh -T git@github.com
# 预期: "Hi <用户名>! You've successfully authenticated..."
```

**步骤4:配置git对GitHub使用SSH**

```bash
# 自动将HTTPS GitHub URL重写为SSH
git config --global url."git@github.com:".insteadOf "https://github.com/"
```

**步骤5:配置git身份**

```bash
git config --global user.name "他们的名字"
git config --global user.email "他们的邮箱@example.com"
```

---

## 方法2:gh CLI认证

如果安装了`gh`,它会在一步中处理API访问和git凭据。

### 交互式浏览器登录(桌面)

```bash
gh auth login
# 选择: GitHub.com
# 选择: HTTPS
# 通过浏览器认证
```

### 基于令牌的登录(无头/SSH服务器)

```bash
echo "<他们的令牌>" | gh auth login --with-token

# 通过gh设置git凭据
gh auth setup-git
```

### 验证

```bash
gh auth status
```

---

## 无gh使用GitHub API

当`gh`不可用时,你仍可以使用`curl`和个人访问令牌访问完整的GitHub API。这是其他GitHub技能实现其回退的方式。

### 为API调用设置令牌

```bash
# 选项1:导出为环境变量(首选 — 使其远离命令)
export GITHUB_TOKEN="<令牌>"

# 然后在curl调用中使用:
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user
```

### 从Git凭据提取令牌

如果git凭据已配置(通过credential.helper store),可以提取令牌:

```bash
# 从git凭据存储读取
grep "github.com" ~/.git-credentials 2>/dev/null | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|'
```

### 助手:检测认证方法

在任何GitHub工作流开始时使用此模式:

```bash
# 首先尝试gh,回退到git + curl
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  echo "AUTH_METHOD=gh"
elif [ -n "$GITHUB_TOKEN" ]; then
  echo "AUTH_METHOD=curl"
elif [ -f ~/.VoidCube/.env ] && grep -q "^GITHUB_TOKEN=" ~/.VoidCube/.env; then
  export GITHUB_TOKEN=$(grep "^GITHUB_TOKEN=" ~/.VoidCube/.env | head -1 | cut -d= -f2 | tr -d '\n\r')
  echo "AUTH_METHOD=curl"
elif grep -q "github.com" ~/.git-credentials 2>/dev/null; then
  export GITHUB_TOKEN=$(grep "github.com" ~/.git-credentials | head -1 | sed 's|https://[^:]*:\([^@]*\)@.*|\1|')
  echo "AUTH_METHOD=curl"
else
  echo "AUTH_METHOD=none"
  echo "需要先设置认证"
fi
```

---

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| `git push`要求密码 | GitHub已禁用密码认证。使用个人访问令牌作为密码,或切换到SSH |
| `remote: Permission to X denied` | 令牌可能缺少`repo`范围 — 用正确范围重新生成 |
| `fatal: Authentication failed` | 缓存凭据可能过期 — 运行`git credential reject`然后重新认证 |
| `ssh: connect to host github.com port 22: Connection refused` | 尝试通过HTTPS端口的SSH: 在`~/.ssh/config`中为`Host github.com`添加`Port 443`和`Hostname ssh.github.com` |
| 凭据不持久 | 检查`git config --global credential.helper` — 必须是`store`或`cache` |
| 多个GitHub账号 | 在`~/.ssh/config`中为每个主机别名使用不同密钥的SSH,或每个仓库的凭据URL |
| `gh: command not found` + 无sudo | 使用上面的仅git方法1 — 无需安装 |
