# VoidCube Windows 桌面端方案

> 状态：后置候选方案，暂停实施。  
> 编制日期：2026-07-29。  
> 适用平台：Windows 10/11，目标运行时为项目当前规定的 Python 3.14。
> 前置基线：先完成 [Python 巨石文件治理方案](./Python巨石文件治理方案.md) 的共享应用层和 Go/No-Go 门槛；达到门槛后在同一仓库实现独立 Windows adapter，不直接按旧阶段开工。

## 1. 决策摘要

若 Python 治理完成后重新评审仍决定建设 Windows 桌面端，候选架构是 **轻量原生宿主 + 现有 Web UI + 现有后端服务**，不重写业务后端。当前文字只保留候选设计，不代表已批准实施。

核心决策如下：

1. 使用 `pywebview` 承载 Edge WebView2，桌面宿主保持 Python 技术栈。
2. 复用 Gateway、Memory、Supervisor 和 Execution 的现有边界，桌面宿主不复制服务实现。
3. 复用 `VoidCube_cli.ops.serve` 的服务生命周期能力，但先把其中可复用逻辑整理为无终端输出、可返回结构化结果的接口。
4. 桌面端第一版使用原生标题栏；无边框窗口在 DPI、多显示器、缩放、拖拽和可访问性验证完成后再评估。
5. 现有小屋作为 `Home`，后续增加面向 API-A 日常工作的 `Work`；两者共享后端状态，不把完整开发工作流堆进小屋单屏。
6. Web UI 与 Supervisor 继续使用 HTTP + SSE。桌面 bridge 只提供必须的 Windows 原生能力，不成为第二套业务 API。
7. CLI 和 Supervisor 的 Python 巨石治理是桌面端前置工作，不与桌面壳并行实施。治理完成后，桌面和 CLI 才基于已形成的会话、工具事件、审批等非渲染边界重新设计集成。
8. 首版不做音频 ducking、自动更新、无边框窗口和完整 Work 工作区，避免扩大首个可交付版本。
9. 小屋静态资源等价拆分与 UI 安全基线是桌面壳发布前置条件，避免为兼容内联脚本而长期放宽 CSP。
10. Windows adapter 使用 `VoidCube_app` 的共享 use case 和事件，不导入 `VoidCube_cli`，不复制 session/turn/tool/approval 逻辑。

该方案的目标不是把网页简单装进窗口，而是先建立可靠的 Windows 宿主，再逐步补齐优于终端的信息架构和交互能力。

## 2. 当前基础与缺口

### 2.1 可直接复用的能力

- `systems/supervisor/ui_runtime.py` 已提供小屋、任务卡片、状态面板、语音控制和 SSE 实时更新。
- Supervisor 已提供 `/ui`、`/ui/state`、`/ui/events`、语音事件和多类控制接口。
- `VoidCube_cli/ops/serve.py` 已实现 Gateway `6000`、Memory `6001`、Supervisor `6002` 的启动顺序、PID 文件、健康检查、状态和停止。
- Gateway、Memory、Supervisor、Execution 已有明确服务边界；Execution 仍按架构基线挂载在 Supervisor 进程内，不新增 Executor daemon。
- CLI 已有流式响应、工具执行、会话和终端交互逻辑，可作为 Work 事件协议的行为来源。

### 2.2 当前不能直接视为桌面产品的部分

- `systems/supervisor/ui_runtime.py` 当前超过 10,000 行，HTML、CSS 和 JavaScript 内嵌在 Python 字符串中，不适合继续叠加 Work 页面。
- 根目录 `cli.py` 当前超过 10,000 行，输入、会话、渲染、工具事件和生命周期耦合较重。
- 小屋主要是 Supervisor/星子控制面，不包含完整的 API-A 对话时间线、Markdown、工具状态、diff、审批和附件工作流。
- Supervisor UI 存在多类写操作；绑定 `127.0.0.1` 不能单独替代桌面会话授权、Origin 校验和 CSRF 防护。
- 小屋当前包含内联 CSS、内联 JavaScript、动态 HTML 和远程媒体 iframe；严格 CSP、内容净化和媒体来源白名单尚未形成。
- 项目尚未声明 `pywebview`、`pystray`、`pywin32` 等桌面依赖，也没有 Windows 安装器、WebView2 Runtime 探测和桌面资源打包契约。
- 当前项目限定 Python 3.14。桌面依赖及其二进制轮子必须先做兼容性验证，不能假设在该版本上均可安装。

## 3. 目标、非目标与原则

### 3.1 目标

- 提供可安装、可启动、可恢复、可退出的 Windows 桌面应用。
- 直接复用现有小屋，在较小改动下获得桌面窗口和托盘体验。
- 保持 CLI 可独立使用，并允许 CLI 与桌面端同时连接同一组后端服务。
- 将桌面 Work 接入可恢复的 `VoidCube_app` Runtime，不让 WebView、Windows adapter 或 Supervisor 复制 API-A 会话所有权。
- 逐步建立比纯终端更清晰的对话、工具、diff、审批和后台任务展示。
- 桌面端故障不破坏后台服务、用户会话、配置和运行数据。
- 限制新增依赖和代码体积，优先抽取现有能力而非复制实现。

### 3.2 首版非目标

- 不重写 Gateway、Memory、Supervisor 或 Execution。
- 不把后端服务嵌入 WebView GUI 线程。
- 不一次性重写小屋为大型前端框架项目。
- 不在第一版替代 CLI 的全部高级能力。
- 不首发无边框窗口、音频 ducking、跨设备同步或自动更新。
- 不把通用 shell、任意文件访问或后端管理能力暴露给 JavaScript bridge。

### 3.3 实施原则

- **单一状态所有者**：服务、会话、任务和审批状态仍由现有后端拥有，UI 只投影状态。
- **结构化事件优先**：Work 页面消费稳定事件，不解析 ANSI 终端输出。
- **故障隔离**：窗口关闭或渲染进程崩溃不能直接导致核心服务退出。
- **安全默认值**：仅监听回环地址、写操作需要桌面会话凭证、bridge 使用最小白名单。
- **渐进迁移**：先建立契约和测试，再移动代码；迁移完成后删除失效入口和重复分支。
- **可测量交付**：每个阶段都有明确验收条件，不以“窗口能打开”代表桌面端完成。

## 4. 目标架构

```text
Windows
┌────────────────────────────────────────────────────────────┐
│ VoidCube Desktop Host                                      │
│ ├─ SingleInstanceGuard     named mutex + activate IPC      │
│ ├─ ServiceController       复用 ops.serve 的结构化接口      │
│ ├─ WebViewWindow           Edge WebView2                   │
│ ├─ TrayController          显示/隐藏/停止/退出              │
│ ├─ DesktopSession          UI 会话票据与 bridge 白名单       │
│ ├─ NotificationAdapter     Phase 2                         │
│ ├─ HotkeyAdapter           Phase 2                         │
│ └─ SessionObserver         Phase 2                         │
└─────────────────────┬──────────────────────────────────────┘
                      │ HTTP + SSE（业务）
                      │ pywebview bridge（少量原生能力）
┌─────────────────────▼──────────────────────────────────────┐
│ Supervisor Web App                                         │
│ ├─ Home：现有小屋、星子、语音、Auto、状态                  │
│ └─ Work：会话、回答、工具、diff、审批、附件                 │
└─────────────────────┬──────────────────────────────────────┘
                      │ Supervisor 同源 BFF / Gateway 调用边界
┌─────────────────────▼──────────────────────────────────────┐
│ Gateway 6000 │ Memory 6001 │ Supervisor + Execution 6002  │
│              │ VoidCube_app Runtime（部署方式由 ADR 决定） │
└────────────────────────────────────────────────────────────┘
```

### 4.1 进程边界

桌面宿主是独立进程，只负责：

- 保证桌面 UI 单实例；
- 检查并确保后端服务可用；
- 创建 WebView 窗口和托盘；
- 提供受限的 Windows 原生集成；
- 展示启动失败、服务断开和恢复状态。

Gateway、Memory、Supervisor 继续是独立后台进程。CLI 和 Windows adapter 复用同一 `VoidCube_app` 应用 contract。Phase 3 开始前再用 ADR 决定 Windows 版是在宿主进程内创建应用 runtime，还是连接独立本机应用宿主；该选择不改变共享 use case、事件和 session repository，也不恢复独立 Executor daemon。

### 4.2 建议目录

```text
VoidCube_windows/
├─ __init__.py
├─ desktop/
│  ├─ __init__.py
│  ├─ app.py                 桌面入口与编排
│  ├─ single_instance.py     mutex 与第二实例激活
│  ├─ service_controller.py  服务所有权和结构化状态
│  ├─ window.py              WebView 创建与窗口状态
│  ├─ tray.py                托盘及明确命令
│  ├─ bridge.py              最小原生 bridge
│  ├─ settings.py            桌面设置
│  └─ startup_view.py        启动/错误/恢复页面
└─ ...

VoidCube_app/                 两个前端共享，无 UI/Windows 依赖
├─ contracts/
├─ runtime/
├─ services/
└─ app.py

systems/supervisor/
├─ ui_runtime.py             UI API 和状态装配，逐步缩小
└─ web/
   ├─ index.html
   └─ assets/
      ├─ app.css
      ├─ app.js
      ├─ api_client.js
      ├─ house.js
      ├─ voice.js
      └─ auto.js
```

第一轮只创建实际需要的文件，不能为了匹配目录图生成空模块。

## 5. 桌面生命周期设计

### 5.1 启动顺序

```text
进程启动
  -> 获取 Windows named mutex
  -> 若已有实例：向第一实例发送 activate，当前进程退出
  -> 加载桌面设置与运行时路径
  -> 获取服务启动互斥锁
  -> 读取服务启动前快照
  -> ensure Gateway -> Memory -> Supervisor
  -> 记录各服务 adopted / started / failed 所有权
  -> 等待 Supervisor UI readiness
  -> 建立一次性桌面 UI 会话
  -> 创建窗口和托盘
  -> 加载 /ui；失败时显示本地启动页
```

单实例必须先于服务启动和窗口创建。仅依靠 PID 文件不够可靠，因为 PID 可复用，异常退出也可能留下陈旧文件。

### 5.2 服务控制器契约

不要让桌面端通过前后两次 `status_all()` 猜测服务所有权。应在 `ops.serve` 中形成可复用的结构化接口，例如：

```python
@dataclass(frozen=True)
class ServiceEnsureResult:
    name: str
    action: Literal["adopted", "started", "restarted", "failed"]
    pid: int | None
    healthy: bool
    error: str | None = None
```

要求：

- `ensure_services()` 返回每项操作的真实结果，不直接打印终端文本。
- 现有 CLI 命令只负责格式化这些结果。
- 同一时间只有一个 CLI 或桌面进程执行服务启动/停止编排。
- 服务启动锁必须有超时和持有者诊断，不能无限等待。
- 不复制第二套 `Popen`、端口检测、PID 或健康检查实现。

### 5.3 服务所有权

| 启动前状态 | 桌面动作 | 桌面记录 | “退出桌面端” | “停止 VoidCube” |
| --- | --- | --- | --- | --- |
| 服务已健康 | 复用 | `adopted` | 不停止 | 用户明确确认后停止 |
| 服务未运行 | 启动 | `started` | 默认仍不停止 | 停止 |
| PID 存在但不健康 | 走现有恢复策略 | `restarted` 或 `failed` | 不做隐式清理 | 显式停止/恢复 |
| 端口被未知进程占用 | 拒绝接管 | `failed` | 无影响 | 不终止未知进程 |

即使服务由本次桌面会话启动，“退出桌面端”也默认只退出宿主。后台服务是否停止必须是用户选择，避免桌面端与 CLI 并用时误杀服务。

### 5.4 窗口、托盘与退出语义

- 点击窗口关闭：隐藏到托盘。
- 托盘“打开 VoidCube”：显示并聚焦现有窗口。
- 托盘“重载界面”：只重载 WebView，不重启服务。
- 托盘“服务状态”：展示三项服务健康和日志位置。
- 托盘“停止 VoidCube”：二次确认后调用统一停止接口。
- 托盘“退出桌面端”：关闭 WebView、热键和托盘，不隐式停止服务。
- Windows 注销/关机：释放宿主资源，不执行耗时重启，也不强制杀死独立服务。

托盘首版保持命令少而明确。模式切换、语音和业务设置继续在 Web UI 内完成，避免两个控制面状态不一致。

### 5.5 断线和恢复

- 启动期使用本地内置页面显示 `starting / ready / failed`，不要让用户看到浏览器连接错误页。
- 运行中每隔合理间隔检查 Supervisor 健康；SSE 自身使用有上限的指数退避重连。
- Supervisor 恢复后自动回到最近的 Home/Work 路由和窗口状态。
- 后端连续失败后停止高频重试，显示日志入口和“重试”命令。
- 桌面宿主不得自动删除数据库、配置、PID 目录或用户会话数据。

## 6. Web UI 演进

### 6.1 信息架构

桌面端采用两个一级工作面：

- `Home`：现有小屋、星子状态、语音、提醒、Auto 和系统概览。
- `Work`：API-A 会话、消息、工具、diff、审批、后台任务和输入区。

Home 保持有生命感和空间感；Work 采用安静、紧凑、适合长时间开发的工作台布局。两者共享统一导航、连接状态、通知和设置，不相互复制业务状态。

### 6.2 Work 最小能力

1. 会话列表、创建、恢复和明确的当前会话。
2. 流式 Markdown、代码高亮、复制与长内容折叠。
3. 工具调用的 queued、running、completed、failed、cancelled 状态。
4. stdout/stderr 和结构化结果按需展开。
5. diff 文件列表、逐文件查看、审批和拒绝理由。
6. clarify/approval 的结构化交互，不把它们伪装为普通聊天文字。
7. 附件和图片预览、上传状态和失败恢复。
8. 中断、排队、后台任务与完成通知。
9. context、模型、token/usage 和连接状态。

### 6.3 API-A 应用 Runtime 所有权

当前 API-A `user_chat` 的主运行时存在于活动 CLI 进程中。治理阶段应把它提取为 `VoidCube_app` 的无界面应用 runtime。Supervisor 是 API-B 治理和任务投影所有者，不能因为桌面端需要 Work 页面就顺带变成 API-A 用户会话所有者；Windows 渲染、托盘和窗口对象也不能成为会话真相。

Phase 3 将 Windows adapter 接入已经形成的 **VoidCube_app Runtime**：

- API-A 会话、turn、工具循环、取消和事件转换已经在前置治理中成为无渲染共享核心，不在桌面阶段再次抽取。
- `VoidCube_app` 是逻辑上的应用会话 owner；物理上可以由 Windows 宿主进程内托管，也可以由独立本机应用宿主托管。
- 跨进程执行继续通过 Gateway 的标准入口，不能绕过 Gateway 直连 Executor。
- 应用 runtime 将事件写入有界、可恢复的 journal；浏览器断线和 WebView 重启不会丢失已确认事件。
- Supervisor 只提供同源 Work BFF：校验 UI 会话，转发命令并投影事件，不保存第二份会话真相。
- Memory 继续持有长期记忆；应用 journal 只保存会话恢复所需的工作状态和事件游标。
- CLI 和 Windows adapter 均调用同一应用 use case；CLI 不提供给 Windows 调用的兼容 facade。

建议调用链：

```text
Work Web UI
  -> Supervisor /work/api/*（UI 会话授权、同源 BFF）
  -> VoidCube_app transport adapter（进程内或本机 API）
  -> VoidCube_app Runtime（API-A logical session owner）
  -> Gateway
  -> Execution / Memory
```

应用 Runtime 的进程复用、空闲退出、崩溃恢复和持久化粒度需要在 Phase 3 开始前通过单独 ADR 定稿。无论采用进程内、单宿主多会话还是按 workspace 分进程，都必须只有一个明确的逻辑 session owner，并通过同一 repository/锁保护并发写入。

### 6.4 统一事件协议

CLI 和 Work 应消费同一组领域事件，渲染层不得解析另一端的终端字符串。建议统一事件信封：

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "session_id": "session_...",
  "turn_id": "turn_...",
  "sequence": 42,
  "timestamp": "2026-07-29T12:00:00Z",
  "type": "tool.completed",
  "payload": {}
}
```

首批事件类型建议：

```text
session.created / session.updated
turn.started / turn.completed / turn.failed / turn.cancelled
message.delta / message.completed
tool.queued / tool.started / tool.progress / tool.completed / tool.failed
approval.requested / approval.resolved
clarification.requested / clarification.resolved
artifact.created / diff.created
usage.updated / context.updated
```

协议要求：

- 同一 turn 内 `sequence` 单调递增，重连后可按游标补发。
- `event_id` 用于幂等去重；UI 收到重复事件不能重复创建卡片。
- 未知事件类型必须安全忽略并记录，不使用复杂的永久兼容分支。
- 破坏性变化提升 `schema_version`；版本支持窗口要明确，到期后删除旧解析器。
- SSE 用于服务端推送，HTTP 用于命令提交；只有确实需要全双工后再引入 WebSocket。

### 6.5 前端源码拆分

在增加 Work 前，先把 `UI_HTML` 原样移到包内静态资源，再按功能拆分。第一步只做行为等价迁移：

- FastAPI 返回 `index.html` 并挂载静态资源；
- 使用原生 ES modules，首阶段不引入 Node/Vite；
- 静态资源通过 `importlib.resources` 或等效包资源接口定位，不能依赖当前工作目录；
- 更新 `pyproject.toml` package-data 和 wheel 契约测试；
- 迁移完成后删除 `UI_HTML` 巨型字符串及失效加载分支。
- 消除内联脚本和内联事件处理器；动态样式优先改为 class、CSS 变量或受控属性。
- 动态 HTML 逐处确认是否已转义；无法证明安全的拼接改为 DOM API 或经过验证的净化器。

只有当 Work 交互复杂度证明原生模块化 JavaScript 难以维护时，再单独评估前端框架和构建链。

### 6.6 视觉与交互标准

- Work 首屏优先显示当前会话和输入区，不做营销式欢迎页。
- 工具、diff、审批和错误使用稳定尺寸与清晰层级，流式更新不能导致明显布局跳动。
- 完整支持键盘操作、焦点可见、屏幕阅读标签和 `prefers-reduced-motion`。
- 适配 100%、125%、150%、200% DPI 及窄窗口；文本和控件不得重叠。
- 大会话采用虚拟化或窗口化渲染，避免消息数量增长后线性拖慢。
- Markdown 和工具输出必须转义/净化，不允许执行模型输出中的脚本或事件属性。
- Home 动画在窗口隐藏、最小化或系统锁屏时降频，减少 CPU/GPU 和电量占用。

## 7. WebView 与原生能力

### 7.1 WebView 首版配置

- 使用可调整大小的原生标题栏窗口。
- 默认窗口尺寸约 `1100 x 760`，记忆上次尺寸、位置、显示器和最大化状态。
- 恢复窗口位置前检查目标显示器工作区，显示器缺失时回到主屏可见范围。
- 设置最小尺寸，保证 Home/Work 控件不重叠。
- 生产环境禁用或隐藏开发者工具入口；开发构建允许显式开启。
- 不允许导航到任意远程页面。外链交给系统默认浏览器，并经过协议白名单校验。

### 7.2 Bridge 边界

允许的 bridge 方法仅包括：

- `get_desktop_info()`
- `hide_window()`
- `show_notification()`（Phase 2）
- `choose_files()` / `choose_directory()`
- `open_external_url()`（仅 `https` 白名单）
- `open_log_directory()`

明确禁止：

- 通用 `run_command()`；
- 任意路径读写；
- 任意 URL 请求代理；
- 向 JavaScript 返回服务密钥或长期令牌；
- 直接修改服务进程和系统注册表的通用接口。

所有参数必须做类型、长度、协议和路径范围校验。原生操作产生结构化审计日志，异常只返回稳定错误码，不向页面泄漏内部堆栈或密钥。

### 7.3 Windows 原生功能优先级

| 能力 | 选择 | 阶段 | 说明 |
| --- | --- | --- | --- |
| WebView | `pywebview` + WebView2 | Phase 1 | 先验证 Python 3.14 支持 |
| 单实例 | `pywin32` named mutex + 本机 IPC | Phase 1 | 第二实例只激活第一实例 |
| 托盘 | `pystray` + Pillow | Phase 1 | 图标资源必须随包分发 |
| 全局热键 | `RegisterHotKey` | Phase 2 | 避免全局键盘 hook |
| Toast | 独立适配器 | Phase 2 | 先验证 AUMID、点击和打包兼容 |
| 自启动 | HKCU Run 或安装器入口 | Phase 2 | 只写稳定 exe 路径并可关闭 |
| 锁屏/解锁 | session-change 消息窗口 | Phase 2 | 用于暂停动画/提醒，不暂停核心服务 |
| 空闲检测 | `GetLastInputInfo` | Phase 2 | 只作为提醒策略信号 |
| 音频 ducking | 暂缓 | 后续评估 | 必须保证异常退出后恢复音量 |
| 无边框窗口 | 暂缓 | 后续评估 | 先通过 DPI/多屏/可访问性验证 |

尽量用 `pywin32` 覆盖 mutex、热键、注册表和 session 消息等能力，减少为每个功能引入一个依赖。Toast 库在技术验证后再锁定，不能先写死实现。

## 8. 安全设计

### 8.1 威胁边界

本项目是单机单所有者系统，但仍需防范：

- 浏览器访问本机 Supervisor 写接口；
- 恶意网站通过用户浏览器向 localhost 发起跨站请求；
- 本机其他低权限进程调用控制接口；
- Web UI 内容注入后滥用 bridge；
- 日志、崩溃报告或 URL 泄漏凭证。

### 8.2 桌面会话授权

Phase 1 在开放桌面写操作前必须完成：

1. 桌面宿主创建短期、高熵、仅内存保存的会话票据。
2. WebView 通过受限 bridge 或一次性 bootstrap 交换票据；不能把票据放在长期 URL、日志或配置文件中。
3. Supervisor 对所有由 Web UI 发起的写接口校验 UI 会话，包括但不限于 `/ui/*`、语音、companion、提醒策略和治理操作；敏感治理操作继续保留其原有明确同意流程。
4. 校验 `Origin`/`Host`，拒绝非预期来源；cookie 方案需使用 `HttpOnly`、`SameSite=Strict`，并对状态变更请求增加 CSRF token。
5. SSE 和读取接口按敏感度决定是否要求同一会话，不把内部状态默认暴露给任意本机网页。
6. 票据在桌面退出、超时或 Supervisor 重启后失效。
7. 不依赖 User-Agent、端口或“运行在 WebView 中”作为身份判断。

浏览器入口需要保留，但必须区分权限：

- 直接访问 `http://127.0.0.1:6002/ui` 默认进入只读/受限模式。
- 由受信任本地启动器打开浏览器时，使用一次性、短时 bootstrap 建立浏览器 UI 会话；bootstrap 消费后立即失效。
- 现有自动打开浏览器的逻辑在安全迁移时改用该受信任入口，不能保留无授权写操作作为兼容回退。
- 本机同一用户权限下的恶意进程仍属于剩余风险；UI 会话主要防跨站网页、误调用和令牌长期暴露，不能虚构操作系统级隔离。

具体传递机制在 Phase 0 技术设计中定稿。不能为了省代码把长期服务 token 注入 JavaScript。

### 8.3 Web 内容安全

- 完成资源拆分后配置严格 CSP，默认禁止远程脚本、内联脚本、内联事件处理器和不必要的网络目标。
- 现有 YouTube/Bilibili 媒体 iframe 只允许明确的 `frame-src` 来源；普通外链和未知媒体 URL 不在 WebView 内嵌入。
- Markdown、HTML、diff、工具输出和文件名均按不可信输入处理。
- 外链仅允许明确协议，并由系统浏览器打开。
- 文件选择结果只提交用户明确选择的路径，不给页面目录遍历能力。
- bridge 和 UI 写接口分别测试越权、恶意参数、跨站请求和重放。

## 9. 依赖、打包与安装

### 9.1 依赖组织

桌面依赖放入 Windows 可选依赖，不污染服务器和纯 CLI 安装：

```toml
[project.optional-dependencies]
desktop = [
    "pywebview...",
    "pystray...",
    "pillow...",
    "pywin32...; platform_system == 'Windows'",
]
```

版本号必须来自技术验证结果，不在方案阶段虚构约束。`full` 是否包含 `desktop` 需谨慎：当前 `full` 具有跨平台含义，初期建议桌面独立安装，避免 Linux/macOS 解析 Windows 依赖。

### 9.2 Phase 0 依赖技术验证

在正式实现前建立最小探针并记录结果：

- Python 3.14 上可安装并导入 `pywebview`、`pystray`、Pillow、`pywin32`；
- WebView2 窗口创建、关闭和线程模型稳定；
- 打包后的程序能加载 WebView2、托盘图标和包内静态资源；
- Windows 10 和 Windows 11 至少各验证一次；
- 缺少 WebView2 Runtime 时能检测、解释并引导安装；
- 依赖无可用 Python 3.14 wheel 时，先解决运行时约束，不以源码编译作为普通用户默认路径。

### 9.3 发行形态

建议区分：

- Python 包：继续提供 `voidcube` / `vc` CLI。
- Windows 桌面安装包：提供稳定的 `VoidCube.exe`、开始菜单入口、卸载项和图标。
- 开发入口：提供 `voidcube desktop` 或独立 console script，便于未打包调试。

最终冻结工具和安装器在 Phase 0 探针后选择。选择标准包括 Python 3.14 支持、包体积、冷启动、资源收集、子进程路径和签名流程，不能只按名义体积决定。

### 9.4 打包契约

- Web 静态资源、托盘图标、启动页、locale 和现有必要包数据必须进入 wheel/安装包。
- 后台服务启动使用打包后稳定入口，不能依赖源码目录或当前工作目录。
- Phase 3 的应用 runtime 和 transport adapter 必须进入同一发行和版本契约，桌面端不能下载或启动版本不匹配的会话宿主。
- 用户数据、日志、PID 和配置写入运行时数据目录，不写安装目录。
- 安装、升级和卸载不删除用户数据；卸载时是否保留数据应明确提示。
- 安装器检测 WebView2 Evergreen Runtime；缺失时使用官方引导或引导式安装。
- 正式版本需要代码签名方案，减少 SmartScreen 和安全软件误报。
- 涉及打包的实现必须运行仓库规定的退役集成扫描、wheel 契约和相关测试。

### 9.5 更新策略

首版采用手动下载并安装更新，不实现静默自动更新。后续更新器必须满足：

- 不在服务和桌面进程仍写文件时原地覆盖；
- 支持失败回滚；
- 校验签名和版本；
- 保留配置、会话和数据库；
- UI 协议升级与后端版本兼容窗口明确。

## 10. 配置、隐私与可观测性

### 10.1 桌面设置

仅保存桌面专属设置：

- 窗口位置、尺寸、最大化状态；
- 启动时显示或隐藏；
- 关闭窗口时隐藏到托盘；
- 通知和热键设置；
- 上次 Home/Work 路由。

模型、服务、语音和 Supervisor 业务配置继续由现有配置所有者管理，不在桌面层复制一份。写设置应采用临时文件 + 原子替换，损坏时回退到默认值并保留诊断。

### 10.2 日志与诊断

- 桌面宿主使用独立滚动日志，记录版本、启动阶段、依赖探测、服务动作、窗口和 bridge 错误。
- 日志不得记录 API 密钥、服务 token、桌面会话票据、完整用户输入或敏感文件内容。
- 启动失败页提供“重试”“打开日志目录”“复制脱敏诊断”。
- 每次启动生成 correlation ID，桌面和服务日志可关联但不改变业务 trace 所有权。
- 记录冷启动分段耗时：宿主初始化、服务确保、Supervisor ready、WebView 创建、首屏 ready。

### 10.3 性能预算

在基准机器上建立可重复测量，建议初始目标：

- 服务已运行时，桌面启动至可交互首屏 P50 小于 2 秒，P95 小于 4 秒。
- 服务冷启动时分别记录三服务 readiness，不用单一总时长掩盖瓶颈。
- 窗口隐藏后 Home 动画和轮询显著降频，宿主空闲 CPU 接近零。
- SSE 断开不产生忙循环；重试有上限和抖动。
- 长会话渲染保持输入响应，不随历史消息无限线性增长。

这些是初始工程预算，Phase 1 基准完成后可按真实硬件修订，但必须保留分段指标。

## 11. 共享应用层前置契约

具体拆分顺序以 [Python 巨石文件治理方案](./Python巨石文件治理方案.md) 为唯一实施基线，本文件不再维护第二份 CLI 拆分目录。Windows 工作开始前必须满足：

- `VoidCube_app` 已承载 session、turn、queue、cancel、tool、approval、clarify 和结构化事件。
- `VoidCube_cli` 只保留 slash command、TUI、ANSI/Rich 渲染及终端平台适配。
- `VoidCube_windows` 只保留 Web UI、WebView、托盘、通知、热键和 Windows 平台适配。
- 两个前端不得互相导入，也不得通过解析对方的输出复用功能。
- 服务控制、canonical 配置、repository 和 schema 必须共享，不在 Windows adapter 中复制。
- 根 `cli.py` 已是薄入口，Windows 入口不导入它。

## 12. 分阶段实施计划

### Phase 0：契约与技术探针

实施内容：

- 验证 Python 3.14 桌面依赖和冻结工具。
- 定义单实例、激活 IPC、服务启动锁和服务所有权结果。
- 抽取无 UI 输出的服务控制接口，保留 CLI 行为。
- 审计 Supervisor UI 读写接口并完成桌面会话授权设计。
- 定义窗口关闭、退出桌面和停止服务的固定语义。
- 定义桌面运行时目录、设置、日志和资源定位。

验收标准：

- 依赖探针能在目标 Windows/Python 组合运行。
- 并发启动不会重复拉起服务或终止未知进程。
- 服务控制器测试覆盖 adopted、started、failed 和端口冲突。
- 安全设计不向 JavaScript 暴露长期服务凭证。
- 形成冻结工具和安装器选择记录。

### Phase 0.5：小屋资源与安全基线

实施内容：

- 将内嵌 HTML/CSS/JS 等价迁到包内静态资源。
- 按 API、Home、语音和 Auto 逐步拆分原生模块。
- 消除内联脚本，收紧 CSP、媒体来源和外链导航。
- 加入 package-data、wheel 和静态资源加载测试。
- 实现 UI 会话、只读直访模式和受信任浏览器 bootstrap。
- 删除迁移完成的内嵌字符串、无授权写入口和重复资源路径。

验收标准：

- 浏览器端视觉、SSE、语音及已有控制行为保持一致。
- 直接访问 `/ui` 无法执行写操作，受信任启动器可以建立短期写会话。
- 从源码和 wheel 启动均能加载资源。
- CSP 不依赖 `unsafe-inline` 或 `unsafe-eval` 执行脚本。
- `ui_runtime.py` 明显缩小，不再承载大段前端源码。

### Phase 1：最小可靠桌面壳

实施内容：

- 桌面入口、单实例与第二实例激活。
- 启动/复用现有服务并记录所有权。
- 原生标题栏 WebView 加载现有 `/ui`。
- 托盘显示、隐藏、重载、服务状态、停止和退出。
- 本地启动/失败页、健康监控和有限重连。
- 桌面会话授权和最小 bridge。
- 开发入口与桌面可选依赖。

验收标准：

- 连续启动十次不出现重复窗口、重复服务或残留锁。
- 服务已运行和未运行两种路径均可进入 UI。
- 关闭窗口只隐藏；退出桌面不停止服务；停止命令行为明确。
- Supervisor 重启后 UI 可恢复，失败时可打开脱敏日志。
- 100%-200% DPI、多显示器和窗口恢复均保持可用。
- bridge 越权、安全头和桌面会话测试通过。

### Phase 2：Windows 产品化能力

实施内容：

- Toast、AUMID 和点击回调。
- 自启动开关和稳定 exe 路径。
- `RegisterHotKey` 触发语音或显示窗口。
- 锁屏/解锁和空闲信号接入提醒策略。
- 安装器、卸载、签名和手动升级流程。

验收标准：

- 通知归属、点击、勿扰和重复抑制正确。
- 热键冲突可诊断、可修改、可禁用。
- 自启动卸载后无残留入口。
- 锁屏只抑制 UI/提醒活动，不破坏服务和正在执行的任务。

### Phase 3：Work 工作区

实施内容：

- 定稿结构化会话事件协议和游标恢复。
- 将 Windows adapter 接入既有 `VoidCube_app` 核心，按 ADR 建立进程内或本机 transport adapter、Gateway 注册和 Supervisor 同源 BFF。
- 会话、Markdown、工具、diff、审批、附件和输入区。
- 中断、排队、后台任务和完成通知。
- 长会话虚拟化及桌面/CLI 共享领域事件。

验收标准：

- Work 不解析 ANSI 或 CLI 文本。
- API-A session 只有一个 `VoidCube_app` logical owner；Supervisor、CLI adapter 和 Windows renderer 均不复制会话状态机。
- 断线重连不会丢失、重复或乱序展示已确认事件。
- 工具、审批、diff 和取消全流程可完成。
- 长会话、流式输出和大工具日志保持响应。
- CLI 行为不因共享逻辑抽取而回归。

### Phase 4：收口与增强评估

实施内容：

- 继续缩小 `cli.py`，删除迁移后的冗余逻辑。
- 建立 Windows 安装升级矩阵、视觉回归和崩溃恢复测试。
- 基于测量评估无边框窗口、自动更新和音频 ducking。

这些增强只有在收益、恢复策略和测试成本清楚后才实施，不作为既定承诺。

## 13. 测试矩阵

### 13.1 自动化测试

- 服务控制器：启动顺序、健康、超时、部分失败、并发锁、所有权。
- 单实例：首实例、第二实例激活、陈旧 IPC、异常退出。
- 桌面设置：原子写、损坏恢复、屏幕外窗口纠正。
- 安全：会话票据、Origin/Host、CSRF、bridge 参数、Markdown 净化。
- UI：快照、SSE 重连、事件幂等、游标补发、未知事件。
- 打包：包内资源、console entry、冻结路径、wheel 内容和退役集成扫描。

### 13.2 Windows 手工/端到端矩阵

| 维度 | 最低覆盖 |
| --- | --- |
| 系统 | Windows 10、Windows 11 |
| DPI | 100%、125%、150%、200% |
| 显示器 | 单屏、双屏、拔掉副屏后恢复 |
| 服务状态 | 全停、全健康、部分失败、未知端口占用 |
| 启动方式 | 开始菜单、开发入口、自启动 |
| 网络 | SSE 断开、Supervisor 重启、Gateway 不可用 |
| 电源会话 | 锁屏、解锁、注销、关机 |
| 安装 | 首装、覆盖升级、卸载、WebView2 缺失 |

使用 Playwright 验证 Web UI 的桌面/窄窗口布局、交互和截图；使用真实 WebView 做窗口、托盘、DPI 和原生 bridge 的端到端验证。纯浏览器测试不能替代 WebView 验收。

## 14. 风险与控制

| 风险 | 影响 | 控制措施 |
| --- | --- | --- |
| Python 3.14 桌面依赖缺少 wheel | 无法稳定安装 | Phase 0 先探针，未通过不进入正式开发 |
| 桌面与 CLI 同时启停服务 | 重复进程或误杀 | 统一服务控制器、跨进程启动锁、明确所有权 |
| localhost 写接口被跨站调用 | 配置或任务被越权修改 | 桌面会话、Origin/Host、CSRF、最小暴露 |
| UI 巨型单文件继续增长 | 修改风险和测试成本上升 | 桌面壳发布前完成等价静态资源拆分 |
| bridge 权限过大 | 本机代码执行或文件泄漏 | 固定白名单、参数校验、无通用命令接口 |
| WebView2 缺失或损坏 | 窗口无法启动 | 安装器探测、本地错误页、官方修复入口 |
| 无边框窗口过早引入 | DPI、多屏和可访问性回归 | 首版原生标题栏，后续用数据决策 |
| 冻结程序子进程路径变化 | 服务无法启动 | 打包路径契约和干净机器端到端测试 |
| 长会话 DOM 无限增长 | Work 逐渐卡顿 | 事件存储与视图分离、虚拟化、日志折叠 |
| Work 直接复用 CLI 输出或复制会话状态机 | 状态不可恢复、CLI/UI 长期分叉 | 共享 `VoidCube_app` use case、repository 和结构化事件 |
| 更新覆盖运行中数据 | 安装损坏或数据丢失 | 首版手动更新，后续签名、停机和回滚协议 |

## 15. 实施时的固定检查清单

每一阶段完成前确认：

- 是否复用了现有服务和领域逻辑，而不是复制一套？
- 是否删除了迁移后失效的旧入口、参数和兼容分支？
- 是否保持 Gateway、Memory、Supervisor、Execution 的架构基线？
- 是否将桌面依赖限制在 Windows 可选安装？
- 是否验证源码、wheel 和冻结安装包三种资源路径？
- 是否覆盖桌面与 CLI 同时运行、重复启动和异常退出？
- 是否确保 JavaScript、日志和 URL 中没有长期凭证？
- 是否测量启动分段耗时、空闲资源和长会话性能？
- 涉及模型、鉴权、请求协议、技能或打包时，是否运行退役集成扫描和相关测试？
- 是否同步更新本方案、架构基线和开发验证文档中的已实施状态？

## 16. 后置实施条件

当前不得直接按本方案实施 Phase 0 或桌面窗口。先完成 Python 巨石治理方案，待其 Go/No-Go 条件全部满足后重新进行桌面 ADR。届时若仍采用本方案，首个实施批次才限定为：

1. 建立 Python 3.14 桌面依赖和 WebView2/冻结程序探针。
2. 为 `VoidCube_cli.ops.serve` 定义结构化服务控制结果和跨进程启动锁。
3. 固定关闭、退出、停止服务语义，并为 adopted/started 所有权补测试。
4. 列出 Supervisor UI 的全部写接口，完成桌面会话授权威胁模型和最小协议。
5. 根据探针结果锁定依赖、冻结工具、安装器和 Phase 1 的精确文件清单。

重新评审通过后，Phase 0 验收再进入 Phase 0.5，之后才创建桌面正式入口。不得把本文件中的旧时间顺序理解为当前开发优先级。
