# VoidCube

大模型是智力，记忆是灵魂，Agent 是躯体。

VoidCube 是面向本地开发与服务器运维的 Python Agent 系统。它以 CLI 为用户入口，使用 Gateway 连接 Memory、Supervisor 和 Execution，把用户对话、工具执行、长期记忆、自主治理与身体升级拆成明确边界。

当前模型请求统一使用 OpenAI-compatible Chat Completions。Provider 配置只描述模型、Base URL、凭据和明确支持的调用选项，不进行消息协议探测或隐式协议切换。

## 当前架构

| 层 | 现役组件 | 职责 |
| --- | --- | --- |
| 智力 | API-A / API-B 模型槽 | API-A 服务用户与自主任务执行；API-B 服务记忆抽取与治理判断 |
| 灵魂 | Memory Service + MemAI | Tier 1 原始轮次、Tier 2 结构化长期记忆、身份经历和治理历史 |
| 躯体 | Agent + Tools + Body runtime | 对话编排、工具调用、候选身体、probe、切换和回滚 |
| 母体 | Gateway + Supervisor + Execution | 服务路由、双泳道观测、内生驱动、治理与受控副作用 |

两条主要链路为：

```text
用户 -> CLI -> API-A Agent -> Tools -> 用户结果

Supervisor -> API-B 判断 -> 治理转交
  -> API-A 自主执行组件 -> Mem 回写 -> Supervisor 后续复核
```

Gateway 使用两个互不覆盖的活动泳道：

- `user_chat`：主 CLI 的用户会话与工具调用。
- `supervisor_task`：Supervisor 已转交给 API-A 自主执行组件的任务。

自主链路门控默认关闭。`/auto` 临时启用内生驱动、治理复核和当前 CLI 内嵌的自主执行组件；`/auto-q` 停用这些周期任务并中断当前自主任务。Gateway、Memory、Supervisor 和用户主 CLI 不随门控停用。

## 环境要求

- Python 3.11 或更高版本
- pip
- 可用的 OpenAI-compatible 推理端点，或受支持的本地模型端点
- 对应工具后端的本地依赖，例如 Docker、SSH 或 Podman（仅在选择该后端时需要）

## 安装

从源码安装：

```bash
git clone https://gitee.com/LSgit-hub/voidcub-CLI.git
cd voidcub-CLI
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[all]"
```

发行包安装选项：

| 命令 | 内容 |
| --- | --- |
| `pip install voidcube-agent` | CLI、Agent 和默认守护服务依赖 |
| `pip install "voidcube-agent[local]"` | 增加本地 Web 解析依赖 |
| `pip install "voidcube-agent[image]"` | 增加图像处理依赖 |
| `pip install "voidcube-agent[voice]"` | 增加语音合成依赖 |
| `pip install "voidcube-agent[web]"` | 增加 Web 服务依赖 |
| `pip install "voidcube-agent[all]"` | 安装全部可选能力 |

## 配置

运行交互式配置向导，创建 API-A Provider 并将其设为活动 Provider：

```bash
voidcube api
```

需要为 Memory 和 Supervisor 单独配置 API-B 时，在向导中选择“记忆系统模型配置”。API-A 与 API-B 可以指向同一供应商，但配置归属和运行语义保持分离。

配置和凭据的 canonical 位置为：

```text
VOIDCUBE_HOME/config.yaml   Provider、模型、工具和运行设置
VOIDCUBE_HOME/.env          API Key 等 secret
```

未设置 `VOIDCUBE_HOME` 时，默认目录是用户目录下的 `.VoidCube`。项目根 `.env` 只作为源码开发回退；不要提交真实密钥。`.env.example` 仅列出现役凭据和环境设置，不负责选择活动模型。

常用配置命令：

```bash
voidcube api             # 新增或修改 API-A/API-B 配置
voidcube model           # 在已配置 Provider 中切换模型
voidcube config          # 查看配置
voidcube config edit     # 编辑 canonical config.yaml
voidcube doctor          # 检查配置、Provider 与运行态
```

## 启动与服务

```bash
voidcube
# 或
vc
```

交互启动器会按 `Gateway -> Memory -> Supervisor` 确保基础服务可用，然后进入完整 CLI。Execution 不单独启动 daemon，而是挂载在 Supervisor 进程中并注册到 Gateway。

生命周期命令：

```bash
voidcube status
voidcube status --full
voidcube serve start
voidcube serve stop
```

无需守护服务的单次查询可使用：

```bash
voidcube chat -q "检查当前目录的项目结构"
```

## 常用命令

顶层命令以 `voidcube --help` 为准：

| 命令 | 用途 |
| --- | --- |
| `voidcube api` | 配置推理 Provider 和 API-A/API-B |
| `voidcube model` | 切换已配置的 Provider/模型 |
| `voidcube status` | 查看服务状态 |
| `voidcube doctor` | 运行配置与运行态诊断 |
| `voidcube memory setup\|status` | 初始化或查看 canonical Mem |
| `voidcube body ...` | 通过 Gateway Executor 管理身体生命周期 |
| `voidcube sessions ...` | 管理会话历史 |
| `voidcube tools` / `voidcube mcp` | 管理工具和 MCP 服务 |
| `voidcube logs` | 查看运行日志 |

交互会话中的核心 slash 命令包括：

| 命令 | 用途 |
| --- | --- |
| `/help` | 显示当前会话命令 |
| `/api` / `/model` | 配置或切换模型 |
| `/tools` / `/config` | 查看工具或配置 |
| `/auto` / `/auto-q` | 临时启用或停用自主链路 |
| `/clear` / `/quit` | 清理当前显示或退出 |

## 执行后端

默认后端是 `local`。推荐在 `VOIDCUBE_HOME/config.yaml` 中设置 `terminal.backend`，也可用 `TERMINAL_ENV` 做进程级覆盖。

| 后端 | 说明 |
| --- | --- |
| `local` | 在本机执行（默认） |
| `docker` / `podman` | 在容器环境执行 |
| `ssh` | 在远程 SSH 主机执行 |
| `modal` | 在 Modal 环境执行 |
| `singularity` | 在 Singularity 容器执行 |
| `daytona` | 在 Daytona 环境执行 |

高风险命令仍受 terminal guard 和 approval 约束。后端切换不会绕过审批、路径检查或身体治理。

## 记忆与身体

Agent 的默认 `mem` Provider 只通过 Gateway 调用 canonical Memory Service，不创建第二套本地记忆库。完成轮次异步写入 Tier 1；统一 `/recall` 同时查询近期 Tier 1 与活跃 Tier 2，并按相关性、时间、去重和字符预算返回证据。“刚才/刚刚/方才”类即时回忆会优先近期 Tier 1，并避免旧长期摘要压过本轮上下文。

MemAI 始终绑定到仓库共享的 `Mem/src`，不跟随活动身体槽切换。服务启动会验证实际导入源，并把绑定审计写入 `VOIDCUBE_HOME/runtime/memory/mem-source-binding.json`。

身体升级必须经过：候选物化、probe、Governor 审查、用户明确同意、激活和观察窗口。自主改进不能直接覆盖活动身体，失败时按已验证 Git lineage 回滚。

## 项目结构

```text
VoidCube/
├─ voidcube.py          统一安装入口与守护服务引导
├─ cli.py               交互会话协调
├─ run_agent.py         API-A Agent 主编排器
├─ config.yaml          从 DEFAULT_CONFIG 生成的 Body/probe 基线
├─ VoidCube_cli/        CLI、配置、Provider、认证和 UI
├─ agent/               请求、响应、上下文、记忆接入和工具调度
├─ tools/               工具注册、安全边界和执行后端
├─ systems/             Gateway、Memory、Supervisor、Execution
├─ Mem/                 独立 MemAI 领域包
├─ plugins/             运行时插件入口
├─ skills/              可加载技能
├─ presets/             运维预设
├─ tests/               主仓测试
├─ scripts/             构建与隔离安装验证
└─ docs/                现役架构与工程文档
```

仓库根 `config.yaml` 不参与用户配置加载；它仅供身体物化和 probe 使用，并由 `python scripts/sync_repo_config.py` 生成。用户设置始终写入 `VOIDCUBE_HOME/config.yaml`。

文档入口见 [docs/README.md](docs/README.md)，稳定系统边界见 [docs/voidcube架构基线.md](docs/voidcube架构基线.md)，开发与发布检查见 [docs/开发与验证.md](docs/开发与验证.md)。

## 开发与验证

```bash
python -m pip install -e ".[all,dev]"
python -m pytest -m smoke -q
python -m pytest -q
python -m pytest Mem/tests -q
python scripts/build_wheel.py
```

涉及模型、鉴权、请求协议、技能或打包时，还必须运行：

```bash
python -m pytest tests/test_integration_policy.py tests/test_packaging_contract.py -q
```

构建入口会清理根目录的 setuptools 中间产物，核对 wheel 与当前源码，并拒绝已退役集成重新进入源码、技能或发行包。完整分层见 [docs/开发与验证.md](docs/开发与验证.md)。

## License

MIT
