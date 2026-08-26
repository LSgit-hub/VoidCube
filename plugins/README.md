# VoidCube 插件开发规范（契约文档）

插件机制负责把**仓库内可复用能力**（工具集、独立 HTTP 服务、Web 面板）以
声明式清单 + 标准入口的形式接入 VoidCube 运行时。本文件是唯一权威契约。

## 1. 插件是什么

- 插件是 `plugins/<name>/` 下的一个 Python 包，必须带 `plugin.json` 清单。
- 插件与主程序同仓库、同虚拟环境、同生命周期；不做进程/依赖隔离。
- 一个插件可同时声明多种能力：`tools`（Agent 工具集）、`service`（独立
  HTTP 服务）、`web`（Supervisor 挂载的静态 UI）。
- 插件故障是**隔离**的：激活失败、清单损坏、静态目录缺失都只记日志，
  不阻断核心服务（gateway / memory / supervisor）与 CLI 启动。

## 2. 目录结构

```
plugins/
├── memory/                    # 既有插件（mem provider，仅 tools 能力）
│   ├── plugin.json
│   └── mem/
└── <name>/                    # 你的插件
    ├── plugin.json            # 清单（唯一事实来源）
    ├── __init__.py            # 入口：activate(manager, config)
    ├── server.py              # service 能力：create_app(config) -> FastAPI app
    └── web/dist/              # web 能力：静态前端产物（index.html）
```

## 3. plugin.json 清单 Schema

```json
{
  "name": "goal_manager",
  "version": "0.1.0",
  "api_version": "1",
  "description": "目标管理：目标、里程碑、OKR 的创建/跟踪/复盘",
  "capabilities": ["tools", "service", "web"],
  "entrypoint": "plugins.goal_manager",
  "config_key": "goal_manager",
  "enabled": true,

  "tools": {
    "namespace": "goal",
    "description": "目标管理工具集"
  },

  "service": {
    "enabled": true,
    "port": 6003,
    "module": "plugins.goal_manager.server:create_app",
    "health_path": "/health",
    "gateway_service_type": "goal_service"
  },

  "web": {
    "mount_path": "/ui/goal-manager",
    "static_dir": "web/dist",
    "entry": "index.html"
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 插件唯一名，必须等于目录名；同时是服务名/服务标识 |
| `entrypoint` | ✅ | 插件入口模块，`importlib.import_module` 直接可导入 |
| `api_version` | ✅ | 清单协议版本，当前 `"1"` |
| `capabilities` | 否 | 从 `tools` / `service` / `web` 中声明 |
| `config_key` | 否 | `config.yaml` 中本插件的配置段名（见 §5） |
| `enabled` | 否 | 默认启用开关，默认 `true`；被 config 段 `enabled` 覆盖 |
| `tools.namespace` | 工具集 | 工具命名空间（唯一 key） |
| `service` | 服务 | 见 §4.2；`enabled=false` 则不启动；`health_path` 默认 `/` |
| `web` | UI | 见 §4.3；`mount_path` 必须以 `/` 开头且不得覆盖保留路由 |

## 4. 四层对接

### 4.1 tools —— Agent 工具集

入口 `activate()` 内完成三步注册：

```python
# plugins/<name>/__init__.py
from voidcube.extensions.tools.registry import registry          # 工具表
from voidcube.extensions.tools.toolsets import create_custom_toolset  # 工具集表
from voidcube.extensions.plugins.manager import get_plugin_manager     # 插件管理器

def activate(manager, config: dict) -> None:
    # 1) 注册工具集（PluginManager 可见，供 toolset 校验/白名单）
    manager.register_toolset("goal", {
        "label": "目标管理",
        "description": "目标、里程碑、OKR 的创建/跟踪/复盘",
        "tools": ["goal_create", "goal_list"],
    })

    # 2) 注册工具集（TOOLSETS 可见，供 validate_toolset / get_toolset_info）
    create_custom_toolset("goal", "目标管理工具集", [
        {"name": "goal_create", "description": "创建目标", "parameters": {...}},
        {"name": "goal_list", "description": "列出目标", "parameters": {...}},
    ])

    # 3) 逐个注册工具（工具表可见，随 Agent 上下文自动聚合）
    registry.register("goal_create", handle_goal_create,
                      toolset="goal", description="创建目标", parameters={...})
    registry.register("goal_list", handle_goal_list,
                      toolset="goal", description="列出目标", parameters={...})

def deactivate(manager) -> None:   # 可选，进程退出清理
    ...
```

`registry.register` 在**模块导入时**执行，工具表是模块级单例；Agent 上下文的
工具 schema 由 `model_tools` 从 `registry.get_definitions(...)` 聚合，插件工具
无需任何额外接线即可被 Agent 调用。

### 4.2 service —— 独立 HTTP 服务

由 `service_launcher` 在启动序列中自动拉起（gateway → memory → supervisor →
插件服务），自动完成健康等待、Gateway 注册、PID/日志/状态管理。

契约：

```python
# plugins/<name>/server.py
from fastapi import FastAPI

def create_app(config: dict) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        # 必须：health_path 返回服务标识，供端口归属校验（_health_endpoint_is_service）
        return {"service": "goal_manager", "status": "ok"}

    @app.get("/api/goals")
    def list_goals():
        ...
    return app
```

- `service.module` 格式固定为 `模块路径:工厂函数名`。
- `create_app` 收到的是普通 dict：`config.yaml[config_key]` 段内容
  （无该段则为空 dict）+ `name` / `port` / `service_port`。
- `service.health_path`（默认 `/`）必须返回 `{"service": "<插件名>", ...}`，
  否则 launcher 无法确认端口归属，服务可能被判定为"端口被他人占用"而反复重启。
- 若声明 `gateway_service_type`，启动后会等待该 type 出现在 Gateway 注册表
  （插件服务需自行调用 Gateway 的注册接口上报身份，参考 supervisor 的
  service_runtime 注册方式）。

### 4.3 web —— Supervisor 挂载的静态 UI

Supervisor 启动时自动把 `web.static_dir`（相对插件目录）挂到
`web.mount_path` 下，`html=True` 模式直接服务 `entry`（默认 index.html）。

- 产物为纯静态文件（构建后的 SPA 或简单页面）。
- `mount_path` 需避开 Supervisor 已占用路径和保留前缀（`/runtime*`、
  `/api*`、`/docs` 等）。目标管理插件使用 `/ui/goal-manager`。
- 前端通过 `/api/*` 与插件服务通信（插件服务与 Supervisor 同机）。

### 4.4 CLI —— 命令扩展

CLI 侧工具集通过 `cli_adapter.get_plugin_toolsets()` 暴露；需要自定义
命令时可实现 `register_command_handler(command, handler)`，在 `activate()`
中注册（参考 `voidcube/extensions/plugins/manager.py`）。

## 5. 配置段（config.yaml）

插件可用 `config_key` 声明自己的配置段，追加到 `config.yaml`：

```yaml
goal_manager:
  enabled: true          # false 则停用插件（工具不激活、服务不启动、UI 不挂载）
  port: 6003             # 可覆盖清单 service.port
  db_path: ~/.VoidCube/runtime/goals/goals.db
  remind_hour: 9
```

- 启用判定：`config.yaml[config_key].enabled` 优先，其次清单顶层 `enabled`。
- 配置段在 CLI 与服务子进程中都会被注入 `create_app(config)` / `activate(manager, config)`。

## 6. 生命周期与启动顺序

```
CLI 启动
 ├─ service_launcher 模块加载 → register_plugin_services()
 │    （扫描 plugins/*/plugin.json，service.enabled 的插件并入 SERVICES）
 ├─ ensure_running → gateway → memory → supervisor → 插件服务（按声明顺序）
 │    （每个插件服务：拉起子进程 → 健康等待 → 可选 Gateway 注册等待）
 └─ 运行时 activate_all_plugins() 激活各插件工具（幂等，单点失败隔离）
```

- 服务子进程的 `sys.path` 已含仓库根与 `Mem/src`，`import plugins.<name>` 可用。
- 插件服务也可用 `voidcube serve status / stop` 单独查看与停止（与核心服务一致）。

## 7. 注意事项与坑

1. **入口模块可导入**：`entrypoint` 必须是 `importlib.import_module` 可直接
   导入的完整模块路径（`plugins.goal_manager`），不要写文件名。
2. **health 契约**：服务端 `service.health_path` 忘返回 `{"service": <name>}` 是最高频
   故障，症状为 launcher 反复提示"端口被占用"。
3. **端口唯一**：插件服务端口不能与 6000/6001/6002 及彼此冲突；改端口同时
   改清单 `service.port` 与 config 段（config 优先）。
4. **不 import 重型主程序**：插件服务子进程只 import 自身依赖与
   `voidcube` 基建；不要在 server.py 顶部 import CLI 运行时。
5. **清单损坏不阻断**：`plugin.json` 缺 `name/entrypoint` 或 JSON 非法时，
   该插件被跳过并记 warning，其余插件照常。
6. **静态目录缺失不阻断**：web 挂载失败仅 warning，Supervisor 正常启动。

## 8. 验证清单

```bash
# 1) 清单合法、插件被发现且启用
.venv/Scripts/python.exe -c "
from voidcube.extensions.plugins.registry import discover_plugin_manifests, get_enabled_plugins
print([(d.name, d.capabilities) for d in discover_plugin_manifests()])
print([d.name for d in get_enabled_plugins()])"

# 2) 服务并入启动序列
.venv/Scripts/python.exe -c "
from voidcube.infrastructure.gateway.service_launcher import SERVICES
print([(n, s.port, s.kind) for n, s in SERVICES.items()])"

# 3) 全量启动 + 健康
.venv/Scripts/python.exe -m voidcube serve start
.venv/Scripts/python.exe -m voidcube serve status
curl http://127.0.0.1:<插件端口>/
```
