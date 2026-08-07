"""
代码执行工具
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional, Dict, Any, List

from agent.redact import redact_sensitive_text
from tools.ansi_strip import strip_ansi
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


EXECUTE_CODE_SCHEMA = {
    "name": "execute_code",
    "description": "Execute Python or JavaScript in an isolated sandbox with resource limits and network access disabled.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The code to execute",
            },
            "language": {
                "type": "string",
                "description": "The programming language to use (python or javascript). Default: python",
                "default": "python",
            },
        },
        "required": ["code"],
    },
}

SANDBOX_ALLOWED_TOOLS: list = []

_SUPPORTED_LANGUAGES = {
    "python": "python3 -",
    "python3": "python3 -",
    "py": "python3 -",
    "javascript": "node -",
    "js": "node -",
    "node": "node -",
}
_SANDBOX_BACKENDS = {"docker", "podman", "singularity", "modal", "daytona"}


def _normalize_language(language: str) -> str:
    return str(language or "python").strip().lower()


def _get_runtime_image(config: Dict[str, Any], env_type: str) -> str:
    if env_type == "podman":
        return config.get("podman_image") or config.get("docker_image") or ""
    if env_type == "docker":
        return config.get("docker_image") or ""
    if env_type == "singularity":
        return config.get("singularity_image") or ""
    if env_type == "modal":
        return config.get("modal_image") or ""
    if env_type == "daytona":
        return config.get("daytona_image") or ""
    return ""


def _build_container_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "container_cpu": config.get("container_cpu", 1),
        "container_memory": config.get("container_memory", 5120),
        "container_disk": config.get("container_disk", 51200),
        "container_persistent": False,
        "docker_volumes": [],
        "docker_mount_cwd_to_workspace": False,
        "docker_forward_env": config.get("docker_forward_env", []),
        "docker_env": config.get("docker_env", {}),
        "modal_mode": config.get("modal_mode", "auto"),
    }


def _serialize_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _execute_code_impl(
    code: str,
    language: str = "python",
    *,
    task_id: str | None = None,
) -> Dict[str, Any]:
    language_key = _normalize_language(language)
    command = _SUPPORTED_LANGUAGES.get(language_key)
    if not command:
        supported = ", ".join(sorted({"python", "javascript"}))
        return {
            "success": False,
            "error": f"Unsupported language '{language}'. Supported languages: {supported}",
        }

    if not code:
        return {"success": False, "error": "No code provided"}

    from tools.terminal_tool import _create_environment, _get_env_config

    config = _get_env_config()
    env_type = str(config.get("env_type") or "local").strip().lower()
    if env_type not in _SANDBOX_BACKENDS:
        return {
            "success": False,
            "error": (
                "Code execution requires a sandbox backend. "
                "Set TERMINAL_ENV to one of: docker, podman, singularity, modal, daytona."
            ),
            "backend": env_type,
        }

    image = _get_runtime_image(config, env_type)
    if not image:
        return {
            "success": False,
            "error": f"No sandbox image configured for backend '{env_type}'",
            "backend": env_type,
        }

    effective_task_id = task_id or f"execute-code-{uuid.uuid4().hex[:12]}"
    env = None
    try:
        env = _create_environment(
            env_type=env_type,
            image=image,
            cwd="/root",
            timeout=int(config.get("timeout", 180)),
            container_config=_build_container_config(config),
            task_id=effective_task_id,
            host_cwd=None,
            fallback_to_local=False,
            network=False,
        )

        result = env.execute(command, timeout=int(config.get("timeout", 180)), stdin_data=code)
        output = strip_ansi(result.get("output", "") or "")
        output = redact_sensitive_text(output.strip()) if output else ""
        exit_code = int(result.get("returncode", 0))

        payload = {
            "success": exit_code == 0,
            "stdout": output,
            "stderr": "",
            "exit_code": exit_code,
            "language": "javascript" if language_key in {"javascript", "js", "node"} else "python",
            "backend": getattr(env, "_voidcube_active_backend", env_type),
        }

        requested_backend = getattr(env, "_voidcube_requested_backend", None)
        if requested_backend and requested_backend != payload["backend"]:
            payload["requested_backend"] = requested_backend

        backend_warning = getattr(env, "_voidcube_backend_warning", None)
        if backend_warning:
            payload["_warning"] = backend_warning

        return payload
    except Exception as exc:
        logger.error("execute_code failed: %s", exc, exc_info=True)
        return {
            "success": False,
            "error": f"Code execution failed: {type(exc).__name__}: {exc}",
            "language": "javascript" if language_key in {"javascript", "js", "node"} else "python",
            "backend": env_type,
        }
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception:
                logger.debug("execute_code sandbox cleanup failed", exc_info=True)


def code_execution_tool(
    code: str,
    language: str = "python",
    **kwargs,
) -> str:
    """代码执行"""
    payload = _execute_code_impl(
        code=code,
        language=language,
        task_id=kwargs.get("task_id"),
    )
    return _serialize_result(payload)


async def execute_code(code: str, **kwargs) -> Dict[str, Any]:
    """执行代码"""
    return _execute_code_impl(
        code=code,
        language=kwargs.get("language", "python"),
        task_id=kwargs.get("task_id"),
    )


def _handle_execute_code(args, **kw):
    return code_execution_tool(
        code=args.get("code", ""),
        language=args.get("language", "python"),
        task_id=kw.get("task_id"),
    )


def build_execute_code_schema(tools: Optional[List[Any]] = None) -> Dict[str, Any]:
    if tools is None:
        tools = []
    return EXECUTE_CODE_SCHEMA


registry.register(
    name="execute_code",
    toolset="code_execution",
    schema=EXECUTE_CODE_SCHEMA,
    handler=_handle_execute_code,
)
