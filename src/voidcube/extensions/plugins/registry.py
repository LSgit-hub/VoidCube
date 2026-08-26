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
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .manifest import PluginManifest, PluginManifestError

logger = logging.getLogger(__name__)

# 仓库根 = src/voidcube/extensions/plugins/registry.py 的 parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PLUGINS_ROOT = _REPO_ROOT / "plugins"
PLUGINS_ROOT: Path = _DEFAULT_PLUGINS_ROOT

_MANIFEST_FILENAME = "plugin.json"
_KNOWN_CAPABILITIES = frozenset(("tools", "service", "web", "memory"))
_RESERVED_WEB_PATHS = frozenset(("/api", "/runtime", "/docs", "/redoc", "/openapi.json"))
_REQUIRED_MANIFEST_KEYS = ("name", "entrypoint")

# 幂等缓存：同一进程内不重复激活
_activated: dict[str, tuple[str, int, int]] = {}
_scan_cache: Optional[List["PluginDescriptor"]] = None
_scan_cache_signature: tuple[tuple[str, int, int], ...] | None = None


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
    """Return the primary repository plugin root."""
    return PLUGINS_ROOT


def get_plugin_roots() -> tuple[Path, ...]:
    """Return repository, user-installed, and wheel plugin roots.

    A monkeypatched ``PLUGINS_ROOT`` remains an explicit isolated discovery
    root for tests and embedders.  Normal execution also checks the user's
    ``VOIDCUBE_HOME/plugins`` directory and package resources exposed by an
    installed wheel.
    """
    roots: list[Path] = [Path(PLUGINS_ROOT)]
    if Path(PLUGINS_ROOT).resolve() == _DEFAULT_PLUGINS_ROOT.resolve():
        try:
            from ...infrastructure.config.runtime_paths import get_VoidCube_home

            roots.insert(0, get_VoidCube_home() / "plugins")
        except Exception:
            pass
        try:
            spec = importlib.util.find_spec("plugins")
            for location in spec.submodule_search_locations or () if spec else ():
                roots.append(Path(location))
        except (ImportError, AttributeError, ValueError):
            pass

    result: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return tuple(result)


def get_plugin_import_paths() -> tuple[Path, ...]:
    """Return parent directories that make discovered plugin packages importable."""
    return tuple(root.parent for root in get_plugin_roots() if root.is_dir())


def prepare_plugin_import_path(root: Path) -> None:
    parent = str(root.resolve().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)


def discover_plugin_manifests() -> List[PluginDescriptor]:
    """扫描插件根目录，解析全部 plugin.json。

    - 损坏/缺关键字段的清单跳过并记日志（不抛异常）。
    - 结果按插件名排序，模块级缓存避免重复扫描。
    """
    global _scan_cache, _scan_cache_signature
    signature = _manifest_signature()
    if _scan_cache is not None and signature == _scan_cache_signature:
        return list(_scan_cache)

    discovered: List[PluginDescriptor] = []
    # Earlier roots have precedence.  This makes a user-installed plugin able
    # to replace the packaged copy without importing duplicate entrypoints.
    by_name: dict[str, PluginDescriptor] = {}
    for root in get_plugin_roots():
        if not root.is_dir():
            logger.debug("插件根目录不存在: %s", root)
            continue
        for manifest_path in sorted(root.glob(f"*/{_MANIFEST_FILENAME}")):
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                descriptor = _parse_manifest(manifest_path.parent, raw)
            except Exception as exc:  # 坏清单不阻断其它插件
                logger.warning("跳过损坏的插件清单 %s: %s", manifest_path, exc)
                continue
            if descriptor is not None and descriptor.name not in by_name:
                by_name[descriptor.name] = descriptor

    discovered = sorted(by_name.values(), key=lambda item: item.name)
    _scan_cache = discovered
    _scan_cache_signature = signature
    return list(discovered)


def _manifest_signature() -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for root in get_plugin_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{_MANIFEST_FILENAME}")):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


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

    raw_capabilities = raw.get("capabilities", [])
    capabilities = [str(c).strip() for c in raw_capabilities]
    unknown = sorted(set(capabilities) - _KNOWN_CAPABILITIES)
    if unknown:
        logger.warning("插件 %s 声明未知能力 %s，跳过", root.name, unknown)
        return None
    if len(capabilities) != len(set(capabilities)):
        capabilities = list(dict.fromkeys(capabilities))
    for capability in ("tools", "service", "web"):
        section = raw.get(capability)
        if section is not None and capability not in capabilities:
            logger.warning("插件 %s 声明了 %s 段但未声明对应 capability，跳过", root.name, capability)
            return None
        if section is not None and not isinstance(section, dict):
            logger.warning("插件 %s 的 %s 段必须是对象，跳过", root.name, capability)
            return None
    tools = raw.get("tools")
    if isinstance(tools, dict):
        module = str(tools.get("module") or "").strip()
        if module and not module.startswith("plugins."):
            logger.warning("插件 %s 的 tools.module 必须是 plugins.* 模块，跳过", root.name)
            return None
    service = raw.get("service")
    if "service" in capabilities:
        if not isinstance(service, dict):
            logger.warning("插件 %s 声明 service capability 但缺少 service 段，跳过", root.name)
            return None
        if not str(service.get("module") or "").strip() or ":" not in str(service.get("module") or ""):
            logger.warning("插件 %s 的 service.module 应为 module:factory 格式", root.name)
            return None
        try:
            service_port = int(service.get("port") or 0)
        except (TypeError, ValueError):
            service_port = 0
        if service_port <= 0:
            logger.warning("插件 %s 的 service.port 非法", root.name)
            return None
    if "web" in capabilities and not isinstance(raw.get("web"), dict):
        logger.warning("插件 %s 声明 web capability 但缺少 web 段，跳过", root.name)
        return None
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
    """Read a dynamic plugin config section without losing unknown keys."""
    if not descriptor.config_key:
        return {}
    try:
        from ...infrastructure.config.runtime_paths import get_config_path

        path = get_config_path()
        if path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            section = raw.get(descriptor.config_key)
            if isinstance(section, dict):
                return dict(section)
    except Exception as exc:
        logger.debug("读取插件原始配置段 %s 失败: %s", descriptor.config_key, exc)
    try:
        from ...infrastructure.config.system import get_config

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


def load_plugin_config(config_key: str | None, system_config: Any | None = None) -> Dict[str, Any]:
    """Load one plugin section for service subprocesses and test adapters."""
    if not config_key:
        return {}
    try:
        from ...infrastructure.config.runtime_paths import get_config_path

        path = get_config_path()
        if path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            section = raw.get(config_key)
            if isinstance(section, dict):
                return dict(section)
    except Exception:
        pass
    section = getattr(system_config, config_key, None) if system_config is not None else None
    if hasattr(section, "model_dump"):
        return dict(section.model_dump())
    return dict(section) if isinstance(section, dict) else {}


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
    identity = _manifest_identity(descriptor)
    if _activated.get(descriptor.name) == identity:
        return True
    if not is_plugin_enabled(descriptor):
        logger.info("插件 %s 未启用，跳过激活", descriptor.name)
        return False

    try:
        prepare_plugin_import_path(descriptor.root)
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
        manager = get_plugin_manager()
        register_plugin = getattr(manager, "register_plugin", None)
        if callable(register_plugin):
            register_plugin(descriptor.name, dict(descriptor.manifest))
        activate(manager, config)
        _activated[descriptor.name] = identity
        logger.info("插件 %s 激活成功（capabilities=%s）", descriptor.name, descriptor.capabilities)
        return True
    except Exception as exc:
        # 插件故障隔离：不抛出，由上层决定是否继续
        logger.error("插件 %s 激活失败: %s", descriptor.name, exc, exc_info=True)
        return False


def activate_all_plugins() -> Dict[str, bool]:
    """激活全部已启用插件。返回 {name: ok}；单个失败不影响其它插件。"""
    result: Dict[str, bool] = {}
    manager = _safe_plugin_manager()
    for descriptor in get_enabled_plugins():
        if manager is not None:
            register_plugin = getattr(manager, "register_plugin", None)
            if callable(register_plugin):
                register_plugin(descriptor.name, dict(descriptor.manifest))
        result[descriptor.name] = activate_plugin(descriptor)
    return result


def register_discovered_manifests() -> None:
    """Register discovered metadata without importing plugin entrypoints."""
    manager = _safe_plugin_manager()
    if manager is None:
        return
    register_plugin = getattr(manager, "register_plugin", None)
    if not callable(register_plugin):
        return
    for descriptor in discover_plugin_manifests():
        register_plugin(descriptor.name, dict(descriptor.manifest))


def _safe_plugin_manager() -> Any | None:
    try:
        from .manager import get_plugin_manager

        return get_plugin_manager()
    except Exception:
        return None


def _manifest_identity(descriptor: PluginDescriptor) -> tuple[str, int, int]:
    try:
        stat = descriptor.manifest_path.stat()
        return (str(descriptor.manifest_path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(descriptor.manifest_path), 0, 0)


# ── 对接点查询（供 launcher / supervisor 使用） ────────────────────────

def find_plugin_services() -> List[Dict[str, Any]]:
    """返回启用了 service 能力的插件服务声明，供 service_launcher 并入启动序列。

    每条：{name, port, create_app(module:factory), config_key, gateway_service_type, enabled}
    清单 service 段不存在或未启用则不返回。
    """
    services: List[Dict[str, Any]] = []
    used_ports = {6000, 6001, 6002}
    for descriptor in get_enabled_plugins():
        if "service" not in descriptor.capabilities:
            continue
        service = descriptor.manifest.get("service")
        if not isinstance(service, dict) or not service.get("enabled", True):
            continue
        create_app = str(service.get("module") or "").strip()
        if not create_app or ":" not in create_app:
            logger.warning("插件 %s 的 service.module 应为 module:factory 格式", descriptor.name)
            continue
        config = _load_plugin_config(descriptor)
        try:
            port = int(config.get("port") or service.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port <= 0:
            logger.warning("插件 %s 的 service.port 非法", descriptor.name)
            continue
        if port in used_ports:
            logger.warning("插件 %s 的 service.port=%s 与已有服务冲突，跳过", descriptor.name, port)
            continue
        used_ports.add(port)
        health_path = _normalize_health_path(service.get("health_path") or "/")
        if health_path is None:
            logger.warning("插件 %s 的 service.health_path 非法", descriptor.name)
            continue
        services.append(
            {
                "name": descriptor.name,
                "port": port,
                "create_app": create_app,
                "config_key": descriptor.config_key,
                "gateway_service_type": str(service.get("gateway_service_type") or "").strip() or None,
                "health_path": health_path,
            }
        )
    return services


def find_plugin_web_uis() -> List[Dict[str, Any]]:
    """返回启用了 web 能力的插件 UI 声明，供 supervisor 静态挂载。

    每条：{name, mount_path, static_dir(绝对路径), entry}
    """
    web_uis: List[Dict[str, Any]] = []
    used_mounts: set[str] = set()
    for descriptor in get_enabled_plugins():
        if "web" not in descriptor.capabilities:
            continue
        web = descriptor.manifest.get("web")
        if not isinstance(web, dict):
            continue
        mount_path = str(web.get("mount_path") or "").strip()
        static_rel = str(web.get("static_dir") or "").strip()
        mount_path = _normalize_mount_path(mount_path)
        if mount_path is None or not static_rel or Path(static_rel).is_absolute():
            logger.warning("插件 %s 的 web 声明缺少合法 mount_path/static_dir", descriptor.name)
            continue
        static_dir = (descriptor.root / static_rel).resolve()
        try:
            static_dir.relative_to(descriptor.root.resolve())
        except ValueError:
            logger.warning("插件 %s 的 web.static_dir 越出插件目录，跳过", descriptor.name)
            continue
        if mount_path in used_mounts:
            logger.warning("插件 %s 的 web.mount_path=%s 重复，跳过", descriptor.name, mount_path)
            continue
        entry = _normalize_relative_path(web.get("entry") or "index.html")
        if entry is None:
            logger.warning("插件 %s 的 web.entry 非法，跳过", descriptor.name)
            continue
        used_mounts.add(mount_path)
        web_uis.append(
            {
                "name": descriptor.name,
                "mount_path": mount_path,
                "static_dir": str(static_dir),
                "entry": entry,
            }
        )
    return web_uis


def _normalize_health_path(value: Any) -> str | None:
    path = str(value or "/").strip()
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path.split("/"):
        return None
    return path or "/"


def _normalize_mount_path(value: Any) -> str | None:
    path = str(value or "").strip().rstrip("/") or "/"
    if not path.startswith("/") or "?" in path or "#" in path:
        return None
    if ".." in path.split("/") or any(
        path == reserved or path.startswith(f"{reserved}/")
        for reserved in _RESERVED_WEB_PATHS
    ):
        return None
    return path


def _normalize_relative_path(value: Any) -> str | None:
    path = Path(str(value or "").strip())
    if not str(path) or path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def reset_scan_cache() -> None:
    """清除扫描/激活缓存（测试用）。"""
    global _scan_cache, _scan_cache_signature
    _scan_cache = None
    _scan_cache_signature = None
    _activated.clear()
