# VoidCube 插件契约

插件位于 `plugins/<name>/`，以 `plugin.json` 为唯一清单来源。插件与主程序共享 Python 环境和生命周期；单个插件加载失败、清单损坏或静态资源缺失只记录 warning，不应阻断核心服务。

## 当前插件

- `plugins/memory/`：注册 MemAI 适配器，清单能力为 `memory`。
- `plugins/goal_manager/`：提供目标管理工具、独立服务和 Supervisor Web UI，服务默认端口 `6003`，健康路径 `/health`。

Goal Manager 的人工审核接口要求本机 UI Origin 和配置的 `human_review_token`，并使用
`X-VoidCube-Review-Token` 传递凭证。Origin 只是跨站请求约束，不能单独构成用户身份认证。
服务级 `goal_manager.service_token` 用于内部客户端；静态 UI 不应携带该服务 token。
UI 通过当前浏览器会话的 `sessionStorage.voidcube_review_token` 向 Supervisor 交换短期审核会话，
Goal Manager 只接受该短期凭证；长期配置凭证不会直接作为审核请求凭证使用。
`human_review_token` 和可选的 `review_session_db_path` 位于 `config.yaml` 的
`goal_manager` 段；Supervisor 会读取同一配置段，确保签发端和校验端使用同一凭证与会话库。
未配置 `review_session_db_path` 时，默认使用 `~/.VoidCube/runtime/goals/review_sessions.db`。

## 清单字段

```json
{
  "name": "goal_manager",
  "version": "0.1.0",
  "api_version": "1",
  "entrypoint": "plugins.goal_manager",
  "capabilities": ["tools", "service", "web"],
  "config_key": "goal_manager",
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

`name` 必须等于目录名；`entrypoint` 必须可被 `importlib.import_module` 导入；服务工厂接收配置字典和服务元数据；健康响应必须包含 `{"service": "<name>"}`。服务端口不可与核心服务或其他插件冲突，配置段中的 `enabled` 和 `port` 优先于清单默认值。

## 生命周期和边界

CLI 启动时扫描清单，按 Gateway、Memory、Supervisor、插件服务的顺序管理服务；运行时再幂等激活插件工具。插件服务不得直接导入 CLI 重型运行时，不得持有其他服务的 SQLite 连接。Web 只挂载静态产物，路径应避开 `/runtime*`、`/api*` 和 `/docs` 等保留前缀。

## 验证

```powershell
.venv\Scripts\python.exe -c "from voidcube.extensions.plugins.registry import discover_plugin_manifests, get_enabled_plugins; print([(d.name, d.capabilities) for d in discover_plugin_manifests()]); print([d.name for d in get_enabled_plugins()])"
.venv\Scripts\python.exe -m pytest tests/test_plugin_manifest_contract.py tests/test_plugin_service_chain.py -q
```

修改 `plugins/` 后运行 `scripts/run_ci_tests.py`。若同时修改技能文档或 registry，按 [docs/testing.md](../docs/testing.md) 单独运行技能 registry 测试，并保持技能和产品代码分开提交。
