# VoidCube Desktop

VoidCube Desktop 是现有 Supervisor Web UI 与 Agent CLI 的跨平台容器。桌面层不复制 Python 业务逻辑：Electron 管理窗口和本地进程，Supervisor 页面保留原有 HTTP/SSE 协议，CLI 通过真实 PTY 运行。

桌面工作区提供上下分屏、监控最大化和终端最大化三种布局，并记忆布局模式和分割比例。工具栏的服务菜单显示 Gateway、Memory、Supervisor 的结构化状态，可直接启动、重启或停止后台服务。

## 控制边界

Electron 不解析终端输出。主进程通过 `python -m voidcube.interfaces.desktop.desktop_control <action>` 调用 Python 服务所有者，使用版本化 JSON 协议执行 `status`、`start`、`restart` 和 `stop`。渲染进程只能通过受限 preload IPC 读取状态或请求生命周期操作，不能直接访问 Node.js 或创建任意进程。

桌面通过 `VOIDCUBE_DESKTOP_MANAGED_SERVICES=1` 明确取得后台服务生命周期所有权。关闭桌面窗口只结束嵌入式 CLI，不停止 Gateway、Memory 和 Supervisor；需要停止服务时使用工具栏服务菜单中的“停止”。

## 开发运行

需要 Node.js 22+、npm，以及仓库根目录的 Python 3.14 虚拟环境：

```powershell
cd desktop
npm install
npm run dev
```

桌面运行时按以下顺序定位 Python：

1. `VOIDCUBE_DESKTOP_PYTHON` 指定的解释器。
2. 发行资源中的内嵌 Python。
3. VoidCube 仓库的 `.venv`。
4. 系统 `PATH` 中的 `python3` 或 `python`。

可选环境变量：

| 变量 | 作用 |
|---|---|
| `VOIDCUBE_PROJECT_ROOT` | 指定包含 `pyproject.toml` 和 `src/voidcube` 的项目根目录 |
| `VOIDCUBE_DESKTOP_PYTHON` | 指定 Python 3.14 可执行文件 |
| `VOIDCUBE_DESKTOP_WORKSPACE` | 指定 CLI 的初始工作目录 |
| `VOIDCUBE_SUPERVISOR_URL` | 指定 Supervisor UI，仅接受本机回环 HTTP 地址 |

## 质量检查

```powershell
npm run typecheck
npm test
npm run build
npm run test:e2e
```

端到端测试会启动真实 Electron 窗口、创建 PTY、运行 VoidCube CLI、等待 Supervisor 页面加载，并向终端发送本地 `/help` 命令。

## 打包

```powershell
# 当前平台的解包目录，适合本地验证
npm run pack

# 当前平台安装包
npm run dist
```

Windows 输出位于 `desktop/release/`。`node-pty` 使用其官方预编译二进制，打包阶段禁止无意义的本机源码重建，因此普通开发环境不要求安装 Visual Studio C++ Spectre 库。

`npm run pack` 和 `npm run dist` 会先从 `scripts/generate-icons.mjs` 确定性生成 PNG、ICO 与 ICNS 品牌图标，不依赖外部图像服务。

当前安装包包含桌面外壳，不复制仓库源码和虚拟环境。开发环境会自动使用仓库 `.venv`；独立发行时应通过 `VOIDCUBE_DESKTOP_PYTHON` 指向已安装 `voidcube-agent` 的 Python 3.14，或者在后续发行流水线中提供 `resources/python` 和 `resources/voidcube` sidecar。

## 安全边界

- 渲染进程启用 `contextIsolation` 与沙箱，禁用 Node 集成。
- preload 只暴露监控探测、PTY 输入、输出、重启和尺寸同步接口。
- Supervisor URL 只允许 `127.0.0.1`、`localhost` 或 `::1` 的 HTTP 地址。
- 桌面 PTY 设置 `VOIDCUBE_DESKTOP=1`，Supervisor 在该宿主模式下不会再调用系统浏览器。
- 新窗口导航被阻止，普通 HTTP/HTTPS 链接交给系统浏览器。
- iframe 不允许顶层导航，Electron 权限请求默认拒绝。
