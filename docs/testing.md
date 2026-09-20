# 测试与验证

所有命令在仓库根目录执行，优先使用 `.venv`。

## 快速检查

```powershell
.venv\Scripts\python.exe scripts/python_architecture.py
.venv\Scripts\python.exe -m pytest tests/test_documentation_contract.py -q
.venv\Scripts\python.exe -m pytest tests/test_integration_policy.py tests/test_packaging_contract.py -q
```

## 全量门禁

```powershell
.venv\Scripts\python.exe scripts/run_ci_tests.py
```

该脚本运行 `pytest tests Mem/tests -q`。任何 `src/`、`plugins/` 或 `Mem/src/` 改动提交前都必须通过它；不能只运行目标测试文件。

## 技能 registry

涉及技能发现、索引或 registry 时额外运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_skill_registry.py -q
```

同时检查测试报告中的 `added/reparsed/reused/removed` 统计是否符合预期。

## 桌面和 wheel

桌面测试在 `desktop/` 执行：`npm run typecheck`、`npm test`、`npm run build`、`npm run test:e2e`。发布前运行 `scripts/build_wheel.py` 和 `scripts/verify_clean_install.py`，确认 wheel 不含退役包或敏感配置。
