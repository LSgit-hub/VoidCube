"""VoidCube Core Infrastructure Module.

This package contains the foundational components shared across all VoidCube modules:
- Constants and configuration
- Custom exceptions
- Logging infrastructure
- State management
- Time utilities
- Utility functions
- Internationalization support

These modules are designed to be import-safe with minimal dependencies.
"""

from .constants import *
from .exceptions import *
from .logging import *
from .state import *
from .time import *
from .utils import *
from .i18n import *

__all__ = [
    # From constants
    'APP_NAME', 'APP_VERSION', 'DEFAULT_CONFIG_PATH',
    # From exceptions
    'VoidCubeError', 'ConfigurationError', 'AuthenticationError',
    # From logging
    'VoidCubeLogger',
    # From state
    'SessionState',
    # From time
    'get_current_time', 'to_utc',
    # From utils
    'is_truthy_value', 'env_var_enabled',
    # From i18n
    'get_lang', 'set_lang', 't',
]