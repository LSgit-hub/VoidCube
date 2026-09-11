# VoidCube Repository Rules

1. 完成阶段任务后给出合理的下一步建议。
2. 修改文件主逻辑后，删除已经失效的代码、参数和冗余的兼容分支，不要让后续会话把旧兼容逻辑重新当成主逻辑（如果兼容为回退拖地方案可保留）。
3. 涉及模型、鉴权、请求协议、技能或打包的改动完成前，必须运行退役集成扫描和相关测试；活跃代码、可加载技能与 wheel 应保持零入口。任何 `src/`、`plugins/`、`Mem/src/` 代码改动，**提交前必须运行全量门禁 `scripts/run_ci_tests.py`**（等价 `pytest tests Mem/tests -q`），不得只跑目标测试文件——只跑目标用例曾漏掉被同批改动破坏的其它测试。
4. 测试使用项目虚拟环境。
5. 涉及技能发现、索引或 registry 机制的改动，提交前必须运行固定自检：`pytest tests/test_skill_registry.py`（含热路径读文件强守卫与耗时基准），并核对变更检测统计（added/reparsed/reused/removed）符合预期；性能回归不得靠临时脚本，一律以入库测试为准。
6. 提交纪律：提交必须**路径限定**（`git add -- <paths>`），禁止裸 `git add -A` / `git add .`；提交信息必须描述本次实际改动，不得只提其中一类文件。禁止把"技能新增"与"产品代码修复"混入同一个提交，否则会破坏变更可追溯性并让 review 漏掉代码改动。
   - 已由仓库级守卫强制：`.githooks/pre-commit`（通过 `git config core.hooksPath .githooks` 启用）。暂存集合同时包含 `skills/` 与 `src/`、`Mem/src/` 时会被拒绝提交。
   - 确需合并提交时使用 git 的显式逃生口 `git commit --no-verify`；停用守卫用 `git config --unset core.hooksPath`。
   - 该守卫只拦截"技能 × 代码"混合这一确定性规则；提交信息与改动范围是否一致仍需人工把关。
