"""Internationalization (i18n) support for Voidcube CLI.

Provides translation functions, language management, and initialization logic
for supporting multiple languages in CLI output and prompts.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class i18n:
    """Internationalization manager with singleton pattern.
    
    Manages language files, translations, and language detection.
    """
    
    _instance: Optional[i18n] = None
    _initialized: bool = False
    
    def __new__(cls) -> i18n:
        """Singleton pattern - ensure only one instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        """Initialize the i18n manager."""
        if self._initialized:
            return
        
        self._current_locale: str = "zh_CN"
        self._translations: dict[str, Any] = {}
        self._locales_dir: Optional[Path] = None
        self._initialized = True
    
    def init(self, locales_dir: Optional[Path] = None) -> None:
        """Initialize i18n with locales directory.
        
        Args:
            locales_dir: Directory containing language files. If None,
                        uses default location (VoidCube_cli/locales).
        """
        if locales_dir is None:
            # Locale JSON remains a package asset during the migration. Prefer a
            # future canonical asset directory, then use the shipped legacy asset.
            canonical_dir = Path(__file__).parent / "locales"
            if canonical_dir.exists():
                locales_dir = canonical_dir
            else:
                # Locale JSON remains packaged with the compatibility package
                # until the asset move is completed.
                locales_dir = Path(resource_files("VoidCube_cli")) / "locales"
        
        self._locales_dir = locales_dir
        
        # Ensure locales directory exists
        if not self._locales_dir.exists():
            logger.warning(f"Locales directory not found: {self._locales_dir}")
            self._locales_dir.mkdir(parents=True, exist_ok=True)
    
    def set_locale(self, locale: str) -> None:
        """Set the current locale and load its translations.
        
        Args:
            locale: Locale code (e.g., "zh_CN", "en_US")
        """
        if locale == self._current_locale and locale in self._translations:
            return
        
        self._current_locale = locale
        self._load_locale(locale)
    
    def _load_locale(self, locale: str) -> None:
        """Load translations for a specific locale.
        
        Args:
            locale: Locale code to load
        """
        if self._locales_dir is None:
            self.init()
        
        locale_file = self._locales_dir / f"{locale}.json"
        
        if not locale_file.exists():
            logger.warning(f"Locale file not found: {locale_file}")
            # Fallback to en_US if available
            if locale != "en_US":
                self._load_locale("en_US")
            return
        
        try:
            with open(locale_file, "r", encoding="utf-8") as f:
                self._translations[locale] = json.load(f)
            logger.debug(f"Loaded locale: {locale}")
        except Exception as e:
            logger.error(f"Failed to load locale {locale}: {e}")
            # Fallback to en_US
            if locale != "en_US":
                self._load_locale("en_US")
    
    def translate(
        self,
        key: str,
        default: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        """Translate a key to the current locale.
        
        Args:
            key: Translation key (e.g., "commands.new.description")
            default: Default value if translation not found
            **kwargs: Parameters for string formatting
            
        Returns:
            Translated and formatted string
        """
        # Get translations for current locale
        locale_data = self._translations.get(self._current_locale, {})
        translations = locale_data.get("translations", {})
        
        # Navigate nested keys (e.g., "commands.new.description")
        value = translations
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        
        # If not found, try fallback to en_US
        if value is None and self._current_locale != "en_US":
            en_locale_data = self._translations.get("en_US", {})
            en_translations = en_locale_data.get("translations", {})
            value = en_translations
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break
        
        # If still not found, use default or key
        if value is None:
            if default is not None:
                value = default
            else:
                logger.debug(f"Translation not found: {key}")
                return key
        
        # Format with kwargs if provided
        if kwargs and isinstance(value, str):
            try:
                return value.format(**kwargs)
            except KeyError as e:
                logger.warning(f"Failed to format translation {key}: missing {e}")
                return value
        
        return str(value)
    
    def get_current_locale(self) -> str:
        """Get the current locale code."""
        return self._current_locale
    
    def get_available_locales(self) -> list[str]:
        """Get list of available locale codes."""
        if self._locales_dir is None:
            return []
        
        locales = []
        for file in self._locales_dir.glob("*.json"):
            locales.append(file.stem)
        return sorted(locales)


# Global i18n instance
_i18n: Optional[i18n] = None


def get_i18n() -> i18n:
    """Get the global i18n instance."""
    global _i18n
    if _i18n is None:
        _i18n = i18n()
    return _i18n


@lru_cache(maxsize=256)
def t(key: str, default: Optional[str] = None, **kwargs: Any) -> str:
    """Translate a key to the current locale (cached).
    
    This is the main translation function used throughout the codebase.
    
    Args:
        key: Translation key (e.g., "commands.new.description")
        default: Default value if translation not found
        **kwargs: Parameters for string formatting
        
    Returns:
        Translated and formatted string
        
    Examples:
        >>> t("commands.new.description")
        "开始新会话(新会话ID + 历史)"
        
        >>> t("banner.tools_count", count=5)
        "5 个工具"
    """
    i18n = get_i18n()
    return i18n.translate(key, default, **kwargs)


def set_locale(locale: str) -> None:
    """Set the current locale.
    
    Args:
        locale: Locale code (e.g., "zh_CN", "en_US")
    """
    i18n = get_i18n()
    i18n.set_locale(locale)
    # Clear the translation cache when locale changes
    t.cache_clear()


def init_i18n(locale: Optional[str] = None) -> None:
    """Initialize i18n system with automatic language detection.
    
    Detection priority:
    1. Explicitly provided locale
    2. VOIDCUBE_LANG environment variable
    3. Configuration file (display.language)
    4. LANG environment variable (extract locale)
    5. System default (en_US)
    
    Args:
        locale: Optional explicit locale to use
    """
    i18n = get_i18n()
    i18n.init()
    
    # Determine locale to use
    target_locale = locale
    
    if target_locale is None:
        # Check VOIDCUBE_LANG environment variable
        target_locale = os.environ.get("VOIDCUBE_LANG")
    
    if target_locale is None:
        # Check configuration file — read directly to avoid importing VoidCube_app.config
        # (~62ms import chain) at module init time
        try:
            from ...infrastructure.config.runtime_paths import get_config_path
            _config_path = get_config_path()
            if _config_path.exists():
                import yaml as _yaml
                with open(_config_path, encoding="utf-8") as _f:
                    _config = _yaml.safe_load(_f) or {}
                if _config and "display" in _config:
                    config_locale = _config["display"].get("language")
                    if config_locale:
                        target_locale = config_locale
        except Exception:
            pass
    
    if target_locale is None:
        # Check LANG environment variable
        lang_env = os.environ.get("LANG", "")
        if lang_env:
            # Extract locale from LANG (e.g., "zh_CN.UTF-8" -> "zh_CN")
            target_locale = lang_env.split(".")[0].split("_")[0]
            # Map common language codes to full locales
            lang_map = {
                "zh": "zh_CN",
                "en": "en_US",
            }
            target_locale = lang_map.get(target_locale, target_locale)
    
    if target_locale is None:
        # Default to zh_CN (Chinese)
        target_locale = "zh_CN"
    
    # Set the locale
    i18n.set_locale(target_locale)
    
    logger.info(f"Initialized i18n with locale: {target_locale}")


def get_available_locales() -> list[str]:
    """Get list of available locale codes."""
    i18n = get_i18n()
    return i18n.get_available_locales()
