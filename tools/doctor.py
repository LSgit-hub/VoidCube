import os
import platform
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"


def _t(key: str, default: str = "", **kwargs) -> str:
    try:
        from VoidCube_cli.i18n import t
        return t(key, default=default, **kwargs)
    except Exception:
        return default.format(**kwargs) if kwargs else default


def run_doctor() -> Dict[str, Any]:
    checks = []
    checks.extend(_check_python())
    checks.extend(_check_psutil())
    checks.extend(_check_package_manager())
    checks.extend(_check_docker())
    checks.extend(_check_api_config())
    checks.extend(_check_i18n())
    checks.extend(_check_disk_space())
    checks.extend(_check_network())

    passed = sum(1 for c in checks if c["status"] == "ok")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")

    return {
        "success": True,
        "total": len(checks),
        "passed": passed,
        "warnings": warnings,
        "failed": failed,
        "checks": checks,
        "healthy": failed == 0,
    }


def _check_python() -> List[Dict[str, Any]]:
    ver = platform.python_version()
    major, minor = map(int, ver.split(".")[:2])
    if major >= 3 and minor >= 11:
        return [{"name": "Python", "status": "ok", "detail": f"Python {ver}"}]
    return [{"name": "Python", "status": "warn", "detail": f"Python {ver} (推荐 >=3.11)"}]


def _check_psutil() -> List[Dict[str, Any]]:
    try:
        import psutil
        return [{"name": "psutil", "status": "ok", "detail": f"psutil {psutil.__version__}"}]
    except ImportError:
        return [{"name": "psutil", "status": "warn", "detail": "未安装，系统信息受限"}]


def _check_package_manager() -> List[Dict[str, Any]]:
    if IS_WINDOWS:
        try:
            r = subprocess.run(["winget", "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return [{"name": "包管理器", "status": "ok", "detail": f"winget {r.stdout.strip()}"}]
        except FileNotFoundError:
            return [{"name": "包管理器", "status": "warn", "detail": "winget 不可用"}]
        except subprocess.TimeoutExpired:
            return [{"name": "包管理器", "status": "warn", "detail": "winget 超时"}]
        except Exception as e:
            logger.debug("Unexpected error checking winget: %s", e)
            return [{"name": "包管理器", "status": "warn", "detail": f"检测失败: {e}"}]

    for path, name in [
        ("/usr/bin/apt", "apt"),
        ("/usr/bin/yum", "yum"),
        ("/usr/bin/dnf", "dnf"),
        ("/usr/bin/pacman", "pacman"),
    ]:
        if os.path.exists(path):
            return [{"name": "包管理器", "status": "ok", "detail": name}]
    return [{"name": "包管理器", "status": "warn", "detail": "未检测到"}]


def _check_docker() -> List[Dict[str, Any]]:
    try:
        r = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return [{"name": "Docker", "status": "ok", "detail": r.stdout.strip()}]
        return [{"name": "Docker", "status": "warn", "detail": f"Docker 存在但返回错误: {r.stderr.strip() or '非零退出码'}"}]
    except FileNotFoundError:
        return [{"name": "Docker", "status": "warn", "detail": "未安装"}]
    except subprocess.TimeoutExpired:
        return [{"name": "Docker", "status": "warn", "detail": "超时"}]
    except Exception as e:
        logger.debug("Unexpected error checking docker: %s", e)
        return [{"name": "Docker", "status": "warn", "detail": f"检测失败: {e}"}]


def _check_api_config() -> List[Dict[str, Any]]:
    checks = []
    try:
        from VoidCube_core.constants import get_VoidCube_home
        env_path = get_VoidCube_home() / ".env"
    except Exception:
        env_path = Path.home() / ".VoidCube" / ".env"

    if not env_path.exists():
        checks.append({"name": "API 配置", "status": "fail", "detail": ".env 文件不存在，请运行 /setup"})
        return checks

    has_key = False
    has_model = False
    has_url = False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if (line.startswith("VOIDCUBE_API_KEY=") or line.startswith("OPENROUTER_API_KEY=")) and len(line.split("=", 1)[1]) > 0:
            has_key = True
        if line.startswith("VOIDCUBE_MODEL=") or line.startswith("LLM_MODEL="):
            has_model = True
        if line.startswith("VOIDCUBE_BASE_URL=") or line.startswith("LLM_BASE_URL="):
            has_url = True

    if has_key and has_model and has_url:
        checks.append({"name": "API 配置", "status": "ok", "detail": "API Key/Model/Base URL 已配置"})
    elif has_key:
        checks.append({"name": "API 配置", "status": "warn", "detail": "API Key 已设置，但 Model/Base URL 可能缺失"})
    else:
        checks.append({"name": "API 配置", "status": "fail", "detail": "API Key 未设置"})

    return checks


def _check_i18n() -> List[Dict[str, Any]]:
    try:
        from VoidCube_cli.i18n import get_i18n
        h = get_i18n()
        locale = h.get_current_locale()
        available = h.get_available_locales()
        return [{"name": "i18n", "status": "ok", "detail": f"当前语言: {locale}, 可用: {', '.join(available)}"}]
    except Exception as e:
        return [{"name": "i18n", "status": "warn", "detail": f"i18n 初始化异常: {e}"}]


def _check_disk_space() -> List[Dict[str, Any]]:
    try:
        import psutil
        usage = psutil.disk_usage("C:\\" if IS_WINDOWS else "/")
        free_gb = round(usage.free / (1024 ** 3), 1)
        percent = usage.percent
        if percent > 95:
            return [{"name": "磁盘空间", "status": "fail", "detail": f"已用 {percent}%, 仅剩 {free_gb}GB"}]
        elif percent > 85:
            return [{"name": "磁盘空间", "status": "warn", "detail": f"已用 {percent}%, 剩余 {free_gb}GB"}]
        return [{"name": "磁盘空间", "status": "ok", "detail": f"已用 {percent}%, 剩余 {free_gb}GB"}]
    except ImportError:
        return [{"name": "磁盘空间", "status": "warn", "detail": "psutil 未安装，无法检测磁盘空间"}]
    except Exception as e:
        logger.debug("Unexpected error checking disk space: %s", e)
        return [{"name": "磁盘空间", "status": "warn", "detail": f"检测失败: {e}"}]


def _check_network() -> List[Dict[str, Any]]:
    try:
        import socket
        with socket.create_connection(("8.8.8.8", 53), timeout=3):
            return [{"name": "网络", "status": "ok", "detail": "网络可达"}]
    except Exception:
        return [{"name": "网络", "status": "warn", "detail": "网络可能不可达"}]
