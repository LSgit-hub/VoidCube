"""VoidCube 插件全链路冒烟（B1 registry + B2 launcher + B3 web 挂载）。

用法：cd <repo_root> && .venv/Scripts/python.exe <本文件>
要点（都是踩坑换来的）：
- 必须在 import service_launcher 之前 monkeypatch registry.PLUGINS_ROOT，
  否则模块级 register_plugin_services() 扫描的是真实 plugins/ 目录。
- fake 插件 server 必须同时定义 "/" 与 "/health"：
  _health_endpoint_is_service(port, name) 只请求根路径 "/"。
- 端口归属校验必须起真实 uvicorn 线程（TestClient 进程内测不了）。
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, "."); sys.path.insert(0, "Mem/src")

from voidcube.extensions.plugins import registry as pr

# ── 构造假插件树 ─────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="vc_plugin_smoke_"))
(root := tmp / "plugins").mkdir()
(fake := root / "fake_goal").mkdir()
fake_static = fake / "web" / "dist"; fake_static.mkdir(parents=True)
(fake_static / "index.html").write_text("<h1>goal ui</h1>", encoding="utf-8")
(fake / "plugin.json").write_text(json.dumps({
    "name": "fake_goal",
    "version": "0.1.0",
    "api_version": "1",
    "capabilities": ["tools", "service", "web"],
    "entrypoint": "plugins.fake_goal",
    "config_key": "goal_manager",
    "service": {"enabled": True, "port": 6010,
                "module": "plugins.fake_goal.server:create_app",
                "gateway_service_type": "goal"},
    "web": {"mount_path": "/fake-goal", "static_dir": "web/dist", "entry": "index.html"},
}, ensure_ascii=False), encoding="utf-8")
(fake / "__init__.py").write_text(
    "def activate(manager, config):\n    manager._fake_activated = config\n", encoding="utf-8")
# 注意："/" 与 "/health" 都要定义
(fake / "server.py").write_text(
    "from fastapi import FastAPI\n"
    "def create_app(config):\n"
    "    app = FastAPI()\n"
    "    @app.get('/')\n"
    "    def root():\n"
    "        return {'service': 'fake_goal', 'status': 'ok', 'port': config.get('service_port')}\n"
    "    @app.get('/health')\n"
    "    def health():\n"
    "        return {'service': 'fake_goal', 'status': 'ok'}\n"
    "    return app\n", encoding="utf-8")

sys.path.insert(0, str(root.parent))   # 让 import plugins.fake_goal 生效
pr.PLUGINS_ROOT = root
pr.reset_scan_cache()

# ── 1. 发现/激活 ─────────────────────────────
descs = pr.discover_plugin_manifests()
print("发现插件:", [(d.name, d.capabilities, d.config_key) for d in descs])
assert len(descs) == 1 and descs[0].name == "fake_goal"

from voidcube.extensions.plugins import manager as pm
orig = pm.get_plugin_manager
pm.get_plugin_manager = lambda: type("M", (), {"_fake_activated": None})()
try:
    result = pr.activate_all_plugins()
    print("激活结果:", result)
    assert result.get("fake_goal") is True
finally:
    pm.get_plugin_manager = orig

# ── 2. 服务声明 -> launcher 并入 ──────────────
svc_specs = pr.find_plugin_services()
assert svc_specs and svc_specs[0]["port"] == 6010 and svc_specs[0]["gateway_service_type"] == "goal"

# import launcher 必须在此之后（模块级注册会扫描当前 PLUGINS_ROOT）
from voidcube.infrastructure.gateway import service_launcher as sl
sl.register_plugin_services()
info = sl.SERVICES["fake_goal"]
print("SERVICES[fake_goal]:", info.kind, info.port, info.gateway_service_type)
assert info.kind == "plugin" and info.port == 6010

class NS: pass
sys_cfg = NS(); sys_cfg.goal_manager = {"enabled": True, "debug": False}
svc_cfg = sl._build_service_config("fake_goal", 6010, sys_cfg)
assert svc_cfg["service_port"] == 6010 and svc_cfg["debug"] is False
app = sl._build_service_app("fake_goal", 6010)
assert sl._required_gateway_service_types("fake_goal") == ("goal",)

# 端口归属验证：必须真实 uvicorn 线程
import threading, time, uvicorn, urllib.request
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=6010, log_level="error"))
th = threading.Thread(target=server.run, daemon=True); th.start()
for _ in range(100):
    if getattr(server, "started", False):
        break
    time.sleep(0.05)
for _ in range(100):
    try:
        urllib.request.urlopen("http://127.0.0.1:6010/", timeout=1); break
    except Exception:
        time.sleep(0.1)
assert sl._health_endpoint_is_service(6010, "fake_goal") is True
assert sl._health_endpoint_is_service(6010, "gateway") is False
server.should_exit = True; th.join(timeout=5)
print("端口归属验证通过")

# ── 3. web 挂载 ──────────────────────────────
from fastapi import FastAPI
from fastapi.testclient import TestClient
host = FastAPI()
from voidcube.systems.supervisor.ui_routes import mount_plugin_web_routes
mount_plugin_web_routes(host)
r2 = TestClient(host).get("/fake-goal/")
print("挂载后 /fake-goal/:", r2.status_code)
assert r2.status_code == 200 and "goal ui" in r2.text

# ── 4. cleanup ───────────────────────────────
pr.reset_scan_cache()
pr.PLUGINS_ROOT = Path(".").resolve() / "plugins"
sys.path.remove(str(root.parent))
shutil.rmtree(tmp, ignore_errors=True)
print("=== 全部通过 ===")
