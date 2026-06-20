# 约定式提交快速参考

格式：`type(scope): description`

## 类型

| 类型 | 使用场景 | 示例 |
|------|------------|---------|
| `feat` | 新功能或能力 | `feat(auth): add OAuth2 login flow` |
| `fix` | Bug 修复 | `fix(api): handle null response from /users endpoint` |
| `refactor` | 代码重构，无行为变更 | `refactor(db): extract query builder into separate module` |
| `docs` | 仅文档 | `docs: update API usage examples in README` |
| `test` | 添加或更新测试 | `test(auth): add integration tests for token refresh` |
| `ci` | CI/CD 配置 | `ci: add Python 3.12 to test matrix` |
| `chore` | 维护、依赖、工具 | `chore: upgrade pytest to 8.x` |
| `perf` | 性能改进 | `perf(search): add index on users.email column` |
| `style` | 格式化、空白、分号 | `style: run black formatter on src/` |
| `build` | 构建系统或外部依赖 | `build: switch from setuptools to hatch` |
| `revert` | 撤销之前的提交 | `revert: revert "feat(auth): add OAuth2 login flow"` |

## 范围（可选）

代码库区域的简短标识符：`auth`、`api`、`db`、`ui`、`cli` 等。

## 破坏性变更

在类型后添加 `!` 或在页脚添加 `BREAKING CHANGE:`：

```
feat(api)!: change authentication to use bearer tokens

BREAKING CHANGE: API 端点现在需要 Bearer token 而不是 API key 头。
迁移指南：https://docs.example.com/migrate-auth
```

## 多行正文

在 72 字符处换行。使用项目符号列出多个变更：

```
feat(auth): add JWT-based user authentication

- 添加带输入验证的登录/注册端点
- 添加使用 argon2 密码哈希的 User 模型
- 为受保护路由添加认证中间件
- 添加带轮换的 token 刷新端点

Closes #42
```

## 关联 Issues

在提交正文或页脚中：

```
Closes #42          ← 合并时关闭 issue
Fixes #42           ← 同样效果
Refs #42            ← 引用但不关闭
Co-authored-by: Name <email>
```

## 快速决策指南

- 添加了新东西？ → `feat`
- 东西坏了你修好了？ → `fix`
- 改变了代码组织方式但没改变功能？ → `refactor`
- 只改了测试？ → `test`
- 只改了文档？ → `docs`
- 更新了 CI/CD 流水线？ → `ci`
- 更新了依赖或工具？ → `chore`
- 让东西变快了？ → `perf`
