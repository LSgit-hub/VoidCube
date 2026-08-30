"""
Status command for VoidCube CLI.

Shows the status of all Voidcube Agent components.
"""

import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

from .colors import Colors, color
from ...infrastructure.config.configuration import (
    get_active_provider_key,
    get_configured_providers,
    get_env_value,
    load_config,
    redact_key,
)
from ...infrastructure.providers.model_catalog import provider_label
from ...infrastructure.providers.auth import has_usable_secret
from ...infrastructure.config.runtime_paths import get_env_path, get_VoidCube_home
from ...infrastructure.providers.endpoints import OPENROUTER_MODELS_URL
from ...extensions.tools.backend_helpers import managed_nous_tools_enabled

try:
    from .tools_config import get_nous_subscription_features
except Exception:
    def get_nous_subscription_features(_config):
        class _EmptyFeatures:
            nous_auth_present = False

            @staticmethod
            def items():
                return []

        return _EmptyFeatures()

def check_mark(ok: bool) -> str:
    if ok:
        return color("✓", Colors.GREEN)
    return color("✗", Colors.RED)


def _format_iso_timestamp(value) -> str:
    """Format ISO timestamps for status output, converting to local timezone."""
    if not value or not isinstance(value, str):
        return "（未知）"
    from datetime import datetime, timezone
    text = value.strip()
    if not text:
        return "（未知）"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return value
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _configured_model_label(config: dict) -> str:
    """Return the active model from the saved provider config."""
    active_provider = get_active_provider_key(config)
    providers = get_configured_providers(config)
    model = ""
    if active_provider and isinstance(providers.get(active_provider), dict):
        model = str(providers[active_provider].get("selected_model") or "").strip()
    return model or "(not set)"


def _effective_provider_label(config: dict) -> str:
    """Return the active provider label from the saved provider config."""
    active_provider = get_active_provider_key(config)
    if not active_provider:
        return "(not configured)"
    providers = get_configured_providers(config)
    provider_cfg = providers.get(active_provider)
    if isinstance(provider_cfg, dict):
        label = str(provider_cfg.get("label") or provider_cfg.get("name") or "").strip()
        if label:
            return label
    return provider_label(active_provider)


from ...infrastructure.runtime.environment import is_termux as _is_termux


def _print_three_segment_scene_bar() -> None:
    """Render the per-reporter scene bar (baseline §8.1).

    Reads ``/admin/scenes`` from the gateway and prints three independent
    segments — supervisor (API-B), agent (API-A), executor.  Each segment
    reflects the *reporter's own* scene; the gateway never fuses them.
    When the gateway is unreachable, prints a single ⛔ line and returns.
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    from ...infrastructure.gateway.executor import default_gateway_url
    from ...infrastructure.gateway.presence import gateway_auth_headers

    gateway_base = default_gateway_url()
    # /admin/scenes is a GET endpoint; pass refresh=true via the query string
    # so the gateway re-validates each reporter's scene before responding.
    url = f"{gateway_base}/admin/scenes?refresh=true"
    payload: Dict[str, Any] = {}
    try:
        req = urllib.request.Request(
            url,
            headers=gateway_auth_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        pass

    scenes = (payload or {}).get("scenes") or {}
    if not scenes:
        print("  分域场景状态:  ⛔ gateway offline")
        return

    # Per-reporter legal labels (baseline §8.1).  Any scene returned by
    # the gateway that does not appear in this map is rendered as the
    # raw scene name so misclassified scenes are still visible.
    scene_labels = {
        # supervisor (API-B)
        "idle": "静置",
        "planning": "判断安排",
        "drive": "内生判断",
        "memory": "记忆整理",
        "maintenance": "连续性维护",
        "handoff": "执行交接",
        "body_switch": "身体切换",
        # agent (API-A)
        "learning": "自主学习",
        "code_editing": "替身改进",
        "executing": "执行中",
    }

    def _render(key: str, name: str) -> str:
        info = scenes.get(key) or {}
        # Main CLI status observes the user chain. If Gateway exposes API-A
        # lanes, read user_chat so autonomous self-learning subagents in
        # supervisor_task do not leak into the user's status surface.
        if key == "agent":
            lane = ((info.get("lanes") or {}).get("user_chat")) if isinstance(info, dict) else None
            if isinstance(lane, dict) and lane:
                info = lane
        if not info.get("reachable"):
            return f"{name}: ⛔"
        scene = str(info.get("scene") or "idle")
        label = scene_labels.get(scene, scene)
        suffix = ""
        if key == "agent":
            task_id = info.get("scene_task_id")
            if task_id:
                suffix = f" · {str(task_id)[:8]}"
            fg_count = max(0, int(info.get("subagent_foreground_count") or 0))
            bg_count = max(0, int(info.get("subagent_background_count") or 0))
            if fg_count or bg_count:
                counts = f"{fg_count}+{bg_count}" if bg_count else str(fg_count)
                suffix += f" · SA {counts}"
                focus = str(
                    info.get("subagent_focus_tool")
                    or info.get("subagent_focus_preview")
                    or ""
                ).strip()
                if focus:
                    suffix += f" · {focus[:20]}"
        elif key == "supervisor":
            title = info.get("title")
            if title:
                suffix = f" · {str(title)[:24]}"
        return f"{name}: {label}{suffix}"

    print(
        "  分域场景状态:  "
        + "   ".join(
            [
                _render("supervisor", "🧠 API-B"),
                _render("agent",      "🤖 API-A"),
                _render("executor",   "⚙️ Executor"),
            ]
        )
    )


def show_status(args):
    """Show status of all Voidcube Agent components."""
    show_all = getattr(args, 'all', False)
    deep = getattr(args, 'deep', False)
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                 > Voidcube Agent Status                  │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    
    # =========================================================================
    # Environment
    # =========================================================================
    print()
    print(color("◆ Environment", Colors.CYAN, Colors.BOLD))
    print(f"  Project:      {PROJECT_ROOT}")
    print(f"  Python:       {sys.version.split()[0]}")
    
    env_path = get_env_path()
    print(f"  .env file:    {check_mark(env_path.exists())} {'exists' if env_path.exists() else 'not found'}")

    try:
        config = load_config()
    except Exception:
        config = {}

    print(f"  Model:        {_configured_model_label(config)}")
    print(f"  Provider:     {_effective_provider_label(config)}")
    
    # =========================================================================
    # API Keys
    # =========================================================================
    print()
    print(color("◆ API Keys", Colors.CYAN, Colors.BOLD))
    
    keys = {
        "OpenRouter": "OPENROUTER_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
        "DeepSeek": "DEEPSEEK_API_KEY",
        "Z.AI/GLM": "GLM_API_KEY",
        "Kimi": "KIMI_API_KEY",
        "MiniMax": "MINIMAX_API_KEY",
        "MiniMax-CN": "MINIMAX_CN_API_KEY",
        "Firecrawl": "FIRECRAWL_API_KEY",
        "Tavily": "TAVILY_API_KEY",
        "Browser Use": "BROWSER_USE_API_KEY",  # Optional — local browser works without this
        "Browserbase": "BROWSERBASE_API_KEY",  # Optional — direct credentials only
        "Agnes-AI": "AGNES_API_KEY",
        "Tinker": "TINKER_API_KEY",
        "WandB": "WANDB_API_KEY",
        "ElevenLabs": "ELEVENLABS_API_KEY",
        "GitHub": "GITHUB_TOKEN",
    }
    
    for name, env_var in keys.items():
        value = get_env_value(env_var) or ""
        has_key = has_usable_secret(value)
        display = redact_key(value) if not show_all else value
        print(f"  {name:<12}  {check_mark(has_key)} {display}")

    # =========================================================================
    # Auth Providers (OAuth)
    # =========================================================================
    print()
    print(color("◆ Auth Providers", Colors.CYAN, Colors.BOLD))

    try:
        from ...infrastructure.providers.auth import (
            get_nous_auth_status,
            get_qwen_auth_status,
        )
        nous_status = get_nous_auth_status()
        qwen_status = get_qwen_auth_status()
    except Exception:
        nous_status = {}
        qwen_status = {}

    nous_logged_in = bool(nous_status.get("logged_in"))
    print(
        f"  {'Nous Portal':<12}  {check_mark(nous_logged_in)} "
        f"{'logged in' if nous_logged_in else 'not logged in (run: /model)'}"
    )
    if nous_logged_in:
        portal_url = nous_status.get("portal_base_url") or "（未知）"
        access_exp = _format_iso_timestamp(nous_status.get("access_expires_at"))
        key_exp = _format_iso_timestamp(nous_status.get("agent_key_expires_at"))
        refresh_label = "yes" if nous_status.get("has_refresh_token") else "no"
        print(f"    Portal URL: {portal_url}")
        print(f"    Access exp: {access_exp}")
        print(f"    Key exp:    {key_exp}")
        print(f"    Refresh:    {refresh_label}")

    qwen_logged_in = bool(qwen_status.get("logged_in"))
    print(
        f"  {'Qwen OAuth':<12}  {check_mark(qwen_logged_in)} "
        f"{'logged in' if qwen_logged_in else 'not logged in (run: qwen auth qwen-oauth)'}"
    )
    qwen_auth_file = qwen_status.get("auth_file")
    if qwen_auth_file:
        print(f"    Auth file:  {qwen_auth_file}")
    qwen_exp = qwen_status.get("expires_at_ms")
    if qwen_exp:
        from datetime import datetime, timezone
        print(f"    Access exp: {datetime.fromtimestamp(int(qwen_exp) / 1000, tz=timezone.utc).isoformat()}")
    if qwen_status.get("error") and not qwen_logged_in:
        print(f"    错误：      {qwen_status.get('error')}")

    # =========================================================================
    # Nous Subscription Features
    # =========================================================================
    if managed_nous_tools_enabled():
        features = get_nous_subscription_features(config)
        print()
        print(color("◆ Nous 订阅功能", Colors.CYAN, Colors.BOLD))
        if not features.nous_auth_present:
            print("  Nous 门户      ✗ 未登录")
        else:
            print("  Nous 门户      ✓ 托管工具可用")
        for feature in features.items():
            if feature.managed_by_nous:
                state = "通过 Nous 订阅启用"
            elif feature.active:
                current = feature.current_provider or "configured provider"
                state = f"通过 {current} 启用"
            elif feature.included_by_default and features.nous_auth_present:
                state = "订阅已包含，当前未选择"
            elif feature.key == "modal" and features.nous_auth_present:
                state = "订阅可用（可选）"
            else:
                state = "未配置"
            print(f"  {feature.label:<15} {check_mark(feature.available or feature.active or feature.managed_by_nous)} {state}")

    # =========================================================================
    # API-Key Providers
    # =========================================================================
    print()
    print(color("◆ API 密钥提供商", Colors.CYAN, Colors.BOLD))

    apikey_providers = {
        "Z.AI / GLM":       ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        "Kimi / Moonshot":  ("KIMI_API_KEY",),
        "MiniMax":          ("MINIMAX_API_KEY",),
        "MiniMax (China)":  ("MINIMAX_CN_API_KEY",),
    }
    for pname, env_vars in apikey_providers.items():
        key_val = ""
        for ev in env_vars:
            key_val = get_env_value(ev) or ""
            if has_usable_secret(key_val):
                break
        configured = has_usable_secret(key_val)
        label = "已配置" if configured else "未配置（运行：/model）"
        print(f"  {pname:<16} {check_mark(configured)} {label}")

    # =========================================================================
    # Terminal Configuration
    # =========================================================================
    print()
    print(color("◆ 终端后端", Colors.CYAN, Colors.BOLD))
    
    terminal_env = os.getenv("TERMINAL_ENV", "")
    if not terminal_env:
        # Fall back to config file value when env var isn't set
        # (VoidCube status doesn't go through cli.py's config loading)
        try:
            _cfg = load_config()
            terminal_env = _cfg.get("terminal", {}).get("backend", "local")
        except Exception:
            terminal_env = "local"
    print(f"  Backend:      {terminal_env}")
    
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST", "")
        ssh_user = os.getenv("TERMINAL_SSH_USER", "")
        print(f"  SSH Host:     {ssh_host or '(not set)'}")
        print(f"  SSH User:     {ssh_user or '(not set)'}")
    elif terminal_env == "docker":
        docker_image = os.getenv("TERMINAL_DOCKER_IMAGE", "python:3.14-slim")
        print(f"  Docker Image: {docker_image}")
    elif terminal_env == "podman":
        podman_image = os.getenv("TERMINAL_PODMAN_IMAGE", "python:3.14-slim")
        print(f"  Podman Image: {podman_image}")
    elif terminal_env == "daytona":
        daytona_image = os.getenv("TERMINAL_DAYTONA_IMAGE", "nikolaik/python-nodejs:python3.14-nodejs20")
        print(f"  Daytona Image: {daytona_image}")
    
    sudo_password = os.getenv("SUDO_PASSWORD", "")
    print(f"  Sudo：        {check_mark(bool(sudo_password))} {'已启用' if sudo_password else '已禁用'}")
    
    # =========================================================================
    # Gateway Status
    # =========================================================================
    print()
    print(color("◆ 网关服务", Colors.CYAN, Colors.BOLD))

    # Three-segment scene bar (baseline §8.1) — supervisor / agent / executor.
    # Rendered *before* the rest of the gateway block so users always see
    # the live activity of each reporter at a glance.
    _print_three_segment_scene_bar()
    
    if _is_termux():
        try:
            from ...infrastructure.gateway.service_launcher import find_gateway_pids
            gateway_pids = find_gateway_pids()
        except Exception:
            gateway_pids = []
        is_running = bool(gateway_pids)
        print(f"  状态：        {check_mark(is_running)} {'运行中' if is_running else '已停止'}")
        print("  管理器：      Termux / 手动进程")
        if gateway_pids:
            rendered = ", ".join(str(pid) for pid in gateway_pids[:3])
            if len(gateway_pids) > 3:
                rendered += ", ..."
            print(f"  PID(s):       {rendered}")
        else:
            print("  启动方式：    VoidCube gateway")
            print("  注意：        Termux 挂起时 Android 可能停止后台任务")

    elif sys.platform.startswith('linux'):
        from ...infrastructure.runtime.environment import is_container
        if is_container():
            # Docker/Podman: no systemd — check for running gateway processes
            try:
                from ...infrastructure.gateway.service_launcher import find_gateway_pids
                gateway_pids = find_gateway_pids()
                is_active = len(gateway_pids) > 0
            except Exception:
                is_active = False
            print(f"  状态：        {check_mark(is_active)} {'运行中' if is_active else '已停止'}")
            print("  管理器：      docker（前台）")
        else:
            try:
                from ...infrastructure.gateway.service_launcher import get_service_name
                _gw_svc = get_service_name()
            except Exception:
                _gw_svc = "VoidCube-gateway"
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", _gw_svc],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                is_active = result.stdout.strip() == "active"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                is_active = False
            print(f"  状态：        {check_mark(is_active)} {'运行中' if is_active else '已停止'}")
            print("  管理器：      systemd（用户）")
        
    elif sys.platform == 'darwin':
        from ...infrastructure.gateway.service_launcher import get_launchd_label
        try:
            result = subprocess.run(
                ["launchctl", "list", get_launchd_label()],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_loaded = result.returncode == 0
        except subprocess.TimeoutExpired:
            is_loaded = False
        print(f"  状态：        {check_mark(is_loaded)} {'已加载' if is_loaded else '未加载'}")
        print("  管理器：      launchd")
    else:
        print(f"  状态：        {color('不适用', Colors.DIM)}")
        print("  管理器：      （当前平台不支持）")
    
    # =========================================================================
    # Sessions
    # =========================================================================
    print()
    print(color("◆ 会话", Colors.CYAN, Colors.BOLD))
    
    sessions_file = get_VoidCube_home() / "sessions" / "sessions.json"
    if sessions_file.exists():
        import json
        try:
            with open(sessions_file, encoding="utf-8") as f:
                data = json.load(f)
                print(f"  活跃：        {len(data)} 个会话")
        except Exception:
            print("  活跃：        （读取会话文件时出错）")
    else:
        print("  活跃：        0")
    
    # =========================================================================
    # Deep checks
    # =========================================================================
    if deep:
        print()
        print(color("◆ 深度检查", Colors.CYAN, Colors.BOLD))
        
        # Check OpenRouter connectivity
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            try:
                import httpx
                response = httpx.get(
                    OPENROUTER_MODELS_URL,
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                    timeout=10
                )
                ok = response.status_code == 200
                print(f"  OpenRouter：   {check_mark(ok)} {'可访问' if ok else f'错误（{response.status_code}）'}")
            except Exception as e:
                print(f"  OpenRouter：   {check_mark(False)} 错误：{e}")
        
        # Check gateway port
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 18789))
            sock.close()
            # Port in use = gateway likely running
            port_in_use = result == 0
            # This is informational, not necessarily bad
            print(f"  端口 18789：   {'使用中' if port_in_use else '可用'}")
        except OSError:
            pass
    
    print()
    print(color("─" * 60, Colors.DIM))
    print(color("  Run 'VoidCube doctor' for detailed diagnostics", Colors.DIM))
    print(color("  Run '/api' to configure", Colors.DIM))
    print()
