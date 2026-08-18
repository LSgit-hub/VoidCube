# VoidCube 架构

本文描述当前已经落地的目标架构。仓库不再处于旧包迁移窗口：每项源码职责只有一个规范位置，历史包名不再提供导入、执行或发行版入口。

## 1. 运行时边界

VoidCube 的 Python 运行时只有一个产品包：`voidcube`。源码采用 `src` 布局，公开入口来自 `pyproject.toml`：

```text
voidcube -> voidcube.interfaces.cli:main
vc       -> voidcube.interfaces.cli:main
```

API-A Agent 的唯一回合执行器是 `voidcube.runtime.agent.runner`。Supervisor、Gateway 和 Memory 由规范包内的 infrastructure/system 组合点启动。根目录不存在 Python launcher；旧目录 `VoidCube_app`、`VoidCube_cli`、`VoidCube_core`、`agent`、`tools`、`systems` 已从生产源码删除，也不进入 wheel。

`memai` 是独立的记忆产品包，源码位于 `Mem/src/memai`；`plugins` 只包含插件清单和插件实现。Mem 插件通过 `voidcube.domain.contracts.memory` 与主应用交互，不反向导入旧包。

## 2. 目录职责

```text
src/voidcube/
├─ domain/                         稳定领域模型、策略和跨层 contracts
│  ├─ agent/                       回合状态、响应策略、上下文模型
│  ├─ session/                     会话身份与生命周期规则
│  ├─ tasks/                       任务族、治理和执行 profile
│  └─ contracts/                   ports、events、turn、execution、memory
├─ application/                    用例编排和跨端口状态
│  ├─ autonomous/                 daily_companion / auto_evolution
│  ├─ scheduling/                 turn、background、scheduled task
│  └─ sessions/                   新建、恢复、分支、标题
├─ runtime/                        进程级组装与 Agent 回合运行时
│  └─ agent/                       client bootstrap、prompt、tool turn、runner、执行期展示
├─ infrastructure/                 外部技术适配
│  ├─ config/                     配置、profile、环境与运行时路径
│  ├─ execution/                  terminal、process、container、sandbox
│  ├─ gateway/                    服务启动、HTTP、presence、内部 gateway
│  ├─ llm/                        请求、重试、流式传输和错误分类
│  ├─ persistence/                SQLite、文件、checkpoint、journal、scheduled outbox
│  ├─ providers/                  provider、鉴权、模型和凭据
│  └─ observability/              日志和运行观测
├─ interfaces/                     用户与外部系统接口
│  ├─ cli/                        launcher、commands、TUI、voice status
│  ├─ desktop/                    desktop control protocol
│  ├─ http/                       HTTP-facing adapters
│  └─ voice/                      录音、STT、TTS、声纹接口
├─ systems/                        可独立治理的产品系统
│  ├─ supervisor/                 观察、治理、伴侣和 UI projection
│  ├─ evolution_*                 candidate、authoring、evaluation
│  ├─ research_knowledge/         研究知识仓储
│  └─ self_cognition/             自我认知采集
└─ extensions/                     可发现、可禁用的扩展
   ├─ tools/                      tool registry、toolsets、具体工具
   ├─ skills/                     SKILL.md catalog、hub、guard、manager
   └─ plugins/                    manifest、manager、CLI adapter
```

`plugins/memory/mem` 和 `Mem/src/memai` 是包名之外的两个明确发布边界；它们不复制 `voidcube` 的领域规则。

## 3. 依赖规则

所有生产 Python 文件都必须从规范包导入。架构扫描器会拒绝以下入口：`VoidCube_app`、`VoidCube_cli`、`VoidCube_core`、顶层 `agent`、`tools`、`systems`，以及 `src.voidcube` 源码布局导入。

层级规则如下：

1. `domain` 只拥有可测试的规则、值对象和 ports；不得读取终端、网络、数据库或旧包。涉及外部数据的上下文展开位于 `runtime.agent.context_references`。
2. `application` 只编排用例和 ports；模型 API、CLI 渲染和具体存储由组合点注入。
3. `infrastructure` 实现 domain/application ports，并封装具体 provider、文件、数据库、进程和网络技术。
4. `systems` 组合 domain、application 与 infrastructure，拥有 Supervisor、evolution、research 等产品规则；系统之间通过 ports、事件或 gateway 通信。
5. `extensions` 通过 registry/manifest 提供工具、技能和插件；扩展可以使用 domain contracts 以及其所需的 infrastructure adapter，但不得导入历史包。
6. `interfaces` 是外部组合层，可以调用 application、systems、infrastructure 和 extensions；业务规则不得反向进入 CLI/TUI。
7. `runtime` 负责进程生命周期、Agent bootstrap 和回合状态编排；它不提供第二个公开产品入口。

每个公开能力只保留一个组合点：`voidcube.interfaces.cli.launcher`、`voidcube.interfaces.cli.application`、`voidcube.runtime.agent.runner`、各系统的 `runtime_assemblers`/service runtime，以及 extension manager。

## 4. 已完成的架构收拢

以下工作已经完成，不再建立兼容 facade 或迁移窗口：

- `VoidCube_app` 的 contracts、session、provider、configuration、persistence 和 application runtime 已分别归入 `domain`、`application`、`infrastructure`。
- 顶层 `agent` 的 domain 状态、LLM 传输、client bootstrap、prompt、tool turn 和 runner 已归入 `domain.agent`、`infrastructure.llm`、`runtime.agent`。
- `VoidCube_cli` 的 launcher、commands、handlers、chat、turn、TUI、配置和状态投影已归入 `interfaces.cli`；locale 资产也位于规范包内。
- Agent runner 使用的 spinner、tool preview、context-pressure 和 subagent 展示已归入 `runtime.agent`；CLI 仅从该执行期展示端组合。
- profile 路径管理位于 `infrastructure.config`；scheduled executor 只保留调度状态机，HTTP、SQLite outbox、超时配置和 provider 错误投影位于 infrastructure。
- 顶层 `tools` 的执行后端已归入 `infrastructure.execution`，工具定义和 toolset 已归入 `extensions.tools`。
- 顶层 `systems` 的 Supervisor、evolution、research、self-cognition 和 voice 实现已归入 `voidcube.systems`。
- 插件 manifest、manager、CLI adapter 和 Mem host 集成均使用 canonical API。
- 运行时动态工具发现只加载 `voidcube.*` 模块；没有 `src.*` 或旧包 fallback。
- 配置默认 toolset、CLI locale、Supervisor Web 资源和 Podman Containerfile 均使用 canonical 路径。

## 5. 验收与持续检查

本地验证必须使用项目虚拟环境：

```powershell
.venv\Scripts\python.exe scripts/python_architecture.py
.venv\Scripts\python.exe -m compileall -q src/voidcube plugins scripts
.venv\Scripts\python.exe -m pytest tests/test_packaging_contract.py -q
.venv\Scripts\python.exe scripts/build_wheel.py --outdir .test-tmp\canonical-wheel
```

`scripts/python_architecture.py` 检查绝对导入、动态模块名、根 CLI 反向依赖和源码布局导入。`scripts/build_wheel.py` 检查 wheel 文件与源码 parity、Mem 的旧包依赖、插件清单和运行资源。

模型、鉴权、请求协议、技能或打包边界发生变化时，还必须运行项目规定的集成策略扫描及对应契约测试。CI 的顺序是 `compileall -> architecture gate -> packaging contract -> wheel parity`。

## 6. 文件归属判断

1. 没有网络、终端、数据库时仍能运行的规则放 `domain`。
2. 编排一个用户用例放 `application`。
3. 适配具体外部技术放 `infrastructure` 或 `interfaces`。
4. 可发现、可替换、可禁用的能力放 `extensions`。
5. 可独立启停并拥有治理状态的产品能力放 `systems`。

不要按文件名中的 `runtime` 判断归属；只有进程生命周期和组合才属于 `runtime`。

## 7. 完成标准

- 新开发者只需查看 `voidcube.interfaces.cli` 即可找到 CLI 入口，查看 `voidcube.application` 即可找到用例编排。
- 生产源码、脚本和 wheel 中没有旧包运行时入口。
- 一个能力只有一个真实实现；没有旧模块 re-export、旧包 alias 或源码布局 fallback。
- `memai`、插件、voice、desktop、gateway 均保持清晰的可选边界。
- 架构 gate、编译、契约测试和 wheel parity 全部通过。

后续新增功能直接进入上述规范目录并更新对应 port/contract；不再新增迁移层、旧名称或兼容分支。
