# CI 故障排除快速参考

常见 CI 失败模式以及如何从日志中诊断它们。

## 读取 CI 日志

```bash
# 使用 gh
gh run view <RUN_ID> --log-failed

# 使用 curl — 下载并解压
curl -sL -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/$GH_OWNER/$GH_REPO/actions/runs/<RUN_ID>/logs \
  -o /tmp/ci-logs.zip && unzip -o /tmp/ci-logs.zip -d /tmp/ci-logs
```

## 常见失败模式

### 测试失败

**日志中的特征：**
```
FAILED tests/test_foo.py::test_bar - AssertionError
E       assert 42 == 43
ERROR tests/test_foo.py - ModuleNotFoundError
```

**诊断：**
1. 从回溯中找到测试文件和行号
2. 使用 `read_file` 读取失败的测试
3. 检查是代码中的逻辑错误还是过时的测试断言
4. 查找 `ModuleNotFoundError` — 通常是 CI 中缺少依赖

**常见修复：**
- 更新断言以匹配新的预期行为
- 将缺少的依赖添加到 requirements.txt / pyproject.toml
- 修复不稳定的测试（添加重试、模拟外部服务、修复竞态条件）

---

### Lint / 格式化失败

**日志中的特征：**
```
src/auth.py:45:1: E302 expected 2 blank lines, got 1
src/models.py:12:80: E501 line too long (95 > 88 characters)
error: would reformat src/utils.py
```

**诊断：**
1. 读取提到的具体 file:line 行号
2. 检查是哪个 linter 在抱怨（flake8、ruff、black、isort、mypy）

**常见修复：**
- 在本地运行格式化工具：`black .`、`isort .`、`ruff check --fix .`
- 通过编辑文件修复具体的风格违规
- 如果使用 `patch`，确保匹配现有的缩进风格

---

### 类型检查失败（mypy / pyright）

**日志中的特征：**
```
src/api.py:23: error: Argument 1 to "process" has incompatible type "str"; expected "int"
src/models.py:45: error: Missing return statement
```

**诊断：**
1. 读取提到的行号处的文件
2. 检查函数签名和传递的内容

**常见修复：**
- 添加类型转换或转换
- 修复函数签名
- 作为最后手段添加 `# type: ignore` 注释（附带说明）

---

### 构建 / 编译失败

**日志中的特征：**
```
ModuleNotFoundError: No module named 'some_package'
ERROR: Could not find a version that satisfies the requirement foo==1.2.3
npm ERR! Could not resolve dependency
```

**诊断：**
1. 检查 requirements.txt / package.json 中缺少或不兼容的依赖
2. 比较本地与 CI 的 Python/Node 版本

**常见修复：**
- 将缺少的依赖添加到 requirements 文件
- 固定兼容版本
- 更新锁定文件（`pip freeze`、`npm install`）

---

### 权限 / 认证失败

**日志中的特征：**
```
fatal: could not read Username for 'https://github.com': No such device or address
Error: Resource not accessible by integration
403 Forbidden
```

**诊断：**
1. 检查工作流是否需要特殊权限（token 范围）
2. 检查密钥是否已配置（缺少 `GITHUB_TOKEN` 或自定义密钥）

**常见修复：**
- 在工作流 YAML 中添加 `permissions:` 块
- 验证密钥存在：`gh secret list` 或检查仓库设置
- 对于 fork PR：某些密钥按设计不可用

---

### 超时失败

**日志中的特征：**
```
Error: The operation was canceled.
The job running on runner ... has exceeded the maximum execution time
```

**诊断：**
1. 检查哪个步骤超时
2. 查找无限循环、挂起的进程或缓慢的网络调用

**常见修复：**
- 为特定步骤添加超时：`timeout-minutes: 10`
- 修复底层性能问题
- 拆分为并行作业

---

### Docker / 容器失败

**日志中的特征：**
```
docker: Error response from daemon
failed to solve: ... not found
COPY failed: file not found in build context
```

**诊断：**
1. 检查 Dockerfile 中失败的步骤
2. 验证引用的文件在仓库中存在

**常见修复：**
- 修复 COPY/ADD 命令中的路径
- 更新基础镜像标签
- 将缺少的文件添加到 `.dockerignore` 排除或从中移除

---

## 自动修复决策树

```
CI 失败
├── 测试失败
│   ├── 断言不匹配 → 更新测试或修复逻辑
│   └── 导入/模块错误 → 添加依赖
├── Lint 失败 → 运行格式化工具，修复风格
├── 类型错误 → 修复类型
├── 构建失败
│   ├── 缺少依赖 → 添加到 requirements
│   └── 版本冲突 → 更新固定版本
├── 权限错误 → 更新工作流权限（需要用户）
└── 超时 → 调查性能（可能需要用户输入）
```

## 修复后重新运行

```bash
git add <fixed_files> && git commit -m "fix: resolve CI failure" && git push

# 然后监控
gh pr checks --watch 2>/dev/null || \
  echo "轮询：curl -s -H 'Authorization: token ...' https://api.github.com/repos/.../commits/$(git rev-parse HEAD)/status"
```
