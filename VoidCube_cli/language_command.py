"""Language command handler for Voidcube CLI."""

from __future__ import annotations

import sys
from typing import Any


def handle_language_command(self: Any, cmd: str) -> None:
    """Handle /language [-CN|-EN] — show or change the display language.
    
    Args:
        self: VoidcubeCLI instance
        cmd: The full command string (e.g., "/language -CN")
    """
    try:
        from VoidCube_cli.i18n import (
            set_locale,
            get_available_locales,
            get_i18n,
            t,
        )
    except ImportError:
        print("i18n 模块不可用。")
        return

    parts = cmd.strip().split(maxsplit=1)
    
    # Map parameters to locale codes
    param_map = {
        "-cn": "zh_CN",
        "-en": "en_US",
    }
    
    if len(parts) < 2 or not parts[1].strip():
        # Show current language and list available
        i18n = get_i18n()
        current = i18n.get_current_locale()
        available = get_available_locales()
        
        # Fallback: if no locales found, use defaults
        if not available:
            available = ["zh_CN", "en_US"]
        
        # Get language names via i18n
        def _lang_name(locale: str) -> str:
            name_map = {
                "zh_CN": t('language_command.lang_zh_CN', default='简体中文'),
                "en_US": t('language_command.lang_en_US', default='English'),
            }
            return name_map.get(locale, locale)
        
        print(f"\n  {t('language_command.current', locale=current, name=_lang_name(current))}")
        print(f"  {t('language_command.available')}")
        for locale in available:
            marker = " ●" if locale == current else "  "
            name = _lang_name(locale)
            print(f"   {marker} {locale} - {name}")
        
        print(f"\n  {t('language_command.usage')}")
        print(f"  {t('language_command.examples')}")
        print(f"    {t('language_command.example_show')}")
        print(f"    {t('language_command.example_cn')}")
        print(f"    {t('language_command.example_en')}")
        print(f"\n  {t('language_command.tip_env')}\n")
        return

    param = parts[1].strip().lower()
    
    # Resolve parameter to locale
    new_locale = param_map.get(param)
    
    if new_locale is None:
        print(f"  {t('language_command.invalid_param', param=parts[1].strip())}")
        print(f"  {t('language_command.usage')}")
        return

    # Set the new locale
    set_locale(new_locale)
    
    # Clear translation cache to force refresh
    try:
        from VoidCube_cli.i18n import t as _t
        _t.cache_clear()
    except Exception:
        pass
    
    # Rebuild command lookups to update translations
    try:
        from VoidCube_cli.commands import rebuild_lookups
        rebuild_lookups()
    except Exception:
        pass
    
    # Try to save to config
    try:
        from VoidCube_app.config import read_raw_config, save_config
        config = read_raw_config()
        if not config:
            config = {}
        if "display" not in config:
            config["display"] = {}
        config["display"]["language"] = new_locale
        save_config(config)
        print(f"  {t('language_command.set_to_saved', locale=new_locale)}")
    except Exception as e:
        print(f"  {t('language_command.set_to', locale=new_locale)}")
        import logging
        logging.getLogger(__name__).debug(f"Failed to save language config: {e}")
    
    # Show confirmation
    if new_locale == "zh_CN":
        print(f"  {t('language_command.switched_cn')}")
    else:
        print(f"  {t('language_command.switched_en')}")
