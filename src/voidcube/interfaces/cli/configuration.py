"""
API 配置向导 - 交互式配置 API 设置
"""

import os
import subprocess
import sys
import re
import getpass
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ApiConfigRuntime:
    """Optional CLI runtime updates applied after a successful wizard save."""

    set_model: Callable[[str], None] | None = None
    set_provider: Callable[[str], None] | None = None
    set_requested_provider: Callable[[str], None] | None = None


def persist_image_generation_config(
    config: dict[str, Any],
    *,
    provider: str = "agnes-ai",
    api_key_env: str = "AGNES_API_KEY",
    endpoint: str = "https://api.agnes-ai.cn/v1/images/generations",
    edit_endpoint: str = "https://api.agnes-ai.cn/v1/images/edits",
    model: str = "agnes-image-2.1-flash",
) -> dict[str, Any]:
    """Return config with only the dedicated image generation route updated."""
    from ...infrastructure.providers.media_generation import default_image_generation_config

    cfg = dict(config or {})
    image_generation = default_image_generation_config()
    existing = cfg.get("image_generation")
    if isinstance(existing, dict):
        image_generation.update(existing)
    image_generation.update(
        {
            "provider": str(provider or "agnes-ai").strip().lower(),
            "api_key_env": str(api_key_env or "AGNES_API_KEY").strip(),
            "endpoint": str(endpoint or "").strip().rstrip("/"),
            "edit_endpoint": str(edit_endpoint or "").strip().rstrip("/"),
            "model": str(model or "").strip(),
        }
    )
    cfg.pop("multimodal", None)
    cfg["image_generation"] = image_generation
    return cfg


def persist_video_generation_config(
    config: dict[str, Any],
    *,
    provider: str = "agnes-ai",
    api_key_env: str = "AGNES_API_KEY",
    endpoint: str = "https://api.agnes-ai.cn/v1/videos",
    result_endpoint: str = "https://api.agnes-ai.cn/agnesapi",
    model: str = "agnes-video-v2.0",
) -> dict[str, Any]:
    """Return config with only the dedicated video generation route updated."""
    from ...infrastructure.providers.media_generation import default_video_generation_config

    cfg = dict(config or {})
    video_generation = default_video_generation_config()
    existing = cfg.get("video_generation")
    if isinstance(existing, dict):
        video_generation.update(existing)
    video_generation.update(
        {
            "provider": str(provider or "agnes-ai").strip().lower(),
            "api_key_env": str(api_key_env or "AGNES_API_KEY").strip(),
            "endpoint": str(endpoint or "").strip().rstrip("/"),
            "result_endpoint": str(result_endpoint or "").strip().rstrip("/"),
            "model": str(model or "").strip(),
        }
    )
    cfg.pop("multimodal", None)
    cfg["video_generation"] = video_generation
    return cfg


def save_image_generation_config(
    *,
    provider: str = "agnes-ai",
    api_key_env: str = "AGNES_API_KEY",
    endpoint: str = "https://api.agnes-ai.cn/v1/images/generations",
    edit_endpoint: str = "https://api.agnes-ai.cn/v1/images/edits",
    model: str = "agnes-image-2.1-flash",
) -> bool:
    try:
        from ...infrastructure.config.configuration import load_config, save_config

        save_config(
            persist_image_generation_config(
                load_config(),
                provider=provider,
                api_key_env=api_key_env,
                endpoint=endpoint,
                edit_endpoint=edit_endpoint,
                model=model,
            )
        )
        return True
    except Exception:
        return False


def save_video_generation_config(
    *,
    provider: str = "agnes-ai",
    api_key_env: str = "AGNES_API_KEY",
    endpoint: str = "https://api.agnes-ai.cn/v1/videos",
    result_endpoint: str = "https://api.agnes-ai.cn/agnesapi",
    model: str = "agnes-video-v2.0",
) -> bool:
    try:
        from ...infrastructure.config.configuration import load_config, save_config

        save_config(
            persist_video_generation_config(
                load_config(),
                provider=provider,
                api_key_env=api_key_env,
                endpoint=endpoint,
                result_endpoint=result_endpoint,
                model=model,
            )
        )
        return True
    except Exception:
        return False


def save_memory_llm_config(
    provider: str,
    model: str,
    *,
    native_audio: bool | None = None,
    native_modalities: Sequence[str] | None = None,
    native_audio_output: bool | None = None,
) -> bool:
    """Persist API-B's Provider/model reference without touching API-A."""
    try:
        from ...infrastructure.config.configuration import load_config, save_config

        cfg = persist_api_b_config(
            load_config(),
            provider=provider,
            model=model,
            native_audio=native_audio,
            native_modalities=native_modalities,
            native_audio_output=native_audio_output,
        )
        save_config(cfg)
        return True
    except Exception:
        return False


def has_configured_api_key(api_key_env: str) -> bool:
    if not api_key_env:
        return True
    try:
        from ...infrastructure.config.configuration import get_env_value
        from ...infrastructure.providers.auth import has_usable_secret

        return has_usable_secret(get_env_value(api_key_env) or "")
    except Exception:
        return False


def _secret_source_status(value: object) -> str:
    try:
        from ...infrastructure.providers.auth import has_usable_secret

        text = str(value or "").strip()
        if not text:
            return "missing"
        return "usable" if has_usable_secret(text) else "present_unusable"
    except Exception:
        return "missing" if not str(value or "").strip() else "present_unusable"


def _credential_source_entry(
    source: str,
    *,
    status: str,
    detail: str = "",
) -> dict[str, str]:
    return {"source": source, "status": status, "detail": detail}


def provider_credential_sources(provider: str, api_key_env: str = "") -> list[dict[str, str]]:
    """Return a secret-free report of credential sources checked for a provider."""
    provider = str(provider or "").strip().lower()
    api_key_env = str(api_key_env or "").strip()
    sources: list[dict[str, str]] = []

    if api_key_env:
        try:
            from ...infrastructure.config.configuration import get_env_value

            sources.append(
                _credential_source_entry(
                    "effective_env",
                    status=_secret_source_status(get_env_value(api_key_env)),
                    detail=api_key_env,
                )
            )
        except Exception as exc:
            sources.append(
                _credential_source_entry(
                    "effective_env",
                    status="error",
                    detail=f"{api_key_env}: {exc}",
                )
            )

        try:
            env_value = os.environ.get(api_key_env)
            sources.append(
                _credential_source_entry(
                    "process_env",
                    status=_secret_source_status(env_value),
                    detail=api_key_env,
                )
            )
        except Exception as exc:
            sources.append(
                _credential_source_entry(
                    "process_env",
                    status="error",
                    detail=f"{api_key_env}: {exc}",
                )
            )

        try:
            from ...infrastructure.config.configuration import load_env
            from ...infrastructure.config.runtime_paths import get_env_path

            env_vars = load_env()
            sources.append(
                _credential_source_entry(
                    "voidcube_env",
                    status=_secret_source_status(env_vars.get(api_key_env)),
                    detail=f"{get_env_path()}::{api_key_env}",
                )
            )
        except Exception as exc:
            sources.append(
                _credential_source_entry(
                    "voidcube_env",
                    status="error",
                    detail=f"{api_key_env}: {exc}",
                )
            )
    else:
        sources.append(
            _credential_source_entry(
                "memory.llm.api_key_env",
                status="missing",
                detail="未设置",
            )
        )

    if provider:
        try:
            from ...infrastructure.providers.auth import (
                _get_auth_store_path,
                _load_auth_store,
            )

            store = _load_auth_store()
            state = store.get(provider)
            if isinstance(state, dict):
                status = "usable" if any(
                    _secret_source_status(state.get(key_name)) == "usable"
                    for key_name in ("api_key", "access_token")
                ) else "present_unusable"
            else:
                status = "missing"
            sources.append(
                _credential_source_entry(
                    "auth_store",
                    status=status,
                    detail=f"{_get_auth_store_path()}::{provider}",
                )
            )
        except Exception as exc:
            sources.append(
                _credential_source_entry(
                    "auth_store",
                    status="error",
                    detail=f"{provider}: {exc}",
                )
            )

        try:
            from ...infrastructure.providers.auth import read_credential_pool

            entries = read_credential_pool(provider)
            usable = False
            present = False
            if isinstance(entries, list):
                present = bool(entries)
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if any(
                        _secret_source_status(entry.get(key_name)) == "usable"
                        for key_name in ("runtime_api_key", "api_key", "access_token")
                    ):
                        usable = True
                        break
            sources.append(
                _credential_source_entry(
                    "credential_pool",
                    status="usable" if usable else ("present_unusable" if present else "missing"),
                    detail=provider,
                )
            )
        except Exception as exc:
            sources.append(
                _credential_source_entry(
                    "credential_pool",
                    status="error",
                    detail=f"{provider}: {exc}",
                )
            )

    return sources


def credential_sources_have_usable_secret(sources: list[dict[str, str]]) -> bool:
    return any(source.get("status") == "usable" for source in sources)


def provider_has_usable_credential(provider: str, api_key_env: str = "") -> bool:
    """Return whether a provider has a usable credential in runtime-readable sources."""
    provider = str(provider or "").strip().lower()
    api_key_env = str(api_key_env or "").strip()
    if credential_sources_have_usable_secret(provider_credential_sources(provider, api_key_env)):
        return True

    try:
        from ...infrastructure.providers.auth import (
            has_usable_secret,
            resolve_api_key_provider_credentials,
        )
        from ...infrastructure.config.configuration import get_env_value

        if api_key_env and has_usable_secret(str(get_env_value(api_key_env) or "")):
            return True

        if provider:
            creds = resolve_api_key_provider_credentials(provider) or {}
            for key_name in ("api_key", "access_token"):
                if has_usable_secret(str(creds.get(key_name) or "")):
                    return True
    except Exception:
        pass

    if provider:
        try:
            from ...infrastructure.providers.credential_pool import load_pool
            from ...infrastructure.providers.auth import has_usable_secret

            pool = load_pool(provider)
            entry = pool.select() if pool and pool.has_credentials() else None
            if entry is not None:
                return any(
                    has_usable_secret(str(value or ""))
                    for value in (
                        getattr(entry, "runtime_api_key", ""),
                        getattr(entry, "access_token", ""),
                        getattr(entry, "api_key", ""),
                    )
                )
        except Exception:
            pass

    return False


def api_a_key_configured(provider_cfg: dict[str, Any]) -> bool:
    auth_mode = str(provider_cfg.get("auth_mode") or "").strip().lower()
    if auth_mode == "none":
        return True
    try:
        from ...infrastructure.providers.auth import has_usable_secret

        if has_usable_secret(str(provider_cfg.get("api_key") or "")):
            return True
    except Exception:
        pass
    return has_configured_api_key(str(provider_cfg.get("api_key_env") or ""))


def api_b_key_configured(
    memory_llm_cfg: dict[str, Any], providers: dict[str, Any] | None = None
) -> bool:
    provider = str(memory_llm_cfg.get("provider") or "").strip().lower()
    provider_cfg = (providers or {}).get(provider)
    if not isinstance(provider_cfg, dict):
        return False
    return api_a_key_configured(provider_cfg)


def api_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return a secret-free API-A/API-B/media generation summary."""
    cfg = dict(config or {})
    runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    active_provider = str(runtime.get("active_provider") or "").strip()
    active_cfg = providers.get(active_provider) if active_provider in providers else {}
    if not isinstance(active_cfg, dict):
        active_cfg = {}

    memory = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    llm = memory.get("llm") if isinstance(memory.get("llm"), dict) else {}
    api_b_provider = str(llm.get("provider") or "").strip().lower()
    api_b_provider_cfg = providers.get(api_b_provider)
    if not isinstance(api_b_provider_cfg, dict):
        api_b_provider_cfg = {}
    api_b_key_env = str(
        api_b_provider_cfg.get("api_key_env")
        or ""
    ).strip()
    image_generation = (
        cfg.get("image_generation")
        if isinstance(cfg.get("image_generation"), dict)
        else {}
    )
    video_generation = (
        cfg.get("video_generation")
        if isinstance(cfg.get("video_generation"), dict)
        else {}
    )

    retired_fields = [
        key
        for key in ("model", "provider", "base_url", "custom_providers")
        if key in cfg
    ]

    return {
        "api_a": {
            "provider": active_provider or "未设置",
            "model": str(active_cfg.get("selected_model") or "").strip() or "未设置",
            "auth_mode": str(active_cfg.get("auth_mode") or "").strip() or "env",
            "api_key_env": str(active_cfg.get("api_key_env") or "").strip() or "无",
            "key_configured": api_a_key_configured(active_cfg),
        },
        "api_b": {
            "provider": api_b_provider or "未设置",
            "model": str(llm.get("model") or "").strip() or "未设置",
            "api_key_env": api_b_key_env or "无",
            "base_url": str(api_b_provider_cfg.get("base_url") or "").strip() or "未设置",
            "key_configured": api_b_key_configured(llm, providers),
            "credential_sources": provider_credential_sources(api_b_provider, api_b_key_env),
        },
        "image_generation": {
            "provider": str(image_generation.get("provider") or "未设置").strip(),
            "endpoint": str(image_generation.get("endpoint") or "未设置").strip(),
            "api_key_env": str(image_generation.get("api_key_env") or "AGNES_API_KEY").strip(),
            "model": str(image_generation.get("model") or "未设置").strip(),
            "key_configured": bool(
                has_configured_api_key(str(image_generation.get("api_key_env") or "AGNES_API_KEY"))
            ),
        },
        "video_generation": {
            "provider": str(video_generation.get("provider") or "未设置").strip(),
            "endpoint": str(video_generation.get("endpoint") or "未设置").strip(),
            "result_endpoint": str(
                video_generation.get("result_endpoint") or "未设置"
            ).strip(),
            "api_key_env": str(video_generation.get("api_key_env") or "AGNES_API_KEY").strip(),
            "model": str(video_generation.get("model") or "未设置").strip(),
            "key_configured": bool(
                has_configured_api_key(str(video_generation.get("api_key_env") or "AGNES_API_KEY"))
            ),
        },
        "retired_fields_present": retired_fields,
    }


def _render_credential_sources(sources: list[dict[str, str]]) -> list[str]:
    labels = {
        "usable": "可用",
        "present_unusable": "存在但不可用",
        "missing": "未找到",
        "error": "检查失败",
    }
    return [
        f"    - {source.get('source', 'unknown')}: "
        f"{labels.get(source.get('status', ''), source.get('status', '未知'))}"
        f" ({source.get('detail', '')})"
        for source in sources
    ]


def render_api_config_summary(config: dict[str, Any]) -> list[str]:
    """Render the secret-free API summary for the interactive /api menu."""
    summary = api_config_summary(config)
    api_a = summary["api_a"]
    api_b = summary["api_b"]
    image_generation = summary["image_generation"]
    video_generation = summary["video_generation"]
    retired = summary["retired_fields_present"]
    return [
        "API-A（用户交互 / 主 CLI）",
        f"  Provider: {api_a['provider']}",
        f"  Model: {api_a['model']}",
        f"  Key: {'已配置' if api_a['key_configured'] else '未配置'} ({api_a['api_key_env']})",
        f"  Auth mode: {api_a['auth_mode']}",
        "",
        "API-B（Mem / Supervisor 自主链路）",
        f"  Provider: {api_b['provider']}",
        f"  Model: {api_b['model']}",
        f"  Key: {'已配置' if api_b['key_configured'] else '未配置'} ({api_b['api_key_env']})",
        f"  Base URL: {api_b['base_url']}",
        "  Credential sources:",
        *_render_credential_sources(api_b.get("credential_sources") or []),
        "",
        "图像生成（独立于 API-A/API-B）",
        f"  Provider: {image_generation['provider']}",
        f"  Key: {'已配置' if image_generation['key_configured'] else '未配置'} ({image_generation['api_key_env']})",
        f"  Endpoint: {image_generation['endpoint']}",
        f"  Model: {image_generation['model']}",
        "",
        "视频生成（独立于 API-A/API-B）",
        f"  Provider: {video_generation['provider']}",
        f"  Key: {'已配置' if video_generation['key_configured'] else '未配置'} ({video_generation['api_key_env']})",
        f"  Submit endpoint: {video_generation['endpoint']}",
        f"  Result endpoint: {video_generation['result_endpoint']}",
        f"  Model: {video_generation['model']}",
        "",
        "废弃字段",
        f"  {'无' if not retired else ', '.join(retired)}",
    ]


# The non-interactive Provider/configuration service lives in infrastructure;
# this module only exposes the CLI wizard adapter.
from voidcube.infrastructure.config import provider_config as _provider_config

load_current_config = _provider_config.load_current_config
save_env_value = _provider_config.save_env_value
_provider_key_from_name = _provider_config.provider_key_from_name
save_provider_pool_entry = _provider_config.save_provider_pool_entry
save_ollama_provider = _provider_config.save_ollama_provider
persist_provider_pool_entry = _provider_config.persist_provider_pool_entry
persist_ollama_provider = _provider_config.persist_ollama_provider
provider_model_catalog = _provider_config.provider_model_catalog
provider_pool_api_key = _provider_config.provider_pool_api_key
persist_api_a_selection = _provider_config.persist_api_a_selection
persist_api_b_config = _provider_config.persist_api_b_config
has_configured_api_key = _provider_config.has_configured_api_key
provider_credential_sources = _provider_config.provider_credential_sources
credential_sources_have_usable_secret = _provider_config.credential_sources_have_usable_secret
provider_has_usable_credential = _provider_config.provider_has_usable_credential
api_a_key_configured = _provider_config.api_a_key_configured
api_b_key_configured = _provider_config.api_b_key_configured
get_provider_models_from_api = _provider_config.get_provider_models_from_api


def refresh_provider_pool_catalog(
    config: dict[str, Any],
    provider_key: str,
    *,
    model_fetcher: Callable[..., list[tuple[str, str]]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """CLI adapter that keeps model discovery injectable for interactive tests."""
    return _provider_config.refresh_provider_pool_catalog(
        config,
        provider_key,
        model_fetcher=model_fetcher or get_provider_models_from_api,
    )


# =========================================================================
# 显示组件插口 - 可在此添加自定义显示功能
# =========================================================================

class DisplayComponents:
    """显示组件集合，提供各种可插拔的显示功能"""
    
    # ANSI 颜色代码
    COLORS = {
        'black': '\033[30m',
        'red': '\033[31m',
        'green': '\033[32m',
        'yellow': '\033[33m',
        'blue': '\033[34m',
        'magenta': '\033[35m',
        'cyan': '\033[36m',
        'white': '\033[37m',
        'bright_black': '\033[90m',
        'bright_red': '\033[91m',
        'bright_green': '\033[92m',
        'bright_yellow': '\033[93m',
        'bright_blue': '\033[94m',
        'bright_magenta': '\033[95m',
        'bright_cyan': '\033[96m',
        'bright_white': '\033[97m',
        'reset': '\033[0m',
        'bold': '\033[1m',
        'dim': '\033[2m',
        'italic': '\033[3m',
        'underline': '\033[4m',
    }
    
    # 进度条样式
    PROGRESS_STYLES = {
        'classic': {'filled': '█', 'empty': '░'},
        'modern': {'filled': '▰', 'empty': '▱'},
        'dots': {'filled': '●', 'empty': '○'},
        'arrows': {'filled': '▶', 'empty': '▷'},
        'stars': {'filled': '★', 'empty': '☆'},
    }
    
    # 边框样式
    BORDER_STYLES = {
        'simple': {'tl': '+', 'tr': '+', 'bl': '+', 'br': '+', 'h': '-', 'v': '|'},
        'double': {'tl': '╔', 'tr': '╗', 'bl': '╚', 'br': '╝', 'h': '═', 'v': '║'},
        'rounded': {'tl': '╭', 'tr': '╮', 'bl': '╰', 'br': '╯', 'h': '─', 'v': '│'},
        'bold': {'tl': '┏', 'tr': '┓', 'bl': '┗', 'br': '┛', 'h': '━', 'v': '┃'},
    }
    
    @staticmethod
    def colored(text: str, color: str = 'white', bold: bool = False) -> str:
        """返回彩色文本
        
        Args:
            text: 要显示的文本
            color: 颜色名称 (red, green, yellow, blue, magenta, cyan, white, etc.)
            bold: 是否加粗
            
        Returns:
            带ANSI颜色代码的文本
        """
        result = DisplayComponents.COLORS.get(color, DisplayComponents.COLORS['white'])
        if bold:
            result += DisplayComponents.COLORS['bold']
        result += text
        result += DisplayComponents.COLORS['reset']
        return result
    
    @staticmethod
    def separator(width: int = 60, char: str = '=', color: str = 'yellow') -> str:
        """生成分隔线
        
        Args:
            width: 分隔线宽度
            char: 分隔线字符
            color: 颜色
            
        Returns:
            分隔线字符串
        """
        return DisplayComponents.colored(char * width, color)
    
    @staticmethod
    def header(text: str, width: int = 60, border_style: str = 'rounded', color: str = 'cyan') -> str:
        """生成带边框的标题
        
        Args:
            text: 标题文本
            width: 标题框宽度
            border_style: 边框样式 (simple, double, rounded, bold)
            color: 颜色
            
        Returns:
            格式化的标题字符串
        """
        border = DisplayComponents.BORDER_STYLES.get(border_style, DisplayComponents.BORDER_STYLES['rounded'])
        content_width = width - 4
        text_lines = text.split('\n')
        
        result = []
        result.append(DisplayComponents.colored(f"{border['tl']}{border['h'] * (width - 2)}{border['tr']}", color))
        
        for line in text_lines:
            padded_line = line.center(content_width)
            result.append(DisplayComponents.colored(f"{border['v']} {padded_line} {border['v']}", color))
        
        result.append(DisplayComponents.colored(f"{border['bl']}{border['h'] * (width - 2)}{border['br']}", color))
        return '\n'.join(result)
    
    @staticmethod
    def progress_bar(current: int, total: int, width: int = 50, style: str = 'classic', 
                    color: str = 'green', show_percent: bool = True, 
                    show_count: bool = True, prefix: str = '') -> str:
        """生成进度条
        
        Args:
            current: 当前进度
            total: 总进度
            width: 进度条宽度
            style: 进度条样式 (classic, modern, dots, arrows, stars)
            color: 颜色
            show_percent: 是否显示百分比
            show_count: 是否显示计数
            prefix: 前缀文本
            
        Returns:
            格式化的进度条字符串
        """
        style_chars = DisplayComponents.PROGRESS_STYLES.get(style, DisplayComponents.PROGRESS_STYLES['classic'])
        
        if total <= 0:
            percent = 0
        else:
            percent = min(current / total, 1.0)
        
        filled = int(width * percent)
        empty = width - filled
        
        bar = style_chars['filled'] * filled + style_chars['empty'] * empty
        
        result = []
        if prefix:
            result.append(f"{prefix} ")
        
        result.append(DisplayComponents.colored(f"[{bar}]", color))
        
        if show_percent:
            result.append(f" {percent * 100:.1f}%")
        
        if show_count:
            result.append(f" ({current}/{total})")
        
        return ''.join(result)
    
    @staticmethod
    def table(data: list[list], headers: list = None, border_style: str = 'simple', 
             cell_padding: int = 2, header_color: str = 'cyan', 
             row_colors: list = None, align: str = 'left') -> str:
        """生成表格
        
        Args:
            data: 表格数据（二维列表）
            headers: 表头列表
            border_style: 边框样式
            cell_padding: 单元格内边距
            header_color: 表头颜色
            row_colors: 行颜色列表（循环使用）
            align: 对齐方式 (left, center, right)
            
        Returns:
            格式化的表格字符串
        """
        if not data:
            return ""
        
        border = DisplayComponents.BORDER_STYLES.get(border_style, DisplayComponents.BORDER_STYLES['simple'])
        
        all_rows = [headers] + data if headers else data
        
        col_widths = []
        for col in range(len(all_rows[0])):
            max_width = max(len(str(row[col])) for row in all_rows if col < len(row))
            col_widths.append(max_width + cell_padding * 2)
        
        result = []
        
        def format_row(row, is_header=False, color='white'):
            cells = []
            for i, cell in enumerate(row):
                if i >= len(col_widths):
                    continue
                cell_str = str(cell)
                width = col_widths[i] - cell_padding * 2
                
                if align == 'left':
                    formatted = cell_str.ljust(width)
                elif align == 'right':
                    formatted = cell_str.rjust(width)
                else:
                    formatted = cell_str.center(width)
                
                cells.append(' ' * cell_padding + formatted + ' ' * cell_padding)
            
            line = border['v'].join(cells)
            return DisplayComponents.colored(f"{border['v']}{line}{border['v']}", color)
        
        def separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['tl']}{border['h'].join(parts)}{border['tr']}", 'dim')
        
        def bottom_separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['bl']}{border['h'].join(parts)}{border['br']}", 'dim')
        
        def middle_separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['v'].replace(border['v'], '├')}{border['h'].join(parts)}{border['v'].replace(border['v'], '┤')}", 'dim')
        
        result.append(separator_line())
        
        if headers:
            result.append(format_row(headers, True, header_color))
            result.append(middle_separator_line())
            data_rows = data
        else:
            data_rows = data
        
        row_colors = row_colors or ['white']
        for i, row in enumerate(data_rows):
            color = row_colors[i % len(row_colors)]
            result.append(format_row(row, False, color))
        
        result.append(bottom_separator_line())
        return '\n'.join(result)
    
    @staticmethod
    def list_items(items: list, bullet: str = '•', color: str = 'white', 
                  indent: int = 2, numbered: bool = False) -> str:
        """生成列表
        
        Args:
            items: 项目列表
            bullet: 项目符号
            color: 颜色
            indent: 缩进
            numbered: 是否使用数字编号
            
        Returns:
            格式化的列表字符串
        """
        result = []
        for i, item in enumerate(items):
            prefix = f"{i + 1}." if numbered else bullet
            line = ' ' * indent + f"{prefix} {item}"
            result.append(DisplayComponents.colored(line, color))
        return '\n'.join(result)
    
    @staticmethod
    def key_value(data: dict, key_color: str = 'yellow', value_color: str = 'white',
                 colon: str = ': ', align_keys: bool = True) -> str:
        """生成键值对显示
        
        Args:
            data: 字典数据
            key_color: 键的颜色
            value_color: 值的颜色
            colon: 分隔符
            align_keys: 是否对齐键
            
        Returns:
            格式化的键值对字符串
        """
        if not data:
            return ""
        
        result = []
        
        if align_keys:
            max_key_len = max(len(str(k)) for k in data.keys())
        else:
            max_key_len = 0
        
        for key, value in data.items():
            key_str = str(key)
            if align_keys:
                key_str = key_str.ljust(max_key_len)
            
            line = (DisplayComponents.colored(key_str, key_color) + 
                   colon + 
                   DisplayComponents.colored(str(value), value_color))
            result.append(line)
        
        return '\n'.join(result)
    
    @staticmethod
    def spinner(message: str = "Loading...", style: str = 'dots') -> 'Spinner':
        """创建一个加载动画
        
        Args:
            message: 加载消息
            style: 动画样式
            
        Returns:
            Spinner实例
        """
        return Spinner(message, style)
    
    @staticmethod
    def success(message: str, icon: str = '✓') -> str:
        """成功消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'green', bold=True)
    
    @staticmethod
    def error(message: str, icon: str = '✗') -> str:
        """错误消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'red', bold=True)
    
    @staticmethod
    def warning(message: str, icon: str = '⚠') -> str:
        """警告消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'yellow', bold=True)
    
    @staticmethod
    def info(message: str, icon: str = 'ℹ') -> str:
        """信息消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'cyan', bold=True)
    
    @staticmethod
    def highlight(text: str, substring: str, highlight_color: str = 'yellow',
                 bold: bool = True) -> str:
        """高亮文本中的子字符串
        
        Args:
            text: 原文本
            substring: 要高亮的子字符串
            highlight_color: 高亮颜色
            bold: 是否加粗
            
        Returns:
            带高亮的文本
        """
        import re
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        
        def replace(match):
            return DisplayComponents.colored(match.group(0), highlight_color, bold=bold)
        
        return pattern.sub(replace, text)
    
    @staticmethod
    def tree(data: dict, prefix: str = '', is_last: bool = True) -> str:
        """生成树形结构显示
        
        Args:
            data: 树数据（嵌套字典）
            prefix: 前缀（用于递归）
            is_last: 是否是最后一个节点（用于递归）
            
        Returns:
            树形结构字符串
        """
        result = []
        items = list(data.items())
        
        for i, (key, value) in enumerate(items):
            is_last_item = i == len(items) - 1
            
            connector = '└── ' if is_last_item else '├── '
            result.append(f"{prefix}{connector}{key}")
            
            if isinstance(value, dict):
                extension = '    ' if is_last_item else '│   '
                result.append(DisplayComponents.tree(value, prefix + extension, True))
            elif isinstance(value, list):
                extension = '    ' if is_last_item else '│   '
                list_dict = {str(j): item for j, item in enumerate(value)}
                result.append(DisplayComponents.tree(list_dict, prefix + extension, True))
            elif value is not None:
                extension = '    ' if is_last_item else '│   '
                result.append(f"{prefix}{extension}└── {value}")
        
        return '\n'.join(result)
    
    @staticmethod
    def git_info(path: str = '.', show_details: bool = True) -> str:
        """生成 Git 仓库信息显示
        
        Args:
            path: Git 仓库路径
            show_details: 是否显示详细信息
            
        Returns:
            格式化的 Git 信息字符串
        """
        import subprocess
        import os
        
        def run_git_cmd(cmd):
            try:
                # 使用 utf-8 编码，避免 Windows 下 GBK 编码问题
                result = subprocess.run(
                    ['git'] + cmd,
                    cwd=path,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5
                )
                if result.returncode == 0:
                    return result.stdout.strip()
                return None
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return None
        
        # 检查是否是 Git 仓库
        git_dir = os.path.join(path, '.git')
        if not os.path.exists(git_dir):
            return DisplayComponents.error("当前目录不是 Git 仓库")
        
        result = []
        
        # 获取基本信息
        branch = run_git_cmd(['rev-parse', '--abbrev-ref', 'HEAD']) or '未知分支'
        commit_hash = run_git_cmd(['rev-parse', '--short', 'HEAD']) or '未知提交'
        commit_msg = run_git_cmd(['log', '-1', '--pretty=%B']) or '无提交信息'
        commit_msg = commit_msg.split('\n')[0][:50]
        
        # 获取状态信息
        status = run_git_cmd(['status', '--porcelain'])
        modified = len([line for line in status.split('\n') if line.strip()]) if status else 0
        staged = len([line for line in status.split('\n') if line.strip() and line[1] != ' ']) if status else 0
        
        # 获取远程信息
        remote = run_git_cmd(['remote', 'get-url', 'origin']) or '无远程仓库'
        if len(remote) > 50:
            remote = remote[:47] + '...'
        
        # 获取作者信息
        author = run_git_cmd(['config', 'user.name']) or '未知用户'
        email = run_git_cmd(['config', 'user.email']) or ''
        
        # 构建显示
        result.append(DisplayComponents.header("Git 仓库信息", width=50, color='green'))
        
        git_data = {
            "分支": branch,
            "提交": commit_hash,
            "提交信息": commit_msg,
            "未提交修改": f"{modified} 个文件" if modified > 0 else "无",
            "暂存文件": f"{staged} 个文件" if staged > 0 else "无",
            "远程仓库": remote,
        }
        
        if email:
            git_data["作者"] = f"{author} <{email}>"
        else:
            git_data["作者"] = author
        
        result.append(DisplayComponents.key_value(git_data, key_color='yellow', value_color='cyan'))
        
        # 如果有修改，显示状态
        if modified > 0 and show_details:
            result.append("")
            result.append(DisplayComponents.colored("  文件变更:", 'yellow'))
            
            if status:
                files = status.split('\n')[:10]  # 最多显示10个文件
                for file in files:
                    if file.strip():
                        status_char = file[0]
                        filename = file[2:].strip()
                        
                        if status_char == 'M':
                            icon = '✏️'
                            color = 'yellow'
                        elif status_char == 'A':
                            icon = '➕'
                            color = 'green'
                        elif status_char == 'D':
                            icon = '🗑️'
                            color = 'red'
                        elif status_char == 'R':
                            icon = '🔄'
                            color = 'cyan'
                        elif status_char == '??':
                            icon = '❓'
                            color = 'magenta'
                        else:
                            icon = '📄'
                            color = 'white'
                        
                        result.append(f"    {icon} {DisplayComponents.colored(filename, color)}")
                
                status_lines = status.split('\n')
                if len(status_lines) > 10:
                    result.append(f"    ... 还有 {len(status_lines) - 10} 个文件")
        
        return '\n'.join(result)

    @staticmethod
    def system_info():
        """生成系统信息显示
        
        Returns:
            格式化的系统信息字符串
        """
        import platform
        import sys
        import os
        
        result = []
        result.append(DisplayComponents.header('系统信息', width=50, border_style='rounded', color='cyan'))
        
        info = {
            '操作系统': f"{platform.system()} {platform.release()}",
            'Python 版本': platform.python_version(),
            '架构': platform.machine(),
            '处理器': platform.processor() or '未知',
            '当前目录': os.getcwd(),
        }
        
        # 尝试获取更多系统信息
        try:
            import psutil
            mem = psutil.virtual_memory()
            info['内存总量'] = f"{mem.total / (1024**3):.1f} GB"
            info['内存使用'] = f"{mem.percent}%"
        except Exception:
            pass
        
        result.append(DisplayComponents.key_value(info, key_color='yellow', value_color='white'))
        
        return '\n'.join(result)


class Spinner:
    """简单的加载动画类"""
    
    SPINNERS = {
        'dots': ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
        'line': ['|', '/', '-', '\\'],
        'bounce': ['◐', '◓', '◑', '◒'],
        'arrow': ['←', '↖', '↑', '↗', '→', '↘', '↓', '↙'],
        'grow': ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█', '▇', '▆', '▅', '▄', '▃', '▂'],
        'moon': ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'],
        'earth': ['🌍', '🌎', '🌏'],
    }
    
    def __init__(self, message: str = "Loading...", style: str = 'dots', output_fn=None):
        self.message = message
        self.style = style
        self.frames = self.SPINNERS.get(style, self.SPINNERS['dots'])
        self.current_frame = 0
        self.output_fn = output_fn or print
        self.running = False
        
    def start(self):
        """开始加载动画（这里只是占位，实际使用可能需要线程）"""
        self.running = True
        
    def stop(self, final_message: str = None):
        """停止加载动画"""
        self.running = False
        if final_message:
            self.output_fn(f"\r{final_message}")
        else:
            self.output_fn("\r" + " " * (len(self.message) + 10))
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
    
    def update(self, message: str = None):
        """更新消息并返回当前帧"""
        if message:
            self.message = message
        frame = self.frames[self.current_frame % len(self.frames)]
        self.current_frame += 1
        return f"\r{frame} {self.message}"


# 便捷访问显示组件
dc = DisplayComponents


def _provider_api_key_env(provider_key: str) -> str:
    """Return the internal environment variable name for one Provider."""
    normalized_key = str(provider_key or "").strip().upper().replace("-", "_")
    return f"VOIDCUBE_PROVIDER_{normalized_key}_API_KEY"


def run_api_config_wizard(runtime: ApiConfigRuntime | None = None):
    """运行 API 配置向导"""
    
    original_stdout = sys.stdout
    try:
        sys.stdout = sys.__stdout__
    except (AttributeError, OSError):
        pass
    
    def p(text):
        """直接写入原始 stdout"""
        sys.stdout.write(str(text) + "\n")
        sys.stdout.flush()
    
    def ph(title):
        p("\n" + "=" * 60)
        p(f"  {title}")
        p("=" * 60)
    
    def ps(msg):
        p(f"  ✅ {msg}")
    
    def pe(msg):
        p(f"  ❌ {msg}")
    
    def pi(msg):
        p(f"  ℹ️  {msg}")
    
    def inp(prompt, default=""):
        pr = f"{prompt}: "
        sys.stdout.write(pr)
        sys.stdout.flush()
        try:
            user_input = sys.stdin.readline().strip()
            return user_input if user_input else default
        except EOFError:
            return default

    def secret_inp(prompt, default=""):
        """Read a secret without echoing it; retain the default on blank input."""
        try:
            value = getpass.getpass(f"{prompt}: ")
            return value if value else default
        except EOFError:
            return default

    def apply_runtime_updates(selected_model: str, provider_key: str) -> None:
        if runtime is None:
            return
        if runtime.set_model is not None:
            runtime.set_model(selected_model)
            ps("CLI 当前模型已更新")
        if runtime.set_provider is not None:
            runtime.set_provider(provider_key)
            ps("CLI 当前 Provider 已更新")
        if runtime.set_requested_provider is not None:
            runtime.set_requested_provider(provider_key)
            ps("CLI 请求 Provider 已更新")
    
    if os.name == 'nt':
        subprocess.call('cls', shell=True)
    else:
        subprocess.call(['clear'])
    
    ph("API 配置向导")
    
    p("\n欢迎使用 VoidCube API 配置向导！")
    p("本向导将帮助您配置 LLM API 设置。\n")
    
    current_config = load_current_config()
    runtime_config = current_config.get("runtime", {}) if isinstance(current_config.get("runtime"), dict) else {}
    providers_config = current_config.get("providers", {}) if isinstance(current_config.get("providers"), dict) else {}
    
    p("📋 当前配置：")
    current_provider = runtime_config.get("active_provider") or "未设置"
    current_provider_cfg = providers_config.get(current_provider, {}) if current_provider in providers_config else {}
    current_model = current_provider_cfg.get("selected_model") or "未设置"
    current_api_a_key = "已配置" if api_a_key_configured(current_provider_cfg) else "未配置"
    p(f"   API-A Provider: {current_provider}")
    p(f"   API-A Model: {current_model}")
    p(f"   API-A Key: {current_api_a_key}")
    
    # 显示记忆系统配置
    memory_config = current_config.get("memory", {})
    memory_llm_config = memory_config.get("llm", {})
    memory_provider = memory_llm_config.get("provider", "未设置")
    memory_model = memory_llm_config.get("model", "未设置")
    memory_provider_cfg = providers_config.get(str(memory_provider or "").strip().lower(), {})
    memory_key_env = memory_provider_cfg.get("api_key_env") or "无"
    memory_key_state = "已配置" if api_b_key_configured(memory_llm_config, providers_config) else "未配置"
    p(f"   API-B Provider: {memory_provider}")
    p(f"   API-B Model: {memory_model}")
    p(f"   API-B Key: {memory_key_state} ({memory_key_env})")
    image_config = current_config.get("image_generation", {})
    video_config = current_config.get("video_generation", {})
    if not isinstance(image_config, dict):
        image_config = {}
    if not isinstance(video_config, dict):
        video_config = {}
    media_key_env = str(
        image_config.get("api_key_env")
        or video_config.get("api_key_env")
        or "AGNES_API_KEY"
    )
    media_key_state = "已配置" if has_configured_api_key(media_key_env) else "未配置"
    p(f"   图像模型: {image_config.get('model', 'agnes-image-2.1-flash')}")
    p(f"   视频模型: {video_config.get('model', 'agnes-video-v2.0')}")
    p(f"   图像/视频 Key: {media_key_state} ({media_key_env})")
    p("")
    
    # 主菜单循环
    while True:
        p("\n请选择配置模式：")
        p("   [1] 添加 Provider")
        p("   [2] 本地模型（Ollama）")
        p("   [3] Agent 模型配置（API-A）")
        p("   [4] 记忆模型配置（API-B）")
        p("   [5] 图像模型配置")
        p("   [6] 视频模型配置")
        p("   [7] 查看当前配置")
        p("   [0] 退出")
        
        choice = inp("\n请选择")
        
        if choice == "0":
            p("\n已取消配置。")
            break
        
        elif choice == "1":
            # Shared Provider pool entry
            while True:
                ph("添加 Provider")
                p("\n直接添加支持 OpenAI 兼容 /models 的 Provider。输入 0 返回。")
                provider_name = inp("Provider 名称")
                if provider_name == "0":
                    break
                if not provider_name:
                    pe("Provider 名称不能为空")
                    continue
                provider_key = inp(
                    "Provider 标识", _provider_key_from_name(provider_name)
                ).strip().lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", provider_key):
                    pe("Provider 标识只能包含小写字母、数字、连字符或下划线")
                    continue
                base_url = inp("Base URL")
                if not base_url:
                    pe("Base URL 不能为空")
                    continue
                api_key = secret_inp("API Key（必填）")
                if not api_key:
                    pe("API Key 不能为空；本地免鉴权请使用 [2] 本地模型（Ollama）")
                    continue
                auth_mode = "env"
                api_key_env = _provider_api_key_env(provider_key)
                model_override = inp(
                    "模型名称（可留空，留空则从 /models 获取）"
                ).strip()
                models = get_provider_models_from_api(
                    provider_key, api_key=api_key, base_url=base_url
                )
                model_ids = [model_id for model_id, _ in models]
                if not model_ids and not model_override:
                    pe("无法从 Provider /models 获取模型列表；请检查 Base URL 和 Key")
                    continue
                if model_override:
                    if model_override not in model_ids:
                        model_ids.append(model_override)
                    selected_model = model_override
                else:
                    for i, model_id in enumerate(model_ids[:50], 1):
                        p(f"   [{i}] {model_id}")
                    try:
                        selected_model = model_ids[int(inp("选择默认模型", "1")) - 1]
                    except (ValueError, IndexError):
                        selected_model = model_ids[0]
                if not save_env_value(api_key_env, api_key):
                    pe("API Key 保存失败")
                    continue
                if not save_provider_pool_entry(provider_key, label=provider_name, model_catalog=model_ids, provider_type="openai_compatible", base_url=base_url, api_key_env=api_key_env, auth_mode=auth_mode, selected_model=selected_model, model_override=model_override):
                    pe("Provider 配置保存失败")
                    continue
                ps(f"Provider {provider_key} 与 {len(model_ids)} 个模型已保存")
                current_config = load_current_config()
                providers_config = current_config.get("providers", {})
                break
        
        elif choice == "2":
            ph("本地模型（Ollama）")
            existing = providers_config.get("ollama", {})
            if not isinstance(existing, dict):
                existing = {}
            base_url = inp(
                "Ollama Base URL",
                str(existing.get("base_url") or "http://localhost:11434/v1"),
            ).strip()
            if not base_url:
                pe("Ollama Base URL 不能为空")
                continue
            pi("正在从 Ollama /models 获取本地模型...")
            models = get_provider_models_from_api(
                "ollama", base_url=base_url
            )
            model_ids = [model_id for model_id, _ in models]
            if not model_ids:
                pe("无法连接 Ollama 或未发现模型；请确认 Ollama 已启动并已拉取模型")
                continue
            for i, model_id in enumerate(model_ids, 1):
                p(f"   [{i}] {model_id}")
            current_model = str(existing.get("selected_model") or "").strip()
            default_index = (
                str(model_ids.index(current_model) + 1)
                if current_model in model_ids
                else "1"
            )
            try:
                selected_model = model_ids[int(inp("选择默认模型", default_index)) - 1]
            except (ValueError, IndexError):
                selected_model = model_ids[int(default_index) - 1]
            if not save_ollama_provider(
                base_url=base_url,
                model_catalog=model_ids,
                selected_model=selected_model,
            ):
                pe("Ollama 配置保存失败")
                continue
            current_config = load_current_config()
            providers_config = current_config.get("providers", {})
            ps(f"Ollama / {selected_model} 已加入统一 API 池")
            pi("API-A、API-B 和员工代理现在都可以选择该 Provider")
            continue

        elif choice == "3":
            # API-A selection
            while True:
                ph("Agent 模型配置（API-A）")
                entries = [(key, value) for key, value in providers_config.items() if isinstance(value, dict)]
                if not entries: pe("请先使用 [1] 添加 Provider 或 [2] 配置本地模型"); break
                for i, (key, value) in enumerate(entries, 1): p(f"   [{i}] {value.get('label', key)} ({key})")
                p("   [0] 返回")
                try: provider_key, provider_cfg = entries[int(inp("选择 Provider")) - 1]
                except (ValueError, IndexError): break
                model_ids = provider_model_catalog(provider_cfg)
                if not model_ids:
                    pi("该 Provider 尚无模型目录，正在从 /models 获取...")
                    refreshed, model_ids = refresh_provider_pool_catalog(
                        load_current_config(), provider_key
                    )
                    if model_ids:
                        from ...infrastructure.config.configuration import save_config

                        save_config(refreshed)
                        current_config = refreshed
                        providers_config = refreshed.get("providers", {})
                    else:
                        pe("模型目录获取失败，请检查该 Provider 的 Base URL 和 Key")
                        break
                for i, model_id in enumerate(model_ids, 1): p(f"   [{i}] {model_id}")
                try: selected_model = model_ids[int(inp("选择模型", "1")) - 1]
                except (ValueError, IndexError): selected_model = model_ids[0]
                current_config = persist_api_a_selection(load_current_config(), provider=provider_key, model=selected_model)
                from ...infrastructure.config.configuration import save_config
                save_config(current_config)
                apply_runtime_updates(selected_model, provider_key)
                ps(f"API-A 已选择 {provider_key} / {selected_model}")
                break
        
        elif choice == "4":
            # API-B selection
            while True:
                ph("记忆模型配置（API-B）")
                entries = [(key, value) for key, value in providers_config.items() if isinstance(value, dict)]
                if not entries: pe("请先使用 [1] 添加 Provider 或 [2] 配置本地模型"); break
                for i, (key, value) in enumerate(entries, 1): p(f"   [{i}] {value.get('label', key)} ({key})")
                p("   [0] 返回")
                try: provider_key, provider_cfg = entries[int(inp("选择 Provider")) - 1]
                except (ValueError, IndexError): break
                model_ids = provider_model_catalog(provider_cfg)
                if not model_ids:
                    pi("该 Provider 尚无模型目录，正在从 /models 获取...")
                    refreshed, model_ids = refresh_provider_pool_catalog(
                        load_current_config(), provider_key
                    )
                    if model_ids:
                        from ...infrastructure.config.configuration import save_config

                        save_config(refreshed)
                        current_config = refreshed
                        providers_config = refreshed.get("providers", {})
                    else:
                        pe("模型目录获取失败，请检查该 Provider 的 Base URL 和 Key")
                        break
                for i, model_id in enumerate(model_ids, 1): p(f"   [{i}] {model_id}")
                try: memory_model = model_ids[int(inp("选择模型", "1")) - 1]
                except (ValueError, IndexError): memory_model = model_ids[0]
                existing_capabilities = (
                    (provider_cfg.get("model_capabilities") or {}).get(memory_model, {})
                    if isinstance(provider_cfg.get("model_capabilities"), dict)
                    else {}
                )
                def capability_default(name: str) -> str:
                    return "y" if (
                        isinstance(existing_capabilities, dict)
                        and existing_capabilities.get(name)
                    ) else "n"

                native_modalities = []
                if inp(
                    "该模型支持 API-B 原生图像输入？(y/n)",
                    capability_default("image_input"),
                ).strip().lower() in {"y", "yes", "1", "true"}:
                    native_modalities.append("image")
                if inp(
                    "该模型支持 API-B 原生音频输入？(y/n)",
                    capability_default("audio_input"),
                ).strip().lower() in {"y", "yes", "1", "true"}:
                    native_modalities.append("audio")
                if inp(
                    "该模型支持 API-B 原生视频输入？(y/n)",
                    capability_default("video_input"),
                ).strip().lower() in {"y", "yes", "1", "true"}:
                    native_modalities.append("video")
                native_audio_output = inp(
                    "该模型支持 API-B 原生语音输出？(y/n)",
                    capability_default("audio_output"),
                ).strip().lower() in {"y", "yes", "1", "true"}
                if not save_memory_llm_config(
                    provider_key,
                    memory_model,
                    native_modalities=native_modalities,
                    native_audio_output=native_audio_output,
                ):
                    pe("保存 API-B Provider/模型引用失败")
                    break
                current_config = load_current_config()
                ps(f"API-B 已选择 {provider_key} / {memory_model}")
                break
        
        elif choice == "5":
            ph("Agnes-AI 图像模型配置")
            from ...infrastructure.providers.media_generation import (
                default_image_generation_config,
            )

            existing = dict(current_config.get("image_generation") or {})
            defaults = default_image_generation_config()
            endpoint = inp(
                "图像生成 Endpoint",
                str(existing.get("endpoint") or defaults["endpoint"]),
            ).rstrip("/")
            model = inp(
                "图像模型",
                str(existing.get("model") or defaults["model"]),
            )
            key_env = str(existing.get("api_key_env") or "AGNES_API_KEY").strip()
            current_key = ""
            try:
                from ...infrastructure.config.configuration import get_env_value

                current_key = str(get_env_value(key_env) or "").strip()
            except Exception:
                current_key = ""
            p(f"\n图像和视频统一使用 {key_env}（输入时不回显，留空保留已有 Key）")
            api_key = secret_inp("请输入 Agnes-AI API Key", current_key)
            if not api_key:
                pe("API Key 不能为空；如需清除请使用 `voidcube config unset AGNES_API_KEY`")
                continue

            if save_image_generation_config(
                provider="agnes-ai",
                api_key_env=key_env,
                endpoint=endpoint,
                model=model,
            ) and save_env_value(key_env, api_key):
                ps("Agnes-AI 图像模型配置保存成功")
            else:
                pe("保存 Agnes-AI 图像模型配置失败")
            current_config = load_current_config()
            continue

        elif choice == "6":
            ph("Agnes-AI 视频模型配置")
            from ...infrastructure.providers.media_generation import (
                default_video_generation_config,
            )

            existing = dict(current_config.get("video_generation") or {})
            defaults = default_video_generation_config()
            endpoint = inp(
                "视频提交 Endpoint",
                str(existing.get("endpoint") or defaults["endpoint"]),
            ).rstrip("/")
            result_endpoint = inp(
                "视频结果查询 Endpoint",
                str(existing.get("result_endpoint") or defaults["result_endpoint"]),
            ).rstrip("/")
            model = inp(
                "视频模型",
                str(existing.get("model") or defaults["model"]),
            )
            key_env = str(existing.get("api_key_env") or "AGNES_API_KEY").strip()
            current_key = ""
            try:
                from ...infrastructure.config.configuration import get_env_value

                current_key = str(get_env_value(key_env) or "").strip()
            except Exception:
                current_key = ""
            p(f"\n图像和视频统一使用 {key_env}（输入时不回显，留空保留已有 Key）")
            api_key = secret_inp("请输入 Agnes-AI API Key", current_key)
            if not api_key:
                pe("API Key 不能为空；如需清除请使用 `voidcube config unset AGNES_API_KEY`")
                continue

            if save_video_generation_config(
                provider="agnes-ai",
                api_key_env=key_env,
                endpoint=endpoint,
                result_endpoint=result_endpoint,
                model=model,
            ) and save_env_value(key_env, api_key):
                ps("Agnes-AI 视频模型配置保存成功")
            else:
                pe("保存 Agnes-AI 视频模型配置失败")
            current_config = load_current_config()
            continue

        elif choice == "7":
            ph("当前配置")

            current_config = load_current_config()
            if not current_config:
                pi("未找到配置文件")
                continue

            for line in render_api_config_summary(current_config):
                p(line)
            continue
        
        else:
            pe("无效选择，请重新选择。")
            continue
        
        # 跳出主循环
        break
    
    sys.stdout = original_stdout
