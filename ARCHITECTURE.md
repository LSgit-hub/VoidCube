# VoidCube 架构与目录规划

本文是 VoidCube 的职责地图和后续迁移基线。目标不是把所有文件机械地搬到新目录，而是让每个模块只有一个含义、依赖方向可检查、旧入口不会继续被误认为主逻辑。

## 1. 当前问题

当前代码同时使用了三种命名维度：

- `VoidCube_app` 表示一部分应用服务，但也包含配置、Provider、鉴权、运行时和领域契约。
- `VoidCube_cli` 表示 CLI，但还包含调度器、自治执行、语音运行时、配置管理和应用组装。
- `VoidCube_core` 只有少量基础设施，却使用了最容易被误解的泛化名称。
- 顶层 `agent`、`systems`、`tools`、`plugins`、`Mem` 又是另一套按能力划分的包，导致调用者很难判断“谁拥有业务规则、谁只是适配器”。

因此，问题的根因是职责边界混合，而不是目录层级不够深。

## 2. 目标架构

推荐最终采用一个规范 Python 包名 `voidcube`，把入口、业务、适配器和扩展分开。`src` 布局是目标状态；迁移期间可以先在现有包内建立同名子包，不要求一次性改完全部导入。

```text
VoidCube/
├─ src/voidcube/                       # 唯一规范运行时代码包
│  ├─ domain/                          # 纯领域模型和跨层协议，不访问网络/终端/文件
│  │  ├─ agent/                        # 回合、上下文、消息、模型选择的领域规则
│  │  ├─ session/                      # 会话、历史、身份、目标
│  │  ├─ execution/                    # 工具调用、审批、执行状态、事件
│  │  └─ contracts/                    # ports、events、artifacts 等稳定协议
│  ├─ application/                     # 用例编排；依赖 domain，不依赖 CLI/UI
│  │  ├─ chat/                         # 单轮、连续对话、流式回合
│  │  ├─ autonomous/                   # daily_companion / auto_evolution
│  │  ├─ scheduling/                   # turn/task 调度
│  │  └─ sessions/                     # 新建、恢复、分支、标题、回滚
│  ├─ runtime/                         # 进程级运行时组装和生命周期
│  │  ├─ agent/                        # API-A agent executor
│  │  ├─ supervisor/                   # API-B/supervisor worker
│  │  └─ bootstrap.py                  # 唯一依赖注入和启动组装点
│  ├─ infrastructure/                  # 外部世界适配器
│  │  ├─ providers/                    # OpenAI-compatible、模型和鉴权
│  │  ├─ persistence/                 # session DB、文件、日志、缓存
│  │  ├─ memory/                       # MemAI/Mem 插件适配器
│  │  ├─ gateway/                      # 服务发现、HTTP、活动/任务泳道
│  │  ├─ execution/                    # terminal/browser/container 等执行后端
│  │  └─ config/                       # 配置文件、环境变量、profile
│  ├─ interfaces/                      # 面向用户或外部系统的入口
│  │  ├─ cli/                          # parser、command、TUI、renderer
│  │  ├─ http/                         # FastAPI/health/admin API
│  │  ├─ voice/                        # 录音、STT、TTS、声纹适配
│  │  └─ desktop/                      # Electron 只保留前端壳和协议
│  ├─ systems/                         # 可独立启停的产品系统
│  │  ├─ supervisor/                   # 伴侣观察、治理、投影
│  │  ├─ evolution/                    # authoring/candidate/evaluation
│  │  ├─ research/                     # research_knowledge
│  │  └─ voice/                        # 若需要独立服务则从 interfaces/voice 拆出
│  └─ extensions/                      # 可发现、可替换、可禁用的扩展
│     ├─ tools/                        # 工具定义 + backend + policy
│     ├─ skills/                       # SKILL.md 和技能元数据（内容优先）
│     └─ plugins/                      # plugin manifest、hooks、注册器
├─ packages/memai/                     # 独立记忆产品/库，保持自己的测试和发布边界
├─ desktop/                            # Electron 工程；不放 Python 业务代码
├─ tests/                              # 跨层集成和入口契约测试
├─ scripts/                            # 一次性维护、构建、扫描脚本
├─ docs/                               # 架构、协议、运行手册
├─ voidcube.py                         # 极薄 launcher，仅调用 interfaces.cli
└─ pyproject.toml
```

`Mem` 不应成为 `voidcube` 内部的普通目录。它有自己的存储、索引、模型配置和测试，适合保持为 `packages/memai`（或独立仓库）；主应用只通过 `domain/contracts` 定义的 memory port 访问它。

## 3. 现有目录到目标目录的映射

| 当前目录/文件 | 目标归属 | 处理原则 |
|---|---|---|
| `VoidCube_core/constants.py`, `time.py`, `redaction.py`, `logging.py` | `infrastructure/persistence`、`infrastructure/config` 或 `shared` | 按职责拆分；禁止继续增加新的 `core` 杂项 |
| `VoidCube_core/state.py` | `infrastructure/persistence/session_db.py` | 它是存储实现，不是核心领域 |
| `VoidCube_app/contracts/*`、`interaction_contract.py`、`tool_events.py` | `domain/contracts` | 作为跨层稳定协议，保持依赖最少 |
| `VoidCube_app/application.py`、`session_lifecycle.py`、`turn_scheduler.py` | `application/*` | 应用用例和编排，不应依赖 TUI/CLI |
| `VoidCube_app/config.py`、`provider_auth.py`、`runtime_provider.py` | `infrastructure/config`、`infrastructure/providers` | 配置与外部 Provider 适配器分离 |
| `agent/*` | `runtime/agent` + `domain/agent` | `models`、状态和策略进 domain；HTTP/重试/流传输进 runtime/infrastructure |
| `VoidCube_cli/app.py` | `interfaces/cli/application.py` | 只做 CLI 组装；禁止继续扩展为 5000 行总控文件 |
| `VoidCube_cli/entrypoint_*`、`command_handlers/*` | `interfaces/cli/commands` | parser、命令、handler 分三级 |
| `VoidCube_cli/tui_*`、`chat_*`、`display*` | `interfaces/cli/tui` | 仅负责展示和输入，不承载业务规则 |
| `VoidCube_cli/ops/*` | `interfaces/cli/ops` | CLI 到 gateway/system 的薄适配器 |
| `VoidCube_cli/daemon_runtime.py` | `infrastructure/gateway` | Gateway/Memory/Supervisor 的启动、停止和进程所有权状态；不依赖 TUI |
| `tools/*` | `extensions/tools` + `infrastructure/execution` | 工具协议/策略与实际执行后端分开 |
| `skills/*` | `extensions/skills` | 技能内容不混入 Python 运行时代码 |
| `plugins/*` | `extensions/plugins` | 统一 manifest、生命周期和 hook 协议 |
| `systems/*` | `systems/*` | 以独立系统/服务为单位，系统内部再按 domain/application/adapters 分层 |
| `voidcube.py`, `cli.py`, `run_agent.py` | `interfaces` / `runtime` 入口 | 根文件只保留兼容转发，最终只保留一个公开 launcher |

## 4. 依赖规则

依赖方向固定为：

```text
interfaces  ->  application  ->  domain
runtime     ->  application  ->  domain
infrastructure  ->  domain contracts
systems     ->  application + domain + infrastructure ports
extensions  ->  domain contracts（由 bootstrap 注册）
```

硬规则：

1. `domain` 不得导入 `VoidCube_cli`、`rich`、`httpx`、数据库驱动或操作系统执行器。
2. `application` 不得导入 TUI、终端颜色、CLI parser；需要外部能力时只接受 port/protocol。
3. `interfaces` 不直接读取数据库或环境变量，所有状态通过 application/infrastructure service 获取。
4. `tools` 不反向调用 CLI；工具事件通过 `domain.contracts.events` 发布。
5. `systems` 之间不互相深度导入，通过事件、port 或 gateway 通信。
6. 每个目录只能有一个公开组装点：`runtime/bootstrap.py`、`interfaces/cli/application.py`、各 system 的 `composition.py`。

## 5. 名称决策

- `VoidCube_core`：**退役名称**。先停止新增文件，再按职责迁移；不建立新的 `core.utils`。
- `VoidCube_app`：**过渡名称**。它不是最终的“应用大杂烩”；其中的 contracts、use cases、providers、config 必须拆开。
- `VoidCube_cli`：**过渡名称**。最终含义只应是命令行接口；agent、scheduler、autonomous 的业务实现移出。当前阶段的命令入口实现位于 `VoidCube_cli/entrypoints`；已有的 `VoidCube_cli/commands.py` 是动态斜杠命令注册模块，不与入口层混用。
- `agent`：保留为内部语义，但最终放在 `voidcube/domain/agent` 和 `voidcube/runtime/agent`，避免成为顶层第二个产品包。
- `tools`、`skills`、`plugins`：统一放在 `extensions` 语义下；其中执行后端仍属于 infrastructure。

## 6. 分阶段迁移

### 阶段 0：冻结边界（当前即可做）

- 把本文件作为架构基线。
- 新代码禁止使用 `VoidCube_core`；禁止在 `VoidCube_cli.app` 增加业务逻辑。
- 在代码审查中检查上述依赖规则。
- 记录公开入口：`voidcube:main`、`vc:main`，其余根级脚本标为内部入口。

### 阶段 1：低风险建立语义子包

- 在现有包内建立 `VoidCube_app/contracts`、`VoidCube_app/use_cases`、`VoidCube_app/adapters`、`VoidCube_cli/entrypoints`、`VoidCube_cli/tui` 等目录。
- 只做移动和导入更新，每组移动后运行对应测试。
- 旧模块只保留一行显式转发，并标注删除版本；不要保留两套实现。

### 阶段 2：拆分最大文件

- 优先拆 `VoidCube_cli/app.py`（当前约 4700 行）：启动、交互循环、会话、自治、展示分别移出。
- 再拆 `VoidCube_cli/api_config.py`、`tools_config.py` 和 `agent/auxiliary_client.py`。
- 每次拆分新增针对 use case 的测试，而不是继续增加 facade 的分支。

### 阶段 3：统一规范包名

- 创建 `src/voidcube`，以真实实现替换过渡包。
- `voidcube.py`、`cli.py` 仅保留兼容入口，发布一个明确的弃用周期后删除。
- 更新 `pyproject.toml` 的 package discovery、entry points、coverage source 和文档。

### 阶段 4：插件化和可选服务

- 为 tool/skill/plugin 定义 manifest 和版本化 protocol。
- 用 entry points 或显式 registry 加载扩展；扩展不得通过隐式 import 修改全局状态。
- `MemAI`、voice、desktop、gateway 作为可选能力或独立服务打包，主应用只依赖 ports。

## 7. 判断文件放在哪里

问三个问题即可：

1. 没有网络、终端、数据库时仍能运行吗？能，放 `domain`。
2. 它是在编排一个用户用例吗？是，放 `application`。
3. 它是在适配具体外部技术吗？是，放 `infrastructure` 或 `interfaces`；若可插拔，再放 `extensions`。

不要按“文件名里有 runtime 就放 runtime”判断，`runtime` 只表示进程生命周期和组装；业务规则仍应回到 domain/application。

## 8. 验收标准

- 新开发者只看 `interfaces/cli` 就能找到命令入口，只看 `application` 就能找到用例。
- `domain` 的测试不需要启动 CLI、Gateway 或真实 Provider。
- 每个扩展可以单独禁用、测试和打包。
- 仓库中只有一个真实实现；旧路径只有明确的兼容转发，并有删除日期。
- 模型、鉴权、请求协议、技能或打包改动仍需运行项目规定的退役集成扫描和相关测试。
