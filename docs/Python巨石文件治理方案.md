# VoidCube Python 巨石文件治理方案

> 当前状态：Stage 0 至 Stage 8 已完成；三个 P0 边界已完成 owner 收口、生产调用者切换、旧逻辑清理和总验收，Windows adapter 继续保持 No-Go。
> 当前快照：2026-08-05。
> 文档定位：治理基线、目标边界、阶段退出条件和当前状态快照；不记录实施日志。

## 1. 方案定位

本方案治理 Python 代码中的责任聚合、反向依赖和状态所有权。目标不是单纯减少文件行数，而是让每个责任拥有可枚举的 owner，让共享能力可以脱离 CLI 或 Supervisor 独立测试，并为未来 Windows 前端保留稳定的共享应用边界。

产品形态保持不变：一个仓库、一套版本和共享核心；CLI 与未来 Windows 前端分别承担终端或桌面适配；两者共享 session、turn、工具、审批、模型配置、Memory、Gateway、Supervisor 和 Execution 能力，但不互相导入，也不复制业务状态机。

当前决策：先完成 Python 巨石治理和 Stage 8 验收，再重新评审 Windows 桌面端方案。Windows adapter、桌面依赖和新的传输协议不属于当前阶段。

## 2. 治理基线与原则

事实基线由当前源码、可加载包、边界测试和发行物契约共同构成。责任所有权和依赖方向是完成判断的主要依据，文件规模只用于发现趋势。

- **单一 owner**：状态、持久化、策略判断和外部副作用各有一个生产写入 owner；组合根只装配和协调。
- **显式边界**：跨模块调用使用明确的输入、输出、contract 或 port；不使用完整 host、隐式 `self`、`Any`、`getattr` 或 `hasattr` 代替接口。
- **行为等价**：迁移只改变实现位置，不顺带改变模型调用、记忆真相、Gateway 路由、执行互斥或 Auto 语义；行为变化另立设计和验收。
- **先切换、后清理**：生产调用者切换后删除旧实现、重复字段、失效参数、双写路径和无调用兼容分支，不保留永久委托壳。
- **依赖单向**：共享层不依赖前端；CLI 和未来 Windows adapter 只依赖共享层，不能互相导入。
- **可独立验证**：新 owner 不需要构造完整 CLI、Supervisor 或真实外部服务即可进行核心测试。
- **不以拆分制造新巨石**：不把完整 host 搬到新文件，不用 `helpers.py` 或无边界 `runtime.py` 重新聚合责任，不为硬性行数制造空壳模块。
- **退役集成零入口**：项目规定已退役的模型集成及其 API、Provider、OAuth、适配器、协议兼容、别名、回退、技能示例、市场缓存和隐藏入口均不得新增、恢复或保留。
- **文档保持当前态**：只维护有效约束、owner、阶段状态、退出条件和后续顺序；删除过期快照，不追加历史批次。

## 3. 目标架构

```text
VoidCube_cli（终端 adapter）       VoidCube_windows（未来桌面 adapter）
             \                         /
              \                       /
                    VoidCube_app
          （use case / session / turn / event / port）
                    |             |
                    v             v
              agent / tools   systems clients
                    \             /
                     v           v
                    VoidCube_core

systems.supervisor.supervisor（HTTP 装配）
                    |
                    v
 planning / endogenous / observation / UI projection
                    |
                    v
 stores / Execution / Memory / Gateway
```

### 3.1 稳定依赖规则

- 根 `cli.py` 只保留公开入口或短期兼容导出，包内生产代码不得反向导入它。
- `VoidCube_app`、`agent`、`systems` 和 `VoidCube_core` 不导入 `VoidCube_cli` 或未来 `VoidCube_windows`。
- CLI 与 Windows adapter 不共享 renderer、全局状态或私有设备对象。
- 领域和应用层不依赖 prompt_toolkit、Rich、DOM、pywebview、pystray 或 Windows API。
- route 只做验证、调用和响应映射；projector 只读快照并返回结构化结果；repository 只负责读写，不承担策略。
- 传输方式不能渗入 use case。CLI 可以进程内调用，未来 Windows 版的进程内或本机 BFF 方式另由 ADR 决定。

### 3.2 共享与隔离

| 能力 | canonical owner | 前端关系 |
| --- | --- | --- |
| 模型请求、上下文和工具循环 | `agent` | 共享 |
| session、turn、queue、cancel、approval、clarify | `VoidCube_app` | 共享 |
| Provider、模型解析和 canonical 配置 | `VoidCube_app` 及明确的基础配置模块 | 共享 |
| Memory、Gateway、Supervisor、Execution client | `systems` / `VoidCube_app` | 共享 |
| 结构化应用事件、错误和 artifact contract | `VoidCube_app.contracts` | 共享 |
| slash command、ANSI、Rich、prompt_toolkit | `VoidCube_cli` | CLI 独有 |
| Web DOM、托盘、通知、热键和窗口生命周期 | `VoidCube_windows` | Windows 独有 |

CLI 的 slash command 只是输入映射，不是共享 API；Windows 控件直接调用相同 use case，不模拟 slash command。

## 4. 当前治理范围

| 边界 | 当前判断 | 阶段 |
| --- | --- | --- |
| `run_agent.py` | 客户端启动、session persistence bootstrap 和 turn 内输出/持久化协调已由显式 runtime 与 ports 承担；会话循环保留为编排根并受增长护栏约束 | 已收口，Stage 8-A |
| `systems/memory/memory_service.py` | 数据库装配/schema/migration、Memory use case 和 HTTP adapter 已分离，服务类仅作 FastAPI/uvicorn 组合根 | 已收口，Stage 8-B |
| `VoidCube_cli/main.py` | 参数解析、命令注册、配置初始化和 dispatch 已拆为独立 entrypoint owner，入口仅作组合根 | 已收口，Stage 8-C |
| `systems/gateway/internal_gateway.py` | registry、auth、session lease、projection 和 route 边界可观察，尚未达到升级条件 | P1，观察 |
| `VoidCube_cli/app.py`、Planning、Endogenous | 主要 owner 已收口，维持防回迁护栏 | 已收口 |
| Supervisor UI runtime 与静态资源 | UI 状态、SSE、身份代理、媒体生命周期和资源加载已有明确 owner | 已收口 |

P0 按单一责任边界依次推进，不并行迁移三个 Stage 8 文件。Gateway 和薄配置导出不因文件规模单独升级，只有满足本节末尾的升级条件才重新分级。

升级条件至少满足一项：责任无法枚举并阻塞依赖收口；跨责任方法持续扩张且频繁变化；缺陷集中在跨责任区域；核心逻辑无法脱离完整系统测试。

## 5. 治理完成判据

治理完成必须同时满足结构、行为和交付三类条件：

1. 生产代码不反向导入根 `cli.py`；共享层不导入任一前端。
2. CLI 与未来 Windows adapter 不互相导入，且不各自实现 session/turn/tool/approval 状态机。
3. `VoidCube_app` 持有无界面的共享 use case、状态转换、结构化事件和端口；前端只负责输入、渲染和平台集成。
4. 每类共享状态和并发原语只有一个生产写入 owner；没有旧字段与新字段双写。
5. Planning、Endogenous 和 Supervisor UI 通过显式 service、projector、route 或 port 协作，不通过无限扩张的 Mixin 或完整 host 传递状态。
6. 静态 UI 资源使用包资源加载，源码运行与 wheel 运行使用同一 canonical 资源，不恢复内嵌 HTML fallback。
7. 新 owner 可以脱离完整宿主进行单元测试，受影响的 CLI、服务、UI 和发行物行为保持等价。
8. 生产调用者已切换；旧实现、失效参数、重复入口和迁移兼容分支已删除。
9. 相关测试、架构依赖检查、源码编译、smoke、wheel 契约和退役集成扫描通过。

行数只能作为趋势信号。不得以缩短方法、增加 dataclass、创建新模块或保留永久委托壳单独宣告阶段完成，也不得制造新的超大聚合文件。

## 6. 已完成阶段基线

以下只保留每个阶段的当前有效结论，不记录实施过程：

| 阶段 | 状态 | 当前结论 |
| --- | --- | --- |
| Stage 0 | 已完成 | 基线、依赖方向、P0 增长护栏、打包契约、退役扫描和可重复验证入口已建立。 |
| Stage 1 | 已完成 | 根 `cli.py` 已成为薄入口；共享配置、Provider 和 Gateway 基础能力已归位。 |
| Stage 2 | 已完成 | Supervisor UI 静态资源和主要只读 projector 已外移，源码与 wheel 使用统一资源契约。 |
| Stage 3 | 已完成 | 共享 session/turn use case、状态 owner、结构化事件和无 CLI 依赖的 adapter contract 已建立。 |
| Stage 4 | 已完成 | Planning 的持久化、投影/策略、任务状态、review/recovery 和执行交接已有显式 owner。 |
| Stage 5 | 已完成 | Endogenous 主要流水线已组件化，Engine 保持 facade 和 runtime state 的单一写入 owner。 |
| Stage 6 | 已完成 | CLI TUI、语音、后台任务、自主组件和 Supervisor UI route/lifecycle 已按 runtime/port 收口。 |
| Stage 7 | 已完成 | 全量回归、smoke、发行物、退役集成和性能基线已完成，次级巨石已重新分级。 |

## 7. Stage 8：次级巨石 owner 收口

Stage 8 只处理三个当前 P0 边界，顺序固定为 A → B → C。每个子阶段都必须完成生产调用者切换和旧逻辑清理，不能只增加新模块。

### Stage 8-A：`run_agent.py`

目标是形成可独立测试的 Agent 初始化/客户端生命周期或会话准备责任边界，并使会话循环通过显式 runtime、输出和持久化 ports 协作。不得把整个 `AIAgent` 原样搬到另一个聚合文件，也不得复制已有 `VoidCube_app` turn contract。

当前状态：已完成。客户端凭证解析、primary client bootstrap、会话 ID、session DB 初始登记、checkpoint、turn 内中间持久化、截断/失败/中断结果和 `SessionPersistence` 组合均由显式 runtime 或 ports 承担；会话循环只保留生命周期编排。

必须满足：

- 输入、输出、配置/凭证、模型客户端、工具、上下文、session persistence 和输出事件边界可枚举；
- 生产调用者使用新 owner，Agent 生命周期和会话状态不在旧类与新组件双写；
- 新 owner 不接收完整 `AIAgent`、CLI 或 Supervisor 作为无边界 context；
- 旧初始化路径、失效参数、重复客户端创建和无调用兼容分支在切换后删除。

退出条件：Agent 生命周期或会话准备可脱离完整 CLI 测试；turn、工具事件、取消、恢复、输出和持久化语义无变化；依赖图没有新增反向边或聚合巨石。

### Stage 8-B：Memory Service

当前状态：已完成。`MemoryDatabaseBootstrap` 统一连接、schema、legacy migration、备份和 subsystem setup；`MemoryApplicationService` 持有可脱离 HTTP 的 use case，`http_adapter.py` 持有路由组合，`MemoryService` 仅作 FastAPI/uvicorn 组合根。

目标是分离数据库连接/装配、schema 与 migration owner，使 HTTP route 和 memory use case 只通过明确服务或 repository 调用，同时保持 recall、governance、backup、maintenance 和现有 Memory 真相边界。

必须满足：

- schema/migration 有唯一 owner，服务启动不会由多个入口重复决定数据库真相；
- repository、domain/use case、maintenance 和 HTTP adapter 的责任可单独测试；
- route 不承载迁移、长流程或策略；调用者不直接操作内部数据库对象；
- 生产调用者完成切换后删除重复初始化、旧参数和兼容路径，不改变既有数据语义。

退出条件：Memory 核心 use case 可脱离 HTTP 服务测试；迁移、召回、治理和维护行为等价；源码与发行物的 Memory 入口一致。

### Stage 8-C：`VoidCube_cli/main.py`

当前状态：已完成。parser 注册、启动初始化、provider/session/operation/management handler 和 dispatch 已迁移到独立 entrypoint 模块，`main.py` 只保留 parser/dispatch 组合入口。

目标是将入口收口为参数解析、命令注册、配置/环境初始化、dispatch 和退出码映射的薄组合根。

必须满足：

- 命令实现位于明确的 handler/service，不在 `main.py` 聚合业务流程；
- 注册表、解析器和 dispatch 使用显式 context/protocol，不传递完整 CLI host；
- 启动、帮助、参数错误、未知命令、profile/env 初始化和退出码语义保持不变；
- 生产导入切换后删除旧 handler、重复注册和双路径兼容分支。

退出条件：`main.py` 只负责入口装配和结果映射；命令路由可以脱离完整 CLI 测试；CLI 包不重新成为共享配置、Provider 或 Gateway owner。

### Stage 8 总验收

状态：已完成。三个子阶段均已满足退出条件；相关回归、架构依赖检查、生产编译、Gateway/Memory/Supervisor smoke、wheel 契约、source-to-artifact parity 和退役集成扫描均已纳入验收。Windows adapter 仍不在本阶段范围内。

## 8. 每个阶段的固定工作方式

每个阶段只围绕一个完整责任执行以下流程：

1. 明确责任、输入、输出、状态 owner 和外部副作用。
2. 为既有行为补最小 characterization test，并先建立目标边界。
3. 切换所有生产调用者，再删除旧实现、重复字段、失效参数和无调用兼容分支。
4. 运行 owner 测试、受影响链路、架构护栏、production compileall、smoke 和 `git diff --check`；涉及模型、鉴权、请求协议、技能或打包时，追加退役扫描和 wheel source-to-artifact parity。

以下情况不算完成：新模块仍接收完整 host；旧类和新组件双写；原方法只变成永久委托壳；使用 `Any`/`getattr` 模拟接口；长期保留两个导入路径；将多个责任汇总到新的通用大文件。

验证数量、具体命令、临时失败、性能测量和提交过程不写入本文，放在 CI、提交说明或交付报告中。本文只在阶段状态、owner、边界或退出条件变化时覆盖当前快照。

## 9. 双发行与 Windows Go/No-Go

CLI 和未来 Windows 应用共享版本、配置 schema、数据迁移、repository、use case 和领域事件；平台依赖、资源和入口分别隔离。CLI 发行物不依赖 Windows GUI 包，Windows 入口不导入 `VoidCube_cli`。

重新评审 Windows adapter 前必须满足：

- Stage 8 总验收通过，三个 P0 不再存在跨责任巨型方法；
- `VoidCube_app` 已是稳定的无界面共享应用层，CLI contract tests 不依赖 slash command 或 ANSI；
- session、turn、tool、approval、clarify、Memory、Gateway、Supervisor 和 Execution 的 owner 与代码一致；
- 静态资源、wheel、平台依赖和共享事件契约满足双发行约束；
- 退役集成在活跃代码、可加载技能和 wheel 中保持零入口，且没有迁移遗留的大规模双路径兼容层。

当前判定：**No-Go**。Stage 8 已完成，但 Windows adapter 仍需单独完成 ADR、共享应用层 contract 和平台边界评审；在该评审通过前不实现 `VoidCube_windows`，不引入桌面依赖，不重新决定后台传输或进程模型。

## 10. 当前快照与下一步

当前快照：Stage 8 总验收已完成。`run_agent.py`、Memory Service 和 `VoidCube_cli/main.py` 的生产调用者已切换到明确 owner，旧初始化、迁移、路由和入口聚合逻辑已清理；P0 增长护栏冻结当前编排根规模并禁止新增超大方法。

下一步：重新评审 Windows adapter 的 ADR、共享 `VoidCube_app` contract、平台资源/依赖隔离和进程边界。评审通过前保持 No-Go，不实现 `VoidCube_windows`。
