# 发布与交付

## 发布前

1. 确认版本来自 `voidcube.version.__version__`，没有手工改 wheel 元数据。
2. 运行架构检查、文档契约、集成/打包测试和 `scripts/run_ci_tests.py`。
3. 使用 `scripts/build_wheel.py` 构建并检查 wheel；需要隔离验证时运行 `scripts/verify_clean_install.py`。
4. 执行 `git diff --check`，确认没有 `.env`、数据库、日志、构建目录或测试临时产物进入提交。

## 发行内容

发行包包含 `voidcube*`、`plugins*` 和 `memai*` 规范包及声明的资源。顶层旧包、测试目录和本地运行数据不属于 wheel。技能 `SKILL.md` 是可加载契约，变更它们时单独审查并运行技能 registry 测试。

## 回滚

代码回滚与运行数据回滚分开处理。先停止服务并保留 `runtime/memory/backups/`、outbox 和日志，再回退代码版本；不要用删除数据库来解决版本不匹配。恢复后运行 `doctor`、服务 health 检查和最小 smoke 测试。
