"""
配置与 agent 运行时诊断。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator, List, Optional
from urllib.parse import urlparse


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ConfigIssue:
    """配置问题"""

    severity: Severity
    key_path: str
    message: str
    suggestion: str


@dataclass
class AgentCheck:
    """Agent 工具链诊断项。"""

    severity: Severity
    name: str
    message: str
    suggestion: str = ""
    details: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def load_config() -> dict:
    """加载当前配置"""
    try:
        from VoidCube_app.config import load_config as _load_config

        return _load_config()
    except Exception:
        return {}


def validate_config() -> List[ConfigIssue]:
    """验证配置完整性，返回问题列表。"""
    cfg = load_config()
    issues: list[ConfigIssue] = []
    issues.extend(_validate_api_a_config(cfg))
    issues.extend(_validate_api_b_config(cfg))
    return issues


def _validate_api_a_config(cfg: dict[str, Any]) -> list[ConfigIssue]:
    """Validate API-A: the main user-facing CLI agent."""
    issues: list[ConfigIssue] = []
    runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    providers_cfg = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    active_provider = str(runtime_cfg.get("active_provider") or "").strip()

    if not providers_cfg:
        issues.append(
            ConfigIssue(
                severity=Severity.INFO,
                key_path="providers",
                message="尚未配置任何 Provider",
                suggestion="首次启动这是允许的。运行 /api 添加 Provider 后，再用 /model 切换模型",
            )
        )
        return issues

    if not active_provider:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="runtime.active_provider",
                message="未设置当前激活 Provider",
                suggestion="运行 /api 选择当前 Provider",
            )
        )
        return issues

    active_cfg = providers_cfg.get(active_provider)
    if not isinstance(active_cfg, dict):
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="runtime.active_provider",
                message=f"当前激活 Provider '{active_provider}' 不存在于 providers 配置中",
                suggestion="运行 /api 重新选择有效的 Provider",
            )
        )
        return issues

    selected_model = str(active_cfg.get("selected_model") or "").strip()
    if not selected_model:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path=f"providers.{active_provider}.selected_model",
                message="当前激活 Provider 未选择模型",
                suggestion="运行 /model 为当前 Provider 选择模型",
            )
        )

    auth_mode = str(active_cfg.get("auth_mode") or "env").strip().lower()
    if auth_mode == "none":
        return issues

    try:
        from VoidCube_cli.api_config import api_a_key_configured

        key_configured = api_a_key_configured(active_cfg)
    except Exception:
        from VoidCube_app.provider_auth import has_usable_secret

        key_configured = has_usable_secret(str(active_cfg.get("api_key") or ""))

    if not key_configured:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path=f"providers.{active_provider}.api_key",
                message="API-A 当前激活 Provider 缺少可用凭证",
                suggestion="运行 /api 配置该 Provider 的 API Key",
            )
        )

    return issues


def _validate_api_b_config(cfg: dict[str, Any]) -> list[ConfigIssue]:
    """Validate API-B: Mem/supervisor autonomous-chain LLM config."""
    issues: list[ConfigIssue] = []
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    llm_cfg = memory_cfg.get("llm") if isinstance(memory_cfg.get("llm"), dict) else {}

    provider = str(llm_cfg.get("provider") or "").strip().lower()
    if not provider:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.provider",
                message="API-B 未配置 Mem/Supervisor 模型 Provider",
                suggestion="运行 /api -> 3 记忆系统模型配置，选择 API-B Provider",
            )
        )
        return issues

    try:
        from VoidCube_cli.api_config import (
            memory_llm_provider_defaults,
            memory_llm_provider_options,
        )

        supported_providers = {key for key, _label in memory_llm_provider_options()}
        defaults = memory_llm_provider_defaults(provider)
    except Exception:
        supported_providers = {"openrouter", "deepseek", "openai", "ollama", "custom"}
        defaults = {}

    if provider not in supported_providers:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.provider",
                message=f"API-B Provider '{provider}' 不在 Mem 支持列表中",
                suggestion="运行 /api -> 3 记忆系统模型配置，选择内置或自定义 Provider",
            )
        )
        return issues

    model = str(llm_cfg.get("model") or "").strip()
    if not model:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.model",
                message=f"API-B Provider '{provider}' 未选择模型",
                suggestion="运行 /api -> 3 记忆系统模型配置，为 API-B 选择模型",
            )
        )

    base_url = str(llm_cfg.get("base_url") or defaults.get("base_url") or "").strip()
    parsed_base_url = urlparse(base_url)
    if not base_url or parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.base_url",
                message=f"API-B Provider '{provider}' 缺少有效的 http(s) Base URL",
                suggestion="运行 /api -> 3 记忆系统模型配置，填写 OpenAI 兼容 API 根地址",
            )
        )
    elif _is_local_gateway_loop_base_url(base_url):
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.base_url",
                message="API-B base_url 指向本地 Gateway，会把 Mem/Supervisor 绕回 API-A",
                suggestion="运行 /api -> 3 记忆系统模型配置，保存该 Provider 的直接模型 endpoint",
            )
        )

    if provider == "ollama":
        return issues

    api_key_env = str(llm_cfg.get("api_key_env") or defaults.get("api_key_env") or "").strip()
    if not api_key_env:
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.api_key_env",
                message=f"API-B Provider '{provider}' 未配置专用 key 环境变量",
                suggestion="运行 /api -> 3 记忆系统模型配置，让 API-B 写入 memory.llm.api_key_env",
            )
        )
        return issues

    from VoidCube_cli.api_config import (
        credential_sources_have_usable_secret,
        provider_credential_sources,
    )

    credential_sources = provider_credential_sources(provider, api_key_env)
    if not credential_sources_have_usable_secret(credential_sources):
        checked_sources = ", ".join(
            f"{source.get('source')}={source.get('status')}"
            for source in credential_sources
        )
        issues.append(
            ConfigIssue(
                severity=Severity.ERROR,
                key_path="memory.llm.api_key_env",
                message=(
                    f"API-B Provider '{provider}' 缺少当前可读取的可用凭证: "
                    f"{api_key_env}；已检查 {checked_sources}"
                ),
                suggestion=(
                    f"运行 /api -> 3 记忆系统模型配置，并把 API-B key 保存到 {api_key_env}；"
                    "API-A 的 agnes-ai 凭证不会用于 Mem/Supervisor"
                ),
            )
        )

    return issues


def _is_local_gateway_loop_base_url(base_url: str) -> bool:
    try:
        parsed = urlparse(str(base_url or "").strip())
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0"} and parsed.port == 6000


def _icon_for_severity(severity: Severity) -> str:
    if severity == Severity.ERROR:
        return "[ERROR]"
    if severity == Severity.WARNING:
        return "[WARN]"
    return "[OK]"


def _effective_terminal_config() -> dict[str, Any]:
    from tools.terminal_tool import _get_env_config

    return _get_env_config()


def _configured_terminal_config(cfg: dict) -> dict[str, Any]:
    terminal_cfg = cfg.get("terminal")
    return terminal_cfg if isinstance(terminal_cfg, dict) else {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _run_command(command: list[str], timeout: int = 5) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "command not found"
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except Exception as exc:
        return False, str(exc)

    if result.returncode == 0:
        summary = result.stdout.strip() or "ok"
        return True, summary

    stderr = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return False, stderr


def _suggest_docker_fix(detail: str, *, requested_backend: str, fallback_to_local: bool) -> str:
    low = (detail or "").lower()

    if "docker_engine" in low or "//./pipe/docker_engine" in low:
        if "access is denied" in low or "elevated privileges" in low:
            return (
                "Windows Docker named pipe 可见但当前终端权限不足。先确认 Docker Desktop 已启动，"
                "再尝试用管理员权限打开终端，或确认当前用户属于 docker-users 组。"
            )
        return (
            "看起来是 Windows Docker named pipe 连不上。先启动 Docker Desktop，等待 Engine 就绪后"
            "再重试；也可以先运行 `docker version` 单独确认。"
        )

    if "timed out" in low or "deadline exceeded" in low or "daemon is not responding" in low:
        return "Docker daemon 响应超时。先启动或重启 Docker Desktop，等引擎稳定后再重试。"

    if "cannot find the file specified" in low or "command not found" in low:
        return "未找到可用 Docker 运行时。请安装并启动 Docker Desktop，或切回 local backend。"

    if requested_backend == "docker" and fallback_to_local:
        return "当前请求 docker backend 时会自动回退到 local；若想恢复容器沙箱，请先修复 Docker daemon。"

    return "确认 Docker Desktop 已启动，并先单独运行 `docker version` 查看更具体的报错。"


def _suggest_podman_fix(detail: str) -> str:
    low = (detail or "").lower()

    if "podman machine init" in low or "podman machine start" in low:
        return "先运行 `podman machine init`，再运行 `podman machine start`，然后用 `podman version` 复查。"

    if "unable to connect to podman socket" in low or "cannot connect to podman" in low:
        return (
            "Podman socket 当前不可用。先运行 `podman system connection list` 检查连接，"
            "再尝试 `podman machine start`。"
        )

    if "cannot find the path specified" in low or "no such file or directory" in low:
        return "Podman 本地 machine 元数据缺失。通常先执行 `podman machine init` 再 `podman machine start`。"

    return "如果你不打算使用 podman 可以忽略；若要使用，请先检查 `podman system connection list` 并启动 podman machine。"


@contextmanager
def _temporary_env(overrides: dict[str, str | None]) -> Iterator[None]:
    old_values: dict[str, str | None] = {}
    missing: set[str] = set()

    for key, value in overrides.items():
        if key in os.environ:
            old_values[key] = os.environ[key]
        else:
            missing.add(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    try:
        yield
    finally:
        for key in overrides:
            if key in old_values:
                os.environ[key] = old_values[key]
            elif key in missing:
                os.environ.pop(key, None)


def _diagnose_terminal_backend(cfg: dict) -> AgentCheck:
    configured = _configured_terminal_config(cfg)
    effective = _effective_terminal_config()

    configured_backend = str(configured.get("backend") or configured.get("env_type") or "local").strip().lower()
    effective_backend = str(effective.get("env_type") or "local").strip().lower()

    configured_fallback = bool(configured.get("fallback_to_local", True))
    effective_fallback = _env_bool("TERMINAL_FALLBACK_TO_LOCAL", configured_fallback)

    if configured_backend != effective_backend:
        return AgentCheck(
            severity=Severity.WARNING,
            name="terminal_backend",
            message=(
                f"config.yaml 配置 backend={configured_backend}，但当前运行时实际读取到 "
                f"TERMINAL_ENV={effective_backend}"
            ),
            suggestion="如果最近改过 terminal.backend，确认 ~/.VoidCube/.env 已同步，或重新运行 `VoidCube config set terminal.backend ...`。",
            details=f"fallback_to_local={effective_fallback}",
            data={
                "configured_backend": configured_backend,
                "effective_backend": effective_backend,
                "fallback_to_local": effective_fallback,
            },
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="terminal_backend",
        message=f"当前 terminal backend 为 {effective_backend}",
        suggestion="",
        details=f"fallback_to_local={effective_fallback}",
        data={
            "configured_backend": configured_backend,
            "effective_backend": effective_backend,
            "fallback_to_local": effective_fallback,
        },
    )


def _diagnose_git_bash() -> AgentCheck:
    if platform.system() != "Windows":
        return AgentCheck(
            severity=Severity.INFO,
            name="git_bash",
            message="当前平台不是 Windows，无需 Git Bash 诊断",
        )

    try:
        from tools.environments.local import _find_bash

        bash_path = _find_bash()
        return AgentCheck(
            severity=Severity.INFO,
            name="git_bash",
            message="本地 shell 运行时已解析到可用 bash",
            details=bash_path,
            data={"bash_path": bash_path},
        )
    except Exception as exc:
        return AgentCheck(
            severity=Severity.ERROR,
            name="git_bash",
            message="Windows 本地 backend 未找到可用的 Git Bash",
            suggestion="安装 Git for Windows，或设置 VOIDCUBE_GIT_BASH_PATH 指向 Git\\bin\\bash.exe。",
            details=str(exc),
        )


def _diagnose_docker(cfg: dict) -> AgentCheck:
    configured = _configured_terminal_config(cfg)
    requested_backend = str(
        os.getenv("TERMINAL_ENV") or configured.get("backend") or "local"
    ).strip().lower()
    fallback_to_local = _env_bool(
        "TERMINAL_FALLBACK_TO_LOCAL",
        bool(configured.get("fallback_to_local", True)),
    )
    requested = requested_backend == "docker"

    try:
        from tools.environments.docker import find_docker

        docker_exe = find_docker()
    except Exception as exc:
        docker_exe = None
        docker_find_error = str(exc)
    else:
        docker_find_error = ""

    if not docker_exe:
        severity = (
            Severity.ERROR if requested and not fallback_to_local
            else Severity.WARNING if requested
            else Severity.INFO
        )
        suggestion = ""
        if requested and not fallback_to_local:
            suggestion = "安装并启动 Docker Desktop，或切回 local backend。"
        elif requested:
            suggestion = "Docker 不可用时当前会自动回退到 local；若想恢复容器沙箱，请先启动 Docker。"
        return AgentCheck(
            severity=severity,
            name="docker_runtime",
            message="未找到 docker 可执行文件" if requested else "未检测到 docker（当前未使用）",
            suggestion=suggestion,
            details=docker_find_error or "docker not found in PATH/common install locations",
            data={"requested_backend": requested_backend, "fallback_to_local": fallback_to_local},
        )

    ok, detail = _run_command([docker_exe, "version"])
    if ok:
        return AgentCheck(
            severity=Severity.INFO,
            name="docker_runtime",
            message="docker 可执行文件与 daemon 响应正常",
            details=docker_exe,
            data={"docker_executable": docker_exe},
        )

    severity = (
        Severity.ERROR if requested and not fallback_to_local
        else Severity.WARNING if requested
        else Severity.INFO
    )
    suggestion = _suggest_docker_fix(
        detail,
        requested_backend=requested_backend,
        fallback_to_local=fallback_to_local,
    )
    return AgentCheck(
        severity=severity,
        name="docker_runtime",
        message="docker 命令存在，但 `docker version` 失败",
        suggestion=suggestion,
        details=detail,
        data={"docker_executable": docker_exe, "requested_backend": requested_backend},
    )


def _diagnose_podman(cfg: dict) -> AgentCheck:
    configured = _configured_terminal_config(cfg)
    requested_backend = str(
        os.getenv("TERMINAL_ENV") or configured.get("backend") or "local"
    ).strip().lower()
    fallback_to_local = _env_bool(
        "TERMINAL_FALLBACK_TO_LOCAL",
        bool(configured.get("fallback_to_local", True)),
    )
    image = str(
        os.getenv("TERMINAL_PODMAN_IMAGE")
        or configured.get("podman_image")
        or "localhost/voidcube-podman-local:latest"
    ).strip()
    requested = requested_backend == "podman"
    required = requested and not fallback_to_local
    unavailable_severity = (
        Severity.ERROR if required
        else Severity.WARNING if requested
        else Severity.INFO
    )
    podman_exe = shutil.which("podman")
    if not podman_exe:
        return AgentCheck(
            severity=unavailable_severity,
            name="podman_runtime",
            message="当前 terminal 使用 Podman，但未检测到 podman" if required else "未检测到 podman，可忽略",
            suggestion=(
                "安装并启动 Podman machine，然后重新运行 `VoidCube doctor`。"
                if requested else ""
            ),
            data={"requested_backend": requested_backend, "fallback_to_local": fallback_to_local},
        )

    ok, detail = _run_command([podman_exe, "version"])
    if not ok:
        return AgentCheck(
            severity=unavailable_severity,
            name="podman_runtime",
            message="podman 命令存在，但 `podman version` 失败",
            suggestion=_suggest_podman_fix(detail),
            details=detail,
            data={"podman_executable": podman_exe, "requested_backend": requested_backend},
        )

    image_ok, image_detail = _run_command([podman_exe, "image", "exists", image])
    if not image_ok:
        return AgentCheck(
            severity=unavailable_severity,
            name="podman_runtime",
            message=f"Podman 沙箱镜像不存在: {image}",
            suggestion=f"运行 `python -m tools.podman_sandbox build --image {image}` 构建正式沙箱镜像。",
            details=image_detail,
            data={"podman_executable": podman_exe, "podman_image": image},
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="podman_runtime",
        message=f"podman 运行时与沙箱镜像均可用: {image}",
        details=podman_exe,
        data={"podman_executable": podman_exe, "podman_image": image},
    )


def _diagnose_tool_registration() -> AgentCheck:
    from tools.model_tools import get_all_tool_names

    required_tools = ["terminal", "read_file", "write_file", "patch", "search_files"]
    registered_tools = set(get_all_tool_names())
    missing = [name for name in required_tools if name not in registered_tools]

    if missing:
        return AgentCheck(
            severity=Severity.ERROR,
            name="tool_registration",
            message=f"缺少关键工具注册: {', '.join(missing)}",
            suggestion="检查 tools.model_tools 的发现导入链，以及对应工具模块是否导入失败。",
            data={"missing_tools": missing},
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="tool_registration",
        message="核心 agent 工具已完成注册",
        details=", ".join(required_tools),
        data={"required_tools": required_tools},
    )


def _diagnose_body_registry() -> AgentCheck:
    from systems.body_registry import BodyRegistryManager
    from systems.config import get_config

    try:
        supervisor_config = get_config().supervisor
        body_config = supervisor_config.body_runtime
        manager = BodyRegistryManager(
            supervisor_config.execution.git_repo_path,
            state_root=body_config.state_root,
            slot_ids=(body_config.slot_a_name, body_config.slot_b_name),
        )
        if not manager.registry_path.is_file():
            return AgentCheck(
                severity=Severity.INFO,
                name="body_registry",
                message="身体槽位尚未初始化",
                suggestion="启动 Supervisor 后再次运行 doctor 检查 active/shell 基线。",
                details=str(manager.registry_path),
            )
        report = manager.inspect_layout()
    except Exception as exc:
        return AgentCheck(
            severity=Severity.ERROR,
            name="body_registry",
            message="身体槽位诊断无法执行",
            suggestion="检查 SUPERVISOR_GIT_REPO 与身体槽位路径配置。",
            details=str(exc),
        )

    if report["healthy"]:
        registry = report["registry"] or {}
        return AgentCheck(
            severity=Severity.INFO,
            name="body_registry",
            message="active/shell 身体基线与指针一致",
            details=(
                f"active={registry.get('active_slot')}, "
                f"shell={registry.get('shell_slot')}"
            ),
            data=report,
        )

    violation_codes = [item["code"] for item in report["violations"]]
    return AgentCheck(
        severity=Severity.ERROR,
        name="body_registry",
        message="身体槽位基线或 active 指针损坏",
        suggestion="停止 Supervisor，修复报告中的槽位状态后重新启动进行确定性初始化。",
        details=", ".join(violation_codes),
        data=report,
    )


def _diagnose_terminal_cwd(cfg: dict) -> AgentCheck:
    configured = _configured_terminal_config(cfg)
    effective = _effective_terminal_config()
    backend = str(effective.get("env_type") or "local").strip().lower()
    raw_terminal_cwd = os.getenv("TERMINAL_CWD")

    if not raw_terminal_cwd:
        return AgentCheck(
            severity=Severity.INFO,
            name="terminal_cwd",
            message=f"当前 backend={backend}，未设置显式 TERMINAL_CWD",
        )

    if backend == "local":
        return AgentCheck(
            severity=Severity.INFO,
            name="terminal_cwd",
            message="本地 backend 会按 TERMINAL_CWD 解析相对路径",
            details=raw_terminal_cwd,
            data={"terminal_cwd": raw_terminal_cwd},
        )

    if backend in ("docker", "podman") and bool(effective.get("docker_mount_cwd_to_workspace")):
        host_cwd = effective.get("host_cwd")
        return AgentCheck(
            severity=Severity.INFO,
            name="terminal_cwd",
            message=f"{backend.capitalize()} backend 已启用将主机 cwd 映射到 /workspace",
            details=f"host_cwd={host_cwd or raw_terminal_cwd}, backend_cwd={effective.get('cwd')}",
            data={"terminal_cwd": raw_terminal_cwd, "host_cwd": host_cwd, "backend_cwd": effective.get("cwd")},
        )

    if not os.path.isabs(raw_terminal_cwd) or raw_terminal_cwd.startswith(("C:\\", "C:/", "/Users/", "/home/")):
        return AgentCheck(
            severity=Severity.WARNING,
            name="terminal_cwd",
            message="当前容器类 backend 下，TERMINAL_CWD 看起来是主机路径或相对路径，运行时可能会忽略它",
            suggestion="如果要让容器 backend 使用主机目录，开启 `terminal.docker_mount_cwd_to_workspace=true`；否则改用容器内路径如 /root 或 /workspace。",
            details=f"raw={raw_terminal_cwd}, effective={effective.get('cwd')}",
            data={"terminal_cwd": raw_terminal_cwd, "effective_cwd": effective.get("cwd")},
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="terminal_cwd",
        message="当前 backend 使用的 cwd 看起来合理",
        details=f"raw={raw_terminal_cwd}, effective={effective.get('cwd')}",
        data={"terminal_cwd": raw_terminal_cwd, "effective_cwd": effective.get("cwd")},
    )


def _diagnose_path_runtime() -> AgentCheck:
    from tools.path_runtime import resolve_runtime_path

    env = SimpleNamespace(
        _voidcube_active_backend="local",
        cwd=os.getenv("TERMINAL_CWD") or os.getcwd(),
    )

    relative_result = resolve_runtime_path("doctor-relative.txt", env)
    details = [f"relative->{relative_result.backend_path}"]

    if not relative_result.host_path:
        return AgentCheck(
            severity=Severity.ERROR,
            name="path_runtime",
            message="本地 backend 的相对路径未解析出 host_path",
            suggestion="检查 tools.path_runtime 中 local backend 的路径归一化逻辑。",
            details=" | ".join(details),
        )

    if platform.system() == "Windows":
        cwd = Path(os.getcwd()).resolve()
        drive = cwd.drive.rstrip(":")
        if drive:
            wsl_like = f"/mnt/{drive.lower()}/{cwd.as_posix().split(':', 1)[1].lstrip('/')}/README.md"
            wsl_result = resolve_runtime_path(wsl_like, env)
            details.append(f"wsl->{wsl_result.backend_path}")
            if not wsl_result.host_path:
                return AgentCheck(
                    severity=Severity.ERROR,
                    name="path_runtime",
                    message="WSL 风格路径未能映射回 Windows 主机路径",
                    suggestion="检查 WSL / Git Bash 路径转换与 host_path 回填逻辑。",
                    details=" | ".join(details),
                )

    return AgentCheck(
        severity=Severity.INFO,
        name="path_runtime",
        message="路径运行时归一化检查通过",
        details=" | ".join(details),
        data={
            "relative_backend_path": relative_result.backend_path,
            "relative_host_path": relative_result.host_path,
        },
    )


def _diagnose_terminal_probe() -> AgentCheck:
    from tools.model_tools import handle_function_call
    from tools.terminal_tool import cleanup_vm

    task_id = "doctor-terminal-probe"
    try:
        payload = json.loads(
            handle_function_call(
                "terminal",
                {"command": "printf doctor-terminal-ok"},
                task_id=task_id,
                user_task="doctor terminal probe",
            )
        )
    finally:
        cleanup_vm(task_id)

    if payload.get("error"):
        return AgentCheck(
            severity=Severity.ERROR,
            name="terminal_probe",
            message="terminal 工具真实调用失败",
            suggestion="先修复当前 backend 或其 fallback，然后再重试 agent 自检。",
            details=str(payload.get("error")),
            data=payload,
        )

    requested = payload.get("requested_backend")
    active = payload.get("active_backend")
    warning = payload.get("_warning")
    if warning:
        return AgentCheck(
            severity=Severity.WARNING,
            name="terminal_probe",
            message=f"terminal 工具可用，但发生 backend 回退或运行时警告（requested={requested}, active={active}）",
            suggestion="如果你期待的是容器沙箱，请优先修复对应 backend；否则当前 local fallback 仍可继续工作。",
            details=warning,
            data=payload,
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="terminal_probe",
        message=f"terminal 工具真实调用成功（requested={requested or active}, active={active or requested}）",
        details=(payload.get("output") or "").strip(),
        data=payload,
    )


def _diagnose_tool_call_smoke() -> AgentCheck:
    from tools.model_tools import handle_function_call
    from tools.terminal_tool import cleanup_vm

    task_id = "doctor-tool-smoke"
    with tempfile.TemporaryDirectory(prefix="voidcube-doctor-") as tmpdir:
        env_updates = {
            "TERMINAL_ENV": "local",
            "TERMINAL_CWD": tmpdir,
        }
        with _temporary_env(env_updates):
            try:
                write_payload = json.loads(
                    handle_function_call(
                        "write_file",
                        {"path": "docs/note.txt", "content": "hello\n"},
                        task_id=task_id,
                        user_task="doctor file smoke write",
                    )
                )
                patch_payload = json.loads(
                    handle_function_call(
                        "patch",
                        {
                            "mode": "replace",
                            "path": "docs/note.txt",
                            "old_string": "hello",
                            "new_string": "hello world",
                        },
                        task_id=task_id,
                        user_task="doctor file smoke patch",
                    )
                )
                search_payload = json.loads(
                    handle_function_call(
                        "search_files",
                        {"pattern": "hello world", "target": "content", "path": "."},
                        task_id=task_id,
                        user_task="doctor file smoke search",
                    )
                )
                read_payload = json.loads(
                    handle_function_call(
                        "read_file",
                        {"path": "docs/note.txt", "offset": 1, "limit": 5},
                        task_id=task_id,
                        user_task="doctor file smoke read",
                    )
                )
            finally:
                cleanup_vm(task_id)

    failures = []
    if write_payload.get("error"):
        failures.append(f"write_file: {write_payload.get('error')}")
    if patch_payload.get("success") is not True:
        failures.append(f"patch: {patch_payload.get('error') or 'not successful'}")
    if search_payload.get("error"):
        failures.append(f"search_files: {search_payload.get('error')}")
    if read_payload.get("error"):
        failures.append(f"read_file: {read_payload.get('error')}")

    if failures:
        return AgentCheck(
            severity=Severity.ERROR,
            name="tool_call_smoke",
            message="agent 文件工具链 smoke test 失败",
            suggestion="优先检查 handle_function_call、registry.dispatch，以及 file_tools/terminal backend 集成。",
            details=" | ".join(failures),
            data={
                "write": write_payload,
                "patch": patch_payload,
                "search": search_payload,
                "read": read_payload,
            },
        )

    return AgentCheck(
        severity=Severity.INFO,
        name="tool_call_smoke",
        message="agent 文件工具链 smoke test 成功",
        details="write_file -> patch -> search_files -> read_file",
        data={
            "write": write_payload,
            "patch": patch_payload,
            "search": search_payload,
            "read": read_payload,
        },
    )


def collect_agent_diagnostics(cfg: Optional[dict] = None) -> List[AgentCheck]:
    """收集 agent/backend/path/tool-call 诊断项。"""
    cfg = cfg or load_config()
    checks = [
        _diagnose_terminal_backend(cfg),
        _diagnose_git_bash(),
        _diagnose_docker(cfg),
        _diagnose_podman(cfg),
        _diagnose_tool_registration(),
        _diagnose_body_registry(),
        _diagnose_terminal_cwd(cfg),
        _diagnose_path_runtime(),
        _diagnose_terminal_probe(),
        _diagnose_tool_call_smoke(),
    ]
    return checks


def validate_all() -> dict:
    """运行所有验证，返回汇总报告。"""
    cfg = load_config()
    config_issues = validate_config()
    invalid_aliases = []
    agent_checks = collect_agent_diagnostics(cfg)

    return {
        "config_issues": config_issues,
        "invalid_aliases": invalid_aliases,
        "agent_checks": agent_checks,
        "has_errors": any(i.severity == Severity.ERROR for i in config_issues)
        or any(i.severity == Severity.ERROR for i in agent_checks),
        "has_warnings": any(i.severity == Severity.WARNING for i in config_issues)
        or any(i.severity == Severity.WARNING for i in agent_checks),
    }


def print_diagnosis():
    """打印配置与 agent 诊断报告。"""
    print("\n" + "=" * 60)
    print("VoidCube 诊断")
    print("=" * 60)

    report = validate_all()
    config_issues = report["config_issues"]
    invalid_aliases = report["invalid_aliases"]
    agent_checks = report["agent_checks"]

    print("\n配置完整性检查：")
    if not config_issues:
        print("   [OK] 配置完整")
    else:
        for issue in config_issues:
            icon = "[ERROR]" if issue.severity == Severity.ERROR else "[WARN]"
            print(f"\n   {icon} [{issue.key_path}] {issue.message}")
            print(f"      建议: {issue.suggestion}")

    print("\n模型别名检查：")
    if not invalid_aliases:
        print("   [OK] 所有别名有效")
    else:
        print(f"   [WARN] 发现 {len(invalid_aliases)} 个无效别名：")
        for alias in invalid_aliases:
            print(f"      - {alias}")
        print("\n   建议: 运行 /api 重新配置模型别名")

    print("\nAgent 工具链检查：")
    for check in agent_checks:
        icon = _icon_for_severity(check.severity)
        print(f"   {icon} [{check.name}] {check.message}")
        if check.details:
            print(f"      详情: {check.details}")
        if check.suggestion:
            print(f"      建议: {check.suggestion}")

    print("\n" + "=" * 60)
    if report["has_errors"]:
        print("建议: 存在阻断项，优先修复上面的 [ERROR] 项")
    elif report["has_warnings"]:
        print("建议: 目前可以继续使用，但建议关注上面的 [WARN] 项")
    else:
        print("建议: 自检通过，agent 工具链看起来是健康的")
    print("=" * 60 + "\n")
