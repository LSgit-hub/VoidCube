# VoidCube

VoidCube 是一个本地运行的 Python 智能体系统。API-A 负责 CLI 对话，Supervisor 负责治理、调度和 UI 投影，MemAI 负责长期记忆，API-B 负责后台规划与复核；获准的后台任务由员工代理执行并回写结果。

## 当前边界

- `daily_companion` 是默认日常模式；`auto_evolution` 由 `/auto` 开启，`/auto-q` 收口并返回日常模式。
- API-A 只处理用户对话和一次性回合。API-B 不直接执行工具副作用，获准任务进入员工队列。
- `memory.db` 位于 `VOIDCUBE_HOME/runtime/memory/`，由 MemAI 独占；其他 SQLite 文件由各自领域 owner 管理。
- Provider 通过 `/api` 配置。API-A、API-B 和员工代理共享 Provider 池，但模型和凭据引用分别保存。
- Gateway 是生命周期、健康、presence 和外部管理入口，不是 Memory CRUD 的必经总线。

## 安装与运行

项目需要 Python 3.14。开发环境建议使用仓库虚拟环境：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[all,dev]"
.venv\Scripts\python.exe -m voidcube
```

运行 `voidcube doctor` 检查依赖和配置，运行 `voidcube /api`（交互命令）配置 Provider。用户运行时配置位于 `VOIDCUBE_HOME/config.yaml`，默认目录为 `~/.VoidCube`；凭据放在 `VOIDCUBE_HOME/.env` 或受支持的凭据存储中，不要提交真实密钥。

后台服务由 CLI 管理：

```powershell
.venv\Scripts\python.exe -m voidcube serve start
.venv\Scripts\python.exe -m voidcube serve status
.venv\Scripts\python.exe -m voidcube serve stop
```

架构边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。日常运行和恢复步骤见 [docs/operations.md](docs/operations.md)，测试见 [docs/testing.md](docs/testing.md)，故障排查见 [docs/troubleshooting.md](docs/troubleshooting.md)。

## 目录

```text
src/voidcube/       VoidCube 唯一运行时包
Mem/src/memai/      MemAI 持久化记忆领域和服务
plugins/             插件清单与 Agent 侧适配器
desktop/             Electron 桌面容器
skills/              可加载技能；每个技能的 SKILL.md 是运行契约
tests/               根项目测试（开发验证）
Mem/tests/           MemAI 测试
```

## 质量门禁

```powershell
.venv\Scripts\python.exe scripts/python_architecture.py
.venv\Scripts\python.exe -m pytest tests/test_documentation_contract.py -q
.venv\Scripts\python.exe scripts/run_ci_tests.py
```

模型、鉴权、协议、技能或打包改动还要运行 `tests/test_integration_policy.py`、`tests/test_packaging_contract.py`，并按 [docs/release.md](docs/release.md) 验证 wheel。桌面端开发说明见 [desktop/README.md](desktop/README.md)，插件契约见 [plugins/README.md](plugins/README.md)，MemAI 说明见 [Mem/README.md](Mem/README.md)。

## 许可

本项目采用 [MIT License](LICENSE)。
