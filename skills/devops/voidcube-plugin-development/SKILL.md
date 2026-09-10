---
name: voidcube-plugin-development
category: devops
description: VoidCube 插件开发与集成规范 — 标准插件布局契约、plugin.json 扩展 schema、四层对接（Agent 工具/服务进程/Web UI/配置）、B1-B4 基建已落地的真实契约与已验证流程。当用户要求开发、规划或集成 VoidCube 插件（如目标管理、context_engine、memory 迁移）时使用。
---

# VoidCube 插件开发与集成

## 触发条件
- 用户要开发/规划一个新 VoidCube 插件（目标管理、context_engine 等）
- 要理解插件如何对接 VoidCube（Agent 工具、服务、Web UI、配置）
- 要迁移/维护现有 `plugins/memory` 插件或扩展插件机制本身

## 现状：B 系列插件系统已落地（2026-08-26 实证）

| 组件 | 位置 | 状态 |
|---|---|---|
| 插件目录 | `plugins/`（仓库根，因 repo_root 在 sys.path 中可直接 `import plugins.*`） | 仅有 memory 插件（可作范例） |
| 清单协议模型 | `src/voidcube/extensions/plugins/manifest.py` | `PluginManifest` + `discover_plugin_manifests(root)`；**协议校验的唯一来源**，registry 复用它 |
| 插件注册器 B1 | `src/voidcube/extensions/plugins/registry.py`（新） | **已落地**：扫描/校验/激活/服务与 web 声明发现 |
| launcher 插件服务 B2 | `src/voidcube/infrastructure/gateway/service_launcher.py` | **已落地**：插件服务并入 SERVICES + 启动序列 + 端口归属判定 |
| supervisor UI 挂载 B3 | `src/voidcube/systems/supervisor/ui_routes.py`（`mount_plugin_web_routes`）+ `supervisor.py` | **已落地**：按 web 声明静态挂载，失败隔离 |
| 契约文档 B4 | `plugins/README.md` | **已落地**：唯一权威契约（schema/四层对接/坑/验证清单） |
| 插件配置 | `config.yaml` | `<config_key>:` 段；`enabled=false` 完全静默；`port` 可覆盖清单 |
| 数据契约 | `ARCHITECTURE.md` | 每个 SQLite 文件唯一 owner；数据平面 typed client 直连，不经 Gateway 中转 |

**核心结论**：自动发现-激活闭环已通。新插件只需按 §4 契约写清单 + 入口即可接入，无需改核心进程代码。

## 标准插件布局契约

```
plugins/<plugin_name>/
├── plugin.json            # 清单：身份 + 能力 + 对接点（唯一事实来源）
├── __init__.py            # 插件入口 activate(manager, config) / deactivate()
├── tools/                 # 可选能力1：Agent 工具集
├── server.py              # 可选能力2：后端服务（FastAPI: create_app(config)）
└── web/dist/              # 可选能力3：前端构建产物（index.html）
```

契约规则：
1. plugin.json 是唯一事实来源，发现/激活/对接全部读它，不靠硬编码 import。
2. 插件名（目录名）= 清单 name = 服务名 = 服务标识 = config.yaml 配置键。
3. `capabilities` 声明能力（tools/service/web），注册器按能力对接对应链路。
4. 自带 SQLite 数据声明 `data_owner`，DB 落在 `~/.VoidCube/runtime/<plugin>/`，不进仓库。
5. 插件故障全隔离：激活失败/清单损坏/静态目录缺失只记 warning，不阻断核心服务与 CLI。

## plugin.json 实际实现的 schema（与 README 一致）

```json
{
  "name": "goal_manager",
  "version": "0.1.0",
  "api_version": "1",
  "description": "目标管理",
  "capabilities": ["tools", "service", "web"],
  "entrypoint": "plugins.goal_manager",
  "config_key": "goal_manager",
  "enabled": true,
  "tools": { "namespace": "goal" },
  "service": {
    "enabled": true,
    "port": 6010,
    "module": "plugins.goal_manager.server:create_app",
    "gateway_service_type": "goal"
  },
  "web": { "mount_path": "/goal-manager", "static_dir": "web/dist", "entry": "index.html" }
}
```

注意：实现中**没有** `data_owner`/`data_root`/`health_path` 字段（早期设计的这些字段未落地，别照旧文档写）；`api_version` 是字符串 `"1"`。

## 四层对接（每个插件按需选择）

1. **Agent 工具层**：`activate(manager, config)` 内三步注册：
   - `manager.register_toolset(name, {...})`（PluginManager 可见）
   - `create_custom_toolset(name, desc, [tool_defs])`（toolsets.py 的 TOOLSETS 可见）
   - `registry.register(tool_name, handler, toolset=..., description=..., parameters=...)`（工具表，模块导入时执行，Agent 上下文自动聚合，无需额外接线）
2. **服务层**：`service_launcher` 启动序列自动拉起（gateway→memory→supervisor→插件服务），自动健康等待 + 端口归属校验 + pid/log/状态管理。契约见 §5。
3. **Web UI 层**：supervisor 按 `web` 声明把 `static_dir`（相对插件目录）挂到 `mount_path`，`html=True` 直接服务 `entry`。前端通过 `/api/*` 与插件服务通信。
4. **配置层**：config.yaml 加 `<config_key>:` 段（enabled/port/db_path 等），段内容整体注入 `create_app(config)` / `activate(manager, config)`；`enabled=false` 时完全静默。

## 服务层契约（B2 实证，最高频踩坑点）

- `service.module` 格式固定 `模块路径:工厂函数名`；工厂**直接返回 FastAPI app**（无 wrapper/元组兼容层），接收**普通 dict**（config.yaml 段 + `name`/`port`/`service_port`）。
- **根路径 `/` 必须返回 `{"service": "<插件名>", ...}`**。launcher 的 `_health_endpoint_is_service(port, name)` 请求根路径验证端口归属，缺了它会误判"端口被他人占用"→ 反复重启。这是最高频故障，fake 插件只写 `/health` 不写 `/` 就是这个症状。
- `_health_endpoint_is_service(port, name)` 签名是 **(port, name)**，不是 (name, payload)；插件分支期望值 = 插件名本身。
- `_process_is_service` 靠子进程 cmdline 内嵌服务名匹配归属（后台 spawn 用 `python -c` 内联脚本）。
- **mem 规范绑定校验（`_sync_canonical_mem_binding_before_start` / `_verify_canonical_mem_import_source`）仅 core 服务执行**；插件服务自带依赖，前台线程与后台 script 中都有 `kind == "core"` 条件。
- 后台 spawn script 必须显式加载仓库根 .env：`Path(__file__).resolve().parents[4] / ".env"`（parents[2] 是错的，已修）。
- 若声明 `gateway_service_type`，launcher 会等待该 type 出现在 Gateway 注册表；`_required_gateway_service_types` 对插件读清单字段，core 仍走内置映射（supervisor→("supervisor","executor")）。

## 已落地的实现决策（改代码前必读，别推翻）

- registry.py 的协议校验**复用 `manifest.PluginManifest.from_mapping()`**（单一事实来源），registry 只做目录名==name 一致性 + entrypoint 可导入性检查，并保留 service/web 等扩展字段（PluginManifest 丢弃扩展字段，所以 registry 需持 raw manifest）。勿再写一套重复校验。
- `capabilities` 过滤只认 `_KNOWN_CAPABILITIES`（tools/service/web），memory 插件的 `["memory"]` 会被滤掉 —— 仅影响 descriptor.capabilities 展示，manifest raw 不受影响，正常现象。
- 启动顺序：`ensure_running` 的 `startup_order = ["gateway", "memory", "supervisor"] + _plugin_service_names()`。
- 插件服务的健康/注册等待在 `start_all` 第 4 步，逐插件：start → health 等待 → gateway 注册等待。
- 服务启动链路改动后，进程内重载要小心：`import service_launcher` 时模块级 `register_plugin_services()` 已跑，显式再调是幂等跳过 —— 冒烟测试必须在 monkeypatch PLUGINS_ROOT 之后再 import launcher。

## 验证步骤

1. 清单/发现：
   ```bash
   .venv/Scripts/python.exe -c "
   from voidcube.extensions.plugins.registry import discover_plugin_manifests, find_plugin_services, find_plugin_web_uis
   print([(d.name, d.capabilities) for d in discover_plugin_manifests()])"
   ```
2. 服务并入：`SERVICES` 含插件（kind=plugin/port/create_app/gateway_service_type）；`_plugin_service_names()` 正确。
3. **全链路测试（回归首选，已入库）**：`pytest tests/test_plugin_service_chain.py`（12 用例）是 B 系列全链路的正式回归套件：发现/启用/协议校验/坏清单隔离/空段不误报 → 激活幂等与失败隔离 → launcher 并入与 config/app 构建 → 真实 uvicorn 端口归属（随机空闲端口）→ web 静态挂载与缺失目录不阻断。改动 registry/launcher/ui_routes 后跑它；`scripts/plugin_chain_smoke.py` 保留作快速手动冒烟（临时目录假插件 → monkeypatch `PLUGINS_ROOT` + `reset_scan_cache()` → 发现/激活 → launcher 并入 → 真端口归属验证 → web 挂载），但回归判定以入库测试为准，别靠临时脚本。
4. 测试套件（提交前全跑）：
   - `pytest tests/test_skill_registry.py`（AGENTS.md 规则 5 固定自检，含耗时基准）
   - `pytest tests/test_plugin_manifest_contract.py tests/test_voidcube_launcher.py tests/test_tool_registry_dispatch.py`
   - `pytest tests/test_integration_policy.py tests/test_packaging_contract.py`（退役/打包扫描）
   - supervisor 套件 `tests/test_supervisor_*.py`
5. 真实插件落地后按 `plugins/README.md` §8 验证清单走一遍（含 `voidcube serve start` + `curl http://127.0.0.1:<插件端口>/`；serve 入口是 `.venv/Scripts/voidcube.exe serve`，不是 `python -m voidcube`）。

## 已知陷阱（本轮实测新增）

- **pytest 全链路测试四坑**（写/维护 `test_plugin_service_chain.py` 时实测）：
  1. `discover_plugin_manifests` 有扫描缓存（`_scan_cache`）：测试中途新增插件目录后必须 `pr.reset_scan_cache()`，否则新插件不可见（断言"静默通过"假绿）。
  2. sys.modules 污染：不同测试的 tmp_path 不同但插件模块名相同（`plugins.fake_goal`），fixture 开头必须删除上一测试遗留的 `plugins.<假插件名>*` 模块，否则 import 命中旧路径缓存。
  3. launcher 幂等标志：`sl._plugin_services_registered` 是模块级单例，fixture 中先置 False 再 `sl.register_plugin_services()` 让假插件并入 SERVICES；teardown 要 `SERVICES.pop(插件名)` 并把标志恢复 True，避免污染其它测试。
  4. 失败隔离测试的坏插件清单必须**过协议校验**（含 version/api_version/entrypoint），让它倒在 `activate()` 而不是 discovery —— 否则 `result.get("bad_plugin")` 是 None 而非 False。
- **端口归属测试必须真端口**：TestClient 是进程内调用，测不了 `_health_endpoint_is_service`；用 `socket.bind(("127.0.0.1", 0))` 取随机空闲端口起真实 uvicorn 线程（daemon + `server.should_exit` + join），避免固定端口在 CI/并行下冲突。
- `manifest.get("service") or {}` 陷阱：空段变 `{}` 后 `isinstance({}, dict)` 为真、`not {}.get("enabled", True)` 为假 → 不跳过 → 落到字段校验误报 warning。正确写法：`service = manifest.get("service"); if not isinstance(service, dict) or not service.get("enabled", True): continue`。
- 冒烟脚本里 fake 插件必须同时定义 `/` 与 `/health` 两个路由；`_health_endpoint_is_service` 只请求 `/`。
- `_health_endpoint_is_service` 参数顺序是 (port, name)，传反会静默返回 False（urlopen 到错误 URL）。
- 插件服务端口不能与 6000/6001/6002 及彼此冲突；改端口同时改清单 `service.port` 与 config 段（config 优先）。
- entrypoint 必须是 `importlib.import_module` 可直接导入的完整模块路径（`plugins.goal_manager`），不要写文件名。
- desktop/ 是 Electron 壳，不是插件 Web UI 的挂载点；真正的 Web 宿主是 supervisor(6002)。
- 服务子进程 PYTHONPATH 含 repo_root + Mem/src（`_service_python_path_entries`），顶层 `plugins` 包可直接 import。
- `search_files` 的 files 模式在大目录会截断结果，服务端/仓库结构勘察用 terminal `ls`/`find` 更可靠。
- **`ToolRegistry()` 是新的空实例**：检查插件工具是否注册必须用模块级单例 `from voidcube.extensions.tools.registry import registry`（registry.py 末尾 `registry = ToolRegistry()`）。用 `ToolRegistry()` 新建实例 `list_tools()` 恒为 0，会误判"工具没注册"。
- **Agent 工具快照在会话启动时注入**：插件工具经 ToolRegistry 动态注册后，当前会话的工具清单里看不到 `goal_*`，新会话才聚合（注入链：插件 `create_custom_toolset` → `cli_adapter.get_plugin_toolsets()` → toolsets.py）。用户问"你能用某插件吗"时，以 registry 单例实测为准，并说明新会话生效。

## 验证"Agent 能否使用某插件"的检查流程（2026-08-27 实测）

用户问"你能用目标管理器插件吗"时按此流程，全部步骤实测通过：

1. 插件本体：`ls plugins/<name>/` 看 plugin.json + `__init__.py`(activate) + tools/ + server.py + web/dist。
2. 发现+激活：
   ```bash
   .venv/Scripts/python.exe -c "
   from voidcube.extensions.plugins import registry as pr
   from voidcube.extensions.plugins.registry import reset_scan_cache
   reset_scan_cache()
   print(pr.activate_all_plugins())"
   ```
   返回 `{'goal_manager': True}` 即激活成功（memory 缺可调用 activate() 被跳过是正常现象，能力过滤同理）。
3. 工具注册（关键：用全局单例，别用 `ToolRegistry()` 新实例）：
   ```bash
   .venv/Scripts/python.exe -c "
   from voidcube.extensions.tools.registry import registry
   print(sorted(n for n in registry.list_tools() if 'goal' in n))
   print(registry.list_toolsets())"
   ```
4. 服务存活：`curl http://127.0.0.1:<插件端口>/`，根路径必须返回 `{"service": 插件名}`。
5. 工具集注入链确认：`cli_adapter.get_plugin_toolsets()` 存在且遍历 `_plugin_manager.get_toolsets()` 即说明新会话会带上插件工具。
6. 结论口径：插件可用 ≠ 当前会话工具清单里有 `goal_*`；工具快照是会话启动时注入的，插件工具注册后新会话自动聚合。
