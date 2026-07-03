# VoidCube CLI

 **大模型是智力，记忆是灵魂，Agent是躯体** —— VoidCube 三元架构理论

***

## 项目简介

### 三元架构模型

| 层次 | 组成 | 功能 | 特性 |
|------|------|------|------|
| **智力层（大脑）** | 大语言模型 | 推理、理解、生成、规划 | 可替换，不影响个体性 |
| **记忆层（灵魂）** | MemAI 持久化记忆系统 | 经验、知识、目标的持久化存储 | 跨会话、跨模型持久化 |
| **代理层（躯体）** | Agent 运行环境 + 双槽身体 | 工具执行、用户交互、自主任务、替身改进 | 可轮换与自进化 |
| **治理层（母体）** | Gateway / Supervisor / Execution | 内生驱动、任务列表治理、双泳道观测、身体切换执行 | 全天候后台治理 |

### 核心特性

- **轻量安装** — 核心依赖仅 9 个包，`pip install` 即装即用
- **快速配置** — 一行命令启动，配置 API Key 即可使用，预设模板一键部署
- **友好交互** — 基于 prompt_toolkit 的 REPL，支持历史补全、内置命令，自然语言操作
- **云端智能** — 调用 100+ 云端 LLM（Claude / GPT-4o / DeepSeek / Qwen 等），能力强劲无需本地算力
- **多环境执行** — Local / Docker / SSH / Modal / Singularity / Daytona
- **运维工具集** — 50+ 运维原语：服务管理、包管理、Docker、防火墙、日志、端口扫描、用户管理
- **安全优先** — 危险命令审批机制、敏感信息脱敏、路径安全校验、OSV 漏洞检查
- **多提供商** — 支持 100+ 云端 LLM 提供商，一键切换模型
- **记忆持久化** — MemAI 系统支持跨会话记忆，重启后自动恢复上下文
- **自进化能力** — 监督者全天候运行，Agent 通过自主任务通道拉取学习/改造任务
- **双槽隔离** — 主 CLI 用户交互走 `user_chat` 泳道，监督者后台任务走 `supervisor_task` 泳道；`AUTO` 当前只是监督者临时启停门控，不接管主 CLI
- **身体治理** — 双身体槽位、替身改进、probe、观察窗口与可回滚切换；真正 activate 新替身需用户同意（目标语义）

### 适用场景

- 自动化部署 Web 应用（LNMP、Docker、Node.js 等）
- 服务器安全加固与基线配置
- Docker 容器编排与管理
- 系统监控与日志分析
- Kubernetes 集群节点管理
- 定时任务与运维脚本编写

***

## 一键安装

### Windows 系统

```powershell
# 方式一：使用一键安装脚本（推荐）
powershell -c "Invoke-WebRequest https://gitee.com/LSgit-hub/voidcub-CLI/raw/main/install.bat -OutFile install.bat; .\install.bat"

# 方式二：从 Gitee 直接安装
pip install git+https://gitee.com/LSgit-hub/voidcub-CLI.git[all]

# 方式三：从源码安装
git clone https://gitee.com/LSgit-hub/voidcub-CLI
cd voidcub-CLI
.\install.bat
```

### Linux / macOS 系统

```bash
# 方式一：使用一键安装脚本（推荐）
curl -sSL https://gitee.com/LSgit-hub/voidcub-CLI/raw/main/install.sh | bash

# 方式二：从 Gitee 直接安装
pip install git+https://gitee.com/LSgit-hub/voidcub-CLI.git[all]

# 方式三：从源码安装
git clone https://gitee.com/LSgit-hub/voidcub-CLI
cd voidcub-CLI
./install.sh
```

### 安装选项

| 安装命令 | 说明 |
|---------|------|
| `pip install voidcube-agent` | 仅安装核心依赖 |
| `pip install voidcube-agent[all]` | 安装全部功能（推荐） |
| `pip install voidcube-agent[local]` | 核心 + 本地 LLM 支持 |
| `pip install voidcube-agent[image]` | 核心 + 图像生成 |
| `pip install voidcube-agent[voice]` | 核心 + 语音合成 |
| `pip install voidcube-agent[web]` | 核心 + Web 工具 |

### 离线安装（无网络环境）

```bash
# 1. 在有网络的机器上构建离线包
git clone https://gitee.com/LSgit-hub/voidcub-CLI
cd voidcub-CLI
pip install build
python build_offline_package.py

# 2. 将 dist/voidcube-offline-package.tar.gz 复制到目标机器

# 3. 在目标机器上离线安装
tar -xzf voidcube-offline-package.tar.gz
cd voidcube-offline-package
./install_offline.sh    # Linux/macOS
# 或
install_offline.bat     # Windows
```

---

## 系统配置详情

### Windows 系统

#### 前置要求

- Python 3.11 或更高版本
- pip 包管理器
- 推荐使用 [Python 官网](https://www.python.org/downloads/) 安装，确保勾选 "Add Python to PATH"

#### 2. 配置 API Key

VoidCube 需要配置云端 LLM 的 API Key 才能工作。

```powershell
# 方式一：在用户目录创建配置（推荐）
mkdir $env:USERPROFILE\.VoidCube

# 复制示例配置文件
# 从项目目录复制 .env.example 到 $env:USERPROFILE\.VoidCube\.env
# 然后编辑填入你的 API Key

# 方式二：直接在项目目录创建 .env
# 复制项目中的 .env.example 为 .env，然后编辑
```

配置文件示例（参考项目中的 `.env.example`）：

```env
# 推荐: OpenRouter (聚合多模型平台)
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=deepseek/deepseek-chat
OPENROUTER_API_KEY=sk-or-your-key-here

# 或使用 DeepSeek
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_MODEL=deepseek-chat
# DEEPSEEK_API_KEY=sk-your-key-here

# 或使用 通义千问
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_MODEL=qwen-plus
# DASHSCOPE_API_KEY=sk-your-key-here

# 终端配置
TERMINAL_ENV=local
TERMINAL_CWD=.
TERMINAL_TIMEOUT=120

# 调试模式
DEBUG=false
```

#### 3. 启动 VoidCube

```powershell
# 启动交互式对话
voidcube

# 或使用简写
vc
```

### Windows 常用配置

```env
# OpenRouter（推荐）
OPENROUTER_API_KEY=your-key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-3.5-sonnet

# DeepSeek
DEEPSEEK_API_KEY=sk-your-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 本地执行
TERMINAL_ENV=local
```

### Windows 注意事项

- Windows 默认使用 `local` 执行环境
- 首次运行可能需要允许 Python 通过防火墙
- 中文路径支持良好
- **重要**：`.env` 文件包含 API 密钥，不要提交到 Git！

***

## Linux 系统安装配置

### 前置要求

- Python 3.11 或更高版本
- pip 包管理器
- 推荐使用系统包管理器安装 Python（如 `apt install python3 python3-pip`）

### 安装步骤

#### 1. 安装 VoidCube

```bash
# 方式一：从 Gitee 直接安装（推荐）
pip install git+https://gitee.com/LSgit-hub/voidcub-CLI.git

# 方式二：从源码安装
git clone https://gitee.com/LSgit-hub/voidcub-CLI
cd voidcub-CLI
pip install -e .
```

#### 2. 配置 API Key

```bash
# 方式一：在用户目录创建配置（推荐）
mkdir -p ~/.VoidCube
cp .env.example ~/.VoidCube/.env
nano ~/.VoidCube/.env

# 方式二：直接在项目目录创建
cp .env.example .env
nano .env
```

配置文件参考项目中的 `.env.example`。

#### 3. 启动 VoidCube

```bash
# 启动交互式对话
voidcube

# 或使用简写
vc
```

### Linux 常用配置

```env
# OpenRouter（推荐）
OPENROUTER_API_KEY=your-key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-3.5-sonnet

# DeepSeek
DEEPSEEK_API_KEY=sk-your-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 远程 SSH 执行
TERMINAL_ENV=ssh
TERMINAL_SSH_HOST=your-server-ip
TERMINAL_SSH_USER=username

# Docker 执行
TERMINAL_ENV=docker
TERMINAL_DOCKER_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20
```

### Linux 高级配置

```bash
# Docker 环境配置
export TERMINAL_ENV=docker
export TERMINAL_DOCKER_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20
export TERMINAL_DOCKER_FORWARD_ENV='["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]'

# SSH 密钥认证
export TERMINAL_SSH_HOST=your-server.com
export TERMINAL_SSH_USER=admin
export TERMINAL_SSH_KEY=~/.ssh/id_rsa

# Modal 云函数
export TERMINAL_ENV=modal
export TERMINAL_MODAL_IMAGE=python:3.11
```

### Linux 注意事项

- Linux 默认使用 `local` 执行环境
- 如需远程操作服务器，可配置 `ssh` 模式
- Docker 模式需要本地安装 Docker
- 建议使用 tmux/screen 保持会话
- **重要**：`.env` 文件包含 API 密钥，不要提交到 Git！

***

## 可选功能扩展

```bash
# 本地 Web 搜索（BeautifulSoup）
pip install "voidcube-agent[local]"

# 图像生成
pip install "voidcube-agent[image]"

# 语音合成（Edge TTS，免费）
pip install "voidcube-agent[voice]"

# 全部可选功能
pip install "voidcube-agent[full]"
```

***

## 快速开始

### 1. 启动 VoidCube

```bash
voidcube
# 或简写
vc
```

启动后，主 CLI 始终服务用户交互。监督者后台链路按基线目标为全天候治理；当前实现仍保留 `AUTO` 作为临时启停开关，但它不限制主 CLI 输入，也不阻断用户与主 Agent 对话。

### 2. 开始对话

```
> 帮我部署 LNMP 环境
> 查看磁盘使用情况和运行中的服务
> 安装 Docker 并跑一个 nginx 容器
> 给服务器做安全基线加固
```

***

## 内置命令

| 命令        | 说明                  |
| --------- | ------------------- |
| `/help`   | 显示帮助信息              |
| `/model`  | 交互式切换模型（选供应商 → 选模型） |
| `/setup`  | 配置 API Key（首次使用）    |
| `/tools`  | 列出可用工具              |
| `/config` | 查看/修改配置             |
| `/clear`  | 清空会话历史              |
| `/quit`   | 退出                  |

***

## 运维工具集

VoidCube 内置 50+ 运维原语，覆盖日常运维全流程：

### 系统监控

| 工具               | 说明               |
| ---------------- | ---------------- |
| `system_info`    | 系统基本信息（OS、内核、架构） |
| `cpu_stats`      | CPU 使用率与负载       |
| `memory_stats`   | 内存使用统计           |
| `disk_usage`     | 磁盘空间占用           |
| `service_status` | 服务运行状态           |

### 服务与包管理

| 工具                                        | 说明       |
| ----------------------------------------- | -------- |
| `service_start / stop / restart`          | 服务生命周期管理 |
| `pkg_install / update / upgrade / remove` | 软件包管理    |

### 网络与安全

| 工具                               | 说明       |
| -------------------------------- | -------- |
| `ping`                           | 网络连通性测试  |
| `check_port / scan_ports`        | 端口检测与扫描  |
| `dns_lookup`                     | DNS 解析   |
| `firewall_status / allow / deny` | 防火墙规则管理  |
| `ssh_keygen`                     | SSH 密钥生成 |

### Docker

| 工具                                 | 说明         |
| ---------------------------------- | ---------- |
| `docker_ps / images`               | 容器与镜像列表    |
| `docker_run / stop / rm`           | 容器生命周期     |
| `docker_logs / exec`               | 日志查看与命令执行  |
| `docker_compose_up / compose_down` | Compose 编排 |

### 日志与用户

| 工具                                   | 说明           |
| ------------------------------------ | ------------ |
| `read_log`                           | 日志文件读取       |
| `journalctl`                         | systemd 日志查询 |
| `list_users / user_add / user_del`   | 用户管理         |
| `file_permissions / set_permissions` | 文件权限管理       |

***

## 预设部署模板

一行命令完成环境搭建，预设位于 `presets/` 目录：

| 预设                   | 说明            | 包含组件                                                 |
| -------------------- | ------------- | ---------------------------------------------------- |
| `lnmp`               | LNMP Web 环境   | Nginx + MySQL + PHP-FPM，开放 80/443                    |
| `docker-web`         | Docker 开发环境   | Docker Compose + 开发工具，开放 80/443/8080                 |
| `security-baseline`  | 安全基线加固        | fail2ban + ufw，允许 22/80/443，拒绝 23/3389               |
| `python-datascience` | Python 数据科学   | Jupyter + Pandas + NumPy + Matplotlib + Scikit-learn |
| `k8s-node`           | Kubernetes 节点 | kubeadm + kubelet + containerd，开放 K8s 端口             |
| `monitoring-stack`   | 监控体系          | Prometheus + Grafana + Node Exporter                 |
| `hardened-ssh`       | SSH 加固        | ed25519 密钥 + fail2ban，禁用密码认证                         |

使用示例：

```
> 用 lnmp 预设部署服务器环境
> 应用 security-baseline 预设加固这台机器
> 部署 monitoring-stack 监控体系
```

***

## 执行环境

VoidCube 支持多种命令执行后端，通过 `TERMINAL_ENV` 环境变量切换：

| 环境            | 说明             | 配置                         |
| ------------- | -------------- | -------------------------- |
| `local`       | 本机执行（默认）       | `TERMINAL_ENV=local`       |
| `docker`      | Docker 容器内执行   | `TERMINAL_ENV=docker`      |
| `ssh`         | 远程 SSH 执行      | `TERMINAL_ENV=ssh`         |
| `modal`       | Modal 云函数      | `TERMINAL_ENV=modal`       |
| `singularity` | Singularity 容器 | `TERMINAL_ENV=singularity` |
| `daytona`     | Daytona 开发环境   | `TERMINAL_ENV=daytona`     |

***

## 常见问题

### Q: 推荐什么云端模型？

| 场景   | 推荐模型                          | 提供商        |
| ---- | ----------------------------- | ---------- |
| 日常运维 | `anthropic/claude-3.5-sonnet` | OpenRouter |
| 复杂推理 | `openai/o3-mini`              | OpenRouter |
| 性价比  | `deepseek/deepseek-chat`      | DeepSeek   |
| 中文优化 | `qwen/qwen-plus`              | DashScope  |

### Q: 如何切换模型？

在对话中使用 `/model` 命令，或修改配置文件中的 `LLM_MODEL` 后重启。

### Q: API Key 从哪里获取？

- **OpenRouter**：[openrouter.ai/keys](https://openrouter.ai/keys) - 推荐，支持 100+ 模型
- **DeepSeek**：[platform.deepseek.com](https://platform.deepseek.com) - DeepSeek 系列
- **通义千问**：[dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) - Qwen 系列

### Q: 如何防止 API Key 被提交到 Git？

项目已经配置了 `.gitignore`，会自动忽略：
- `.env`
- `.env.local`
- `.env.*`（除了 `.env.example`）
- `.VoidCube/` 目录

请确保：
1. 永远不要编辑 `.env.example` 中的真实 API Key
2. 使用 `.env` 或 `.env.local` 来存储真实配置
3. 提交前用 `git status` 检查是否有意外的配置文件

***

## 项目结构

详细版请查看 [`docs/项目文件架构说明.md`](docs/项目文件架构说明.md)。

```
VoidCube/
├── voidcube.py             # 统一入口；自动拉起守护服务后委托完整 CLI
├── cli.py                  # 交互式终端会话入口
├── run_agent.py            # AIAgent 主编排器
├── pyproject.toml          # 打包配置，暴露 voidcube / vc 命令
├── config.yaml             # 项目默认配置
├── docs/                   # 架构与改造文档
│   ├── voidcube架构基线.md
│   ├── 全链条报告.md
│   ├── 内生驱动问题清单.md
│   ├── 内生驱动核心设计.md
│   ├── CLI展示与gateway双槽设计.md
│   └── 项目文件架构说明.md
├── VoidCube_core/          # 底层共享能力：常量、日志、状态、i18n
├── VoidCube_cli/           # CLI 子系统：命令分发、配置、Provider、UI、ops
├── agent/                  # Agent 内部能力：prompt、压缩、记忆、调度、展示
├── tools/                  # 工具层：注册、终端、文件、Web、浏览器、MCP、delegate
├── systems/                # 服务化母体：gateway、memory、supervisor、execution
├── Mem/                    # 独立长期记忆子项目（src/tests/docs/benchmarks）
├── plugins/                # 插件入口，当前以 memory 插件为主
├── presets/                # 运维预设模板
├── skills/                 # 技能包与引用资料
├── tests/                  # 主仓测试
├── containers/             # 容器相关文件
└── cache/logs/sessions/... # 运行态目录
```

当前架构的关键路径是：`CLI -> Agent -> Tools` 服务用户任务；`Supervisor -> 任务列表 -> Agent 自主任务通道 -> Mem` 服务学习与替身改进；`Gateway` 负责服务注册、活动事实与 `user_chat/supervisor_task` 双泳道观测。

***

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

项目地址：https://gitee.com/LSgit-hub/VoidCub
