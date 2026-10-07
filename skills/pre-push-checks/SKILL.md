---
name: pre-push-checks
description: 在推送、标记 PR 就绪或声称检查通过前，为 VoidCube 选择覆盖当前 diff 的最小本地验证，并识别必须运行的全量门禁。需要提交前检查时使用。
---

# 推送前检查

先看 `git diff --stat`、`git diff --check` 和变更路径，再选择检查。不要把未执行的 CI、外部服务或凭据验证写成通过。

## 路径到检查

- `src/`、`Mem/src/` 或跨模块运行时：使用仓库虚拟环境运行 `scripts/run_ci_tests.py`。
- `skills/` 的发现、索引、frontmatter 或同步：运行 `pytest tests/test_skill_registry.py -q`、相关技能同步/策略测试和全量门禁。
- 模型、鉴权、请求协议或打包：运行 `tests/test_integration_policy.py`、`tests/test_packaging_contract.py` 和全量门禁。
- 只改文档：运行 `git diff --check` 与受影响文档契约测试，核对命令和链接。
- 插件、CLI 或 UI：运行对应契约测试，并确认真实入口而不是只调用内部函数。

推送前再次检查工作树、远端分支和提交范围。报告确切命令、结果、跳过项和需要 CI 或真实凭据才能完成的验证。
