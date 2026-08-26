"""插件注册器 —— 补全 VoidCube 插件机制的"自动发现-激活"闭环。

职责：
  1. 扫描 ``plugins/*/plugin.json``，解析并校验清单（唯一事实来源）。
  2. 按能力激活插件入口：``import entrypoint`` → 调用 ``activate(manager, config)``。
  3. 为 service_launcher / supervisor 提供插件服务与 UI 声明（对接点查询）。

插件契约（详见 plugins/README.md）：
  - ``plugins/<name>/plugin.json``  清单：name / entrypoint / capabilities / config_key …
  - ``plugins/<name>/__init__.py``  入口：``activate(manager, config) -> None``（可选
    ``deactivate(manager)``）。activate 内部自行完成工具注册
    （``ToolRegistry.register`` + ``create_custom_toolset``）与后续能力初始化。
  - 插件启用状态：``config.yaml[config_key].enabled``（默认读清单顶层 ``enabled``，缺省 true）。
  - 单插件激活失败只记日志，不阻断核心启动（插件故障隔离）。

本模块保持无第三方依赖，仅使用标准库与同包 manager。
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .manifest import PluginManifest, PluginManifestError

logger = logging.getLogger(__name__)

# 仓库根 = src/voidcube/extensions/plugins/registry.py 的 parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGINS_ROOT: Path = _REPO_ROOT / "plugins"

_MANIFEST_FILENAME = "plugin.json"
_KNOWN_CAPABILITIES = ("tools", "service", "web")
_REQUIRED_MANIFEST_KEYS = ("name", "entrypoint")

# 幂等缓存：同一进程内不重复激活
_activated: set[str] = set()
_scan_cache: Optional[List["PluginDescriptor"]] = None


@dataclass
class PluginDescriptor:
    """解析后的插件清单视图。"""

    name: str
    root: Path
    manifest: Dict[str, Any] = field(default_factory=dict)
    entrypoint: str = ""
    capabilities: List[str] = field(default_factory=list)
    config_key: Optional[str] = None
    enabled: bool = True

    @property
    def manifest_path(self) -> Path:
        return self.root / _MANIFEST_FILENAME


def get_plugins_root() -> Path:
    """返回插件根目录（仓库根/plugins）。"""
    return PLUGINS_ROOT


def discover_plugin_manifests() -> List[PluginDescriptor]:
    """扫描插件根目录，解析全部 plugin.json。

    - 损坏/缺关键字段的清单跳过并记日志（不抛异常）。
    - 结果按插件名排序，模块级缓存避免重复扫描。
    """
    global _scan_cache
    if _scan_cache is not None:
        return list(_scan_cache)

    discovered: List[PluginDescriptor] = []
    if not PLUGINS_ROOT.is_dir():
        logger.debug("插件根目录不存在: %s", PLUGINS_ROOT)
        _scan_cache = []
        return []

    for manifest_path in sorted(PLUGINS_ROOT.glob(f"*/{_MANIFEST_FILENAME}")):
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            descriptor = _parse_manifest(manifest_path.parent, raw)
        except Exception as exc:  # 坏清单不阻断其它插件
            logger.warning("跳过损坏的插件清单 %s: %s", manifest_path, exc)
            continue
        if descriptor is not None:
            discovered.append(descriptor)

    _scan_cache = discovered
    return list(discovered)


def _parse_manifest(root: Path, raw: Dict[str, Any]) -> Optional[PluginDescriptor]:
    """校验并构造 PluginDescriptor；不合法返回 None。

    协议字段（name/version/api_version/capabilities）由 manifest.PluginManifest
    统一校验（单一事实来源），此处只做目录名一致性 + 入口可导入性检查，
    并保留 service/web 等扩展字段供 launcher/supervisor 消费。
    """
    if not isinstance(raw, dict):
        logger.warning("插件清单非法（非 JSON 对象）: %s", root)
        return None
    try:
        PluginManifest.from_mapping(raw)
    except PluginManifestError as exc:
        logger.warning("插件 %s 清单非法: %s", root.name, exc)
        return None

    name = str(raw.get("name") or "").strip()
    if name != root.name:
        logger.warning("插件 %s 清单 name=%r 与目录名不一致，跳过", root.name, name)
        return None
    entrypoint = str(raw.get("entrypoint") or "").strip()
    if not entrypoint:
        logger.warning("插件 %s 缺少 entrypoint，跳过", root.name)
        return None

    capabilities = [c for c in raw.get("capabilities", []) if c in _KNOWN_CAPABILITIES]
    config_key = (str(raw.get("config_key") or "").strip()) or None
    enabled = bool(raw.get("enabled", True))
    return PluginDescriptor(
        name=name,
        root=root,
        manifest=raw,
        entrypoint=entrypoint,
        capabilities=capabilities,
        config_key=config_key,
        enabled=enabled,
    )


def _load_plugin_config(descriptor: PluginDescriptor) -> Dict[str, Any]:
    """读取 config.yaml 中插件配置段（config_key），缺省空 dict。"""
    if not descriptor.config_key:
        return {}
    try:
        from ..config.system import get_config

        config = get_config()
        section = getattr(config, descriptor.config_key, None)
        if section is None:
            return {}
        if hasattr(section, "model_dump"):
            return section.model_dump()
        if isinstance(section, dict):
            return dict(section)
    except Exception as exc:
        logger.debug("读取插件配置段 %s 失败: %s", descriptor.config_key, exc)
    return {}


def is_plugin_enabled(descriptor: PluginDescriptor) -> bool:
    """启用判定：config.yaml[config_key].enabled 优先，其次清单顶层 enabled。"""
    if not descriptor.config_key:
        return descriptor.enabled
    try:
        section = _load_plugin_config(descriptor)
        section_enabled = section.get("enabled")
        if section_enabled is not None:
            return bool(section_enabled)
    except Exception:
        pass
    return descriptor.enabled


def get_enabled_plugins() -> List[PluginDescriptor]:
    """返回当前启用的插件清单（按名排序）。"""
    return [d for d in discover_plugin_manifests() if is_plugin_enabled(d)]


def activate_plugin(descriptor: PluginDescriptor) -> bool:
    """激活单个插件：import 入口并调用 activate(manager, config)。

    约定入口：
      def activate(manager: PluginManager, config: dict) -> None: ...
      def deactivate(manager: PluginManager) -> None: ...（可选）
    """
    if descriptor.name in _activated:
        return True
    if not is_plugin_enabled(descriptor):
        logger.info("插件 %s 未启用，跳过激活", descriptor.name)
        return False

    try:
        module = importlib.import_module(descriptor.entrypoint)
        activate = getattr(module, "activate", None)
        if not callable(activate):
            logger.warning(
                "插件 %s 入口 %s 缺少可调用 activate()，跳过激活",
                descriptor.name,
                descriptor.entrypoint,
            )
            return False
        from .manager import get_plugin_manager

        config = _load_plugin_config(descriptor)
        activate(get_plugin_manager(), config)
        _activated.add(descriptor.name)
        logger.info("插件 %s 激活成功（capabilities=%s）", descriptor.name, descriptor.capabilities)
        return True
    except Exception as exc:
        # 插件故障隔离：不抛出，由上层决定是否继续
        logger.error("插件 %s 激活失败: %s", descriptor.name, exc, exc_info=True)
        return False


def activate_all_plugins() -> Dict[str, bool]:
    """激活全部已启用插件。返回 {name: ok}；单个失败不影响其它插件。"""
    result: Dict[str, bool] = {}
    for descriptor in get_enabled_plugins():
        result[descriptor.name] = activate_plugin(descriptor)
    return result


# ── 对接点查询（供 launcher / supervisor 使用） ────────────────────────

def find_plugin_services() -> List[Dict[str, Any]]:
    """返回启用了 service 能力的插件服务声明，供 service_launcher 并入启动序列。

    每条：{name, port, create_app(module:factory), config_key, gateway_service_type, enabled}
    清单 service 段不存在或未启用则不返回。
    """
    services: List[Dict[str, Any]] = []
    for descriptor in get_enabled_plugins():
        service = descriptor.manifest.get("service")
        if not isinstance(service, dict) or not service.get("enabled", True):
            continue
        create_app = str(service.get("module") or "").strip()
        if not create_app or ":" not in create_app:
            logger.warning("插件 %s 的 service.module 应为 module:factory 格式", descriptor.name)
            continue
        try:
            port = int(service.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port <= 0:
            logger.warning("插件 %s 的 service.port 非法", descriptor.name)
            continue
        services.append(
            {
                "name": descriptor.name,
                "port": port,
                "create_app": create_app,
                "config_key": descriptor.config_key,
                "gateway_service_type": str(service.get("gateway_service_type") or "").strip() or None,
            }
        )
    return services


def find_plugin_web_uis() -> List[Dict[str, Any]]:
    """返回启用了 web 能力的插件 UI 声明，供 supervisor 静态挂载。

    每条：{name, mount_path, static_dir(绝对路径), entry}
    """
    web_uis: List[Dict[str, Any]] = []
    for descriptor in get_enabled_plugins():
        web = descriptor.manifest.get("web")
        if not isinstance(web, dict):
            continue
        mount_path = str(web.get("mount_path") or "").strip()
        static_rel = str(web.get("static_dir") or "").strip()
        if not mount_path.startswith("/") or not static_rel:
            logger.warning("插件 %s 的 web 声明缺少合法 mount_path/static_dir", descriptor.name)
            continue
        static_dir = (descriptor.root / static_rel).resolve()
        web_uis.append(
            {
                "name": descriptor.name,
                "mount_path": mount_path,
                "static_dir": str(static_dir),
                "entry": str(web.get("entry") or "index.html"),
            }
        )
    return web_uis


def reset_scan_cache() -> None:
    """清除扫描/激活缓存（测试用）。"""
    global _scan_cache
    _scan_cache = None
    _activated.clear()
