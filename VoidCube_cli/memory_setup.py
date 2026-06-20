"""VoidCube memory setup|status — configure memory provider plugins.

Auto-detects installed memory providers via the plugin system.
Interactive curses-based UI for provider selection, then walks through
the provider's config schema. Writes config to config.yaml + .env.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from VoidCube_core.constants import get_VoidCube_home
from VoidCube_cli.cli_output import prompt


# ---------------------------------------------------------------------------
# Curses-based interactive picker (same pattern as VoidCube tools)
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    """Interactive single-select with arrow keys.

    items: list of (label, description) tuples.
    Returns selected index, or default on escape/quit.
    """
    from VoidCube_cli.curses_ui import curses_radiolist
    # Format (label, desc) tuples into display strings
    display_items = [
        f"{label}  {desc}" if desc else label
        for label, desc in items
    ]
    return curses_radiolist(title, display_items, selected=default, cancel_returns=default)


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    """Prompt for a value with optional default and secret masking."""
    return prompt(label, default=default, password=secret)


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------

def _install_dependencies(provider_name: str) -> None:
    """Install pip dependencies declared in plugin.yaml."""
    import subprocess
    from pathlib import Path as _Path

    plugin_dir = _Path(__file__).parent.parent / "plugins" / "memory" / provider_name
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        return

    try:
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return

    pip_deps = meta.get("pip_dependencies", [])
    if not pip_deps:
        return

    # pip name → import name mapping for packages where they differ
    _IMPORT_NAMES = {
        "hindsight-client": "hindsight_client",
        "hindsight-all": "hindsight",
    }

    # Check which packages are missing
    missing = []
    for dep in pip_deps:
        import_name = _IMPORT_NAMES.get(dep, dep.replace("-", "_").split("[")[0])
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    from VoidCube_cli.i18n import t

    print(f"\n  {t('memory.installing_deps', deps=', '.join(missing))}")

    import shutil
    uv_path = shutil.which("uv")
    if not uv_path:
        print(f"  {t('memory.uv_not_found')}")
        print(f"  {t('memory.install_uv')}")
        print(f"  {t('memory.re_run')}")
        return

    try:
        subprocess.run(
            [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing,
            check=True, timeout=120,
            capture_output=True,
        )
        print(f"  {t('memory.installed', deps=', '.join(missing))}")
    except subprocess.CalledProcessError as e:
        print(f"  {t('memory.failed_install', deps=', '.join(missing))}")
        stderr = (e.stderr or b"").decode()[:200]
        if stderr:
            print(f"    {stderr}")
        print(f"  {t('memory.run_manually', cmd=f'uv pip install --python {sys.executable} {" ".join(missing)}')}")
    except Exception as e:
        print(f"  {t('memory.install_failed', error=e)}")
        print(f"  {t('memory.run_manually', cmd=f'uv pip install --python {sys.executable} {" ".join(missing)}')}")

    # Also show external dependencies (non-pip) if any
    ext_deps = meta.get("external_dependencies", [])
    for dep in ext_deps:
        dep_name = dep.get("name", "")
        check_cmd = dep.get("check", "")
        install_cmd = dep.get("install", "")
        if check_cmd:
            try:
                import shlex
                subprocess.run(
                    shlex.split(check_cmd), capture_output=True, timeout=5
                )
            except Exception:
                if install_cmd:
                    print(f"\n  {t('memory.dep_not_found', dep=dep_name)}")
                    print(f"    {install_cmd}")


def _get_available_providers() -> list:
    """Discover memory providers from plugins/memory/.

    Returns list of (name, description, provider_instance) tuples.
    """
    try:
        from plugins.memory import discover_memory_providers, load_memory_provider
        raw = discover_memory_providers()
    except Exception:
        raw = []

    results = []
    for name, desc, available in raw:
        try:
            provider = load_memory_provider(name)
            if not provider:
                continue
        except Exception:
            continue

        schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        has_non_secrets = any(not f.get("secret") for f in schema)
        if has_secrets and has_non_secrets:
            setup_hint = "API key / local"
        elif has_secrets:
            setup_hint = "requires API key"
        elif not schema:
            setup_hint = "no setup needed"
        else:
            setup_hint = "local"

        results.append((name, setup_hint, provider))
    return results


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup_provider(provider_name: str) -> None:
    """Run memory setup for a specific provider, skipping the picker."""
    from VoidCube_cli.config import load_config, save_config

    providers = _get_available_providers()
    match = None
    for name, desc, provider in providers:
        if name == provider_name:
            match = (name, desc, provider)
            break

    if not match:
        print(f"\n  {t('memory.provider_not_found', provider=provider_name)}")
        print(f"  {t('memory.run_setup')}\n")
        return

    name, _, provider = match

    _install_dependencies(name)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    if hasattr(provider, "post_setup"):
        VoidCube_home = str(get_VoidCube_home())
        provider.post_setup(VoidCube_home, config)
        return

    # Fallback: generic schema-based setup (same as cmd_setup)
    config["memory"]["provider"] = name
    save_config(config)
    print(f"\n  {t('memory.provider', name=name)}")
    print(f"  {t('memory.activation_saved')}\n")


def cmd_setup(args) -> None:
    """Interactive memory provider setup wizard."""
    from VoidCube_cli.config import load_config, save_config
    from VoidCube_cli.i18n import t

    providers = _get_available_providers()

    if not providers:
        print(f"\n  {t('memory.no_providers')}")
        print(f"  {t('memory.install_plugin')}\n")
        return

    # Build picker items
    items = []
    for name, desc, _ in providers:
        items.append((name, f"— {desc}"))
    items.append((t("memory.builtin_only").replace("✓ ", ""), "— MEMORY.md / USER.md (default)"))

    builtin_idx = len(items) - 1
    selected = _curses_select(t("memory.setup"), items, default=builtin_idx)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    # Built-in only
    if selected >= len(providers) or selected < 0:
        config["memory"]["provider"] = ""
        save_config(config)
        print(f"\n  {t('memory.builtin_only')}")
        print(f"  {t('memory.saved_to_config')}\n")
        return

    name, _, provider = providers[selected]

    # Install pip dependencies if declared in plugin.yaml
    _install_dependencies(name)

    # If the provider has a post_setup hook, delegate entirely to it.
    # The hook handles its own config, connection test, and activation.
    if hasattr(provider, "post_setup"):
        VoidCube_home = str(get_VoidCube_home())
        provider.post_setup(VoidCube_home, config)
        return

    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []

    provider_config = config["memory"].get(name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    env_path = get_VoidCube_home() / ".env"
    env_writes = {}

    if schema:
        print(f"\n  {t('memory.configuring', name=name)}\n")

        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            # Dynamic default: look up default from another field's value
            default_from = field.get("default_from")
            if default_from and isinstance(default_from, dict):
                ref_field = default_from.get("field", "")
                ref_map = default_from.get("map", {})
                ref_value = provider_config.get(ref_field, "")
                if ref_value and ref_value in ref_map:
                    default = ref_map[ref_value]
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            # Skip fields whose "when" condition doesn't match
            when = field.get("when")
            if when and isinstance(when, dict):
                if not all(provider_config.get(k) == v for k, v in when.items()):
                    continue

            if choices and not is_secret:
                # Use curses picker for choice fields
                choice_items = [(c, "") for c in choices]
                current = provider_config.get(key, default)
                current_idx = 0
                if current and current in choices:
                    current_idx = choices.index(current)
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                provider_config[key] = choices[sel]
            elif is_secret:
                # Prompt for secret
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "set"
                    val = _prompt(f"{desc} (current: {masked}, blank to keep)", secret=True)
                else:
                    hint = f"  Get yours at {url}" if url else ""
                    if hint:
                        print(hint)
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                # Regular text prompt
                current = provider_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    provider_config[key] = val

    # Write activation key to config.yaml
    config["memory"]["provider"] = name
    save_config(config)

    # Write non-secret config to provider's native location
    VoidCube_home = str(get_VoidCube_home())
    if provider_config and hasattr(provider, "save_config"):
        try:
            provider.save_config(provider_config, VoidCube_home)
        except Exception as e:
            print(f"  {t('memory.failed_write_config', error=e)}")

    # Write secrets to .env
    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  {t('memory.provider', name=name)}")
    print(f"  {t('memory.activation_saved')}")
    if provider_config:
        print(f"  {t('memory.config_saved')}")
    if env_writes:
        print(f"  {t('memory.api_keys_saved')}")
    print(f"\n  {t('memory.start_new_session')}\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    """Append or update env vars in .env file."""
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)

    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    """Show current memory provider config."""
    from VoidCube_cli.config import load_config
    from VoidCube_cli.i18n import t

    config = load_config()
    mem_config = config.get("memory", {})
    provider_name = mem_config.get("provider", "")

    print(f"\n{t('memory.status')}\n" + "─" * 40)
    print(f"  {t('memory.builtin_always_active')}")
    print(f"  {t('memory.provider', name=provider_name or t('memory.none_builtin_only'))}")

    if provider_name:
        provider_config = mem_config.get(provider_name, {})
        if provider_config:
            print(f"\n  {provider_name} config:")
            for key, val in provider_config.items():
                print(f"    {key}: {val}")

        providers = _get_available_providers()
        found = any(name == provider_name for name, _, _ in providers)
        if found:
            print(f"\n  {t('memory.plugin_installed')}")
            for pname, _, p in providers:
                if pname == provider_name:
                    if p.is_available():
                        print(f"  {t('memory.status_available')}")
                    else:
                        print(f"  {t('memory.status_not_available')}")
                        schema = p.get_config_schema() if hasattr(p, "get_config_schema") else []
                        secrets = [f for f in schema if f.get("secret")]
                        if secrets:
                            print(f"  {t('memory.missing')}")
                            for s in secrets:
                                env_var = s.get("env_var", "")
                                url = s.get("url", "")
                                is_set = bool(os.environ.get(env_var))
                                mark = "✓" if is_set else "✗"
                                line = f"    {mark} {env_var}"
                                if url and not is_set:
                                    line += f"  → {url}"
                                print(line)
                    break
        else:
            print(f"\n  {t('memory.plugin_not_installed')}")
            print(f"  {t('memory.install_plugin_to', provider=provider_name)}")

    providers = _get_available_providers()
    if providers:
        print(f"\n  {t('memory.installed_plugins')}")
        for pname, desc, _ in providers:
            active = t("memory.active") if pname == provider_name else ""
            print(f"    • {pname}  ({desc}){active}")

    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def memory_command(args) -> None:
    """Route memory subcommands."""
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        cmd_setup(args)
    elif sub == "status":
        cmd_status(args)
    else:
        cmd_status(args)
