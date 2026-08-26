"""B 系列插件系统全链路测试：registry 发现/激活 → launcher 服务并入 → supervisor web 挂载。

覆盖 plugins/README.md 契约：
- 清单解析（service/web/config_key/enabled）+ 协议校验复用 manifest.PluginManifest
- 无 config 段时回退清单 enabled 的启用判定
- activate(manager, config) 幂等激活与单插件失败隔离
- service 声明 → launcher SERVICES 并入 → config/app 构建 → 端口归属判定（真实 uvicorn）
- web 声明 → supervisor 静态挂载（html=True，缺失目录不阻断）
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voidcube.extensions.plugins import manager as pm
from voidcube.extensions.plugins import registry as pr
from voidcube.infrastructure.gateway import service_launcher as sl
from voidcube.systems.supervisor.ui_routes import mount_plugin_web_routes

PLUGIN_NAME = "fake_goal"
CORE_SERVICES = ("gateway", "memory", "supervisor")

FAKE_MANIFEST = {
    "name": PLUGIN_NAME,
    "version": "0.1.0",
    "api_version": "1",
    "capabilities": ["tools", "service", "web"],
    "entrypoint": f"plugins.{PLUGIN_NAME}",
    "config_key": "goal_manager",
    "service": {
        "enabled": True,
        "port": 6010,
        "module": f"plugins.{PLUGIN_NAME}.server:create_app",
        "gateway_service_type": "goal",
    },
    "web": {"mount_path": "/fake-goal", "static_dir": "web/dist", "entry": "index.html"},
}

FAKE_SERVER_PY = (
    "from fastapi import FastAPI\n"
    "def create_app(config):\n"
    "    app = FastAPI()\n"
    "    @app.get('/')\n"
    "    def root():\n"
    "        return {'service': 'fake_goal', 'status': 'ok', 'port': config.get('service_port')}\n"
    "    @app.get('/health')\n"
    "    def health():\n"
    "        return {'service': 'fake_goal', 'status': 'ok', 'port': config.get('service_port')}\n"
    "    return app\n"
)

FAKE_INIT_PY = (
    "def activate(manager, config):\n"
    "    manager._fake_activated = config\n"
    "def deactivate(manager):\n"
    "    pass\n"
)


class _DummyManager:
    _fake_activated = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """构造临时假插件树，并让 launcher 将该插件并入 SERVICES；测试结束自动恢复。"""
    # 清理上一测试遗留的假插件模块（模块名相同、路径不同，避免命中旧缓存）
    for mod in list(sys.modules):
        if mod.startswith("plugins.") and mod.split(".", 2)[1] in (
            PLUGIN_NAME, "bad_plugin", "plain_plugin", "broken_web",
        ):
            del sys.modules[mod]

    root = tmp_path / "plugins"
    plugin_root = root / PLUGIN_NAME
    (plugin_root / "web" / "dist").mkdir(parents=True)
    (plugin_root / "web" / "dist" / "index.html").write_text(
        "<h1>goal ui</h1>", encoding="utf-8"
    )
    (plugin_root / "plugin.json").write_text(
        json.dumps(FAKE_MANIFEST, ensure_ascii=False), encoding="utf-8"
    )
    (plugin_root / "__init__.py").write_text(FAKE_INIT_PY, encoding="utf-8")
    (plugin_root / "server.py").write_text(FAKE_SERVER_PY, encoding="utf-8")

    # 指向假插件根（PLUGINS_ROOT + sys.path 顶层命名空间）
    monkeypatch.setattr(pr, "PLUGINS_ROOT", root)
    pr.reset_scan_cache()
    monkeypatch.syspath_prepend(str(tmp_path))

    # 重置 launcher 幂等标志，重新并入插件服务
    sl._plugin_services_registered = False
    sl.register_plugin_services()

    # activate 用 dummy manager（隔离真实 PluginManager）
    monkeypatch.setattr(pm, "get_plugin_manager", lambda: _DummyManager())
    yield root

    # 清理：移除注入的插件服务，恢复"已注册"状态
    sl.SERVICES.pop(PLUGIN_NAME, None)
    sl._plugin_services_registered = True
    pr.reset_scan_cache()


# ── 1. 发现 / 启用 / 协议校验 ───────────────────────────────────

def test_discover_parses_extended_manifest(plugin_env):
    descs = pr.discover_plugin_manifests()
    assert len(descs) == 1
    desc = descs[0]
    assert desc.name == PLUGIN_NAME
    assert desc.config_key == "goal_manager"
    assert desc.manifest["service"]["gateway_service_type"] == "goal"
    assert desc.manifest["web"]["mount_path"] == "/fake-goal"


def test_enabled_defaults_to_manifest_without_config(plugin_env):
    descs = pr.discover_plugin_manifests()
    assert pr.is_plugin_enabled(descs[0]) is True


def test_bad_manifest_skipped_and_isolated(plugin_env):
    bad = plugin_env / "bad_plugin"
    bad.mkdir()
    (bad / "plugin.json").write_text(
        json.dumps({"name": "bad_plugin"}), encoding="utf-8"  # 缺 entrypoint，协议非法
    )
    pr.reset_scan_cache()  # 新增目录需显式失效缓存
    names = [d.name for d in pr.discover_plugin_manifests()]
    assert PLUGIN_NAME in names
    assert "bad_plugin" not in names


def test_empty_service_web_sections_produce_no_false_warnings(plugin_env, caplog):
    """无 service/web 段的插件不应触发误报 warning（回归：空 dict 走了段校验）。"""
    plain = plugin_env / "plain_plugin"
    plain.mkdir()
    (plain / "plugin.json").write_text(
        json.dumps(
            {"name": "plain_plugin", "api_version": "1", "entrypoint": "plugins.plain_plugin"}
        ),
        encoding="utf-8",
    )
    pr.reset_scan_cache()
    with caplog.at_level("WARNING", logger="voidcube.extensions.plugins.registry"):
        services = pr.find_plugin_services()
        web_uis = pr.find_plugin_web_uis()
    # 只返回 fake_goal 的合法声明；plain_plugin 的缺失段不产生误报
    assert [s["name"] for s in services] == [PLUGIN_NAME]
    assert [w["name"] for w in web_uis] == [PLUGIN_NAME]
    assert not any("service.module" in r.message or "mount_path" in r.message
                   for r in caplog.records)


# ── 2. 激活 ─────────────────────────────────────────────────────

def test_activate_success_and_idempotent(plugin_env):
    result = pr.activate_all_plugins()
    assert result.get(PLUGIN_NAME) is True
    again = pr.activate_all_plugins()
    assert again.get(PLUGIN_NAME) is True


def test_activate_failure_isolated(plugin_env):
    bad = plugin_env / "bad_plugin"
    bad.mkdir()
    (bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "bad_plugin",
                "version": "0.1.0",
                "api_version": "1",
                "entrypoint": "plugins.bad_plugin",
            }
        ),
        encoding="utf-8",
    )
    (bad / "__init__.py").write_text(
        "def activate(manager, config):\n    raise RuntimeError('boom')\n", encoding="utf-8"
    )
    pr.reset_scan_cache()
    result = pr.activate_all_plugins()
    assert result.get(PLUGIN_NAME) is True
    assert result.get("bad_plugin") is False


# ── 3. launcher 服务并入与 app 构建 ─────────────────────────────

def test_service_merged_into_launcher(plugin_env):
    assert PLUGIN_NAME in sl.SERVICES
    info = sl.SERVICES[PLUGIN_NAME]
    assert info.kind == "plugin"
    assert info.port == 6010
    assert info.gateway_service_type == "goal"
    assert "create_app" in info.create_app


def test_service_config_and_app_build(plugin_env):
    sys_cfg = SimpleNamespace(goal_manager={"enabled": True, "debug": False})
    cfg = sl._build_service_config(PLUGIN_NAME, 6010, sys_cfg)
    assert cfg["service_port"] == 6010
    assert cfg["debug"] is False
    assert cfg["name"] == PLUGIN_NAME

    app = sl._build_service_app(PLUGIN_NAME, 6010)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == PLUGIN_NAME


def test_gateway_service_types_contract(plugin_env):
    assert sl._required_gateway_service_types(PLUGIN_NAME) == ("goal",)
    # core 服务回归
    assert sl._required_gateway_service_types("supervisor") == ("supervisor", "executor")
    assert sl._required_gateway_service_types("memory") == ("memory",)


def test_port_identity_with_real_server(plugin_env):
    """真实 uvicorn 下，端口归属判定能认出插件服务（根路径 / 返回 service 标识）。"""
    import uvicorn

    port = _free_port()
    app = sl._build_service_app(PLUGIN_NAME, port)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=1
                ) as resp:
                    assert json.loads(resp.read().decode())["service"] == PLUGIN_NAME
                    break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("插件服务未在预期时间内就绪")

        assert sl._health_endpoint_is_service(port, PLUGIN_NAME) is True
        assert sl._health_endpoint_is_service(port, "gateway") is False
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ── 4. supervisor web 挂载 ──────────────────────────────────────

def test_web_mount_serves_static_ui(plugin_env):
    app = FastAPI()
    mount_plugin_web_routes(app)
    resp = TestClient(app).get("/fake-goal/")
    assert resp.status_code == 200
    assert "goal ui" in resp.text


def test_web_mount_missing_dir_does_not_block(plugin_env):
    """web.static_dir 缺失只记 warning，挂载其它插件不受影响。"""
    broken = plugin_env / "broken_web"
    broken.mkdir()
    (broken / "plugin.json").write_text(
        json.dumps(
            {
                "name": "broken_web",
                "api_version": "1",
                "entrypoint": "plugins.broken_web",
                "web": {"mount_path": "/broken", "static_dir": "web/dist"},
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    mount_plugin_web_routes(app)  # 不抛异常
    resp = TestClient(app).get("/fake-goal/")
    assert resp.status_code == 200
